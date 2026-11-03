#!/usr/bin/env python3
"""Index dashboard exports that the user drops into the BI exports folder, so clu can use them for sanity checks.

Folder layout (outside the repo, never committed):
  ~/agent-stack/clu-snapshots/bi-exports/<YYYY-MM-DD>/<report name>[ - <visual>].<csv|xlsx|pdf|png>

  bi_exports.py index            # (re)index every file; writes one KB source row per file
  bi_exports.py list [--q TEXT]  # one line per export: date, report, rows, columns, path
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "kb"))
import store  # noqa: E402

INBOX = Path(os.environ.get("CLU_BI_EXPORTS", str(Path.home() / "agent-stack-data/clu-snapshots/bi-exports")))
DATE_DIR = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TABLE_EXT = {".csv", ".xlsx", ".xls"}
OTHER_EXT = {".pdf", ".png", ".jpg", ".jpeg"}


def _describe_table(p: Path) -> dict:
    if p.suffix == ".csv":
        with p.open(newline="", encoding="utf-8-sig", errors="replace") as f:
            rows = list(csv.reader(f))
        header, body = (rows[0], rows[1:]) if rows else ([], [])
        return {"columns": header, "rows": len(body)}
    import pandas as pd  # xlsx only
    sheets = pd.read_excel(p, sheet_name=None)
    first = next(iter(sheets.values())) if sheets else None
    return {"columns": [str(c) for c in (first.columns if first is not None else [])],
            "rows": int(sum(len(s) for s in sheets.values())), "sheets": list(sheets)}


def scan(inbox: Path = INBOX) -> list[dict]:
    out = []
    if not inbox.exists():
        return out
    for day in sorted(d for d in inbox.iterdir() if d.is_dir() and DATE_DIR.match(d.name)):
        for f in sorted(day.iterdir()):
            ext = f.suffix.lower()
            if ext not in TABLE_EXT | OTHER_EXT:
                continue
            item = {"date": day.name, "report": f.stem, "path": str(f), "kind": ext.lstrip(".")}
            if ext in TABLE_EXT:
                try:
                    item.update(_describe_table(f))
                except Exception as e:  # noqa: BLE001 - one bad file must not stop the index
                    item["error"] = str(e)[:200]
            out.append(item)
    return out


def index(conn, inbox: Path = INBOX) -> int:
    n = 0
    for it in scan(inbox):
        cols = ", ".join(it.get("columns", [])[:30])
        notes = f"{it['kind']} export dated {it['date']}; rows {it.get('rows', 'n/a')}; columns: {cols}; file: {it['path']}"
        if it.get("error"):
            notes += f"; read error: {it['error']}"
        store.upsert(conn, "source", {"kind": "bi-export", "database": it["date"], "schema_name": "",
                                      "table_name": it["report"], "last_checked": it["date"], "notes": notes},
                     origin="bi-export", tags=["bi-export", "official-dashboard"])
        n += 1
    store.export(conn, etype="source")
    return n


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="bi_exports.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("index")
    ls = sub.add_parser("list")
    ls.add_argument("--q", default="")
    a = ap.parse_args(argv)
    if a.cmd == "index":
        print(f"indexed {index(store.connect())} export(s) from {INBOX}")
    else:
        for it in scan():
            if a.q.lower() in it["report"].lower():
                print(f"{it['date']}  {it['report']}  rows={it.get('rows', '-')}  cols={len(it.get('columns', []))}  {it['path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
