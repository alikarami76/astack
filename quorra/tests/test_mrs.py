import json
from pathlib import Path

import pytest

import mr_registry
import mrs

FIX = Path(__file__).parent / "fixtures" / "gitlab"


class FakeGL:
    def __init__(self, mr_state="opened", pipeline="failed"):
        self.posts = []
        self.mr = {"iid": 1042, "state": mr_state, "detailed_merge_status": "mergeable", "has_conflicts": False,
                   "source_branch": "feat/proj-98", "target_branch": "dev",
                   "web_url": "https://git.example/team/web-app/-/merge_requests/1042",
                   "head_pipeline": {"id": 9, "status": pipeline, "web_url": "https://git.example/p/9"}}

    def get_json(self, endpoint, params=None):
        if endpoint.endswith("/merge_requests/1042"):
            return self.mr
        if endpoint.endswith("/pipelines/9/jobs"):
            return json.loads((FIX / "jobs_failed.json").read_text())
        if endpoint.endswith("/discussions"):
            return json.loads((FIX / "discussions.json").read_text()) if (params or {}).get("page", 1) == 1 else []
        raise AssertionError(endpoint)

    def get_text(self, endpoint):
        return (FIX / "trace.txt").read_text()

    def post_json(self, endpoint, body):
        self.posts.append((endpoint, body))
        return {"id": 555}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("QUORRA_STATE_DIR", str(tmp_path / "state"))
    repos = {"web-app": {"path": str(tmp_path), "gitlab": "team/web-app", "ref": "dev", "lang": "ts"}}
    (tmp_path / "repos.json").write_text(json.dumps(repos))
    monkeypatch.setenv("QUORRA_REPOS", str(tmp_path / "repos.json"))
    monkeypatch.setenv("QUORRA_WORK_DIR", str(tmp_path / "work"))
    clone = tmp_path / "work" / "web-app" / ".git"
    clone.mkdir(parents=True)
    (clone / "quorra.json").write_text(json.dumps({"branches": {"quorra/proj-98": {"base": "abc"}}}))
    run = tmp_path / "run"
    run.mkdir()
    return run


URL = "https://git.example/team/web-app/-/merge_requests/1042"


def test_register_reads_branches(env):
    ref = mrs.register(URL, env, "PROJ-98", "quorra/proj-98", gitlab=FakeGL())
    e = mr_registry.get(ref)
    assert ref == "web-app!1042" and e["source_branch"] == "feat/proj-98" and e["work_branch"] == "quorra/proj-98"


def test_register_refusals(env):
    with pytest.raises(ValueError, match="quorra/"):
        mrs.register(URL, env, None, "main", gitlab=FakeGL())
    with pytest.raises(Exception, match="PROJ"):
        mrs.register(URL, env, "MP-1", "quorra/x", gitlab=FakeGL())


def test_status_reports_failed_jobs_new_and_unresolved_threads(env):
    ref = mrs.register(URL, env, None, "quorra/proj-98", gitlab=FakeGL())
    res = mrs.status(gitlab=FakeGL(), out_dir=env)
    m = res["mrs"][0]
    assert m["pipeline"]["status"] == "failed" and m["failed_jobs"][0]["name"] == "jest"
    assert any("Expected" in line for line in m["failed_jobs"][0]["log"])
    assert all("\x1b" not in line and "section_start" not in line for line in m["failed_jobs"][0]["log"])
    threads = m["threads"]
    assert {t["discussion_id"] for t in threads} == {"2776546b9c4bf083d08741f9e478ff726ac1edae", "731909068924cd61f8d6351b06705e4c4df4761b", "93bf2a32ab2535ff51e49a6f05ca3bac5725dceb"}  # first check: all human threads are new; system and bot dropped
    assert "hunter2" not in json.dumps(res)                                      # bodies scrubbed
    assert mr_registry.get(ref)["last_note_id"] == 303
    assert list((env / "mrs").glob("*.json"))
    again = mrs.status(gitlab=FakeGL(), out_dir=env)["mrs"][0]["threads"]
    assert [t["discussion_id"] for t in again] == ["93bf2a32ab2535ff51e49a6f05ca3bac5725dceb"] and not again[0]["new"]


def test_status_marks_merged(env):
    ref = mrs.register(URL, env, None, "quorra/proj-98", gitlab=FakeGL())
    mrs.status(gitlab=FakeGL(mr_state="merged"), out_dir=env)
    assert mr_registry.get(ref)["state"] == "merged" and mr_registry.open_refs() == []


def test_log_tail_limits_and_scrubs():
    lines = mrs.log_tail("\x1b[31mERR\x1b[0m\n" + "\n".join(f"l{i}" for i in range(100)) + "\nAPI_TOKEN=abcdefghijklmnopqrstuvwx\n", n=5)
    assert len(lines) == 5 and "abcdefghijklmnopqrstuvwx" not in "".join(lines)


def test_reply_needs_fingerprint_and_posts_into_thread(env, tmp_path):
    mrs.register(URL, env, None, "quorra/proj-98", gitlab=FakeGL())
    batch = tmp_path / "replies.json"
    batch.write_text(json.dumps({"replies": [{"ref": "web-app!1042", "discussion_id": "731909068924cd61f8d6351b06705e4c4df4761b",
                                              "body": "Fixed in the latest commit."}]}))
    p = mrs.reply_plan(batch)
    gl = FakeGL()
    with pytest.raises(ValueError, match="fingerprint"):
        mrs.reply(batch, "000000000000", gitlab=gl)
    assert gl.posts == []
    out = mrs.reply(batch, p["fingerprint"], gitlab=gl)
    assert gl.posts[0][0].endswith("/merge_requests/1042/discussions/731909068924cd61f8d6351b06705e4c4df4761b/notes") and out[0]["note_id"] == 555


def test_reply_refuses_attribution(env, tmp_path):
    mrs.register(URL, env, None, "quorra/proj-98", gitlab=FakeGL())
    batch = tmp_path / "r.json"
    batch.write_text(json.dumps({"replies": [{"ref": "web-app!1042", "discussion_id": "731909068924cd61f8d6351b06705e4c4df4761b",
                                              "body": "ok\nBuilt with [SomeTool](https://tool.example.com)"}]}))
    with pytest.raises(Exception, match="attribution"):
        mrs.reply_plan(batch)


def test_no_resolve_edit_or_delete_calls():
    src = open(mrs.__file__).read()
    assert "resolved=" not in src and "PUT" not in src and "DELETE" not in src


D_NEW = "731909068924cd61f8d6351b06705e4c4df4761b"
D_OLD = "2776546b9c4bf083d08741f9e478ff726ac1edae"


def _batch(tmp_path, *replies, name="replies.json"):
    b = tmp_path / name
    b.write_text(json.dumps({"replies": [{"ref": "web-app!1042", "discussion_id": d, "body": body}
                                         for d, body in replies]}))
    return b


def test_register_refuses_bad_source_branch_fork_and_unknown_work_branch(env):
    gl = FakeGL()
    gl.mr["source_branch"] = "release/x"
    with pytest.raises(ValueError, match="feat/ or fix/"):
        mrs.register(URL, env, None, "quorra/proj-98", gitlab=gl)
    gl = FakeGL()
    gl.mr.update(source_project_id=1, target_project_id=2)
    with pytest.raises(ValueError, match="fork"):
        mrs.register(URL, env, None, "quorra/proj-98", gitlab=gl)
    with pytest.raises(ValueError, match="not a branch work.py created"):
        mrs.register(URL, env, None, "quorra/other", gitlab=FakeGL())
    assert mr_registry.open_refs() == []


def test_register_refuses_when_work_clone_missing(env, tmp_path):
    import shutil
    shutil.rmtree(tmp_path / "work")
    with pytest.raises(Exception, match="not a prepared work clone"):
        mrs.register(URL, env, None, "quorra/proj-98", gitlab=FakeGL())


@pytest.mark.parametrize("bad", ["x/../../1", "d-new", "abc", "G" * 40, "a" * 65, "../1234abcd"])
def test_reply_plan_refuses_non_hex_discussion_id(env, tmp_path, bad):
    mrs.register(URL, env, None, "quorra/proj-98", gitlab=FakeGL())
    with pytest.raises(ValueError, match="discussion id"):
        mrs.reply_plan(_batch(tmp_path, (bad, "ok")))


def test_status_one_failing_mr_does_not_block_others(env):
    ref1 = mrs.register(URL, env, None, "quorra/proj-98", gitlab=FakeGL())
    mr_registry.add({"repo": "web-app", "project": "team/web-app", "iid": 6000,
                     "url": "u2", "source_branch": "feat/b", "target_branch": "dev", "work_branch": "quorra/proj-98",
                     "run": str(env), "jira": None})

    class Failing(FakeGL):
        def get_json(self, endpoint, params=None):
            if "/merge_requests/6000" in endpoint:
                raise mrs.GitLabError("GitLab GET failed (500)")
            return super().get_json(endpoint, params)
    res = mrs.status(gitlab=Failing(), out_dir=env)
    saved = json.loads(Path(res["file"]).read_text())
    assert saved["mrs"][0]["ref"] == ref1 and "error" not in saved["mrs"][0]
    assert saved["mrs"][1]["ref"] == "web-app!6000" and "500" in saved["mrs"][1]["error"]
    assert mr_registry.get(ref1)["last_note_id"] == 303
    assert mr_registry.get("web-app!6000")["last_note_id"] == 0


def test_status_registry_untouched_when_result_file_cannot_be_written(env, tmp_path):
    ref = mrs.register(URL, env, None, "quorra/proj-98", gitlab=FakeGL())
    blocker = tmp_path / "blocked"
    blocker.write_text("a file, not a folder")
    with pytest.raises(OSError):
        mrs.status(gitlab=FakeGL(), out_dir=blocker)
    assert mr_registry.get(ref)["last_note_id"] == 0


def test_status_without_mrs_writes_under_state_dir(env, tmp_path):
    res = mrs.status(gitlab=FakeGL())
    assert Path(res["file"]).parent == tmp_path / "state" / "mrs"


def test_reply_url_carries_the_hex_discussion_id(env, tmp_path):
    mrs.register(URL, env, None, "quorra/proj-98", gitlab=FakeGL())
    b = _batch(tmp_path, (D_NEW, "ok"))
    gl = FakeGL()
    mrs.reply(b, mrs.reply_plan(b)["fingerprint"], gitlab=gl)
    assert gl.posts[0][0].endswith(f"/discussions/{D_NEW}/notes")


def test_reply_rerun_posts_only_missing_replies(env, tmp_path):
    mrs.register(URL, env, None, "quorra/proj-98", gitlab=FakeGL())
    b = _batch(tmp_path, (D_NEW, "first"), (D_OLD, "second"))
    fp = mrs.reply_plan(b)["fingerprint"]

    class Flaky(FakeGL):
        def post_json(self, endpoint, body):
            if body["body"] == "second":
                raise mrs.GitLabError("GitLab POST failed (500)")
            return super().post_json(endpoint, body)
    first = Flaky()
    with pytest.raises(mrs.GitLabError):
        mrs.reply(b, fp, gitlab=first)
    assert [x[1]["body"] for x in first.posts] == ["first"]
    gl = FakeGL()
    out = mrs.reply(b, fp, gitlab=gl)
    assert [x[1]["body"] for x in gl.posts] == ["second"] and len(out) == 2
    again = FakeGL()
    mrs.reply(b, fp, gitlab=again)
    assert again.posts == []
    assert json.loads((tmp_path / "replies-posted.json").read_text())[fp].keys() == {"0", "1"}


def test_cli_status_prints_error_items_and_exits_zero(env, tmp_path, monkeypatch, capsys):
    mrs.register(URL, env, None, "quorra/proj-98", gitlab=FakeGL())
    mr_registry.add({"repo": "web-app", "project": "team/web-app", "iid": 6000,
                     "url": "u2", "source_branch": "feat/b", "target_branch": "dev", "work_branch": "quorra/proj-98",
                     "run": str(env), "jira": None})

    class Failing(FakeGL):
        def get_json(self, endpoint, params=None):
            if "/merge_requests/6000" in endpoint:
                raise mrs.GitLabError("GitLab GET failed (500)")
            return super().get_json(endpoint, params)
    monkeypatch.setattr(mrs, "GitLab", Failing)
    assert mrs.main(["status", "--out", str(tmp_path / "out")]) == 0
    items = json.loads(capsys.readouterr().out)["mrs"]
    assert items[0]["ref"] == "web-app!1042" and items[0]["state"] == "opened"
    assert items[1]["ref"] == "web-app!6000" and "500" in items[1]["error"]
