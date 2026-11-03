import json

import fetch_mr_reviews as f
import gitsrc
import prep_repo
from gitfix import commit, make_repo
from qindex import areas, build


def test_recent_order_ranks_newest_first(tmp_path):
    repo, _ = make_repo(tmp_path, {"a.php": "1"})
    commit(repo, {"b.php": "1"})
    sha = commit(repo, {"c.php": "1"})
    order = gitsrc.recent_order(repo, sha)
    assert order["c.php"] < order["b.php"] < order["a.php"]


def test_count_commits(tmp_path):
    repo, s1 = make_repo(tmp_path, {"a.php": "1"})
    commit(repo, {"b.php": "1"})
    s3 = commit(repo, {"c.php": "1"})
    assert gitsrc.count_commits(repo, s1, s3) == 2


def test_is_bot_catches_unseparated_tool_names_but_not_humans():
    for u in ("sonarqube", "renovate-bot", "sonarcloud", "renovate-bot", "gitlab-ci", "danger", "ci-bot", "project_123_bot"):
        assert f.is_bot({"username": u}), u
    for u in ("abbott", "robotics-dev", "jane.doe", "sonarsson", "ali.bot", "bot.smith", "john.danger", "x.renovate"):
        assert not f.is_bot({"username": u}), u


def test_fetch_sorts_by_merged_at_and_paginates_notes():
    def get_json(endpoint, params):
        if endpoint.endswith("/merge_requests"):
            return [{"iid": 1, "merged_at": "2026-09-01T00:00:00Z"}, {"iid": 2, "merged_at": "2026-09-20T00:00:00Z"}]
        return [{"notes": [{"system": False, "body": "please reuse the existing repository method here",
                            "author": {"username": "rev"}}]}]
    rows = f.fetch("g/x", "master", 10, get_json)
    assert [r["mr_iid"] for r in rows] == [2, 1]


def test_mr_notes_paginates():
    pages = []

    def get_json(endpoint, params):
        pages.append(params["page"])
        n = 100 if params["page"] == 1 else 1
        return [{"notes": [{"system": False, "body": "x" * 30, "author": {"username": "rev"}}]}] * n
    notes = f.mr_notes("g%2Fx", 7, get_json)
    assert pages == [1, 2] and len(notes) == 101


def test_area_files_recency_order_and_prefix(tmp_path):
    repo, _ = make_repo(tmp_path, {"src/Voucher/AService.php": "<?php class AService {}",
                                   "src/Other/BService.php": "<?php class BService {}"})
    sha = commit(repo, {"src/Voucher/CService.php": "<?php class CService {}"})
    build.build_index("x", repo, "php", sha, tmp_path / "i.sqlite")
    got = areas.area_files(repo, sha, "php", tmp_path / "i.sqlite", path_prefix="src/Voucher/")
    assert got["layout"] == ["src/Voucher/CService.php", "src/Voucher/AService.php"]


def test_prep_with_prefix_writes_scoped_folder_and_recent_txt(tmp_path, monkeypatch):
    repo, _ = make_repo(tmp_path, {"src/Voucher/AService.php": "<?php class AService {}",
                                   "src/Other/BService.php": "<?php class BService {}"})
    cfg = tmp_path / "repos.json"
    cfg.write_text(json.dumps({"x": {"path": str(repo), "gitlab": "g/x", "ref": "master", "lang": "php"}}))
    monkeypatch.setenv("QUORRA_REPOS", str(cfg))
    monkeypatch.setenv("QUORRA_INDEX_DIR", str(tmp_path / "idx"))
    m = prep_repo.prep("x", tmp_path / "run", fetch=False, path_prefix="src/Voucher/")
    out = tmp_path / "run" / "x--src--Voucher"
    assert m["scope"] == "src/Voucher/"
    assert (out / "recent.txt").read_text().split() == ["src/Voucher/AService.php"]
    assert "src/Other/BService.php" not in (out / "areas" / "layout.txt").read_text()


def _prep_setup(tmp_path, monkeypatch, files):
    repo, _ = make_repo(tmp_path, files)
    cfg = tmp_path / "repos.json"
    cfg.write_text(json.dumps({"x": {"path": str(repo), "gitlab": "g/x", "ref": "master", "lang": "php"}}))
    monkeypatch.setenv("QUORRA_REPOS", str(cfg))
    monkeypatch.setenv("QUORRA_INDEX_DIR", str(tmp_path / "idx"))
    return repo


def test_prefix_normalised_and_segment_bounded(tmp_path, monkeypatch):
    _prep_setup(tmp_path, monkeypatch, {"src/Voucher/A.php": "<?php class A {}", "src/VoucherExtra/B.php": "<?php class B {}"})
    m1 = prep_repo.prep("x", tmp_path / "r1", fetch=False, path_prefix="src/Voucher")
    m2 = prep_repo.prep("x", tmp_path / "r2", fetch=False, path_prefix="./src/Voucher/")
    assert m1["scope"] == m2["scope"] == "src/Voucher/"
    assert m1["areas"] == m2["areas"]
    assert (tmp_path / "r1" / "x--src--Voucher" / "recent.txt").read_text().split() == ["src/Voucher/A.php"]
    assert prep_repo.normalize_prefix("a/b-c") != prep_repo.normalize_prefix("a/b/c")
    assert (tmp_path / "r1" / "x--src--Voucher").name != "x--src-Voucher"


def test_tooling_and_reviews_areas_ignore_prefix_and_recent_skips_deleted(tmp_path, monkeypatch):
    repo = _prep_setup(tmp_path, monkeypatch, {"src/Voucher/A.php": "<?php class A {}", "composer.json": "{}",
                                               "src/Voucher/Gone.php": "<?php class Gone {}"})
    commit(repo, {"src/Voucher/Gone.php": None})
    prep_repo.prep("x", tmp_path / "run", fetch=False, path_prefix="src/Voucher")
    out = tmp_path / "run" / "x--src--Voucher"
    assert "composer.json" in (out / "areas" / "tooling.txt").read_text()
    assert "Gone.php" not in (out / "recent.txt").read_text()
