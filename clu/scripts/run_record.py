#!/usr/bin/env python3
"""Create and maintain clu run folders (runs/<date>-<slug>/run.json).

Every command that takes <dir> also accepts a bare run id (the folder name under runs/).
Closing checks the protocol (tier, contract confirmation, verification verdict) and commits the
run record and kb/export to git, so learned knowledge is versioned.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "kb"))
import store  # noqa: E402

sys.path.insert(0, str(REPO / "scripts"))
import helper_output  # noqa: E402

LIST_KEYS = ("sources", "subagents", "queries", "corrections", "deliverables", "kb_writes", "proposals", "steps",
             "challenges", "reviews")
TYPES = ("lookup", "analysis", "dashboard", "bug", "experiment", "causal")
MODES = ("direct", "teammate")
TIERS = ("quick", "standard", "deep")
VERIFY_RESULTS = ("sustained", "partial", "refuted", "not_run")
RECORD_TYPES = ("analysis", "causal", "experiment", "dashboard")  # runs that need methods.md + reproduce.ipynb
TEMPLATE = REPO / "skills/clu-core/references/contract-template.md"
KEYGRAPH_SRC = REPO.parent / "keygraph" / "src"  # the keygraph checkout next to clu, when there is one
KEYGRAPH_DB = REPO / "kb" / "keygraph" / "keygraph.db"  # the private build; override with CLU_KEYGRAPH_DB
DIALECT = {"mysql": "mysql", "clickhouse": "clickhouse", "ods": "tsql"}


def _now() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def resolve(d) -> Path:
    """Accept a run folder path or a bare run id; fail with a clear message otherwise."""
    p = Path(d)
    if (p / "run.json").exists():
        return p
    q = REPO / "runs" / str(d)
    if (q / "run.json").exists():
        return q
    raise FileNotFoundError(f"no run.json at {d!s} or at runs/{d!s}; pass the run folder path printed by `new`")


def _load(d) -> dict:
    return json.loads((resolve(d) / "run.json").read_text())


def _save(d, rj: dict) -> None:
    ((Path(d) if Path(d).is_dir() else resolve(d)) / "run.json").write_text(json.dumps(rj, ensure_ascii=False, indent=2) + "\n")


def new(slug: str, rtype: str, question: str, mode: str = "direct", caller_cwd: str = "", runs_dir=None,
        tier: str | None = None) -> Path:
    base = Path(runs_dir) if runs_dir else REPO / "runs"
    name = f"{_dt.date.today().isoformat()}-{slug}"
    d, i = base / name, 2
    while d.exists():
        d, i = base / f"{name}-{i}", i + 1
    (d / "queries").mkdir(parents=True)
    rj = {"schema_version": 1, "run_id": d.name, "created_at": _now(), "ended_at": None, "mode": mode,
          "caller_cwd": caller_cwd, "type": rtype, "tier": tier, "question": question, "topics": [],
          "contract_confirmed_at": None, "contract_skipped": None,
          "verification": {"result": None, "discrepancies": []}, "outcome": None,
          **{k: [] for k in LIST_KEYS}}
    _save(d, rj)
    (d / "contract.md").write_text(TEMPLATE.read_text() if TEMPLATE.exists() else f"# Contract: {question}\n")
    return d


def set_field(run_dir, key: str, value) -> None:
    rj = _load(run_dir)
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            pass
    rj[key] = value
    _save(run_dir, rj)


def append(run_dir, key: str, item: dict) -> None:
    if key not in LIST_KEYS:
        raise ValueError(f"{key} is not a list field; use one of {LIST_KEYS}")
    if not isinstance(item, dict):
        raise ValueError(f"{key} items must be JSON objects, got {type(item).__name__}")
    rj = _load(run_dir)
    rj.setdefault(key, []).append({"at": _now(), **item})
    _save(run_dir, rj)


def log(run_dir, batch: dict) -> None:
    """Apply many set/append updates in one call (one tool turn instead of many)."""
    for key, value in (batch.get("set") or {}).items():
        set_field(run_dir, key, value if not isinstance(value, str) else json.dumps(value))
    for key, items in (batch.get("append") or {}).items():
        for item in items:
            append(run_dir, key, item)


def validate(run_dir, closing: bool = False) -> list[str]:
    """Structural checks always; with closing=True also the protocol checks a finished run must pass."""
    rj, problems = _load(run_dir), []
    for k in ("schema_version", "run_id", "created_at", "mode", "type", "question"):
        if rj.get(k) in (None, ""):
            problems.append(f"missing {k}")
    if rj.get("schema_version") != 1:
        problems.append("schema_version must be 1")
    if rj.get("type") not in TYPES:
        problems.append(f"type must be one of {TYPES}")
    if rj.get("mode") not in MODES:
        problems.append(f"mode must be one of {MODES}")
    for k in LIST_KEYS:
        if not isinstance(rj.get(k, []), list):
            problems.append(f"{k} must be a list")
    if closing:
        problems += _protocol_problems(rj, resolve(run_dir))
    return problems


def _protocol_problems(rj: dict, d: Path | None = None) -> list[str]:
    out, tier = [], rj.get("tier")
    if tier not in TIERS:
        out.append(f"tier must be one of {TIERS}: set it with `set <dir> tier <quick|standard|deep>`")
        return out
    if tier != "quick" and rj.get("type") != "lookup":
        if not rj.get("contract_confirmed_at") and not (rj.get("contract_skipped") or "").strip():
            out.append("contract not confirmed: set contract_confirmed_at when the user (or caller) confirmed it, "
                       "or contract_skipped to the reason it was skipped")
    res = (rj.get("verification") or {}).get("result")
    if tier == "quick":
        if res is not None and res not in VERIFY_RESULTS:
            out.append(f"verification.result must be one of {VERIFY_RESULTS}")
    elif res not in VERIFY_RESULTS:
        out.append(f"verification.result must be one of {VERIFY_RESULTS} for a {tier} run "
                   "(ingest the verifier's verify/result.json, or set not_run with a reason)")
    elif res == "not_run" and not ((rj.get("verification") or {}).get("reason") or "").strip():
        out.append("verification not_run needs a reason")
    if d is not None and rj.get("type") in RECORD_TYPES:
        for f in ("analysis_steps.json", "methods.md", "reproduce.ipynb"):
            if not (d / f).exists():
                out.append(f"{f} is missing: every {rj['type']} run needs its analysis record "
                           "(`run_report.py scaffold`, fill analysis_steps.json, then `check`)")
        rep = rj.get("reproduction") or {}
        if rep.get("status") == "skipped":
            if not (rep.get("reason") or "").strip():
                out.append("reproduction skipped needs a reason")
        elif rep.get("status") != "pass":
            out.append("reproduce.ipynb has not passed `run_report.py check` "
                       f"(status: {rep.get('status') or 'not run'}); fix it, or set reproduction "
                       '{"status": "skipped", "reason": "<why>"} when re-running is impossible')
    return out


def ingest(run_dir, path) -> str:
    """Validate one helper output file, record it in run.json, and return a compact summary for the lead."""
    d, p = resolve(run_dir), Path(path)
    if not p.is_absolute() and not p.exists():
        p = d / p
    obj = helper_output.load(p)
    problems = helper_output.check(obj)
    if problems:
        raise ValueError("invalid helper output: " + "; ".join(problems))
    kind, rel = obj["kind"], str(p.relative_to(d)) if p.is_relative_to(d) else str(p)
    rj = _load(d)
    rj.setdefault("subagents", []).append({"at": _now(), "name": helper_output.AGENT[kind], "purpose": kind,
                                           "output": rel})
    extra = None
    if kind == "objections":
        rj.setdefault("challenges", []).append({"at": _now(), "mode": obj["mode"], "file": rel,
                                                "blockers": sum(o["severity"] == "blocker" for o in obj["objections"]),
                                                "majors": sum(o["severity"] == "major" for o in obj["objections"])})
    elif kind == "runner_result":
        rj.setdefault("queries", []).append({"at": _now(), "id": obj["name"], "source": obj["source"],
                                             "runtime_s": obj["runtime_s"], "rows": obj["rows"],
                                             "capped": obj["capped"], "file": obj["sql_file"]})
        print(_log_query(d, obj, rj), file=sys.stderr)
    elif kind == "verification":
        claims_p = d / "verify" / "claims.json"
        if not claims_p.exists():
            raise ValueError(f"{claims_p} is missing: the lead writes the claimed headline numbers there "
                             '({"numbers": [{"name": ..., "value": ..., "unit": ...}]}) before spawning the verifier')
        extra = helper_output.verdict(obj, json.loads(claims_p.read_text()))
        rj["verification"] = {"result": extra["result"], "discrepancies": extra["discrepancies"],
                              "numbers": extra["numbers"], "failed_checks": extra["failed_checks"], "file": rel}
        if extra.get("reason"):
            rj["verification"]["reason"] = extra["reason"]
    elif kind == "edit_review":
        rj.setdefault("reviews", []).append({"at": _now(), "file": obj["file"], "verdict": obj["verdict"],
                                             "fixes": len(obj["fixes"])})
    elif kind == "curation":
        for i in obj["kb_writes"]:
            rj.setdefault("kb_writes", []).append({"at": _now(), "id": i})
        for s_ in obj["proposals"]:
            rj.setdefault("proposals", []).append({"at": _now(), "id": s_})
    _save(d, rj)
    return helper_output.summary(obj, extra)


def _business_question(d: Path, rj: dict) -> str:
    """The contract's "Question (user's words)" line, else the run's question."""
    c = d / "contract.md"
    if c.exists():
        m = re.search(r"^- \*\*Question \(user's words\):\*\*[ \t]*(.+)$", c.read_text(encoding="utf-8"), re.M)
        if m and m.group(1).strip():
            return m.group(1).strip()
    return rj.get("question") or ""


def _log_query(d: Path, obj: dict, rj: dict) -> str:
    """Log a runner's SQL into the keygraph query log (keygraph queries add). Never raises; returns one status line.
    Off with CLU_KEYGRAPH_LOG=0; a missing build or a missing keygraph checkout only skips."""
    if os.environ.get("CLU_KEYGRAPH_LOG", "1") == "0":
        return "keygraph: query log off"
    db = Path(os.environ.get("CLU_KEYGRAPH_DB") or KEYGRAPH_DB)
    if not db.exists():
        return f"keygraph: no build at {db}, query not logged"
    try:
        if KEYGRAPH_SRC.exists() and str(KEYGRAPH_SRC) not in sys.path:
            sys.path.insert(0, str(KEYGRAPH_SRC))
        from keygraph import queries as kq
    except ImportError:
        return "keygraph: not installed, query not logged"
    try:
        files = obj["sql_file"] if isinstance(obj["sql_file"], list) else [obj["sql_file"]]
        chunked = len(files) > 1
        source = obj["source"]
        dialect = DIALECT.get(source.split(":", 1)[0])
        error = None if obj.get("status", "ok") == "ok" else (obj.get("issues") or "blocked")
        context = _business_question(d, rj)
        fps = []
        for f in files:
            p = Path(f) if Path(f).is_absolute() else d / f
            r = kq.add(db, p.read_text(encoding="utf-8"), source,
                       ms=None if chunked or obj.get("runtime_s") is None else obj["runtime_s"] * 1000,
                       rows=None if chunked else obj.get("rows"), context=context, tags=[rj["run_id"], obj["name"]],
                       by="clu", error=error, dialect=dialect)
            fps.append(r["fp"])
        return f"keygraph: logged {len(fps)} quer{'y' if len(fps) == 1 else 'ies'} ({', '.join(sorted(set(fps)))})"
    except Exception as e:  # noqa: BLE001 - logging must never block ingest
        return f"keygraph: query not logged ({type(e).__name__}: {str(e)[:120]})"


def _git_commit(run_dir: Path, run_id: str) -> str:
    """Commit this run's record files and kb/export. Never raises; returns a one-line status."""
    try:
        def g(*a):
            return subprocess.run(["git", "-C", str(REPO), *a], capture_output=True, text=True, timeout=30)
        if g("rev-parse", "--git-dir").returncode:
            return "git: not a repository, skipped"
        if not run_dir.resolve().is_relative_to(REPO.resolve()) or os.environ.get("CLU_KB_EXPORT"):
            return "git: run outside the repo (or a test KB), skipped"
        rel_run = str(run_dir.resolve().relative_to(REPO.resolve()))
        paths = [rel_run, "kb/export"]
        g("add", "--", *paths)  # .gitignore keeps raw data out of run folders
        if not g("diff", "--cached", "--quiet", "--", *paths).returncode:
            return "git: nothing to commit"
        r = g("commit", "-q", "-m", f"run {run_id}: close (run record + KB export)", "--", *paths)
        return "git: committed" if r.returncode == 0 else f"git: commit failed ({r.stderr.strip()[:120]})"
    except Exception as e:  # noqa: BLE001 - versioning must never block closing a run
        return f"git: skipped ({type(e).__name__})"


def close(run_dir, outcome: str, commit: bool = True) -> str:
    run_dir = resolve(run_dir)
    problems = validate(run_dir, closing=True)
    if problems:
        raise ValueError("cannot close: " + "; ".join(problems))
    rj = _load(run_dir)
    rj["ended_at"], rj["outcome"] = _now(), outcome
    _save(run_dir, rj)
    if (run_dir / "analysis_steps.json").exists():
        try:
            import run_report
            run_report.build_methods(run_dir)  # refresh methods.md only; the executed notebook keeps its outputs
        except Exception as e:  # noqa: BLE001 - the record was already checked; never block closing here
            print(f"run_record.py close: methods.md not refreshed ({e})", file=sys.stderr)
    try:
        import run_tokens
        rj2 = _load(run_dir)
        rj2["tokens"] = run_tokens.measure(run_dir)
        _save(run_dir, rj2)
    except Exception as e:  # noqa: BLE001 - token accounting must never block closing a run
        rj2 = _load(run_dir)
        rj2["tokens"] = {"error": str(e)[:200]}
        _save(run_dir, rj2)
    c = store.connect()
    store.upsert(c, "run", {"run_id": rj["run_id"], "date": rj["created_at"][:10], "type": rj["type"],
                            "question": rj["question"], "topics": rj.get("topics") or [], "outcome": outcome,
                            "corrections_count": len(rj.get("corrections", [])),
                            "verify_result": (rj.get("verification") or {}).get("result")},
                 origin=f"run:{rj['run_id']}")
    store.export(c, etype="run")
    if commit:
        print(_git_commit(run_dir, rj["run_id"]), file=sys.stderr)
    print(_embed_in_background(), file=sys.stderr)
    return rj["run_id"]


def _embed_in_background() -> str:
    """Refresh the curated, schema and runs vectors after the run is recorded: a detached `kb.py embed --lane all`
    (log in kb/embed.log). Never raises and never waits; a missing daemon only shows up in the log."""
    try:
        import kb
        return kb.spawn_embed("all")
    except Exception as e:  # noqa: BLE001 - vectors must never block closing a run
        return f"vectors: background embed not started ({type(e).__name__})"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="run_record.py")
    sub = p.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("new")
    n.add_argument("--slug", required=True)
    n.add_argument("--type", required=True, choices=TYPES)
    n.add_argument("--question", required=True)
    n.add_argument("--mode", default="direct", choices=MODES)
    n.add_argument("--caller-cwd", default="")
    n.add_argument("--tier", required=True, choices=TIERS)
    s = sub.add_parser("set")
    s.add_argument("dir"); s.add_argument("key"); s.add_argument("value")
    a = sub.add_parser("append")
    a.add_argument("dir"); a.add_argument("key"); a.add_argument("json")
    lg = sub.add_parser("log")
    lg.add_argument("dir"); lg.add_argument("json", help='{"set": {key: value}, "append": {list_key: [items]}}')
    v = sub.add_parser("validate")
    v.add_argument("dir")
    v.add_argument("--for-close", action="store_true", help="also run the checks close will run")
    ig = sub.add_parser("ingest", help="validate and record one helper output file")
    ig.add_argument("dir"); ig.add_argument("file")
    c = sub.add_parser("close")
    c.add_argument("dir"); c.add_argument("--outcome", required=True)
    c.add_argument("--no-commit", action="store_true")
    args = p.parse_args(argv)
    try:
        return _run(args)
    except (FileNotFoundError, ValueError, KeyError) as e:
        print(f"run_record.py {args.cmd}: {e}", file=sys.stderr)
        return 1


def _run(args) -> int:
    if args.cmd == "new":
        print(new(args.slug, args.type, args.question, args.mode, args.caller_cwd, tier=args.tier))
    elif args.cmd == "set":
        set_field(args.dir, args.key, args.value)
    elif args.cmd == "append":
        append(args.dir, args.key, _json(args.json))
    elif args.cmd == "log":
        log(args.dir, _json(args.json))
    elif args.cmd == "validate":
        probs = validate(args.dir, closing=args.for_close)
        print("\n".join(probs) or "ok")
        return 1 if probs else 0
    elif args.cmd == "ingest":
        print(ingest(args.dir, args.file))
    elif args.cmd == "close":
        print(close(args.dir, args.outcome, commit=not args.no_commit))
    return 0


def _json(text: str):
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"not valid JSON ({e.msg} at char {e.pos}); quote the argument in single quotes") from None


if __name__ == "__main__":
    raise SystemExit(main())
