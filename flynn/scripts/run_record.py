#!/usr/bin/env python3
"""flynn run records: the process log each design run leaves behind. Adapted from quorra/scripts/run_record.py.
  run_record.py new orders-page --type ui --request "..." --system admin-panel [--mode teammate --caller-cwd <dir>]
  run_record.py append <dir> audits '{"round": 1, "by": "flynn-critic", "model": "sonnet", "verdict": "revise"}'
  run_record.py close <dir> --outcome "..."
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "kb")]
import fpaths  # noqa: E402

RUN_TYPES = ("ui", "deck", "graphic", "chart", "learn-system", "sync", "question")
LIST_FIELDS = ("questions", "subagents", "references", "created", "audits", "corrections", "notes")


def _load(d) -> dict:
    return json.loads((Path(d) / "run.json").read_text(encoding="utf-8"))


def _save(d, run: dict) -> None:
    (Path(d) / "run.json").write_text(json.dumps(run, indent=1, ensure_ascii=False), encoding="utf-8")


def new(slug: str, type_: str, request: str, system: str = "", runs_root=None, mode: str = "direct", caller_cwd=None) -> Path:
    if mode not in ("direct", "teammate"):
        raise ValueError(f"invalid mode {mode!r}")
    if type_ not in RUN_TYPES:
        raise ValueError(f"invalid type {type_!r}; use one of {RUN_TYPES}")
    if not slug or "/" in slug or "\\" in slug or ".." in slug:
        raise ValueError(f"invalid slug {slug!r}")
    root = Path(runs_root or fpaths.runs_dir())
    base = f"{_dt.date.today().isoformat()}-{slug}"
    d, i = root / base, 2
    while d.exists():
        d, i = root / f"{base}-{i}", i + 1
    d.mkdir(parents=True)
    (d / "docs").mkdir()
    (d / "shots").mkdir()
    run = {"run_id": d.name, "date": _dt.date.today().isoformat(), "type": type_, "request": request, "system": system,
           "mode": mode, "caller_cwd": caller_cwd, **{f: [] for f in LIST_FIELDS}, "outcome": None, "closed_at": None}
    _save(d, run)
    return d


def append(d, field: str, item) -> None:
    if field not in LIST_FIELDS:
        raise KeyError(f"{field!r} is not a list field; use one of {LIST_FIELDS}")
    run = _load(d)
    run[field].append(item)
    _save(d, run)


def set_(d, field: str, value) -> None:
    run = _load(d)
    run[field] = value
    _save(d, run)


def close(d, outcome: str) -> dict:
    import store
    run = _load(d)
    run["outcome"] = outcome
    run["closed_at"] = _dt.datetime.now().isoformat(timespec="seconds")
    _save(d, run)
    c = store.connect()
    store.upsert(c, "run", {"run_id": run["run_id"], "date": run["date"], "type": run["type"], "request": run["request"],
                            "system": run.get("system") or "", "outcome": outcome,
                            "corrections_count": len(run["corrections"]),
                            "verdict": json.dumps(run["audits"][-1], ensure_ascii=False) if run["audits"] else "none"},
                 origin="run_record")
    store.export(c, etype="run")
    return run


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="run_record.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("new")
    n.add_argument("slug")
    n.add_argument("--type", required=True, choices=RUN_TYPES)
    n.add_argument("--request", required=True)
    n.add_argument("--system", default="")
    n.add_argument("--mode", default="direct", choices=["direct", "teammate"])
    n.add_argument("--caller-cwd", default=None)
    a = sub.add_parser("append")
    a.add_argument("dir"); a.add_argument("field"); a.add_argument("json")
    s = sub.add_parser("set")
    s.add_argument("dir"); s.add_argument("field"); s.add_argument("json")
    c = sub.add_parser("close")
    c.add_argument("dir"); c.add_argument("--outcome", required=True)
    args = ap.parse_args(argv)
    if args.cmd == "new":
        print(new(args.slug, args.type, args.request, args.system, mode=args.mode, caller_cwd=args.caller_cwd))
    elif args.cmd == "append":
        append(args.dir, args.field, json.loads(args.json))
    elif args.cmd == "set":
        set_(args.dir, args.field, json.loads(args.json))
    elif args.cmd == "close":
        import store
        try:
            print(json.dumps(close(args.dir, args.outcome), ensure_ascii=False)[:500])
        except store.ExportRefused as e:
            print(f"run_record.py close: run saved, KB row saved, but export refused: {e}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
