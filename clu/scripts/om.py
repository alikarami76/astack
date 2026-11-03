#!/usr/bin/env python3
"""Read-only OpenMetadata client for clu. Examples:
  om.py pull                      snapshot tables, metrics, glossary terms, tags and domains, then refresh state.json
  om.py search "rrp price"        keyword search (use when the openmetadata MCP tools are not loaded)
  om.py status                    latest snapshot, its age and counts

The base URL and token come from the `openmetadata` MCP entry in ~/.claude.json, or from OM_URL and OM_TOKEN.
Snapshots go to $CLU_OM_DIR (default ~/agent-stack/clu-snapshots/om/<date>/); they hold company metadata
and are never committed. HTTP goes through curl: the system Python's CA store rejects some company certificates.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import subprocess
import sys
import urllib.parse
from pathlib import Path

INDEXES = ("table", "metric", "glossaryTerm", "tag", "domain")
PAGE = 100
STALE_DAYS = 7
CLAUDE_JSON = Path.home() / ".claude.json"


def om_dir() -> Path:
    return Path(os.environ.get("CLU_OM_DIR") or Path.home() / "agent-stack-data/clu-snapshots/om")


def config(claude_json: Path = CLAUDE_JSON) -> tuple[str, str]:
    """(REST base ending in /api/v1/, Authorization header value)."""
    url, tok = os.environ.get("OM_URL"), os.environ.get("OM_TOKEN")
    auth = f"Bearer {tok}" if tok else None
    if not (url and auth):
        try:
            ent = json.loads(Path(claude_json).read_text())["mcpServers"]["openmetadata"]
        except (OSError, KeyError, ValueError):
            ent = {}
        url = url or ent.get("url")
        auth = auth or (ent.get("headers") or {}).get("Authorization")
    if not (url and auth):
        raise ValueError("no openmetadata MCP entry with an Authorization header in ~/.claude.json, and OM_URL/OM_TOKEN "
                         "are not set; add the server with `claude mcp add ... openmetadata <url>/mcp --header ...`")
    base = re.sub(r"/mcp/?$", "", url.rstrip("/"))
    return base + "/api/v1/", auth


def curl_fetch(path: str, params: dict) -> dict:
    base, auth = config()
    u = base + path + ("?" + urllib.parse.urlencode(params) if params else "")
    out = subprocess.run(["curl", "-sS", "-m", "90", "-H", f"Authorization: {auth}", u],
                         capture_output=True, text=True)
    if out.returncode:
        raise ValueError(f"curl failed for {path}: {out.stderr.strip()[:200]}")
    try:
        return json.loads(out.stdout)
    except ValueError:
        raise ValueError(f"OpenMetadata returned non-JSON for {path}: {out.stdout[:200]}") from None


def search_all(fetch, index: str, q: str = "*") -> list[dict]:
    rows, start = [], 0
    while True:
        d = fetch("search/query", {"q": q, "index": index, "from": start, "size": PAGE})
        hits = (d.get("hits") or {}).get("hits") or []
        rows += [h["_source"] for h in hits]
        total = ((d.get("hits") or {}).get("total") or {}).get("value", 0)
        start += PAGE
        if not hits or start >= total:
            return rows


def pull(root: Path | None = None, fetch=None, today: _dt.date | None = None) -> Path:
    root, fetch = Path(root or om_dir()), fetch or curl_fetch
    snap = root / (today or _dt.date.today()).isoformat()
    snap.mkdir(parents=True, exist_ok=True)
    counts = {}
    for ix in INDEXES:
        rows = search_all(fetch, ix)
        (snap / f"{ix}.json").write_text(json.dumps(rows, ensure_ascii=False))
        counts[ix] = len(rows)
    version = (fetch("system/version", {}) or {}).get("version")
    state = {"snapshot": str(snap), "pulled_at": _dt.datetime.now().isoformat(timespec="seconds"),
             "om_version": version, "counts": counts, "conflicts": []}
    (root / "state.json").write_text(json.dumps(state, ensure_ascii=False, indent=1))
    return snap


def state() -> dict | None:
    f = om_dir() / "state.json"
    try:
        return json.loads(f.read_text())
    except (OSError, ValueError):
        return None


def save_state(st: dict) -> None:
    (om_dir() / "state.json").write_text(json.dumps(st, ensure_ascii=False, indent=1))


def age_days(now: _dt.datetime | None = None) -> int | None:
    st = state()
    if not st or not st.get("pulled_at"):
        return None
    return ((now or _dt.datetime.now()) - _dt.datetime.fromisoformat(st["pulled_at"])).days


def load(index: str, snapshot: Path | None = None) -> list[dict]:
    if snapshot is None:
        st = state()
        if not st:
            raise FileNotFoundError("no OpenMetadata snapshot yet; run `om.py pull`")
        snapshot = Path(st["snapshot"])
    f = Path(snapshot) / f"{index}.json"
    return json.loads(f.read_text()) if f.exists() else []


SEM = re.compile(r"\n*\[semantics\]\s*\n?(.*)\Z", re.S)


def split_semantics(desc: str | None) -> tuple[str, dict]:
    """OM column descriptions end in a `[semantics]` JSON block. Returns (prose, parsed block or {})."""
    desc = desc or ""
    m = SEM.search(desc)
    if not m:
        return desc.strip(), {}
    try:
        sem = json.loads(m.group(1).strip())
    except ValueError:
        sem = {}
    return desc[:m.start()].strip(), sem if isinstance(sem, dict) else {}


def tag_fqns(ent: dict) -> list[str]:
    return sorted({t.get("tagFQN") for t in ent.get("tags") or [] if t.get("tagFQN")})


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="om.py")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("pull")
    s = sub.add_parser("search")
    s.add_argument("query")
    s.add_argument("--index", default="all", help="table, metric, glossaryTerm, ... or all")
    s.add_argument("--k", type=int, default=10)
    sub.add_parser("status")
    a = p.parse_args(argv)
    try:
        if a.cmd == "pull":
            snap = pull()
            print(json.dumps({"snapshot": str(snap), **(state() or {}).get("counts", {})}))
            print("next: python3 kb/kb.py import-legacy --only om", file=sys.stderr)
        elif a.cmd == "search":
            ix = "dataAsset" if a.index == "all" else a.index
            d = curl_fetch("search/query", {"q": a.query, "index": ix, "size": a.k})
            for h in (d.get("hits") or {}).get("hits") or []:
                s_ = h["_source"]
                print(f"{s_.get('entityType', ix)}  {s_.get('fullyQualifiedName')} — "
                      f"{' '.join((s_.get('description') or '').split())[:160]}")
        elif a.cmd == "status":
            st = state()
            if not st:
                print("no snapshot yet; run `om.py pull`")
            else:
                print(json.dumps({"snapshot": st["snapshot"], "age_days": age_days(), "counts": st.get("counts"),
                                  "conflicts": len(st.get("conflicts") or [])}))
    except (ValueError, FileNotFoundError) as e:
        print(f"om.py {a.cmd}: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
