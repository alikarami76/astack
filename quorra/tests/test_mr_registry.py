import json

import pytest

import mr_registry as r


@pytest.fixture
def reg(tmp_path, monkeypatch):
    monkeypatch.setenv("QUORRA_STATE_DIR", str(tmp_path / "state"))
    repos = {"web-app": {"path": str(tmp_path), "gitlab": "team/web-app", "ref": "dev", "lang": "ts"}}
    (tmp_path / "repos.json").write_text(json.dumps(repos))
    monkeypatch.setenv("QUORRA_REPOS", str(tmp_path / "repos.json"))
    return tmp_path


def test_parse_mr_url():
    assert r.parse_mr_url("https://gitlab.example.com/team/web-app/-/merge_requests/5206") == \
        ("team/web-app", 5206)
    with pytest.raises(ValueError):
        r.parse_mr_url("https://gitlab.example.com/team/x/-/issues/3")


def test_repo_for_project(reg):
    assert r.repo_for_project("team/web-app") == "web-app"
    with pytest.raises(KeyError):
        r.repo_for_project("team/unknown")


def test_add_get_update_open_refs_roundtrip(reg):
    ref = r.add({"repo": "web-app", "project": "team/web-app", "iid": 5206,
                 "url": "u", "source_branch": "feat/x", "target_branch": "dev", "work_branch": "quorra/x",
                 "run": "runs/r", "jira": "PROJ-98"})
    assert ref == "web-app!5206"
    assert r.get(ref)["state"] == "opened" and r.get(ref)["last_note_id"] == 0
    r.update(ref, state="merged")
    assert r.open_refs() == []
    assert (reg / "state" / "mrs.json").exists()


def test_add_refuses_duplicate_and_get_unknown(reg):
    e = {"repo": "web-app", "project": "p", "iid": 1, "url": "u", "source_branch": "feat/a",
         "target_branch": "dev", "work_branch": "quorra/a", "run": "r", "jira": None}
    r.add(e)
    with pytest.raises(ValueError, match="already"):
        r.add(e)
    with pytest.raises(KeyError, match="not a quorra MR"):
        r.get("web-app!2")
