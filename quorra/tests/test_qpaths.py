import json

import pytest

import qpaths


def test_load_repos_expands_paths(tmp_path, monkeypatch):
    cfg = tmp_path / "repos.json"
    cfg.write_text(json.dumps({"api-app": {"path": "~/x", "gitlab": "g/x", "ref": "master", "lang": "php"}}))
    monkeypatch.setenv("QUORRA_REPOS", str(cfg))
    repos = qpaths.load_repos()
    assert repos["api-app"]["path"].is_absolute()
    assert repos["api-app"]["name"] == "api-app"


def test_repo_cfg_unknown_lists_known(tmp_path, monkeypatch):
    cfg = tmp_path / "repos.json"
    cfg.write_text(json.dumps({"a": {"path": "/a", "gitlab": "g/a", "ref": "master", "lang": "php"}}))
    monkeypatch.setenv("QUORRA_REPOS", str(cfg))
    with pytest.raises(KeyError, match="a"):
        qpaths.repo_cfg("nope")


def test_index_path_env(tmp_path, monkeypatch):
    monkeypatch.setenv("QUORRA_INDEX_DIR", str(tmp_path))
    assert qpaths.index_path("api-app") == tmp_path / "api-app.sqlite"


def test_real_config_is_valid():
    monkeypatch_free = qpaths.load_repos(qpaths.REPO / "config" / "repos.example.json")
    assert {"api-app", "web-app", "admin", "pricing", "framework", "shared-lib", "core-app", "ledger"} <= set(monkeypatch_free)
    for r in monkeypatch_free.values():
        assert r["lang"] in {"php", "ts", "java"}
