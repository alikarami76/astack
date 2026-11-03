# scripts/qindex/lookup.py
"""The "does this already exist?" lookup: index + concept map + live git grep (+ optional vectors), fused with RRF."""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

import gitsrc
from qindex import db
from qindex.names import split_words

RRF_K = 60
POOL = 40


@dataclass
class Hit:
    repo: str
    file: str
    line: int
    kind: str = ""
    name: str = ""
    signature: str = ""
    sources: list = field(default_factory=list)
    score: float = 0.0


def _fts_query(terms: list[str]) -> str:
    clean = (re.sub(r"[^\w]", "", w, flags=re.UNICODE).replace("_", "") for t in terms for w in split_words(t))
    words = list(dict.fromkeys(w for w in clean if len(w) >= 2))
    return " OR ".join(f'"{w}"*' if len(w) >= 3 else f'"{w}"' for w in words)


def _rows_to_hits(repo: str, rows) -> list[Hit]:
    return [Hit(repo, r["file"], r["line"], r["kind"], r["name"], r["signature"]) for r in rows]


def _index_ranked(conn, repo: str, terms: list[str], kind: str | None) -> list[Hit]:
    kf, ka = (" AND s.kind = ?", [kind]) if kind else ("", [])
    exact = conn.execute(f"SELECT s.* FROM symbols s WHERE s.name COLLATE NOCASE IN ({','.join('?' * len(terms))}){kf} LIMIT ?",
                         [*terms, *ka, POOL]).fetchall()
    fq = _fts_query(terms)
    fts = []
    if fq:
        try:
            fts = conn.execute(f"SELECT s.* FROM sym_fts f JOIN symbols s ON s.id = f.rowid WHERE sym_fts MATCH ?{kf} "
                               f"ORDER BY bm25(sym_fts, 10.0, 2.0, 1.0, 0.5) LIMIT ?", [fq, *ka, POOL]).fetchall()
        except sqlite3.OperationalError:
            fts = []  # never let odd query text crash the lookup; the exact-name and grep sources still answer
    seen, out = set(), []
    for r in [*exact, *fts]:
        if r["id"] not in seen:
            seen.add(r["id"])
            out.append(r)
    return _rows_to_hits(repo, out)


def _concept_ranked(kb_conn, conns: dict, query: str) -> list[Hit]:
    if kb_conn is None:
        return []
    import store
    out = []
    for r in store.search(kb_conn, query, etype="concept", k=5):
        row = store.get(kb_conn, r["id"])
        for impl in json.loads(row.get("implementations") or "[]"):
            conn = conns.get(impl.get("repo"))
            line, kind, sig = 1, impl.get("kind", ""), ""
            if conn is not None:
                s = conn.execute("SELECT line, kind, signature FROM symbols WHERE file = ? AND name = ? LIMIT 1",
                                 (impl["file"], impl.get("symbol", ""))).fetchone()
                if s:
                    line, kind, sig = s["line"], s["kind"], s["signature"]
            out.append(Hit(impl["repo"], impl["file"], line, kind, impl.get("symbol", ""), sig))
    return out


def _grep_terms(query: str, terms: list[str]) -> list[str]:
    out = [query] if len(query.strip()) >= 3 else []
    for t in terms:
        t = t.strip()
        if len(t) >= 3 and t not in out and len(out) < 4:
            out.append(t)
    return out


def _fuse(rankings: list[tuple[str, list[Hit]]], k: int) -> list[Hit]:
    merged: dict[tuple, Hit] = {}
    for source, hits in rankings:
        for rank, h in enumerate(hits):
            key = (h.repo, h.file, h.line)
            m = merged.setdefault(key, Hit(h.repo, h.file, h.line))
            m.kind, m.name, m.signature = m.kind or h.kind, m.name or h.name, m.signature or h.signature
            if source not in m.sources:
                m.sources.append(source)
                m.score += 1.0 / (RRF_K + rank + 1)
    return sorted(merged.values(), key=lambda h: -h.score)[:k]


def lookup(query: str, repos: dict[str, dict], kb_conn=None, kind: str | None = None, k: int = 10,
           use_grep: bool = True, use_vectors: str = "auto") -> dict:
    terms = [query]
    if kb_conn is not None:
        import store
        terms += [t for t in store.concept_terms(kb_conn, query) if t != query]
    conns: dict = {}
    try:
        return _lookup(query, repos, kb_conn, kind, k, use_grep, use_vectors, terms, conns)
    finally:
        for c in conns.values():
            c.close()


def _lookup(query, repos, kb_conn, kind, k, use_grep, use_vectors, terms, conns) -> dict:
    stale, missing, shas, grep_failed = [], [], {}, []
    for name, cfg in repos.items():
        if not Path(cfg["db"]).exists():
            missing.append(name)
            continue
        conns[name] = db.connect(cfg["db"])
        shas[name] = db.get_meta(conns[name], "sha")
        try:
            if db.get_meta(conns[name], "sha") != gitsrc.resolve(Path(cfg["path"]), cfg["ref"]):
                stale.append(name)
        except gitsrc.GitError:
            stale.append(name)

    def kind_ok(h: Hit) -> bool:
        return not kind or h.kind == kind

    rankings: list[tuple[str, list[Hit]]] = []
    idx = [h for name, c in conns.items() for h in _index_ranked(c, name, terms, kind)]
    rankings.append(("index", idx))
    rankings.append(("concept", [h for h in _concept_ranked(kb_conn, conns, query) if kind_ok(h)]))
    if use_grep:
        g = []
        for name, cfg in repos.items():
            sha = db.get_meta(conns[name], "sha") if name in conns else None
            if not sha:
                continue
            try:
                found = [(x, term) for term in _grep_terms(query, terms) for x in gitsrc.grep(Path(cfg["path"]), sha, term)]
            except gitsrc.GitError:
                grep_failed.append(name)  # grep could not run: its hits are missing, the index itself is fine
                continue
            for x, _ in found:
                h = Hit(name, x["file"], x["line"], signature=x["text"][:160])
                if kind:  # grep hits carry no kind: keep only those that coincide with an indexed symbol of that kind
                    r = conns[name].execute("SELECT kind, name, signature FROM symbols WHERE file = ? AND line = ? AND kind = ? LIMIT 1",
                                            (x["file"], x["line"], kind)).fetchone()
                    if not r:
                        continue
                    h.kind, h.name, h.signature = r["kind"], r["name"], r["signature"]
                g.append(h)
        rankings.append(("grep", g))
    if use_vectors != "off":
        try:
            from qindex import symvec
        except ImportError:
            symvec = None
        vec = []
        for name, cfg in repos.items():
            if symvec is None or name not in conns:
                continue
            try:
                ids = symvec.semantic(cfg["db"], query)
                if ids:
                    q = f"SELECT * FROM symbols WHERE id IN ({','.join('?' * len(ids))})"
                    by_id = {r["id"]: r for r in conns[name].execute(q, ids)}
                    vec += [h for h in _rows_to_hits(name, [by_id[i] for i in ids if i in by_id]) if kind_ok(h)]
            except (ImportError, FileNotFoundError, OSError, sqlite3.Error, RuntimeError) as e:
                if use_vectors == "on":
                    print(f"[lookup] vectors unavailable for {name} ({type(e).__name__}); keyword sources only", file=sys.stderr)
        if vec:
            rankings.append(("vector", vec))
    hits = _fuse(rankings, k)
    return {"hits": [asdict(h) for h in hits], "stale": stale, "missing": missing, "grep_failed": grep_failed, "terms": terms, "shas": shas}


def _extra(r) -> dict:
    try:
        v = json.loads(r["extra"] or "{}")
        return v if isinstance(v, dict) else {}
    except (ValueError, TypeError):
        return {}


def endpoint_pairs(front_db, back_dbs: dict[str, Path]) -> list[dict]:
    f = sqlite3.connect(front_db)
    f.row_factory = sqlite3.Row
    try:
        calls = [(_extra(r).get("url_norm"), r) for r in f.execute("SELECT * FROM symbols WHERE kind = 'api_client'")]
    finally:
        f.close()
    routes = []
    for repo, p in back_dbs.items():
        b = sqlite3.connect(p)
        b.row_factory = sqlite3.Row
        try:
            routes += [(repo, _extra(r), r) for r in b.execute("SELECT * FROM symbols WHERE kind = 'route'")]
        finally:
            b.close()
    out = []
    for url, c in calls:
        for repo, ex, r in routes:
            if url and ex.get("url_norm") == url:
                out.append({"url_norm": url, "front": {"file": c["file"], "line": c["line"], "name": c["name"]},
                            "back": {"repo": repo, "file": r["file"], "line": r["line"], "method": ex.get("method", ""), "handler": ex.get("handler", "")}})
    return sorted(out, key=lambda p: (p["url_norm"], p["front"]["name"]))
