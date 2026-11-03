#!/usr/bin/env python3
"""quorra publishing: pushes a work clone branch from a temporary clone and opens the MR, only with the fingerprint
of the exact plan the user approved. Work clones themselves stay push-locked. No merge, approve, close, delete or force.
  publish.py plan <run>                                (reads <run>/publish-request.json, writes publish-plan.json)
  publish.py push <run> --approve <fingerprint>
  publish.py update <repo>!<iid> --plan               (prints the new commits and their fingerprint)
  publish.py update <repo>!<iid> --approve <fingerprint>"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import qpaths  # noqa: E402
import work  # noqa: E402
import jira_ops  # noqa: E402
import mr_registry  # noqa: E402
import run_record  # noqa: E402
from gitlab_client import GitLab, GitLabError, project_id  # noqa: E402
from jira_ops import ATTRIBUTION, JiraOpsError, _fp as fingerprint  # noqa: E402

REMOTE_BRANCH = re.compile(r"^(feat|fix)/[a-z0-9][a-z0-9._-]{0,80}$")
USERINFO = re.compile(r"^[a-z+]+://[^/@]*@")


class PublishError(RuntimeError):
    pass


def _text_ok(text: str, what: str) -> None:
    if ATTRIBUTION.search(text):
        raise PublishError(f"{what} carries a tool attribution line; published text never does")
    if work._safe(text) != text:
        raise PublishError(f"{what} contains a possible secret; remove it first")


def push_url(repo: str) -> str:
    cfg = qpaths.repo_cfg(repo)
    p = work._run(cfg["path"], "git", "config", "--get", "remote.origin.url")
    url = p.stdout.strip()
    if p.returncode != 0 or not url:
        raise PublishError(f"the user's clone of {repo} has no origin URL")
    if USERINFO.match(url) and not url.startswith("ssh://git@"):
        raise PublishError(f"the origin URL of {repo} carries credentials; quorra never pushes with embedded credentials")
    scp = bool(re.match(r"^[\w.-]+@[\w.-]+:", url))
    if not url.startswith(work.GITLAB_PREFIXES) and ("://" in url or scp):
        raise PublishError(f"the origin URL of {repo} is not on your configured GitLab; quorra pushes only there")
    path = url.split(":", 1)[1] if scp else url
    path = path[:-4] if path.endswith(".git") else path
    if not path.rstrip("/").endswith("/" + cfg["gitlab"]) and path.rstrip("/") != cfg["gitlab"]:
        raise PublishError(f"the origin URL of {repo} does not point at {cfg['gitlab']}")
    return url


def branch_state(repo: str, branch: str) -> dict:
    clone = work.clone_path(repo)
    meta = work._read_meta(clone)
    if not branch.startswith("quorra/") or branch not in meta.get("branches", {}):
        raise PublishError(f"{branch} is not a branch work.py created in {repo}")
    base = meta["branches"][branch]["base"]
    head = work._git(clone, "rev-parse", f"refs/heads/{branch}")
    log = work._git(clone, "log", "--reverse", "--format=%H%x09%s", f"{base}..{head}")
    commits = [dict(zip(("sha", "subject"), line.split("\t", 1))) for line in log.splitlines() if line]
    if not commits:
        raise PublishError(f"{branch} has no commits to publish")
    return {"base": base, "head": head, "commits": commits}


def check_gate(repo: str, branch: str, head: str) -> dict:
    f = work.clone_path(repo) / ".git" / "quorra-checks.json"
    if not f.exists():
        raise PublishError(f"no check result for {repo}; run work.py check {repo} first")
    res = json.loads(f.read_text(encoding="utf-8"))
    if res.get("branch") != branch or res.get("head") != head:
        raise PublishError(f"{repo} checks are older than the branch head; run work.py check {repo} again")
    if res.get("only"):
        raise PublishError(f"{repo} has a partial check run ({', '.join(res['only'])} only); run work.py check {repo} "
                           "with no --only")
    if res.get("dirty") is not False:
        raise PublishError(f"{repo} checks ran on uncommitted changes; commit them and run work.py check {repo} again")
    failed = [r["name"] for r in res["results"] if r["status"] == "failed"]
    if failed:
        raise PublishError(f"{repo} has a failed check ({', '.join(failed)}); fix it before publishing")
    return {r["name"]: {"status": r["status"], "reason": r.get("reason", "")} for r in res["results"]}


def _checks_table(checks: dict) -> str:
    rows = [f"| {n} | {'passed' if c['status'] == 'passed' else 'not run: ' + (c['reason'] or 'not run')} |"
            for n, c in checks.items()]
    return "\n".join(["| Check | Result |", "|---|---|", *rows])


def _static_checks(repo: str, remote_branch: str, title: str, description: str, jira) -> str | None:
    """The checks that depend only on the item's own fields; run at plan time and again at push time. Returns the
    normalised Jira key (or None)."""
    if repo not in work.WRITABLE:
        raise PublishError(f"{repo} is not a repo quorra may change (allowed: {', '.join(work.WRITABLE)})")
    if not REMOTE_BRANCH.match(remote_branch):
        raise PublishError(f"remote branch {remote_branch!r} must start with feat/ or fix/")
    _text_ok(title, "MR title")
    _text_ok(description, "MR description")
    if not jira:
        return None
    try:
        return jira_ops._key(jira)
    except JiraOpsError as e:
        raise PublishError(str(e)) from None


def plan(run: Path) -> dict:
    run = Path(run)
    req = json.loads((run / "publish-request.json").read_text(encoding="utf-8"))
    items = []
    for r in req["items"]:
        repo = r["repo"]
        jira = _static_checks(repo, r["remote_branch"], r["title"], r["description"], r.get("jira"))
        st = branch_state(repo, r["branch"])
        checks = check_gate(repo, r["branch"], st["head"])
        items.append({
            "repo": repo, "project": qpaths.repo_cfg(repo)["gitlab"], "push_url": push_url(repo),
            "branch": r["branch"], **st, "remote_branch": r["remote_branch"],
            "target": r.get("target") or qpaths.repo_cfg(repo)["ref"], "title": r["title"],
            "description": r["description"].rstrip() + "\n\n" + _checks_table(checks),
            "checks": {n: c["status"] for n, c in checks.items()},
            "jira": jira, "jira_actions": ["review", "comment-mr-link"] if r.get("jira") else []})
    p = {"run": run.name, "items": items}
    p["fingerprint"] = fingerprint(p)
    (run / "publish-plan.json").write_text(json.dumps(p, indent=1, ensure_ascii=False), encoding="utf-8")
    return p


def remote_head(url: str, branch: str) -> str | None:
    try:
        p = work._run(Path.cwd(), "git", "ls-remote", url, f"refs/heads/{branch}",
                      env={**os.environ, "GIT_TERMINAL_PROMPT": "0"}, timeout=120)
    except subprocess.TimeoutExpired:
        raise PublishError(f"reading the remote branch {branch} timed out after 120s") from None
    if p.returncode != 0:
        raise PublishError(f"cannot read the remote branch: {work._safe(p.stderr.strip())[:300]}")
    line = p.stdout.strip().splitlines()
    return line[0].split()[0] if line else None


def push_ref(repo: str, url: str, sha: str, remote_branch: str, expect_remote: str | None) -> str:
    current = remote_head(url, remote_branch)
    if current == sha:
        return "already"
    if current != expect_remote:
        raise PublishError(f"remote branch {remote_branch} exists with other history; quorra does not overwrite it")
    tmp = Path(tempfile.mkdtemp(prefix="quorra-publish-"))
    try:
        work._git(Path.cwd(), "clone", "--quiet", "--no-checkout", "--no-hardlinks",
                  str(work.clone_path(repo)), str(tmp / "c"))
        try:
            p = work._run(tmp / "c", "git", "push", "--porcelain", url, f"{sha}:refs/heads/{remote_branch}",
                          env={**os.environ, "GIT_TERMINAL_PROMPT": "0"}, timeout=600)
        except subprocess.TimeoutExpired:
            raise PublishError(f"push of {remote_branch} timed out after 600s") from None
        if p.returncode != 0:
            raise PublishError(f"push of {remote_branch} failed: {work._safe(p.stderr.strip())[:500]}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return "pushed"


def _scan_outgoing(repo: str, base: str, head: str) -> None:
    """Scan every outgoing commit: each commit message and each added line of each commit's own diff."""
    clone = work.clone_path(repo)
    plus = chr(43)
    if work._git(clone, "rev-list", "--merges", f"{base}..{head}").strip():
        raise PublishError(f"the outgoing commits of {repo} include a merge commit; rebase onto the target instead")
    for rec in work._git(clone, "log", "--reverse", "--format=%H%x1f%B%x1e", f"{base}..{head}").split("\x1e"):
        sha, _, body = rec.strip().partition("\x1f")
        if sha and ATTRIBUTION.search(body):
            raise PublishError(f"tool attribution line in commit {sha[:7]}; commits never carry one; "
                               "reword the commit")
    msgs = work._git(clone, "log", "--reverse", "--format=%B", f"{base}..{head}")
    patch = work._git(clone, "log", "--reverse", "-p", "--format=", f"{base}..{head}")
    added = [ln for ln in patch.splitlines() if ln[:1] == plus and not ln.startswith(plus * 3 + " ")]
    flagged = [str(i) for i, line in enumerate(msgs.splitlines() + added, 1) if work._safe(line) != line]
    if flagged:
        raise PublishError(f"possible secret in the outgoing commits of {repo}, line(s) {', '.join(flagged[:20])}; "
                           "remove it, commit, check and plan again")


def _verify(item: dict, request_item: dict | None = None) -> None:
    if _static_checks(item["repo"], item["remote_branch"], item["title"], item["description"],
                      item.get("jira")) != item.get("jira"):
        raise PublishError(f"the Jira key of the {item['repo']} item is not in canonical form; run publish.py plan again")
    if item["project"] != qpaths.repo_cfg(item["repo"])["gitlab"]:
        raise PublishError(f"the {item['repo']} item names a project that is not the repo's GitLab project")
    st = branch_state(item["repo"], item["branch"])
    if item["base"] != st["base"]:
        raise PublishError(f"the {item['repo']} item names a base commit that is not the branch's base")
    if request_item is not None:
        want = request_item.get("target") or qpaths.repo_cfg(item["repo"])["ref"]
        if item["target"] != want:
            raise PublishError(f"the {item['repo']} item targets {item['target']}, not the requested {want}")
    if st["head"] != item["head"]:
        raise PublishError(f"the {item['repo']} branch moved since the plan; run publish.py plan again")
    check_gate(item["repo"], item["branch"], item["head"])
    if push_url(item["repo"]) != item["push_url"]:
        raise PublishError(f"the {item['repo']} origin URL changed since the plan")
    _scan_outgoing(item["repo"], item["base"], item["head"])


def _open_mr(gl, item: dict) -> dict:
    pid = project_id(item["project"])
    existing = gl.get_json(f"/projects/{pid}/merge_requests",
                           {"state": "opened", "source_branch": item["remote_branch"]}) or []
    if existing:
        if existing[0].get("target_branch") != item["target"]:
            raise PublishError(f"an open MR for {item['remote_branch']} already targets "
                               f"{existing[0].get('target_branch')}, not {item['target']}; quorra does not change it")
        return existing[0]
    return gl.post_json(f"/projects/{pid}/merge_requests", {
        "source_branch": item["remote_branch"], "target_branch": item["target"], "title": item["title"],
        "description": item["description"], "remove_source_branch": False, "squash": False})


def _expect_remote(item: dict) -> str | None:
    """The remote branch tip quorra may move forward: none, or a tip that is an ancestor of the planned head
    (an earlier push of this same branch whose MR step failed)."""
    cur = remote_head(item["push_url"], item["remote_branch"])
    if not cur or cur == item["head"]:
        return None
    clone = work.clone_path(item["repo"])
    if work._run(clone, "git", "cat-file", "-e", f"{cur}^{{commit}}").returncode == 0 and \
            work._run(clone, "git", "merge-base", "--is-ancestor", cur, item["head"]).returncode == 0:
        return cur
    return None


def _jira(run: Path, key: str, mr_url: str, runner, fp: str, repo: str) -> list[str]:
    """Move to review and add the MR link, each at most once per plan: done steps are kept in the run's jira.json."""
    try:
        jira_ops.track(run, key)
        st = jira_ops._state(run)
        steps = st.setdefault("publish", {}).setdefault(fp, {}).setdefault(repo, [])
        out = []
        for name, label, act in (("review", "moved to review", lambda: jira_ops.move(run, key, "review", runner=runner)),
                                 ("comment", "MR link added",
                                  lambda: jira_ops.comment(run, key, f"MR: {mr_url}", runner=runner))):
            if name in steps:
                out.append(f"{label} (already done)")
                continue
            act()
            steps.append(name)
            jira_ops._save_state(run, st)
            out.append(label)
        return out
    except (JiraOpsError, ValueError, OSError) as e:
        return [f"failed: {work._safe(str(e))[:300]}"]


def push(run: Path, approve: str, gitlab=None, jira_runner=None) -> dict:
    run = Path(run)
    p = json.loads((run / "publish-plan.json").read_text(encoding="utf-8"))
    if fingerprint(p) != p.get("fingerprint") or approve != p.get("fingerprint"):
        raise PublishError("the plan changed since the user approved it (fingerprint mismatch); show the user the new plan")
    req_file = run / "publish-request.json"
    req = json.loads(req_file.read_text(encoding="utf-8"))["items"] if req_file.exists() else []
    for item in p["items"]:
        mine = [r for r in req if r["repo"] == item["repo"] and r["branch"] == item["branch"]]
        if not mine:
            raise PublishError(f"the {item['repo']} item is not in the run's publish request; run publish.py plan again")
        _verify(item, mine[0])
    gl = gitlab or GitLab()
    out = []
    for item in p["items"]:
        r = {"repo": item["repo"], "remote_branch": item["remote_branch"], "pushed": None, "mr": None,
             "ref": None, "jira": []}
        try:
            r["pushed"] = push_ref(item["repo"], item["push_url"], item["head"], item["remote_branch"],
                                   _expect_remote(item))
            mr = _open_mr(gl, item)
            r["mr"], r["ref"] = mr["web_url"], mr_registry.ref(item["repo"], mr["iid"])
            try:
                mr_registry.get(r["ref"])
            except KeyError:
                mr_registry.add({"repo": item["repo"], "project": item["project"], "iid": mr["iid"],
                                 "url": mr["web_url"], "source_branch": item["remote_branch"],
                                 "target_branch": item["target"], "work_branch": item["branch"],
                                 "run": str(run), "jira": item["jira"]})
            if item["jira"]:
                r["jira"] = _jira(run, item["jira"], mr["web_url"], jira_runner, p["fingerprint"], item["repo"])
        except (PublishError, GitLabError, work.WorkError, ValueError, OSError, KeyError) as e:
            r["error"] = work._safe(str(e))
        out.append(r)
    res = {"fingerprint": p["fingerprint"], "items": out}
    if (run / "run.json").exists():
        prev = run_record._load(run).get("publish")
        run_record.set_(run, "publish", (prev if isinstance(prev, list) else [prev] if prev else []) + [res])
    return res


def update_plan(ref: str) -> dict:
    e = mr_registry.get(ref)
    if e.get("state") != "opened":
        raise PublishError(f"{ref} is not open ({e.get('state')})")
    if e["repo"] not in work.WRITABLE:
        raise PublishError(f"{e['repo']} is not a repo quorra may change (allowed: {', '.join(work.WRITABLE)})")
    if not REMOTE_BRANCH.match(e["source_branch"]):
        raise PublishError(f"remote branch {e['source_branch']!r} must start with feat/ or fix/")
    repo, clone = e["repo"], work.clone_path(e["repo"])
    url = push_url(repo)
    remote = remote_head(url, e["source_branch"])
    if not remote:
        raise PublishError(f"{e['source_branch']} no longer exists on GitLab")
    head = work._git(clone, "rev-parse", f"refs/heads/{e['work_branch']}")
    if work._run(clone, "git", "cat-file", "-e", f"{remote}^{{commit}}").returncode != 0 or \
            work._run(clone, "git", "merge-base", "--is-ancestor", remote, head).returncode != 0:
        raise PublishError(f"the remote branch has commits quorra does not have; someone else pushed to {ref}")
    log = work._git(clone, "log", "--reverse", "--format=%H%x09%s", f"{remote}..{head}")
    commits = [dict(zip(("sha", "subject"), line.split("\t", 1))) for line in log.splitlines() if line]
    if not commits:
        raise PublishError(f"nothing new to push to {ref}")
    check_gate(repo, e["work_branch"], head)
    up = {"ref": ref, "remote_branch": e["source_branch"], "remote_head": remote, "head": head, "commits": commits}
    up["fingerprint"] = fingerprint(up)
    return up


def update(ref: str, approve: str) -> dict:
    up = update_plan(ref)
    if up["fingerprint"] != approve:
        raise PublishError("the commits changed since the user approved them (fingerprint mismatch); show the user the new list")
    e = mr_registry.get(ref)
    _scan_outgoing(e["repo"], up["remote_head"], up["head"])
    pushed = push_ref(e["repo"], push_url(e["repo"]), up["head"], up["remote_branch"], expect_remote=up["remote_head"])
    return {"ref": ref, "pushed": pushed, "head": up["head"]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="publish.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("plan").add_argument("run")
    pu = sub.add_parser("push")
    pu.add_argument("run")
    pu.add_argument("--approve", required=True)
    u = sub.add_parser("update")
    u.add_argument("ref")
    g = u.add_mutually_exclusive_group(required=True)
    g.add_argument("--plan", action="store_true")
    g.add_argument("--approve")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "plan":
            res = plan(Path(a.run))
        elif a.cmd == "push":
            res = push(Path(a.run), a.approve)
        else:
            res = update_plan(a.ref) if a.plan else update(a.ref, a.approve)
    except (PublishError, GitLabError, JiraOpsError, work.WorkError, KeyError, OSError, ValueError,
            subprocess.TimeoutExpired) as e:
        msg = e.args[0] if isinstance(e, KeyError) and e.args else str(e)
        print(json.dumps({"error": work._safe(str(msg))}), file=sys.stderr)
        return 2
    print(json.dumps(res, indent=1, ensure_ascii=False))
    return 1 if a.cmd == "push" and any("error" in i for i in res["items"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
