import json

import prep_repo
from gitfix import make_repo


def test_prep_collects_tooling_guides_and_areas(tmp_path, monkeypatch):
    repo, sha = make_repo(tmp_path, {"src/AService.php": "<?php class AService {}", ".gitlab-ci.yml": "stages: [test]",
                                     ".danger": "rules", ".cursor/rules/php.mdc": "use DTOs", "ai_guide_x.md": "guide",
                                     "composer.json": "{}"})
    cfg = tmp_path / "repos.json"
    cfg.write_text(json.dumps({"x": {"path": str(repo), "gitlab": "g/x", "ref": "master", "lang": "php"}}))
    monkeypatch.setenv("QUORRA_REPOS", str(cfg))
    monkeypatch.setenv("QUORRA_INDEX_DIR", str(tmp_path / "idx"))
    m = prep_repo.prep("x", tmp_path / "run", fetch=False)
    out = tmp_path / "run" / "x"
    assert m["sha"] == sha
    assert sorted(m["tooling"]) == [".danger", ".gitlab-ci.yml", "composer.json"]
    assert sorted(m["guides"]) == [".cursor/rules/php.mdc", "ai_guide_x.md"]
    assert (out / "tooling" / ".gitlab-ci.yml").read_text() == "stages: [test]"
    assert (out / "guides" / ".cursor/rules/php.mdc").read_text() == "use DTOs"
    assert "src/AService.php" in (out / "areas" / "layout.txt").read_text()
    assert json.loads((out / "prep.json").read_text())["repo"] == "x"


def test_copy_skips_escaping_paths(tmp_path, monkeypatch):
    import gitsrc
    monkeypatch.setattr(gitsrc, "read_files", lambda r, s, p: iter([("../evil.txt", b"x"), ("ok.txt", b"y")]))
    dest = tmp_path / "d"
    skipped = prep_repo._copy(tmp_path, "sha", ["../evil.txt", "ok.txt"], dest)
    assert skipped == ["../evil.txt"] and (dest / "ok.txt").exists() and not (tmp_path / "evil.txt").exists()


def test_minor_prep_records_the_research_commit_committer_date(tmp_path, monkeypatch):
    import subprocess
    repo, sha = make_repo(tmp_path, {"src/AService.php": "<?php class AService {}"})
    cfg = tmp_path / "repos.json"
    cfg.write_text(json.dumps({"x": {"path": str(repo), "gitlab": "g/x", "ref": "master", "lang": "php"}}))
    monkeypatch.setenv("QUORRA_REPOS", str(cfg))
    monkeypatch.setenv("QUORRA_INDEX_DIR", str(tmp_path / "idx"))
    m = prep_repo.prep("x", tmp_path / "run", fetch=False)
    want = subprocess.run(["git", "-C", str(repo), "log", "-1", "--format=%cI", sha], capture_output=True, text=True).stdout.strip()
    assert want and m["sha_date"] == want
    assert json.loads((tmp_path / "run" / "x" / "prep.json").read_text())["sha_date"] == want
