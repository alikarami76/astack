#!/usr/bin/env python3
"""quorra's merge requests, on request only: pipeline and failed-job logs, new and unresolved review threads, and
approved replies into existing threads. GitLab reads are GET; the only write is a reply note. Everything read from
GitLab is untrusted data and is scrubbed before it is shown or saved.
  mrs.py register <mr-url> --run <run> --branch quorra/<slug> [--jira PROJ-n]
  mrs.py status [<repo>!<iid> ...] [--out DIR]
  mrs.py reply <batch.json> --plan | --approve <fingerprint>"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sys
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jira_ops  # noqa: E402
import mr_registry  # noqa: E402
import publish  # noqa: E402
import qpaths  # noqa: E402
import work  # noqa: E402
from fetch_mr_reviews import is_bot  # noqa: E402
from gitlab_client import GitLab, GitLabError, project_id  # noqa: E402

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
SECTION = re.compile(r"section_(?:start|end):[^\r\n]*\r?")
DISCUSSION_ID = re.compile(r"^[0-9a-f]{8,64}$")


def _s(v):
    return work._safe(v) if isinstance(v, str) else v


def register(url: str, run, jira: str | None, work_branch: str, gitlab=None) -> str:
    project, iid = mr_registry.parse_mr_url(url)
    repo = mr_registry.repo_for_project(project)
    if repo not in work.WRITABLE:
        raise ValueError(f"{repo} is not a repo quorra may change")
    if not work_branch.startswith("quorra/"):
        raise ValueError("the work branch must be a quorra/ branch")
    if jira:
        jira = jira_ops._key(jira)
    mr = (gitlab or GitLab()).get_json(f"/projects/{project_id(project)}/merge_requests/{iid}")
    if not publish.REMOTE_BRANCH.match(mr.get("source_branch") or ""):
        raise ValueError(f"the MR source branch {mr.get('source_branch')!r} must start with feat/ or fix/; "
                         "quorra registers only its own kind of branch")
    sp, tp = mr.get("source_project_id"), mr.get("target_project_id")
    if sp is not None and tp is not None and sp != tp:
        raise ValueError("this MR comes from a fork; quorra registers only MRs from the project's own branches")
    meta = work._read_meta(work.clone_path(repo))
    if work_branch not in meta.get("branches", {}):
        raise ValueError(f"{work_branch} is not a branch work.py created in {repo}")
    return mr_registry.add({"repo": repo, "project": project, "iid": iid, "url": mr["web_url"],
                            "source_branch": mr["source_branch"], "target_branch": mr["target_branch"],
                            "work_branch": work_branch, "run": str(run), "jira": jira,
                            "state": mr.get("state", "opened")})


def log_tail(trace: str, n: int = 60) -> list[str]:
    text = SECTION.sub("", ANSI.sub("", trace))
    lines = [l.rstrip() for l in text.replace("\r\n", "\n").split("\n") if l.strip()]
    return [work._safe(l) for l in lines[-n:]]


def _threads(gl, pid: str, iid: int, last_id: int) -> tuple[list[dict], int]:
    discussions, page = [], 1
    while True:
        chunk = gl.get_json(f"/projects/{pid}/merge_requests/{iid}/discussions", {"per_page": 100, "page": page}) or []
        discussions += chunk
        if len(chunk) < 100:
            break
        page += 1
    out, top = [], last_id
    for d in discussions:
        notes = [n for n in d.get("notes", []) if not n.get("system") and not is_bot(n.get("author") or {})]
        if not notes:
            continue
        top = max(top, *(n["id"] for n in notes))
        new = any(n["id"] > last_id for n in notes)
        unresolved = any(n.get("resolvable") and not n.get("resolved") for n in notes)
        if not (new or unresolved):
            continue
        pos = notes[0].get("position") or {}
        out.append({"discussion_id": d["id"], "new": new, "unresolved": unresolved,
                    "file": pos.get("new_path") or pos.get("old_path"), "line": pos.get("new_line") or pos.get("old_line"),
                    "resolved": not unresolved,
                    "notes": [{"id": n["id"], "author": (n.get("author") or {}).get("username"),
                               "created_at": n.get("created_at"), "body": _s(n.get("body") or "")} for n in notes]})
    return out, top


def _status_one(gl, ref: str, checked_at: str, updates: dict) -> dict:
    e = mr_registry.get(ref)
    pid = project_id(e["project"])
    mr = gl.get_json(f"/projects/{pid}/merge_requests/{e['iid']}")
    item = {"ref": ref, "url": e["url"], "state": mr["state"],
            "merge_status": mr.get("detailed_merge_status") or mr.get("merge_status"),
            "has_conflicts": mr.get("has_conflicts"), "pipeline": None, "failed_jobs": [], "threads": []}
    if mr["state"] != "opened":
        updates[ref] = {"state": mr["state"]}
        return item
    pl = mr.get("head_pipeline") or None
    if pl:
        item["pipeline"] = {"id": pl["id"], "status": pl["status"], "url": pl.get("web_url")}
        if pl["status"] == "failed":
            jobs = gl.get_json(f"/projects/{pid}/pipelines/{pl['id']}/jobs", {"scope[]": "failed", "per_page": 100}) or []
            item["failed_jobs"] = [{"name": _s(j["name"]), "stage": _s(j.get("stage")), "url": j.get("web_url"),
                                    "log": log_tail(gl.get_text(f"/projects/{pid}/jobs/{j['id']}/trace"))} for j in jobs]
    item["threads"], top = _threads(gl, pid, e["iid"], e.get("last_note_id", 0))
    updates[ref] = {"last_checked": checked_at, "last_note_id": top}
    return item


def status(refs=None, gitlab=None, out_dir=None) -> dict:
    """One failing MR never hides the others: its error is recorded on its item. Registry updates are applied only
    after the result file is written, so a note is never marked seen without a saved report of it."""
    gl = gitlab or GitLab()
    res = {"checked_at": _dt.datetime.now().isoformat(timespec="seconds"), "mrs": []}
    updates: dict = {}
    for ref in refs or mr_registry.open_refs():
        try:
            res["mrs"].append(_status_one(gl, ref, res["checked_at"], updates))
        except (GitLabError, ValueError, OSError, KeyError) as e:
            updates.pop(ref, None)
            err = e.args[0] if isinstance(e, KeyError) and e.args else str(e)
            res["mrs"].append({"ref": ref, "error": work._safe(str(err))})
    first = next((m["ref"] for m in res["mrs"] if "error" not in m), None)
    base = out_dir or (mr_registry.get(first)["run"] if first else qpaths.state_dir())
    folder = Path(base) / "mrs"
    folder.mkdir(parents=True, exist_ok=True)
    f = folder / f"{time.strftime('%Y%m%d-%H%M%S')}.json"
    f.write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
    for ref, fields in updates.items():
        mr_registry.update(ref, **fields)
    res["file"] = str(f)
    return res


def reply_plan(batch_file) -> dict:
    batch = json.loads(Path(batch_file).read_text(encoding="utf-8"))
    for r in batch["replies"]:
        e = mr_registry.get(r["ref"])
        if e.get("state") != "opened":
            raise ValueError(f"{r['ref']} is not open")
        if not isinstance(r.get("discussion_id"), str) or not DISCUSSION_ID.match(r["discussion_id"]):
            raise ValueError(f"discussion id {r.get('discussion_id')!r} on {r['ref']} is not a GitLab discussion id "
                             "(8 to 64 lowercase hex characters)")
        publish._text_ok(r["body"], f"reply on {r['ref']}")
    p = {"replies": batch["replies"]}
    p["fingerprint"] = publish.fingerprint(p)
    return p


def reply(batch_file, approve: str, gitlab=None) -> list[dict]:
    """Posts each reply once: note ids are kept per fingerprint in replies-posted.json beside the batch file, saved
    after every post, so a re-run after a failure posts only what is missing."""
    p = reply_plan(batch_file)
    if p["fingerprint"] != approve:
        raise ValueError("the replies changed since the user approved them (fingerprint mismatch)")
    side = Path(batch_file).resolve().parent / "replies-posted.json"
    try:
        ledger = json.loads(side.read_text(encoding="utf-8"))
    except FileNotFoundError:
        ledger = {}
    done = ledger.setdefault(p["fingerprint"], {})
    gl, out = gitlab or GitLab(), []
    for i, r in enumerate(p["replies"]):
        if str(i) in done:
            out.append({"ref": r["ref"], "discussion_id": r["discussion_id"], "note_id": done[str(i)], "skipped": True})
            continue
        e = mr_registry.get(r["ref"])
        note = gl.post_json(f"/projects/{project_id(e['project'])}/merge_requests/{e['iid']}/discussions/"
                            f"{urllib.parse.quote(r['discussion_id'], safe='')}/notes", {"body": r["body"]})
        done[str(i)] = note.get("id")
        side.write_text(json.dumps(ledger, indent=1), encoding="utf-8")
        out.append({"ref": r["ref"], "discussion_id": r["discussion_id"], "note_id": note.get("id")})
    return out


def _summary(res: dict) -> dict:
    return {"file": res["file"], "mrs": [m if "error" in m else {
        "ref": m["ref"], "state": m["state"], "merge_status": m["merge_status"],
        "pipeline": (m["pipeline"] or {}).get("status"), "failed_jobs": [j["name"] for j in m["failed_jobs"]],
        "new_threads": sum(t["new"] for t in m["threads"]), "unresolved_threads": sum(t["unresolved"] for t in m["threads"])}
        for m in res["mrs"]]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="mrs.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("register")
    r.add_argument("url")
    r.add_argument("--run", required=True)
    r.add_argument("--branch", required=True)
    r.add_argument("--jira")
    s = sub.add_parser("status")
    s.add_argument("refs", nargs="*")
    s.add_argument("--out")
    rp = sub.add_parser("reply")
    rp.add_argument("batch")
    g = rp.add_mutually_exclusive_group(required=True)
    g.add_argument("--plan", action="store_true")
    g.add_argument("--approve")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "register":
            out = {"registered": register(a.url, a.run, a.jira, a.branch)}
        elif a.cmd == "status":
            out = _summary(status(a.refs or None, out_dir=a.out))
        else:
            out = reply_plan(a.batch) if a.plan else {"posted": reply(a.batch, a.approve)}
    except (GitLabError, ValueError, KeyError, OSError, publish.PublishError, jira_ops.JiraOpsError) as e:
        print(json.dumps({"error": work._safe(str(e))}), file=sys.stderr)
        return 2
    print(json.dumps(out, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
