#!/usr/bin/env python3
"""Bug drafts: write, check for duplicates, record the verifier's verdict, get the owner's approval, then file.

  bug_draft.py new <draft.yaml> --out-dir <run>/bugs          writes BUG-<slug>.json and .md, records a KB row (draft)
  bug_draft.py dedupe <bug.json> [--project PROJ]               local KB matches and the JQL to search Jira with
  bug_draft.py verdict <bug.json> --verdict sustained|refuted --notes "..."
  bug_draft.py show <bug.json>                                 the draft as the owner reviews it, with its fingerprint
  bug_draft.py approve <bug.json> --fingerprint <hash>         only after the owner said yes to that exact draft
  bug_draft.py payload <bug.json> [--project PROJ]              fields, the Jira-formatted description file and the exact jira_tool.py create command (refuses unless approved)
  bug_draft.py paste <bug.json>                                ready-to-paste Jira text (works for unapproved drafts too)
  bug_draft.py filed <bug.json> --key PROJ-1234

Nothing here talks to Jira. Filing is done by Rinzler through the your-jira skill
(python3 ~/.claude/skills/your-jira/scripts/jira_tool.py create ...), only with the command this
script produced after approval, so a draft that changed after the owner approved it cannot be filed by mistake.
Severity is how bad the effect is (Rinzler proposes it with a reason); priority is how soon to fix (proposed, the
owner or PM decides).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import re
import shlex
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "kb")]
import rpaths  # noqa: E402
import safety  # noqa: E402

SEVERITY = {
    "S1": "Critical: wrong money or data at scale, security or privacy exposure, or a core flow is unusable with no workaround.",
    "S2": "Major: a core flow is blocked or gives a wrong result for a real group of sellers, and a workaround exists.",
    "S3": "Minor: wrong or misleading behaviour in an edge case or secondary flow; the seller can still finish the task.",
    "S4": "Trivial: cosmetic, wording or layout with no effect on what the seller decides or does.",
}
PRIORITY = {"P1": "fix now (hotfix)", "P2": "fix in the current sprint", "P3": "plan in the backlog", "P4": "fix when convenient"}
REQUIRED = ("title", "feature", "environment", "steps", "expected", "actual", "severity", "severity_rationale",
            "priority", "priority_rationale", "requirement_id", "evidence", "frequency")
CONTENT = REQUIRED + ("feature_fa", "component", "preconditions", "impact", "workaround", "notes", "links", "data")
VAGUE = re.compile(r"^(?:\W*)(bug|issue|problem|error|broken|doesn'?t work|not working)\W*$", re.I)


def fingerprint(bug: dict) -> str:
    content = {k: bug.get(k) for k in CONTENT}
    return hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]


def _redact(v):
    if isinstance(v, str):
        return safety.redact(v)
    if isinstance(v, list):
        return [_redact(x) for x in v]
    if isinstance(v, dict):
        return {k: _redact(x) for k, x in v.items()}
    return v


def check(bug: dict, base: Path) -> list[str]:
    errs = [f"missing {k}" for k in REQUIRED if not bug.get(k)]
    t = bug.get("title", "")
    if len(t) > 120:
        errs.append("title longer than 120 characters")
    if t and (VAGUE.match(t) or len(t.split()) < 4):
        errs.append("title is vague: say what is wrong, where, and under which condition")
    if bug.get("severity") and bug["severity"] not in SEVERITY:
        errs.append(f"severity must be one of {list(SEVERITY)}")
    if bug.get("priority") and bug["priority"] not in PRIORITY:
        errs.append(f"priority must be one of {list(PRIORITY)}")
    if isinstance(bug.get("steps"), list) and len(bug["steps"]) < 2:
        errs.append("give at least two steps a stranger can follow")
    if bug.get("expected") and bug.get("expected") == bug.get("actual"):
        errs.append("expected and actual are the same")
    env = bug.get("environment") or {}
    if isinstance(env, dict):
        for k in ("name", "build"):
            if not env.get(k):
                errs.append(f"environment.{k} missing (name and build or commit)")
        if env.get("name") == "production" or (env.get("url") and safety.classify_host(env["url"]) == "production"):
            errs.append("environment is production: Rinzler reproduces on local, demo or stage only")
    for e in bug.get("evidence") or []:
        p = (base / e) if not Path(e).is_absolute() else Path(e)
        if safety.is_secret_path(p):
            errs.append(f"evidence {e} is a secret file")
        elif not p.exists():
            errs.append(f"evidence {e} not found")
    return errs


def slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:48] or "bug"


def _kb_upsert(bug: dict, path: Path) -> str:
    import store
    c = store.connect()
    eid = store.upsert(c, "bug", {"title": bug["title"], "feature": bug["feature"], "component": bug.get("component", ""),
                                  "severity": bug["severity"], "priority": bug["priority"], "status": bug["status"],
                                  "summary": f"{bug['actual'][:300]}", "requirement_id": bug["requirement_id"],
                                  "jira_key": bug.get("jira_key", ""), "draft_path": str(path), "run_id": bug.get("run_id", "")})
    store.export(c, "bug")
    return eid


def to_markdown(bug: dict, for_jira=False) -> str:
    env = bug["environment"]
    head = [] if for_jira else [f"# {bug['title']}", "",
                                f"Status: {bug['status']}. Fingerprint: `{fingerprint(bug)}`.", ""]
    L = head + ["*Summary*" if for_jira else "## Summary", "", bug.get("impact") or bug["actual"], "",
                "*Environment*" if for_jira else "## Environment", "",
                f"- {env.get('name')} {env.get('url', '')}".rstrip(), f"- Build: {env.get('build')}",
                *(f"- {k}: {v}" for k, v in env.items() if k not in ("name", "url", "build")), "",
                "*Preconditions*" if for_jira else "## Preconditions", "", bug.get("preconditions") or "None.", "",
                "*Steps to reproduce*" if for_jira else "## Steps to reproduce", ""]
    L += [f"{'#' if for_jira else str(i) + '.'} {s}" for i, s in enumerate(bug["steps"], 1)]
    L += ["", "*Expected*" if for_jira else "## Expected", "", bug["expected"], "",
          "*Actual*" if for_jira else "## Actual", "", bug["actual"], "",
          f"Frequency: {bug['frequency']}. Workaround: {bug.get('workaround') or 'none known'}.", "",
          "*Severity and priority (proposed)*" if for_jira else "## Severity and priority (proposed)", "",
          f"- Severity {bug['severity']} ({SEVERITY[bug['severity']].split(':')[0]}): {bug['severity_rationale']}",
          f"- Priority {bug['priority']} ({PRIORITY[bug['priority']]}): {bug['priority_rationale']}", "",
          "*Requirement*" if for_jira else "## Requirement", "", bug["requirement_id"], "",
          "*Evidence*" if for_jira else "## Evidence", ""]
    L += [f"- {e}" for e in bug["evidence"]]
    if bug.get("notes"):
        L += ["", "*Notes*" if for_jira else "## Notes", "", bug["notes"]]
    v = bug.get("verification")
    if v and not for_jira:
        L += ["", "## Verification", "", f"{v['verdict']} by {v['by']} on {v['at']}: {v['notes']}"]
    if bug.get("duplicates_checked") and not for_jira:
        d = bug["duplicates_checked"]
        L += ["", "## Duplicate check", "", f"KB matches: {d.get('kb') or 'none'}. Jira: {d.get('jira') or 'not searched yet'}."]
    L += ["", "_Drafted by Rinzler (QA agent); verified before filing; filed only with the owner's approval._"]
    return "\n".join(L) + "\n"


def load(path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save(path, bug: dict) -> None:
    p = Path(path)
    safety.check_writable(p)
    p.write_text(json.dumps(bug, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    p.with_suffix(".md").write_text(to_markdown(bug), encoding="utf-8")


def dedupe_query(bug: dict, project: str) -> dict:
    import store
    words = [w for w in re.findall(r"\w{4,}", f"{bug['title']} {bug.get('component', '')}") if w.lower() not in store.STOPWORDS]
    kb = [r for r in store.search(store.connect(), " ".join(words), "bug", 5) if r["id"] != bug.get("kb_id")]
    terms = " ".join(words[:6]).replace('"', "")
    return {"kb_matches": kb,
            "jql": [f'project = {project} AND issuetype = Bug AND statusCategory != Done AND text ~ "{terms}" ORDER BY updated DESC',
                    f'project = {project} AND issuetype = Bug AND summary ~ "{bug["feature"]}" ORDER BY updated DESC']
            + ([f'project = {project} AND issuetype = Bug AND text ~ "{bug["feature_fa"]}" ORDER BY updated DESC']
               if bug.get("feature_fa") else []),
            "how": "run each JQL with `python3 ~/.claude/skills/your-jira/scripts/jira_tool.py search --jql \"<JQL>\" --limit 10`; read the results; record the outcome with "
                   "`bug_draft.py verdict` notes or in duplicates_checked.jira"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="bug_draft.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("new")
    n.add_argument("draft")
    n.add_argument("--out-dir", required=True)
    for name in ("dedupe", "show", "payload", "paste"):
        x = sub.add_parser(name)
        x.add_argument("bug")
        if name in ("dedupe", "payload"):
            x.add_argument("--project")
    v = sub.add_parser("verdict")
    v.add_argument("bug")
    v.add_argument("--verdict", choices=["sustained", "refuted"], required=True)
    v.add_argument("--notes", required=True)
    v.add_argument("--by", default="rinzler-verifier")
    ap_ = sub.add_parser("approve")
    ap_.add_argument("bug")
    ap_.add_argument("--fingerprint", required=True)
    ap_.add_argument("--by", default="owner")
    f = sub.add_parser("filed")
    f.add_argument("bug")
    f.add_argument("--key", required=True)
    a = ap.parse_args(argv)
    project = getattr(a, "project", None) or json.loads(rpaths.config("jira.json").read_text())["project"]

    if a.cmd == "new":
        src = Path(a.draft)
        bug = _redact(yaml.safe_load(src.read_text(encoding="utf-8")))
        errs = check(bug, src.parent)
        if errs:
            print("draft not ready:\n  " + "\n  ".join(errs), file=sys.stderr)
            return 2
        out = Path(a.out_dir)
        safety.check_writable(out)
        out.mkdir(parents=True, exist_ok=True)
        bug["evidence"] = [str((src.parent / e).resolve()) if not Path(e).is_absolute() else e for e in bug["evidence"]]
        bug |= {"status": "draft", "created_at": _dt.datetime.now().isoformat(timespec="seconds")}
        path = out / f"BUG-{slug(bug['title'])}.json"
        bug["kb_id"] = _kb_upsert(bug, path)
        save(path, bug)
        print(path)
        return 0
    bug = load(a.bug)
    if a.cmd == "dedupe":
        d = dedupe_query(bug, project)
        bug["duplicates_checked"] = {"kb": [m["id"] for m in d["kb_matches"]], "jira": bug.get("duplicates_checked", {}).get("jira")}
        save(a.bug, bug)
        print(json.dumps(d, ensure_ascii=False, indent=1))
    elif a.cmd == "verdict":
        bug["verification"] = {"verdict": a.verdict, "by": a.by, "notes": a.notes,
                               "at": _dt.datetime.now().isoformat(timespec="seconds")}
        bug["status"] = "verified" if a.verdict == "sustained" else "not-a-bug"
        save(a.bug, bug)
        _kb_upsert(bug, Path(a.bug))
        print(bug["status"])
    elif a.cmd == "show":
        print(to_markdown(bug))
    elif a.cmd == "approve":
        if bug.get("status") != "verified":
            print(f"REFUSED: status is {bug.get('status')}; only a draft the verifier sustained can be approved", file=sys.stderr)
            return 4
        if a.fingerprint != fingerprint(bug):
            print("REFUSED: the draft changed since the owner saw it; show it again and get a new yes", file=sys.stderr)
            return 4
        bug["approval"] = {"fingerprint": a.fingerprint, "by": a.by, "at": _dt.datetime.now().isoformat(timespec="seconds")}
        bug["status"] = "approved"
        save(a.bug, bug)
        _kb_upsert(bug, Path(a.bug))
        print("approved")
    elif a.cmd == "payload":
        appr = bug.get("approval") or {}
        if bug.get("status") != "approved" or appr.get("fingerprint") != fingerprint(bug):
            print("REFUSED: no owner approval for this exact draft (run show, ask the owner, then approve)", file=sys.stderr)
            return 4
        desc_file = Path(a.bug).with_suffix(".jira.txt")
        safety.check_writable(desc_file)
        desc_file.write_text(to_markdown(bug, for_jira=True), encoding="utf-8")
        labels = ["rinzler", re.sub(r"\W+", "-", bug["feature"].lower())]
        jira = "python3 ~/.claude/skills/your-jira/scripts/jira_tool.py"
        cli = (f"{jira} create --project {shlex.quote(project)} --issue-type Bug --summary {shlex.quote(bug['title'])} "
               f"--description \"$(cat {shlex.quote(str(desc_file))})\" --labels {' '.join(shlex.quote(x) for x in labels)}")
        print(json.dumps({"projectKey": project, "issueTypeName": "Bug", "summary": bug["title"],
                          "description": to_markdown(bug, for_jira=True), "description_file": str(desc_file),
                          "labels": labels, "cli": cli,
                          "how": "run `cli` with --dry-run appended first, then without it; then bug_draft.py filed <bug.json> --key <KEY>",
                          "proposed_priority": bug["priority"], "attachments_to_add_by_hand": bug["evidence"]},
                         ensure_ascii=False, indent=1))
    elif a.cmd == "paste":
        tag = "" if bug.get("status") == "approved" else "[DRAFT, NOT APPROVED] "
        print(f"Summary: {tag}{bug['title']}\n\n" + to_markdown(bug, for_jira=True))
    elif a.cmd == "filed":
        if bug.get("status") != "approved":
            print("REFUSED: only an approved bug can be marked filed", file=sys.stderr)
            return 4
        bug |= {"status": "filed", "jira_key": a.key}
        save(a.bug, bug)
        _kb_upsert(bug, Path(a.bug))
        print(a.key)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
