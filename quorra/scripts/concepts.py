#!/usr/bin/env python3
"""Concept map helpers.
  concepts.py candidates --kb "$HOME/knowledge-base" --out runs/<run>/concept-candidates.json
  concepts.py verify runs/<run>/concepts.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import qpaths  # noqa: E402

ROLE = r"(?:Service|Entity|Repository|Command|Controller|View|Form|Listener|Model|UseCase|Calculator|Enum|DTO|Dto|Consumer|Handler|Builder|Factory)"
KEYWORD = os.environ.get("QUORRA_CONCEPT_KEYWORDS") or r"(?:Order|Price|Coupon|Promotion|Invoice)"
RE_TICK = re.compile(r"`([^`\n]+)`")
# optional namespace, class name, optional ::member / (...) / .php
RE_QUAL = re.compile(r"\\?(?:\w+\\)*([A-Z][A-Za-z0-9]*)(?:::[\w$]+)?(?:\(.*\))?(?:\.php)?")
RE_ROLE_END = re.compile(rf".+{ROLE}$")
RE_KEYWORD = re.compile(KEYWORD)
RE_TABLE = re.compile(r"`([a-z][a-z0-9]*(?:_[a-z0-9]+)+)`")
_FA_LETTER = "[\u0621-\u063a\u0641-\u064a\u064b-\u0652\u0670-\u06d3]"
RE_FA = re.compile(f"{_FA_LETTER}+(?:[ \u200c]{_FA_LETTER}+){{0,4}}")
RE_HEAD = re.compile(r"^#{1,3}\s+(.+?)\s*$")
CAP = 40


def _top(counter: Counter, key: str) -> list[dict]:
    return [{key: k, "count": v} for k, v in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[:CAP]]


def _fa_runs(text: str) -> Counter:
    c: Counter = Counter()
    for m in RE_FA.finditer(text):
        words = m.group(0).split()
        for n in range(1, min(5, len(words)) + 1):
            for i in range(len(words) - n + 1):
                t = " ".join(words[i:i + n])
                if 2 <= len(t) <= 40:
                    c[t] += 1
    return c


def _identifiers(text: str) -> list[str]:
    out = []
    for span in RE_TICK.findall(text):
        m = RE_QUAL.fullmatch(span.strip())
        if m and (RE_ROLE_END.match(m.group(1)) or RE_KEYWORD.search(m.group(1))):
            out.append(m.group(1))
    return out


def _headings(text: str) -> list[str]:
    out, fenced = [], False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
        elif not fenced and (m := RE_HEAD.match(line)):
            out.append(m.group(1))
    return out


def _drop_subterms(terms: dict[str, int]) -> dict[str, int]:
    """Drop a term when a longer term that contains it (as whole words) has the same count."""
    keep = {}
    for t, n in terms.items():
        padded = f" {t} "
        if not any(o != t and n == m and padded in f" {o} " for o, m in terms.items()):
            keep[t] = n
    return keep


def candidates(kb_root: Path) -> list[dict]:
    out = []
    for feat in sorted(p for p in (Path(kb_root) / "features").iterdir() if p.is_dir()):
        docs = sorted(feat.rglob("*.md"))
        ids, tables, fa, heads = Counter(), Counter(), Counter(), []
        for d in docs:
            text = d.read_text(encoding="utf-8", errors="replace")
            ids.update(_identifiers(text))
            tables.update(RE_TABLE.findall(text))
            fa.update(_fa_runs(text))
            for h in _headings(text):
                if h not in heads:
                    heads.append(h)
        out.append({"feature": feat.name, "docs": [str(d.relative_to(kb_root)) for d in docs],
                    "identifiers": _top(ids, "name"), "tables": _top(tables, "name"),
                    "persian_terms": _top(Counter(_drop_subterms({k: v for k, v in fa.items() if v >= 2})), "text"),
                    "headings": heads[:CAP]})
    return out


def verify(concepts: list[dict], index_dir: Path | None = None) -> list[dict]:
    """Status per implementation: ok, moved, missing, no_index (index absent or unreadable), invalid (malformed entry)."""
    res = []
    for c in concepts if isinstance(concepts, list) else []:
        if not isinstance(c, dict):
            res.append({"concept": None, "repo": None, "file": None, "symbol": None, "found": [], "status": "invalid",
                        "reason": "concept entry is not an object"})
            continue
        impls = c.get("implementations", [])
        if not isinstance(impls, list):
            res.append({"concept": c.get("name_en"), "repo": None, "file": None, "symbol": None, "found": [], "status": "invalid",
                        "reason": "implementations must be a list"})
            continue
        for impl in impls:
            row = {"concept": c.get("name_en"), "repo": impl.get("repo") if isinstance(impl, dict) else None,
                   "file": impl.get("file") if isinstance(impl, dict) else None,
                   "symbol": impl.get("symbol") if isinstance(impl, dict) else None, "found": []}
            bad = [k for k in ("repo", "file", "symbol") if not isinstance(impl, dict) or not impl.get(k)]
            if bad:
                res.append({**row, "status": "invalid", "reason": "missing field(s): " + ", ".join(bad)})
                continue
            p = (Path(index_dir) / f"{impl['repo']}.sqlite") if index_dir else qpaths.index_path(impl["repo"])
            if not p.exists():
                res.append({**row, "status": "no_index"})
                continue
            try:
                conn = sqlite3.connect(Path(p).resolve().as_uri() + "?mode=ro", uri=True)  # as_uri encodes spaces
                try:
                    found = [{"file": f, "line": ln} for f, ln in conn.execute(
                        "SELECT file, line FROM symbols WHERE name = ? ORDER BY file", (impl["symbol"],))]
                finally:
                    conn.close()
            except sqlite3.Error:
                res.append({**row, "status": "no_index"})
                continue
            status = ("ok" if any(f["file"] == impl["file"] for f in found) else "moved" if found else "missing")
            res.append({**row, "status": status, "found": found[:5]})
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="concepts.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("candidates"); c.add_argument("--kb", required=True); c.add_argument("--out")
    v = sub.add_parser("verify"); v.add_argument("file")
    a = ap.parse_args(argv)
    if a.cmd == "candidates":
        data = json.dumps(candidates(Path(a.kb)), ensure_ascii=False, indent=1)
        Path(a.out).write_text(data, encoding="utf-8") if a.out else print(data)
        return 0
    res = verify(json.loads(Path(a.file).read_text(encoding="utf-8")))
    print(json.dumps(res, ensure_ascii=False, indent=1))
    if any(r["status"] in ("no_index", "invalid") for r in res):
        return 2
    return 1 if any(r["status"] == "missing" for r in res) else 0


if __name__ == "__main__":
    raise SystemExit(main())
