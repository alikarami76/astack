#!/usr/bin/env python3
"""clu role guard (PreToolUse): enforce in code the rules clu's prompts state.

- clu-verifier stays blind: no access to the runner's <run>/queries/, <run>/results/ or <run>/helpers/;
  it writes only under <run>/verify/ (or a temp dir).
- clu-challenger, clu-editor: read-only. Their only writes are their own output under <run>/helpers/
  (the editor may also render pages into a temp dir). Bash commands that change files, the KB, run
  records, proposals or git are refused.
- clu-curator: writes only in the run folder, the KB (via kb.py) and temp files. It never edits agents,
  skills, scripts, hooks or tests, and never applies or rejects proposals: it only drafts them.
- clu-methodologist, clu-data-runner: write only in the run folder or a temp dir.
- clu (lead): never edits its own agents, skills, scripts, hooks or tests directly; changes go through
  `proposals.py new`.

Every other agent (and any session that is not clu) is untouched. This is a guard against honest
mistakes, not a sandbox: a determined agent could still write files through python -c. On any internal
error the hook allows the call, because a broken guard must not stop every session.
"""
from __future__ import annotations

import json
import os
import re
import sys

# clu lives in a folder called agent-stack/clu. Set CLU_HOME if yours is elsewhere; symlinked paths are also resolved.
CLU_DIR = re.escape(os.environ["CLU_HOME"].rstrip("/")) if os.environ.get("CLU_HOME") else r"/agent-stack/clu"
TMP = ("/tmp/", "/private/tmp/", "/var/folders/", "$TMPDIR/", "${TMPDIR}/")
PROTECTED = re.compile(CLU_DIR + r"/(agents|skills|scripts|hooks|tests|kb/[^/]+\.py)(/|$)"
                       r"|/\.claude/(agents|skills)/clu|/agent-stack/(agents|skills)/clu")
BLIND = re.compile(r"runs/[^/\s\"']+/(queries|results|helpers)/")
BLIND_REL = re.compile(r"(^|[\s\"'=(])(\./)?(queries|results|helpers)/")
BLIND_ANY = re.compile(r"/(queries|results|helpers)/")  # e.g. "$R/queries/a.sql" once a run folder is named
# Bash that changes state. Redirection to /dev/null or a temp dir is fine.
MUTATING = [
    (re.compile(r"(^|[;&|]\s*|\s)(rm|mv|cp|rmdir|truncate|touch|ln|chmod)\s"), "file changes"),
    (re.compile(r"\bsed\s+(-[a-zA-Z]*i|--in-place)"), "in-place edits"),
    (re.compile(r"\btee\b"), "tee"),
    (re.compile(r"kb\.py\"?\s+(add|add-many|supersede|link|verify|rebuild|init|import-legacy|embed|export)\b"), "KB writes"),
    (re.compile(r"run_record\.py\"?\s+(new|set|append|log|close|ingest)\b"), "run-record writes"),
    (re.compile(r"proposals\.py\"?\s+(new|apply|reject)\b"), "proposal changes"),
    (re.compile(r"\bgit\s+(-C\s+\S+\s+)?(add|commit|push|reset|checkout|restore|rm|mv|stash|apply|merge|rebase)\b"), "git changes"),
]
# A shell redirect to a file: `> x`, `>> x`, not `2>&1`, `->`, `>=`, and not a comparison like `x > 5`.
REDIRECT = re.compile(r"(?<![0-9&<>=-])>{1,2}(?![=&>])\s*(\"[^\"]*\"|'[^']*'|[^\s;&|)]+)")
READ_ONLY = {"clu-challenger", "clu-editor"}
RUN_WRITERS = {"clu-methodologist", "clu-data-runner", "clu-curator"}


def _norm(p: str) -> str:
    """The path as given plus, when it resolves, its real path (so symlinked and agent-stack forms both match)."""
    try:
        real = os.path.realpath(os.path.expanduser(p))
    except (OSError, ValueError):
        return p
    return real if real == p else f"{p}\n{real}"


def _paths(tool: str, tin: dict) -> list[str]:
    keys = {"Read": ["file_path"], "Write": ["file_path"], "Edit": ["file_path"], "NotebookEdit": ["notebook_path"],
            "Grep": ["path", "glob"], "Glob": ["path", "pattern"]}.get(tool, [])
    return [str(tin[k]) for k in keys if tin.get(k)]


def _unquoted(cmd: str) -> str:
    """The command with quoted strings blanked, so words inside quotes are not read as commands."""
    return re.sub(r"'[^']*'|\"(?:[^\"\\]|\\.)*\"", "''", cmd)


def _is_tmp(p: str) -> bool:
    return any(x.startswith(TMP) for x in _norm(p).split("\n"))


def _in_run(p: str, sub: str | None = None) -> bool:
    for x in _norm(p).split("\n"):
        m = re.search(CLU_DIR + r"/runs/[^/]+/(.*)$", x)
        if m and (sub is None or m.group(1).startswith(sub + "/")):
            return True
    return False


def _protected(p: str) -> bool:
    return any(PROTECTED.search(x) for x in _norm(p).split("\n"))


def _redirect_targets(cmd: str) -> list[str]:
    out = []
    for t in (t.strip("\"'") for t in REDIRECT.findall(cmd)):
        if t and not t[0].isdigit() and ("/" in t or re.search(r"\.[A-Za-z]\w*$", t) or t.startswith("$")):
            out.append(t)
    return out


def decide(payload: dict) -> str | None:
    """Return a denial reason, or None to allow."""
    agent = payload.get("agent_type")
    if not isinstance(agent, str) or not (agent == "clu" or agent.startswith("clu-")):
        return None
    tool, tin = payload.get("tool_name") or "", payload.get("tool_input") or {}
    cwd = payload.get("cwd") or ""
    writes = tool in ("Write", "Edit", "NotebookEdit")
    paths = _paths(tool, tin)
    cmd = str(tin.get("command") or "") if tool == "Bash" else ""

    if agent == "clu-verifier":
        norm = [x for p in paths for x in _norm(p).split("\n")]
        if any(BLIND.search(p) for p in norm) or BLIND.search(cmd) or ("/runs/" in cwd and BLIND_REL.search(cmd)) \
                or ("/runs/" in cmd and BLIND_ANY.search(cmd)):
            return ("clu-verifier is blind to the runner's work: do not open <run>/queries, <run>/results or "
                    "<run>/helpers. Write your own queries and results under <run>/verify/.")
        if tool in ("Grep", "Glob") and tin.get("path") and any(
                re.search(CLU_DIR + r"(/runs(/[^/]+)?)?/?$", x) for x in _norm(str(tin["path"])).split("\n")):
            return "clu-verifier: search <run>/verify/ or name the file (contract.md); the run root holds the runner's work."
        if writes and not all(_in_run(p, "verify") or _is_tmp(p) for p in paths):
            return "clu-verifier writes only under <run>/verify/ (or a temp dir)."
        return None

    if agent in READ_ONLY:
        if writes and not all(_in_run(p, "helpers") or (agent == "clu-editor" and _is_tmp(p)) for p in paths):
            return f"{agent} is read-only: its only write is its JSON output under <run>/helpers/."
        if cmd:
            for i, (rx, what) in enumerate(MUTATING):
                # plain file commands are matched outside quotes (so `search "last touch"` is fine);
                # clu's own tools are matched on the raw command, because their paths are quoted
                if rx.search(_unquoted(cmd) if i == 0 else cmd):
                    return f"{agent} is read-only: Bash {what} are not allowed. Report the change in your output instead."
            bad = [t for t in _redirect_targets(cmd) if t != "/dev/null" and not _is_tmp(t)]
            if bad:
                return f"{agent} is read-only: redirecting output to {bad[0]} is not allowed (use a temp dir)."
            if agent == "clu-challenger" and re.search(r"\bmkdir\b", _unquoted(cmd)):
                return "clu-challenger is read-only: it creates no folders."
        return None

    if agent in RUN_WRITERS:
        ok = (lambda p: _in_run(p) or _is_tmp(p) or (agent == "clu-curator" and p.endswith("/clu/kb/eval/recall_set.jsonl")))
        if writes and not all(ok(p) for p in paths):
            return f"{agent} writes only in the run folder or a temp dir."
        if agent == "clu-curator" and cmd:
            if re.search(r"proposals\.py\"?\s+(apply|reject)\b", cmd):
                return "clu-curator only drafts proposals (`proposals.py new`); the user applies or rejects them via /clu-review."
            if any(_protected(t) for t in _redirect_targets(cmd)) or (
                    re.search(r"\bsed\s+(-[a-zA-Z]*i|--in-place)", cmd) and PROTECTED.search(cmd)):
                return "clu-curator never edits agents, skills, scripts, hooks or tests: draft a proposal instead."
        return None

    if agent == "clu":
        if writes and any(_protected(p) for p in paths):
            return ("clu does not edit its own agents, skills, scripts, hooks or tests directly. Draft the change "
                    'with `python3 "$CLU/scripts/proposals.py" new ...`; the user approves it in /clu-review.')
    return None


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        reason = decide(payload)
    except Exception:  # noqa: BLE001 - a broken guard must never block Claude
        return 0
    if reason:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                                 "permissionDecisionReason": reason}}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
