import json
import subprocess
import sys

import pytest

from gitfix import _git, commit
import mr_registry
import publish
import work

PY = sys.executable
CHECKS = {"profiles": {"ts": {"checks": [
    {"name": "tsc", "cmd": [PY, "-c", "print(1)"]},
    {"name": "jest", "cmd": [PY, "-c", "print(1)"], "needs": ["db"]}]}},
    "repos": {"web-app": {"profile": "ts"}, "api-app": {"profile": "ts"}}}


def _bare(tmp_path, name):
    b = tmp_path / "remote" / "team" / f"{name}.git"
    b.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "--bare", "-b", "dev", str(b)], check=True)
    return b


@pytest.fixture
def pub(tmp_path, monkeypatch):
    """the user's clone (origin = a local bare repo standing in for GitLab), a prepared work clone with one commit and a
    passing check, a run folder and a publish request."""
    name = "web-app"
    bare = _bare(tmp_path, name)
    ali = tmp_path / "ali" / name
    ali.mkdir(parents=True)
    _git(ali, "init", "-q", "-b", "dev")
    commit(ali, {"src/a.ts": "export const a = 1;\n"}, "init")
    _git(ali, "remote", "add", "origin", str(bare))
    _git(ali, "push", "-q", "origin", "dev")
    repos = {name: {"path": str(ali), "gitlab": f"team/{name}", "ref": "dev", "lang": "ts"}}
    (tmp_path / "repos.json").write_text(json.dumps(repos))
    (tmp_path / "checks.json").write_text(json.dumps(CHECKS))
    for k, v in {"QUORRA_REPOS": "repos.json", "QUORRA_WORK_DIR": "work", "QUORRA_CHECKS": "checks.json",
                 "QUORRA_INDEX_DIR": "index", "QUORRA_STATE_DIR": "state"}.items():
        monkeypatch.setenv(k, str(tmp_path / v))
    work.prepare(name, "proj-98-tags", install=False)
    clone = work.clone_path(name)
    commit(clone, {"src/tags.ts": "export const tags = [];\n"}, "PROJ-98: tags column")
    work.check(name)
    run = tmp_path / "runs" / "2026-10-03-build-x"
    run.mkdir(parents=True)
    (run / "publish-request.json").write_text(json.dumps({"items": [{
        "repo": name, "branch": "quorra/proj-98-tags", "remote_branch": "feat/proj-98-tags", "target": None,
        "title": "PROJ-98: product tags column", "description": "Adds the tags column.", "jira": "PROJ-98"}]}))
    return {"name": name, "bare": bare, "ali": ali, "clone": clone, "run": run}


def _req(pub, **over):
    p = pub["run"] / "publish-request.json"
    d = json.loads(p.read_text())
    d["items"][0].update(over)
    p.write_text(json.dumps(d))


def test_plan_writes_plan_with_fingerprint_and_checks_table(pub):
    plan = publish.plan(pub["run"])
    it = plan["items"][0]
    assert plan["fingerprint"] == publish.fingerprint(plan) and len(plan["fingerprint"]) == 12
    assert it["target"] == "dev" and it["push_url"] == str(pub["bare"])
    assert [c["subject"] for c in it["commits"]] == ["PROJ-98: tags column"]
    assert it["checks"] == {"tsc": "passed", "jest": "not_run"}
    assert "| tsc | passed |" in it["description"] and "| jest | not run:" in it["description"]
    assert it["jira_actions"] == ["review", "comment-mr-link"]
    assert json.loads((pub["run"] / "publish-plan.json").read_text()) == plan


@pytest.mark.parametrize("over,msg", [
    ({"repo": "framework"}, "not a repo quorra may change"),
    ({"remote_branch": "quorra/x"}, "feat/ or fix/"),
    ({"remote_branch": "feature/x"}, "feat/ or fix/"),
    ({"branch": "quorra/nope"}, "not a branch work.py created"),
    ({"description": "x\nCo-Authored-By: quorra <c@w>"}, "attribution"),
    ({"title": "token=abcdefghijklmnop1234"}, "secret"),
])
def test_plan_refusals(pub, over, msg):
    _req(pub, **over)
    with pytest.raises(publish.PublishError, match=msg):
        publish.plan(pub["run"])


def test_plan_refuses_failed_and_stale_checks(pub):
    commit(pub["clone"], {"src/more.ts": "export const m = 1;\n"}, "more")
    with pytest.raises(publish.PublishError, match="checks are older than the branch head"):
        publish.plan(pub["run"])
    chk = pub["clone"] / ".git" / "quorra-checks.json"
    d = json.loads(chk.read_text())
    d["head"] = _git(pub["clone"], "rev-parse", "HEAD")
    d["results"][0]["status"] = "failed"
    chk.write_text(json.dumps(d))
    with pytest.raises(publish.PublishError, match="failed check"):
        publish.plan(pub["run"])


def test_plan_refuses_push_url_with_credentials_or_wrong_project(pub):
    _git(pub["ali"], "remote", "set-url", "origin", "https://u:pw@gitlab.example.com/team/web-app.git")
    with pytest.raises(publish.PublishError, match="credentials"):
        publish.plan(pub["run"])
    _git(pub["ali"], "remote", "set-url", "origin", "https://gitlab.example.com/someone/other.git")
    with pytest.raises(publish.PublishError, match="does not point at team/web-app"):
        publish.plan(pub["run"])


def test_push_url_accepts_scp_form(pub):
    _git(pub["ali"], "remote", "set-url", "origin", "git@gitlab.example.com:team/web-app.git")
    assert publish.push_url("web-app") == "git@gitlab.example.com:team/web-app.git"


def test_explicit_target_kept(pub):
    _req(pub, target="release/1.2")
    assert publish.plan(pub["run"])["items"][0]["target"] == "release/1.2"


def test_plan_refuses_non_cpp_jira_key(pub):
    _req(pub, jira="ABC-12")
    with pytest.raises(publish.PublishError, match="outside PROJ"):
        publish.plan(pub["run"])


class FakeGitLab:
    def __init__(self, fail_create=False):
        self.posts, self.fail_create, self.open_mrs = [], fail_create, []

    def get_json(self, endpoint, params=None):
        return list(self.open_mrs)

    def post_json(self, endpoint, body):
        if self.fail_create:
            raise publish.GitLabError("GitLab POST failed (500)")
        self.posts.append((endpoint, body))
        mr = {"iid": 77, "web_url": "https://git.example/team/web-app/-/merge_requests/77",
              "source_branch": body["source_branch"], "target_branch": body["target_branch"]}
        self.open_mrs.append(mr)
        return mr


class FakeJira:
    def __init__(self):
        self.calls = []

    def __call__(self, args):
        self.calls.append(args)
        return {}


def _bare_head(pub, branch):
    p = subprocess.run(["git", "-C", str(pub["bare"]), "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
                       capture_output=True, text=True)
    return p.stdout.strip() or None


def test_push_refuses_wrong_fingerprint_and_pushes_nothing(pub):
    publish.plan(pub["run"])
    with pytest.raises(publish.PublishError, match="changed since the user approved"):
        publish.push(pub["run"], "000000000000", gitlab=FakeGitLab(), jira_runner=FakeJira())
    assert _bare_head(pub, "feat/proj-98-tags") is None


def test_push_pushes_opens_mr_registers_and_moves_jira(pub):
    plan = publish.plan(pub["run"])
    gl, jr = FakeGitLab(), FakeJira()
    res = publish.push(pub["run"], plan["fingerprint"], gitlab=gl, jira_runner=jr)
    it = res["items"][0]
    assert it["pushed"] == "pushed" and _bare_head(pub, "feat/proj-98-tags") == plan["items"][0]["head"]
    assert gl.posts[0][1]["source_branch"] == "feat/proj-98-tags" and gl.posts[0][1]["target_branch"] == "dev"
    assert "remove_source_branch" in gl.posts[0][1] and gl.posts[0][1]["remove_source_branch"] is False
    assert it["ref"] == "web-app!77" and mr_registry.get("web-app!77")["jira"] == "PROJ-98"
    assert [c[0] for c in jr.calls] == ["transition", "comment"]
    # the work clone stays push-locked
    assert _git(pub["clone"], "config", "--get", "remote.origin.pushurl").startswith("no-push://")


def test_push_refuses_when_branch_moved(pub):
    plan = publish.plan(pub["run"])
    commit(pub["clone"], {"src/late.ts": "x\n"}, "late")
    with pytest.raises(publish.PublishError, match="branch moved"):
        publish.push(pub["run"], plan["fingerprint"], gitlab=FakeGitLab(), jira_runner=FakeJira())


def test_push_refuses_diverged_remote_branch(pub, tmp_path):
    plan = publish.plan(pub["run"])
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(pub["bare"]), str(other)], check=True)
    commit(other, {"x.ts": "1\n"}, "someone else")
    _git(other, "push", "-q", "origin", "HEAD:refs/heads/feat/proj-98-tags")
    res = publish.push(pub["run"], plan["fingerprint"], gitlab=FakeGitLab(), jira_runner=FakeJira())
    assert "other history" in res["items"][0]["error"]
    assert _bare_head(pub, "feat/proj-98-tags") != plan["items"][0]["head"]


def test_push_refuses_secret_in_outgoing_diff(pub):
    commit(pub["clone"], {"src/cfg.ts": "const password = 'hunter2hunter2';\n"}, "cfg")
    work.check("web-app")
    plan = publish.plan(pub["run"])
    with pytest.raises(publish.PublishError, match="possible secret"):
        publish.push(pub["run"], plan["fingerprint"], gitlab=FakeGitLab(), jira_runner=FakeJira())
    assert _bare_head(pub, "feat/proj-98-tags") is None


def test_mr_failure_keeps_push_and_retry_only_opens_mr(pub):
    plan = publish.plan(pub["run"])
    res = publish.push(pub["run"], plan["fingerprint"], gitlab=FakeGitLab(fail_create=True), jira_runner=FakeJira())
    assert res["items"][0]["pushed"] == "pushed" and "500" in res["items"][0]["error"]
    gl = FakeGitLab()
    res2 = publish.push(pub["run"], plan["fingerprint"], gitlab=gl, jira_runner=FakeJira())
    assert res2["items"][0]["pushed"] == "already" and len(gl.posts) == 1


def test_jira_failure_never_undoes_push(pub):
    plan = publish.plan(pub["run"])

    def boom(args):
        raise publish.JiraOpsError("Transition 'In Review' not found")
    res = publish.push(pub["run"], plan["fingerprint"], gitlab=FakeGitLab(), jira_runner=boom)
    assert res["items"][0]["mr"] and res["items"][0]["jira"][0].startswith("failed:")


def test_non_json_gitlab_body_is_recorded_as_item_error(pub):
    plan = publish.plan(pub["run"])

    class Bad(FakeGitLab):
        def get_json(self, endpoint, params=None):
            raise ValueError("Expecting value: line 1 column 1 (char 0)")
    res = publish.push(pub["run"], plan["fingerprint"], gitlab=Bad(), jira_runner=FakeJira())
    assert res["items"][0]["pushed"] == "pushed" and "Expecting value" in res["items"][0]["error"]


def test_no_force_anywhere_in_publish():
    src = open(publish.__file__).read()
    assert "--force" not in src and '"+' not in src and "force-with-lease" not in src


def test_secret_added_then_removed_in_later_commit_is_refused(pub):
    commit(pub["clone"], {"src/cfg.ts": "const password = 'hunter2hunter2';\n"}, "cfg")
    commit(pub["clone"], {"src/cfg.ts": "const ok = 1;\n"}, "remove it")
    work.check("web-app")
    plan = publish.plan(pub["run"])
    with pytest.raises(publish.PublishError, match="possible secret"):
        publish.push(pub["run"], plan["fingerprint"], gitlab=FakeGitLab(), jira_runner=FakeJira())
    assert _bare_head(pub, "feat/proj-98-tags") is None


def test_secret_in_commit_message_is_refused(pub):
    commit(pub["clone"], {"src/m.ts": "1\n"}, "note password = 'hunter2hunter2'")
    work.check("web-app")
    plan = publish.plan(pub["run"])
    with pytest.raises(publish.PublishError, match="possible secret"):
        publish.push(pub["run"], plan["fingerprint"], gitlab=FakeGitLab(), jira_runner=FakeJira())


def test_timeout_becomes_item_error_and_result_is_returned(pub, monkeypatch):
    plan = publish.plan(pub["run"])
    real = publish.work._run

    def slow(cwd, *cmd, **kw):
        if "ls-remote" in cmd:
            raise subprocess.TimeoutExpired(cmd, 120)
        return real(cwd, *cmd, **kw)
    monkeypatch.setattr(publish.work, "_run", slow)
    res = publish.push(pub["run"], plan["fingerprint"], gitlab=FakeGitLab(), jira_runner=FakeJira())
    assert "timed out after 120s" in res["items"][0]["error"] and res["items"][0]["mr"] is None


def test_existing_mr_with_other_target_is_refused(pub):
    plan = publish.plan(pub["run"])
    gl = FakeGitLab()
    gl.open_mrs.append({"iid": 5, "web_url": "https://git.example/x/-/merge_requests/5",
                        "source_branch": "feat/proj-98-tags", "target_branch": "main"})
    res = publish.push(pub["run"], plan["fingerprint"], gitlab=gl, jira_runner=FakeJira())
    assert "already targets main" in res["items"][0]["error"] and not gl.posts


def test_no_temp_clone_left_after_success_or_failure(pub, tmp_path, monkeypatch):
    import tempfile
    tdir = tmp_path / "tmpdir"
    tdir.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(tdir))
    plan = publish.plan(pub["run"])
    publish.push(pub["run"], plan["fingerprint"], gitlab=FakeGitLab(), jira_runner=FakeJira())
    assert not list(tdir.glob("quorra-publish-*"))
    real = publish.work._run

    def failing(cwd, *cmd, **kw):
        if "push" in cmd:
            return subprocess.CompletedProcess(cmd, 1, "", "rejected")
        return real(cwd, *cmd, **kw)
    _git(pub["bare"], "update-ref", "-d", "refs/heads/feat/proj-98-tags")
    monkeypatch.setattr(publish.work, "_run", failing)
    res = publish.push(pub["run"], plan["fingerprint"], gitlab=FakeGitLab(), jira_runner=FakeJira())
    assert "failed" in res["items"][0]["error"] and not list(tdir.glob("quorra-publish-*"))


def _shipped(pub):
    plan = publish.plan(pub["run"])
    publish.push(pub["run"], plan["fingerprint"], gitlab=FakeGitLab(), jira_runner=FakeJira())
    return "web-app!77"


def test_update_fast_forward_with_fingerprint(pub):
    ref = _shipped(pub)
    with pytest.raises(publish.PublishError, match="nothing new"):
        publish.update_plan(ref)
    commit(pub["clone"], {"src/fix.ts": "export const f = 1;\n"}, "review fix")
    work.check("web-app")
    up = publish.update_plan(ref)
    assert [c["subject"] for c in up["commits"]] == ["review fix"]
    with pytest.raises(publish.PublishError, match="fingerprint"):
        publish.update(ref, "000000000000")
    assert publish.update(ref, up["fingerprint"])["pushed"] == "pushed"
    assert _bare_head(pub, "feat/proj-98-tags") == up["head"]


def test_update_refuses_when_remote_moved(pub, tmp_path):
    ref = _shipped(pub)
    other = tmp_path / "other2"
    subprocess.run(["git", "clone", "-q", "-b", "feat/proj-98-tags", str(pub["bare"]), str(other)], check=True)
    commit(other, {"y.ts": "1\n"}, "reviewer pushed")
    _git(other, "push", "-q", "origin", "HEAD:refs/heads/feat/proj-98-tags")
    commit(pub["clone"], {"src/fix.ts": "1\n"}, "fix")
    work.check("web-app")
    with pytest.raises(publish.PublishError, match="commits quorra does not have"):
        publish.update_plan(ref)


def test_update_unknown_or_closed_mr(pub):
    with pytest.raises(KeyError, match="not a quorra MR"):
        publish.update_plan("web-app!1")
    ref = _shipped(pub)
    mr_registry.update(ref, state="merged")
    with pytest.raises(publish.PublishError, match="not open"):
        publish.update_plan(ref)


def test_cli_plan_and_bad_approve(pub, capsys):
    assert publish.main(["plan", str(pub["run"])]) == 0
    fp = json.loads(capsys.readouterr().out)["fingerprint"]
    assert len(fp) == 12
    assert publish.main(["push", str(pub["run"]), "--approve", "000000000000"]) == 2
    assert "fingerprint" in capsys.readouterr().err


# ---- final-review fixes ----

def _commit_msg(clone, rel, msg):
    commit(clone, {rel: "export const z = 1;\n"}, msg)
    work.check("web-app")


def _edit_plan(pub, **over):
    f = pub["run"] / "publish-plan.json"
    plan = json.loads(f.read_text())
    plan["items"][0].update(over)
    plan.pop("fingerprint")
    plan["fingerprint"] = publish.fingerprint(plan)
    f.write_text(json.dumps(plan))
    return plan["fingerprint"]


def test_update_plan_refuses_non_writable_repo_and_bad_remote_branch(pub):
    base = {"project": "team/x", "iid": 9, "url": "u", "target_branch": "dev",
            "work_branch": "quorra/proj-98-tags", "run": str(pub["run"]), "jira": None}
    mr_registry.add({**base, "repo": "framework", "source_branch": "feat/x"})
    with pytest.raises(publish.PublishError, match="not a repo quorra may change"):
        publish.update_plan("framework!9")
    mr_registry.add({**base, "repo": "web-app", "source_branch": "release/x"})
    with pytest.raises(publish.PublishError, match="feat/ or fix/"):
        publish.update_plan("web-app!9")


def test_check_gate_refuses_partial_check_run(pub):
    work.check("web-app", only=["tsc"])
    with pytest.raises(publish.PublishError, match="partial check run"):
        publish.plan(pub["run"])
    work.check("web-app")
    assert publish.plan(pub["run"])["items"]


def test_check_gate_refuses_checks_run_on_uncommitted_changes(pub):
    (pub["clone"] / "src" / "wip.ts").write_text("export const wip = 1;\n")
    res = work.check("web-app")
    assert res["dirty"] is True and res["only"] is None
    with pytest.raises(publish.PublishError, match="uncommitted changes"):
        publish.plan(pub["run"])
    (pub["clone"] / "src" / "wip.ts").unlink()
    work.check("web-app")
    assert publish.plan(pub["run"])["items"]


def test_check_gate_refuses_old_result_without_the_new_keys(pub):
    chk = pub["clone"] / ".git" / "quorra-checks.json"
    d = json.loads(chk.read_text())
    d.pop("dirty")
    chk.write_text(json.dumps(d))
    with pytest.raises(publish.PublishError, match="uncommitted changes"):
        publish.plan(pub["run"])


def test_push_refuses_hand_edited_plan_with_recomputed_fingerprint(pub):
    publish.plan(pub["run"])
    fp = _edit_plan(pub, remote_branch="release/x")
    with pytest.raises(publish.PublishError, match="feat/ or fix/"):
        publish.push(pub["run"], fp, gitlab=FakeGitLab(), jira_runner=FakeJira())
    assert _bare_head(pub, "release/x") is None


def test_push_refuses_hand_edited_project_title_and_jira(pub):
    publish.plan(pub["run"])
    fp = _edit_plan(pub, project="team/other")
    with pytest.raises(publish.PublishError, match="project"):
        publish.push(pub["run"], fp, gitlab=FakeGitLab(), jira_runner=FakeJira())
    publish.plan(pub["run"])
    fp = _edit_plan(pub, title="x\nCo-Authored-By: quorra <c@w>")
    with pytest.raises(publish.PublishError, match="attribution"):
        publish.push(pub["run"], fp, gitlab=FakeGitLab(), jira_runner=FakeJira())
    publish.plan(pub["run"])
    fp = _edit_plan(pub, jira="MP-5")
    with pytest.raises(publish.PublishError, match="outside PROJ"):
        publish.push(pub["run"], fp, gitlab=FakeGitLab(), jira_runner=FakeJira())


def test_push_refuses_commit_message_with_attribution_line(pub):
    _commit_msg(pub["clone"], "src/z.ts", "feat: z\n\nCo-Authored-By: quorra <noreply@example.com>")
    plan = publish.plan(pub["run"])
    with pytest.raises(publish.PublishError, match=r"tool attribution line in commit [0-9a-f]{7}"):
        publish.push(pub["run"], plan["fingerprint"], gitlab=FakeGitLab(), jira_runner=FakeJira())
    assert _bare_head(pub, "feat/proj-98-tags") is None


def test_push_refuses_merge_commits(pub):
    clone = pub["clone"]
    _git(clone, "checkout", "-q", "-b", "side")
    commit(clone, {"src/side.ts": "1\n"}, "side")
    _git(clone, "checkout", "-q", "quorra/proj-98-tags")
    commit(clone, {"src/main2.ts": "1\n"}, "main2")
    _git(clone, "-c", "user.name=t", "-c", "user.email=t@t", "merge", "--no-ff", "-q", "-m", "merge side", "side")
    work.check("web-app")
    plan = publish.plan(pub["run"])
    with pytest.raises(publish.PublishError, match="merge commit"):
        publish.push(pub["run"], plan["fingerprint"], gitlab=FakeGitLab(), jira_runner=FakeJira())
    assert _bare_head(pub, "feat/proj-98-tags") is None


def test_push_after_mr_failure_and_new_commit_fast_forwards_and_opens_mr(pub):
    plan = publish.plan(pub["run"])
    res = publish.push(pub["run"], plan["fingerprint"], gitlab=FakeGitLab(fail_create=True), jira_runner=FakeJira())
    assert res["items"][0]["pushed"] == "pushed" and "error" in res["items"][0]
    commit(pub["clone"], {"src/more.ts": "export const m = 1;\n"}, "more")
    work.check("web-app")
    plan2 = publish.plan(pub["run"])
    gl = FakeGitLab()
    res2 = publish.push(pub["run"], plan2["fingerprint"], gitlab=gl, jira_runner=FakeJira())
    it = res2["items"][0]
    assert it["pushed"] == "pushed" and "error" not in it and len(gl.posts) == 1
    assert _bare_head(pub, "feat/proj-98-tags") == plan2["items"][0]["head"]


def test_rerun_push_makes_no_second_jira_calls(pub):
    plan = publish.plan(pub["run"])
    jr = FakeJira()
    publish.push(pub["run"], plan["fingerprint"], gitlab=FakeGitLab(), jira_runner=jr)
    assert [c[0] for c in jr.calls] == ["transition", "comment"]
    jr2 = FakeJira()
    res = publish.push(pub["run"], plan["fingerprint"], gitlab=FakeGitLab(), jira_runner=jr2)
    assert jr2.calls == [] and all("already done" in x for x in res["items"][0]["jira"])


def test_jira_failure_on_comment_retries_only_the_comment(pub):
    plan = publish.plan(pub["run"])

    class Half(FakeJira):
        def __call__(self, args):
            if args[0] == "comment":
                raise publish.JiraOpsError("comment refused")
            return super().__call__(args)
    first = Half()
    publish.push(pub["run"], plan["fingerprint"], gitlab=FakeGitLab(), jira_runner=first)
    second = FakeJira()
    publish.push(pub["run"], plan["fingerprint"], gitlab=FakeGitLab(), jira_runner=second)
    assert [c[0] for c in second.calls] == ["comment"]


def test_push_url_refuses_other_hosts(pub):
    _git(pub["ali"], "remote", "set-url", "origin", "https://other-host/team/web-app.git")
    with pytest.raises(publish.PublishError, match="not on your configured GitLab"):
        publish.push_url("web-app")
    _git(pub["ali"], "remote", "set-url", "origin", "git@other-host.com:team/web-app.git")
    with pytest.raises(publish.PublishError, match="not on your configured GitLab"):
        publish.push_url("web-app")


def test_push_results_are_appended_to_the_run_record(pub):
    import run_record
    d = run_record.new("ship-x", "ship", "ship it", ["web-app"], runs_root=pub["run"].parent)
    pub["run"] = d
    (d / "publish-request.json").write_text(json.dumps({"items": [{
        "repo": "web-app", "branch": "quorra/proj-98-tags", "remote_branch": "feat/proj-98-tags",
        "target": None, "title": "PROJ-98: product tags column", "description": "Adds the tags column.",
        "jira": "PROJ-98"}]}))
    plan = publish.plan(d)
    publish.push(d, plan["fingerprint"], gitlab=FakeGitLab(), jira_runner=FakeJira())
    publish.push(d, plan["fingerprint"], gitlab=FakeGitLab(), jira_runner=FakeJira())
    rec = json.loads((d / "run.json").read_text())["publish"]
    assert isinstance(rec, list) and len(rec) == 2


def test_push_refuses_hand_edited_base_and_target(pub):
    plan = publish.plan(pub["run"])
    fp = _edit_plan(pub, base=plan["items"][0]["head"])
    with pytest.raises(publish.PublishError, match="base commit"):
        publish.push(pub["run"], fp, gitlab=FakeGitLab(), jira_runner=FakeJira())
    publish.plan(pub["run"])
    fp = _edit_plan(pub, target="release/x")
    with pytest.raises(publish.PublishError, match="not the requested dev"):
        publish.push(pub["run"], fp, gitlab=FakeGitLab(), jira_runner=FakeJira())
    assert _bare_head(pub, "feat/proj-98-tags") is None


def test_old_dict_publish_value_is_kept_as_first_list_entry(pub):
    import run_record
    d = run_record.new("ship-y", "ship", "x", ["web-app"], runs_root=pub["run"].parent)
    run_record.set_(d, "publish", {"fingerprint": "old", "items": []})
    (d / "publish-request.json").write_text((pub["run"] / "publish-request.json").read_text())
    plan = publish.plan(d)
    publish.push(d, plan["fingerprint"], gitlab=FakeGitLab(), jira_runner=FakeJira())
    rec = json.loads((d / "run.json").read_text())["publish"]
    assert [r["fingerprint"] for r in rec] == ["old", plan["fingerprint"]]
