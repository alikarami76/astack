#!/usr/bin/env python3
"""Rinzler run records: one folder per QA run, runs/<YYYY-MM-DD>-<slug>/run.json (docs/run-schema.md).
  run_record.py new flash-discount-plan --type plan --request "..." --feature "Flash discount form" [--env local]
                [--mode teammate --caller-cwd <dir>]
  run_record.py append <dir> bugs '{"draft": "bugs/BUG-001.md", "status": "draft"}'
  run_record.py set <dir> environment '{"name": "local", "url": "http://localhost:3333", "commit": "189e76d2d"}'
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
import rpaths  # noqa: E402

TYPES = ("plan", "explore", "scripted", "eval", "ai-eval", "data-sim", "reconcile", "perf", "bug", "build", "regression")
LIST_FIELDS = ("questions", "subagents", "assets", "results", "bugs", "verification", "corrections", "notes")


def _load(d) -> dict:
    return json.loads((Path(d) / "run.json").read_text(encoding="utf-8"))


def _save(d, run: dict) -> None:
    (Path(d) / "run.json").write_text(json.dumps(run, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def resolve(d: str) -> Path:
    p = Path(d)
    return p if p.is_dir() else rpaths.runs_dir() / d


def new(slug: str, type_: str, request: str, feature: str = "", env: str = "offline", mode: str = "direct",
        caller_cwd: str | None = None, runs_root=None) -> Path:
    if type_ not in TYPES:
        raise ValueError(f"type must be one of {TYPES}")
    if mode not in ("direct", "teammate"):
        raise ValueError("mode must be direct or teammate")
    if not slug or any(c in slug for c in "/\\") or ".." in slug:
        raise ValueError(f"invalid slug {slug!r}")
    root = Path(runs_root or rpaths.runs_dir())
    base = f"{_dt.date.today().isoformat()}-{slug}"
    d, i = root / base, 2
    while d.exists():
        d, i = root / f"{base}-{i}", i + 1
    d.mkdir(parents=True)
    _save(d, {"run_id": d.name, "date": _dt.date.today().isoformat(), "type": type_, "request": request,
              "feature": feature, "environment": {"name": env}, "mode": mode, "caller_cwd": caller_cwd,
              **{f: [] for f in LIST_FIELDS}, "outcome": None, "closed_at": None})
    return d


def append(d, field: str, item) -> None:
    if field not in LIST_FIELDS:
        raise KeyError(f"{field!r} is not a list field; use one of {LIST_FIELDS}")
    run = _load(d)
    run[field].append(item)
    _save(d, run)


def set_(d, field: str, value) -> None:
    if field in ("run_id", "date"):
        raise KeyError(f"{field} is fixed at creation")
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
                            "feature": run.get("feature", ""), "outcome": outcome,
                            "corrections_count": len(run["corrections"]), "bugs_found": len(run["bugs"])},
                 origin="run_record")
    store.export(c, "run")
    return run


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="run_record.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("new")
    n.add_argument("slug")
    n.add_argument("--type", required=True, choices=TYPES)
    n.add_argument("--request", required=True)
    n.add_argument("--feature", default="")
    n.add_argument("--env", default="offline")
    n.add_argument("--mode", default="direct", choices=["direct", "teammate"])
    n.add_argument("--caller-cwd")
    for name in ("append", "set"):
        x = sub.add_parser(name)
        x.add_argument("dir")
        x.add_argument("field")
        x.add_argument("value", help="JSON value (a bare string is accepted)")
    c = sub.add_parser("close")
    c.add_argument("dir")
    c.add_argument("--outcome", required=True)
    s = sub.add_parser("show")
    s.add_argument("dir")
    a = ap.parse_args(argv)
    if a.cmd == "new":
        print(new(a.slug, a.type, a.request, a.feature, a.env, a.mode, a.caller_cwd))
        return 0
    d = resolve(a.dir)
    if a.cmd in ("append", "set"):
        try:
            value = json.loads(a.value)
        except ValueError:
            value = a.value
        (append if a.cmd == "append" else set_)(d, a.field, value)
    elif a.cmd == "close":
        print(json.dumps(close(d, a.outcome)["outcome"]))
    elif a.cmd == "show":
        print(json.dumps(_load(d), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
