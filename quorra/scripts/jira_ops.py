#!/usr/bin/env python3
"""Guarded Jira for quorra over a Jira helper script (config/jira.json). The configured project only, and only tickets of this run: the build's own
ticket (track) or tickets quorra created after the user approved the proposal's fingerprint.
  jira_ops.py track <run> PROJ-97
  jira_ops.py propose <run>                       (reads <run>/jira-proposal.json, writes jira-proposal.md)
  jira_ops.py create <run> --approve <fingerprint>
  jira_ops.py move <run> PROJ-97 start|review
  jira_ops.py comment <run> PROJ-97 --body TEXT --plan | --approve <fingerprint>"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import qpaths  # noqa: E402
from work import _safe  # noqa: E402

KEY = re.compile(r"^([A-Za-z]+)-(\d+)$")
ATTRIBUTION = re.compile(r"(?i)co-authored-by|built with [^a-z ]|generated with claude")


class JiraOpsError(RuntimeError):
    pass


def _cfg() -> dict:
    p = Path(os.environ.get("QUORRA_JIRA_CONFIG") or qpaths._config("jira"))
    return json.loads(p.read_text(encoding="utf-8"))


def _fp(obj) -> str:
    o = {k: v for k, v in obj.items() if k != "fingerprint"} if isinstance(obj, dict) else obj
    return hashlib.sha256(json.dumps(o, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()[:12]


def _key(key: str) -> str:
    m = KEY.match(key.strip())
    if not m or m.group(1).upper() != _cfg()["project"]:
        raise JiraOpsError(f"{key} is outside {_cfg()['project']}; quorra writes only to {_cfg()['project']}")
    return f"{m.group(1).upper()}-{m.group(2)}"


def _text_ok(text: str, what: str) -> None:
    if ATTRIBUTION.search(text):
        raise JiraOpsError(f"{what} carries a tool attribution line; published text never does")
    if _safe(text) != text:
        raise JiraOpsError(f"{what} contains a possible secret; remove it first")


def _state(run: Path) -> dict:
    p = Path(run) / "jira.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"tickets": {}}


def _save_state(run: Path, st: dict) -> None:
    (Path(run) / "jira.json").write_text(json.dumps(st, indent=1, ensure_ascii=False), encoding="utf-8")


def default_runner(args: list[str]) -> dict:
    c = _cfg()
    try:
        p = subprocess.run([c["python"], os.path.expanduser(c["tool"]), *args], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=120)
    except subprocess.TimeoutExpired:
        raise JiraOpsError(f"jira_tool {args[0]} timed out after 120s") from None
    if p.returncode != 0:
        raise JiraOpsError(f"jira_tool {args[0]} failed: {_safe(p.stderr.strip())[:500]}")
    return json.loads(p.stdout or "{}")


def track(run: Path, key: str) -> None:
    k = _key(key)
    st = _state(run)
    st["tickets"].setdefault(k, {"origin": "build"})
    _save_state(run, st)


def allowed(run: Path, key: str) -> bool:
    try:
        return _key(key) in _state(run)["tickets"]
    except JiraOpsError:
        return False


def _require(run: Path, key: str) -> str:
    k = _key(key)
    if k not in _state(run)["tickets"]:
        raise JiraOpsError(f"{k} is not a ticket of this run (track the build ticket or create it from the proposal)")
    return k


def _proposal(run: Path) -> dict:
    prop = json.loads((Path(run) / "jira-proposal.json").read_text(encoding="utf-8"))
    for t in prop["tickets"]:
        for field in ("summary", "description"):
            _text_ok(t[field], f"ticket {t['summary']!r} {field}")
        if t.get("parent"):
            t["parent"] = _key(t["parent"])
    return prop


def propose(run: Path) -> dict:
    prop = _proposal(run)
    fp = _fp(prop)
    lines = [f"# Jira proposal (fingerprint {fp})", ""]
    for i, t in enumerate(prop["tickets"], 1):
        parent = f", under {t['parent']}" if t.get("parent") else ""
        lines += [f"## {i}. {t['summary']} ({t.get('issue_type', 'Task')}{parent})", "", t["description"], ""]
    md = "\n".join(lines)
    (Path(run) / "jira-proposal.md").write_text(md, encoding="utf-8")
    return {"fingerprint": fp, "markdown": md}


def create(run: Path, approve: str, runner=None) -> list[str]:
    runner = runner or default_runner
    prop = _proposal(run)
    if _fp(prop) != approve:
        raise JiraOpsError("the proposal changed since the user approved it (fingerprint mismatch); run propose again")
    st = _state(run)
    done = st.setdefault("proposals", {}).setdefault(approve, {})
    keys = []
    for i, t in enumerate(prop["tickets"]):
        if str(i) in done:
            keys.append(done[str(i)])
            continue
        args = ["create", "--project", _cfg()["project"], "--issue-type", t.get("issue_type", "Task"),
                "--summary", t["summary"], "--description", t["description"]]
        if t.get("parent"):
            args += ["--field", "parent=" + json.dumps({"key": t["parent"]})]
        k = _key(runner(args)["created"]["key"])
        st["tickets"][k] = {"origin": "created"}
        done[str(i)] = k
        _save_state(run, st)
        keys.append(k)
    return keys


def move(run: Path, key: str, step: str, runner=None) -> None:
    names = _cfg()["transitions"]
    if step not in names:
        raise JiraOpsError(f"unknown step {step!r}; use one of {', '.join(names)}")
    (runner or default_runner)(["transition", _require(run, key), "--transition", names[step]])


def comment(run: Path, key: str, body: str, runner=None) -> None:
    k = _require(run, key)
    _text_ok(body, "comment")
    (runner or default_runner)(["comment", k, "--body", body])


def comment_fp(key: str, body: str) -> str:
    return _fp({"key": _key(key), "body": body})


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="jira_ops.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for n in ("track", "move", "comment"):
        s = sub.add_parser(n)
        s.add_argument("run"); s.add_argument("key")
        if n == "move":
            s.add_argument("step")
        if n == "comment":
            s.add_argument("--body", required=True)
            g = s.add_mutually_exclusive_group(required=True)
            g.add_argument("--plan", action="store_true")
            g.add_argument("--approve")
    sub.add_parser("propose").add_argument("run")
    c = sub.add_parser("create")
    c.add_argument("run"); c.add_argument("--approve", required=True)
    a = ap.parse_args(argv)
    try:
        run = Path(a.run)
        if a.cmd == "track":
            track(run, a.key); res = {"tracked": _key(a.key)}
        elif a.cmd == "propose":
            res = propose(run)
        elif a.cmd == "create":
            res = {"created": create(run, a.approve)}
        elif a.cmd == "move":
            move(run, a.key, a.step); res = {"moved": _key(a.key), "step": a.step}
        elif a.plan:
            _text_ok(a.body, "comment")
            res = {"key": _key(a.key), "body": a.body, "fingerprint": comment_fp(a.key, a.body)}
        else:
            if a.approve != comment_fp(a.key, a.body):
                raise JiraOpsError("the comment does not match the fingerprint the user approved; run comment --plan again")
            comment(run, a.key, a.body); res = {"commented": _key(a.key)}
    except (JiraOpsError, OSError, KeyError, ValueError) as e:
        print(json.dumps({"error": _safe(str(e))}), file=sys.stderr)
        return 2
    print(json.dumps(res, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
