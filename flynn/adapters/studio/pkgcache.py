#!/usr/bin/env python3
"""Studio's package cache on a team server: what people's computers install, served from the server itself.

Three kinds of files, all kept in Postgres (they survive a redeploy) with a copy on the server's disk:
  npm      a pull-through cache of the npm registry at /npm/ for the App builder. Tarballs never change once
           published, so they are kept for good; package metadata is refreshed after META_TTL and served stale when the
           upstream registry can't be reached. npm checks every tarball against the integrity in the metadata, so the
           cache can't change what is installed. The app template's lockfile is fetched ahead of time (warm).
  wheels   the flynn agent installer's Python packages (deploy/server/wheels.sh fills the folder; restore-dir and
           save-dir keep it across deploys).
  node     Node.js for Macs that have none (or one older than 18): the official macOS builds, checked against
           nodejs.org's SHASUMS256.txt.

  pkgcache.py warm-npm <package-lock.json>      fetch the lockfile's tarballs (macOS and platform-free ones)
  pkgcache.py restore-dir <prefix> <folder>     write cached files under <prefix> into the folder
  pkgcache.py save-dir <prefix> <folder>        cache every file in the folder under <prefix>
  pkgcache.py fetch-node [22]                   cache the latest Node.js <major> macOS builds
  pkgcache.py status

Settings: STUDIO_CACHE_DB_URL (Postgres; without it the cache is the disk only), STUDIO_CACHE_DIR (default
$FLYNN_HOME/cache/pkg), STUDIO_CACHE_MAX_MB (default 3072), STUDIO_NPM_UPSTREAM (default https://registry.npmjs.org).
"""
from __future__ import annotations

import hashlib
import json
import shutil
import os
import re
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

PART = 8 * 1024 * 1024
META_TTL = 600.0
TARBALL = re.compile(r"^(@[a-z0-9][\w.-]*/)?[a-z0-9][\w.-]*/-/[\w.@+-]+\.tgz$", re.I)
NAME = re.compile(r"^(@[a-z0-9][\w.-]*(/|%2f))?[a-z0-9][\w.-]*$", re.I)
_locks: dict[str, threading.Lock] = {}
_locks_lock = threading.Lock()

DDL = """
CREATE TABLE IF NOT EXISTS studio_blob (
  key text PRIMARY KEY, size bigint NOT NULL, sha256 text NOT NULL, parts integer NOT NULL,
  meta jsonb NOT NULL DEFAULT '{}'::jsonb, created timestamptz NOT NULL DEFAULT now(),
  last_used timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS studio_blob_part (
  key text NOT NULL REFERENCES studio_blob(key) ON DELETE CASCADE, n integer NOT NULL, data bytea NOT NULL,
  PRIMARY KEY (key, n))"""


def log(msg: str) -> None:
    print(time.strftime("%H:%M:%S ") + "pkgcache   " + msg, file=sys.stderr, flush=True)


def cache_dir() -> Path:
    d = Path(os.environ.get("STUDIO_CACHE_DIR") or Path(os.environ.get("FLYNN_HOME", str(Path.home() / ".flynn"))) / "cache" / "pkg")
    d.mkdir(parents=True, exist_ok=True)
    return d


def upstream() -> str:
    return (os.environ.get("STUDIO_NPM_UPSTREAM") or "https://registry.npmjs.org").rstrip("/")


def _lock(key: str) -> threading.Lock:
    with _locks_lock:
        return _locks.setdefault(key, threading.Lock())


_db_state: dict = {}


def _db():
    """A Postgres connection (one per thread), or None when the cache is disk only."""
    url = os.environ.get("STUDIO_CACHE_DB_URL")
    if not url:
        return None
    local = _db_state.setdefault("local", threading.local())
    c = getattr(local, "conn", None)
    if c is not None and not c.closed:
        return c
    try:
        import psycopg
        c = psycopg.connect(url, autocommit=True, connect_timeout=10)
        if not _db_state.get("ddl"):
            for stmt in DDL.split(";"):
                c.execute(stmt)
            _db_state["ddl"] = True
        local.conn = c
        return c
    except Exception as e:  # noqa: BLE001 - the disk copy still works
        if time.time() - _db_state.get("warned", 0) > 300:
            log(f"Postgres not available ({type(e).__name__}); caching on disk only")
            _db_state["warned"] = time.time()
        return None


def _path(key: str) -> Path:
    if ".." in key.split("/") or key.startswith("/"):
        raise ValueError("bad cache key")
    return cache_dir() / key


# ---------------------------------------------------------------- the store
def get(key: str) -> Path | None:
    """The cached file for key: from disk, else from Postgres (written back to disk), else None."""
    p = _path(key)
    if p.is_file():
        return p
    c = _db()
    if c is None:
        return None
    try:
        row = c.execute("SELECT size, sha256, parts FROM studio_blob WHERE key = %s", (key,)).fetchone()
        if not row:
            return None
        h = hashlib.sha256()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + f".{os.getpid()}.part")
        with open(tmp, "wb") as f:
            for (data,) in c.execute("SELECT data FROM studio_blob_part WHERE key = %s ORDER BY n", (key,)):
                f.write(data)
                h.update(data)
        if h.hexdigest() != row[1]:
            tmp.unlink(missing_ok=True)
            c.execute("DELETE FROM studio_blob WHERE key = %s", (key,))
            log(f"dropped a damaged copy of {key}")
            return None
        tmp.replace(p)
        c.execute("UPDATE studio_blob SET last_used = now() WHERE key = %s", (key,))
        return p
    except Exception as e:  # noqa: BLE001
        log(f"read {key} from Postgres failed: {e}")
        return None


def put(key: str, src: Path, meta: dict | None = None, save: bool = True) -> Path:
    """Keep src under key (moved into the disk cache) and, with save, in Postgres too."""
    p = _path(key)
    p.parent.mkdir(parents=True, exist_ok=True)
    os.replace(src, p)
    if save:
        c = _db()
        if c is not None:
            try:
                h, n = hashlib.sha256(), 0
                with c.transaction():
                    c.execute("DELETE FROM studio_blob WHERE key = %s", (key,))
                    c.execute("INSERT INTO studio_blob (key, size, sha256, parts, meta) VALUES (%s, %s, '', 0, %s)",
                              (key, p.stat().st_size, json.dumps(meta or {})))
                    with open(p, "rb") as f:
                        while True:
                            chunk = f.read(PART)
                            if not chunk:
                                break
                            h.update(chunk)
                            c.execute("INSERT INTO studio_blob_part (key, n, data) VALUES (%s, %s, %s)", (key, n, chunk))
                            n += 1
                    c.execute("UPDATE studio_blob SET sha256 = %s, parts = %s WHERE key = %s", (h.hexdigest(), n, key))
            except Exception as e:  # noqa: BLE001 - the disk copy still serves
                log(f"save {key} to Postgres failed: {e}")
    return p


def trim(max_mb: float | None = None) -> int:
    """Drop the least recently used files until the cache in Postgres fits; returns how many were dropped."""
    limit = (max_mb if max_mb is not None else float(os.environ.get("STUDIO_CACHE_MAX_MB") or 3072)) * 1024 * 1024
    c = _db()
    if c is None:
        return 0
    total = c.execute("SELECT coalesce(sum(size), 0) FROM studio_blob").fetchone()[0]
    dropped = 0
    for key, size in c.execute("SELECT key, size FROM studio_blob WHERE key LIKE 'npm/%%' ORDER BY last_used").fetchall():
        if total <= limit:
            break
        c.execute("DELETE FROM studio_blob WHERE key = %s", (key,))
        _path(key).unlink(missing_ok=True)
        total -= size
        dropped += 1
    return dropped


_ctx: list = []


def _ssl():
    """The system's trusted certificates (truststore), else certifi, else Python's defaults."""
    if not _ctx:
        import ssl
        try:
            import truststore
            _ctx.append(truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT))
        except Exception:  # noqa: BLE001
            try:
                import certifi
                _ctx.append(ssl.create_default_context(cafile=certifi.where()))
            except Exception:  # noqa: BLE001
                _ctx.append(ssl.create_default_context())
    return _ctx[0]


def _fetch(url: str, dest: Path, headers: dict | None = None, timeout: float = 120) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "flynn-studio-cache/1", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl()) as r, open(dest, "wb") as f:
        while True:
            chunk = r.read(1024 * 1024)
            if not chunk:
                break
            f.write(chunk)
        return {"content-type": r.headers.get("Content-Type") or "application/octet-stream",
                "content-encoding": r.headers.get("Content-Encoding") or ""}


def _tmp() -> Path:
    fd, name = tempfile.mkstemp(dir=cache_dir(), prefix=".dl-")
    os.close(fd)
    return Path(name)


# ---------------------------------------------------------------- npm
def npm_tarball(path: str) -> Path | None:
    """A tarball (immutable): cached, or fetched once from the upstream registry."""
    key = "npm/" + path
    hit = get(key)
    if hit:
        return hit
    with _lock(key):
        hit = get(key)
        if hit:
            return hit
        tmp = _tmp()
        try:
            _fetch(f"{upstream()}/{path}", tmp, timeout=300)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
        return put(key, tmp, {"from": "npm"})


def npm_meta(path: str, accept: str) -> tuple[Path, dict] | None:
    """Package metadata: fresh for META_TTL, then refetched; the old copy is served when the registry fails."""
    kind = "corgi" if "vnd.npm.install-v1" in (accept or "") else "full"
    key = f"npm-meta/{kind}/{path.replace('%2f', '/').replace('%2F', '/')}.json"
    p = _path(key)
    mp = p.with_suffix(".hdr")
    fresh = p.is_file() and time.time() - p.stat().st_mtime < META_TTL
    if not fresh:
        with _lock(key):
            fresh = p.is_file() and time.time() - p.stat().st_mtime < META_TTL
            if not fresh:
                tmp = _tmp()
                try:
                    hdr = _fetch(f"{upstream()}/{path}", tmp, {"Accept": accept or "application/json",
                                                               "Accept-Encoding": "gzip"}, timeout=60)
                    put(key, tmp, save=False)
                    mp.write_text(json.dumps(hdr))
                except urllib.error.HTTPError as e:
                    tmp.unlink(missing_ok=True)
                    if e.code == 404:
                        return None
                    if not p.is_file():
                        raise
                    log(f"registry answered {e.code} for {path}; serving the cached copy")
                except (urllib.error.URLError, OSError) as e:
                    tmp.unlink(missing_ok=True)
                    if not p.is_file():
                        raise
                    log(f"registry not reachable for {path} ({e}); serving the cached copy")
    hdr = json.loads(mp.read_text()) if mp.is_file() else {"content-type": "application/json"}
    return p, hdr


def handle(h, path: str) -> None:
    """GET /npm/<path> for npm (registry=<server>/npm/). h is Studio's request handler."""
    rest = path[len("/npm/"):]
    try:
        if rest in ("-/ping", "-/ping/"):
            return h._json({"ok": True, "cache": "flynn studio"})
        if TARBALL.match(rest):
            f = npm_tarball(rest)
            return _send_file(h, f, {"content-type": "application/octet-stream"})
        if NAME.match(rest):
            r = npm_meta(rest, h.headers.get("Accept") or "")
            if r is None:
                return h._json({"error": "Not found"}, 404)
            return _send_file(h, r[0], r[1])
        return h._json({"error": "not a package path"}, 404)
    except urllib.error.HTTPError as e:
        return h._json({"error": f"registry answered {e.code}"}, 404 if e.code == 404 else 502)
    except (urllib.error.URLError, OSError) as e:
        return h._json({"error": f"registry not reachable: {e}"[:200]}, 502)


def _send_file(h, f: Path, hdr: dict) -> None:
    h.send_response(200)
    h.send_header("Content-Type", hdr.get("content-type") or "application/octet-stream")
    if hdr.get("content-encoding"):
        h.send_header("Content-Encoding", hdr["content-encoding"])
    h.send_header("Content-Length", str(f.stat().st_size))
    h.send_header("Cache-Control", "no-store")
    h.end_headers()
    with open(f, "rb") as src:
        while True:
            chunk = src.read(1024 * 1024)
            if not chunk:
                break
            h.wfile.write(chunk)


def lock_tarballs(lock: dict) -> list[str]:
    """Tarball paths a Mac needs from a package-lock.json (platform-free and macOS packages only)."""
    out = []
    reg = "https://registry.npmjs.org/"
    for v in (lock.get("packages") or {}).values():
        r = v.get("resolved") or ""
        osl = v.get("os") or []
        if r.startswith(reg) and (not osl or "darwin" in osl):
            out.append(r[len(reg):])
    return sorted(set(out))


def warm_npm(lockfile: Path) -> dict:
    ok = bad = 0
    for t in lock_tarballs(json.loads(Path(lockfile).read_text())):
        try:
            npm_tarball(t)
            ok += 1
        except Exception as e:  # noqa: BLE001
            bad += 1
            log(f"could not cache {t}: {e}")
    return {"cached": ok, "failed": bad}


# ---------------------------------------------------------------- folders (wheels) and Node.js
def _db_to_file(c, key: str, dest: Path) -> bool:
    """Write a cached file straight from Postgres to dest (no second copy on the server's disk)."""
    row = c.execute("SELECT sha256 FROM studio_blob WHERE key = %s", (key,)).fetchone()
    if not row:
        return False
    h = hashlib.sha256()
    tmp = dest.with_name(dest.name + ".part")
    with open(tmp, "wb") as f:
        for (data,) in c.execute("SELECT data FROM studio_blob_part WHERE key = %s ORDER BY n", (key,)):
            f.write(data)
            h.update(data)
    if h.hexdigest() != row[0]:
        tmp.unlink(missing_ok=True)
        return False
    tmp.replace(dest)
    return True


def restore_dir(prefix: str, folder: Path) -> int:
    """Cached files under prefix into folder, straight from Postgres (the folder is the only copy on disk)."""
    folder.mkdir(parents=True, exist_ok=True)
    c = _db()
    keys = [k for (k,) in c.execute("SELECT key FROM studio_blob WHERE key LIKE %s", (prefix + "%",))] if c else []
    n = 0
    for k in keys:
        dest = folder / Path(k).name
        if not dest.is_file() and _db_to_file(c, k, dest):
            n += 1
    return n


def save_dir(prefix: str, folder: Path) -> int:
    c = _db()
    have = {k for (k,) in c.execute("SELECT key FROM studio_blob WHERE key LIKE %s", (prefix + "%",))} if c else set()
    n = 0
    for f in sorted(folder.glob("*")):
        if f.is_file() and not f.name.endswith(".part") and prefix + f.name not in have:
            tmp = _tmp()
            shutil.copyfile(f, tmp)
            put(prefix + f.name, tmp)
            _path(prefix + f.name).unlink(missing_ok=True)     # the folder keeps the disk copy; Postgres the saved one
            n += 1
    return n


NODE_DIST = "https://nodejs.org/dist"
NODE_ARCHES = ("darwin-arm64", "darwin-x64")


def fetch_node(major: str = "22") -> dict:
    """Cache the latest Node.js <major> macOS builds, checked against the release's SHASUMS256.txt."""
    base = f"{NODE_DIST}/latest-v{major}.x"
    tmp = _tmp()
    _fetch(f"{base}/SHASUMS256.txt", tmp, timeout=60)
    sums = {}
    for line in tmp.read_text().splitlines():
        parts = line.split()
        if len(parts) == 2:
            sums[parts[1]] = parts[0]
    tmp.unlink(missing_ok=True)
    files = {}
    for arch in NODE_ARCHES:
        name = next((n for n in sums if n.endswith(f"-{arch}.tar.gz") and n.startswith("node-v")), None)
        if not name:
            continue
        key = f"node/{name}"
        if not get(key):
            t = _tmp()
            _fetch(f"{base}/{name}", t, timeout=600)
            if hashlib.sha256(t.read_bytes()).hexdigest() != sums[name]:
                t.unlink(missing_ok=True)
                raise RuntimeError(f"{name} doesn't match nodejs.org's checksum")
            put(key, t, {"sha256": sums[name]})
        files[arch] = {"file": name, "sha256": sums[name], "version": name.split("-")[1]}
    m = _tmp()
    m.write_text(json.dumps(files))
    put("node/manifest.json", m)
    return files


def node_file(name: str) -> Path | None:
    if not re.fullmatch(r"manifest\.json|node-v[\d.]+-darwin-(arm64|x64)\.tar\.gz", name or ""):
        return None
    return get("node/" + name)


def status() -> dict:
    c = _db()
    out = {"disk_mb": round(sum(f.stat().st_size for f in cache_dir().rglob("*") if f.is_file()) / 1048576, 1)}
    if c:
        for kind, n, size in c.execute("SELECT split_part(key, '/', 1), count(*), coalesce(sum(size), 0) FROM studio_blob GROUP BY 1"):
            out[kind] = {"files": int(n), "mb": round(float(size) / 1048576, 1)}
    return out


def main(argv=None) -> int:
    a = list(sys.argv[1:] if argv is None else argv)
    cmd = a[0] if a else "status"
    if cmd == "warm-npm":
        r = warm_npm(Path(a[1]))
        trim()
    elif cmd == "restore-dir":
        r = {"restored": restore_dir(a[1], Path(a[2]))}
    elif cmd == "save-dir":
        r = {"saved": save_dir(a[1], Path(a[2]))}
    elif cmd == "fetch-node":
        r = fetch_node(a[1] if len(a) > 1 else "22")
    elif cmd == "status":
        r = status()
    else:
        print(__doc__)
        return 2
    print(json.dumps(r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
