#!/usr/bin/env python3
"""quorra run records: the process log each task leaves behind (docs/run-schema.md).
  run_record.py new research-api-app --type research --request "..." --repos api-app [--mode teammate --caller-cwd <dir>]
  run_record.py append <dir> subagents '{"name": "quorra-scout", "model": "haiku", "purpose": "naming", "tokens": 12000}'
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
import qpaths  # noqa: E402

LIST_FIELDS = ("questions", "subagents", "reused", "created", "verification", "corrections", "notes")


def _load(d: Path) -> dict:
    return json.loads((Path(d) / "run.json").read_text(encoding="utf-8"))


def _save(d: Path, run: dict) -> None:
    (Path(d) / "run.json").write_text(json.dumps(run, indent=1, ensure_ascii=False), encoding="utf-8")


def new(slug: str, type_: str, request: str, repos: list[str], runs_root=None, mode: str = "direct", caller_cwd=None) -> Path:
    if mode not in ("direct", "teammate"):
        raise ValueError(f"invalid mode {mode!r}")
    if not slug or "/" in slug or "\\" in slug or ".." in slug:
        raise ValueError(f"invalid slug {slug!r}")
    root = Path(runs_root or qpaths.runs_dir())
    base = f"{_dt.date.today().isoformat()}-{slug}"
    d, i = root / base, 2
    while d.exists():
        d, i = root / f"{base}-{i}", i + 1
    d.mkdir(parents=True)
    run = {"run_id": d.name, "date": _dt.date.today().isoformat(), "type": type_, "request": request, "repos": repos,
           "mode": mode, "caller_cwd": caller_cwd,
           **{f: [] for f in LIST_FIELDS}, "outcome": None, "closed_at": None}
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
                            "repos": ",".join(run["repos"]), "outcome": outcome, "corrections_count": len(run["corrections"]),
                            "verify_result": json.dumps(run["verification"][-1], ensure_ascii=False) if run["verification"] else "none"}, origin="run_record")
    store.export(c, etype="run")
    return run


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="run_record.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("new")
    n.add_argument("slug")
    n.add_argument("--type", required=True, choices=["research", "lookup", "question", "refresh", "explain", "build", "ship", "doc"])
    n.add_argument("--request", required=True)
    n.add_argument("--repos", default="")
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
        print(new(args.slug, args.type, args.request, [r for r in args.repos.split(",") if r], mode=args.mode, caller_cwd=args.caller_cwd))
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
