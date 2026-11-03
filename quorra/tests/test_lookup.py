from pathlib import Path

import pytest

import store
from gitfix import make_repo
from qindex import build
from qindex.lookup import endpoint_pairs, lookup

FX = Path(__file__).parent / "fixtures"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("QUORRA_KB_DB", str(tmp_path / "kb.sqlite"))
    monkeypatch.setenv("QUORRA_KB_EXPORT", str(tmp_path / "export"))
    files = {f"src/{p.name}": p.read_text() for p in (FX / "php").glob("*.php")}
    files["src/Service/FeaturedofferPriceService.php"] = ("<?php\nnamespace P\\Service;\n\nclass FeaturedofferPriceService\n{\n"
                                                   "    public function calculateFeaturedofferPrice(int $variantId): int\n    {\n        return 0;\n    }\n}\n")
    (tmp_path / "back").mkdir()
    repo, sha = make_repo(tmp_path / "back", files)
    dbp = tmp_path / "back.sqlite"
    build.build_index("pricing", repo, "php", sha, dbp)
    kb = store.connect()
    store.upsert(kb, "concept", {"name_en": "featuredoffer price", "names": {"en": ["featuredoffer price"], "fa": ["قیمت پیشنهاد ویژه"], "codename": []},
                                 "description": "price of the featuredoffer winner",
                                 "implementations": [{"repo": "pricing", "file": "src/Service/FeaturedofferPriceService.php",
                                                      "symbol": "FeaturedofferPriceService", "kind": "service"}]})
    return {"repos": {"pricing": {"path": repo, "ref": "master", "db": dbp}}, "kb": kb, "tmp": tmp_path}


def test_exact_name_ranks_first(env):
    res = lookup("calculateFeaturedofferPrice", env["repos"], env["kb"])
    top = res["hits"][0]
    assert top["name"] == "calculateFeaturedofferPrice" and top["file"] == "src/Service/FeaturedofferPriceService.php" and top["line"] == 6
    assert "index" in top["sources"] and "grep" in top["sources"]


def test_words_find_it(env):
    names = [h["name"] for h in lookup("featuredoffer price", env["repos"], env["kb"], use_grep=False)["hits"][:3]]
    assert "FeaturedofferPriceService" in names


def test_persian_via_concept_map(env):
    res = lookup("قیمت پیشنهاد ویژه", env["repos"], env["kb"])
    assert "featuredoffer price" in res["terms"]
    top = res["hits"][0]
    assert top["file"] == "src/Service/FeaturedofferPriceService.php" and "concept" in top["sources"]


def test_stale_reported(env):
    from gitfix import commit
    commit(env["repos"]["pricing"]["path"], {"src/New.php": "<?php class NewThing {}"})
    assert lookup("NewThing", env["repos"], env["kb"], use_grep=False)["stale"] == ["pricing"]


def test_shas_reported(env):
    import gitsrc
    res = lookup("NewThing", env["repos"], env["kb"], use_grep=False)
    assert set(res["shas"]) == set(env["repos"])
    for name, sha in res["shas"].items():
        assert sha == gitsrc.resolve(Path(env["repos"][name]["path"]), env["repos"][name]["ref"])


# ---- fix round 1 regressions (F1-F7) ----

def test_f1_fts_special_characters_never_raise(env):
    for q in ['قیمت"', '"', "a*b", "(price)", "price AND", "NEAR(", "x:y", "^z", "'"]:
        res = lookup(q, env["repos"], env["kb"])
        assert "hits" in res


def test_f3_missing_index_reported(env):
    repos = {**env["repos"], "ghost": {"path": env["tmp"], "ref": "master", "db": env["tmp"] / "nope.sqlite"}}
    res = lookup("calculateFeaturedofferPrice", repos, env["kb"])
    assert res["missing"] == ["ghost"] and res["hits"]
    assert lookup("calculateFeaturedofferPrice", env["repos"], env["kb"])["missing"] == []


def test_f4_grep_short_terms_and_git_errors(env):
    from qindex.lookup import _grep_terms
    assert _grep_terms("ab", ["ab", "xy"]) == []
    assert _grep_terms("featuredoffer price", ["featuredoffer price", "قیمت پیشنهاد ویژه", "a1", "t3", "t4", "t5"])[:2] == ["featuredoffer price", "قیمت پیشنهاد ویژه"]
    assert len(_grep_terms("featuredoffer price", ["t1x", "t2x", "t3x", "t4x", "t5x"])) == 4
    bad = {"pricing": {**env["repos"]["pricing"], "path": env["tmp"] / "not-a-repo"}}
    (env["tmp"] / "not-a-repo").mkdir()
    res = lookup("calculateFeaturedofferPrice", bad, env["kb"])  # resolve and grep both fail; must not raise
    assert res["stale"] == ["pricing"]


def test_f5_vectors_per_repo_failure_does_not_drop_others(env, monkeypatch):
    from qindex import symvec
    repos = {**env["repos"], "other": {**env["repos"]["pricing"]}}
    import shutil
    shutil.copy(env["repos"]["pricing"]["db"], env["tmp"] / "other.sqlite")
    repos["other"]["db"] = env["tmp"] / "other.sqlite"
    sid = build_id = None
    from qindex import db as qdb
    c = qdb.connect(env["repos"]["pricing"]["db"])
    sid = c.execute("SELECT id FROM symbols WHERE name = 'FeaturedofferPriceService'").fetchone()["id"]

    def fake(db_path, query, limit=50):
        if "other" in str(db_path):
            raise RuntimeError("boom")
        return [sid]
    monkeypatch.setattr(symvec, "semantic", fake)
    res = lookup("zzzunmatchable", repos, env["kb"], use_grep=False, use_vectors="auto")
    assert [h["name"] for h in res["hits"]] == ["FeaturedofferPriceService"] and res["hits"][0]["sources"] == ["vector"]


def test_f6_cli_unknown_repo_and_endpoints_single_array(tmp_path, monkeypatch, capsys):
    import json as _json
    import lookup as cli
    cfg = tmp_path / "repos.json"
    cfg.write_text(_json.dumps({"a": {"path": str(tmp_path), "ref": "master", "lang": "php"}}))
    monkeypatch.setenv("QUORRA_REPOS", str(cfg))
    monkeypatch.setenv("QUORRA_INDEX_DIR", str(tmp_path / "idx"))
    assert cli.main(["x", "--repo", "zzz"]) == 2
    assert "unknown repo zzz; known: a" in capsys.readouterr().err
    assert cli.main(["--endpoints"]) == 0
    assert _json.loads(capsys.readouterr().out) == []


def test_f7_endpoint_pairs_tolerates_bad_extra(tmp_path):
    import sqlite3
    from qindex import db as qdb
    f = qdb.connect(tmp_path / "f.sqlite")
    b = qdb.connect(tmp_path / "b.sqlite")
    ins = "INSERT INTO symbols (repo,kind,name,fqname,file,line,signature,extra) VALUES (?,?,?,?,?,?,?,?)"
    f.execute(ins, ("f", "api_client", "ok", "ok", "a.ts", 1, "", '{"url_norm": "/x"}'))
    f.execute(ins, ("f", "api_client", "bad", "bad", "a.ts", 2, "", "not json"))
    f.execute(ins, ("f", "api_client", "nul", "nul", "a.ts", 3, "", None))
    b.execute(ins, ("b", "route", "/x", "/x", "r.php", 1, "", '{"url_norm": "/x"}'))
    b.execute(ins, ("b", "route", "/y", "/y", "r.php", 2, "", None))
    f.commit(); b.commit(); f.close(); b.close()
    pairs = endpoint_pairs(tmp_path / "f.sqlite", {"b": tmp_path / "b.sqlite"})
    assert len(pairs) == 1 and pairs[0]["back"]["method"] == ""


# ---- final review M4: missing indexes are never a silent "no matches" ----

def _cli_env(tmp_path, monkeypatch):
    import json as _json
    (tmp_path / "g").mkdir()
    repo, _ = make_repo(tmp_path / "g", {"src/Svc/PriceThing.php": "<?php\nnamespace P;\nclass PriceThing {}\n"})
    (tmp_path / "notrepo").mkdir()
    cfg = tmp_path / "repos.json"
    cfg.write_text(_json.dumps({"good": {"path": str(repo), "ref": "master", "lang": "php"},
                                "front": {"path": str(tmp_path / "notrepo"), "ref": "dev", "lang": "ts"},
                                "jv": {"path": str(tmp_path / "notrepo"), "ref": "main", "lang": "java"}}))
    monkeypatch.setenv("QUORRA_REPOS", str(cfg))
    monkeypatch.setenv("QUORRA_INDEX_DIR", str(tmp_path / "idx"))  # empty: no index built yet


def test_m4_cli_empty_index_dir_builds_what_it_can_and_warns_about_the_rest(tmp_path, monkeypatch, capsys):
    import json as _json
    import lookup as cli
    _cli_env(tmp_path, monkeypatch)
    assert cli.main(["PriceThing", "--no-grep", "--json"]) == 0
    res = _json.loads(capsys.readouterr().out)
    assert [h["name"] for h in res["hits"]] == ["PriceThing"]  # the missing index was built on the way
    assert sorted(res["missing"]) == ["front", "jv"] and any("front" in w for w in res["warnings"])


def test_m4_cli_no_refresh_text_output_warns(tmp_path, monkeypatch, capsys):
    import lookup as cli
    _cli_env(tmp_path, monkeypatch)
    assert cli.main(["PriceThing", "--no-grep", "--no-refresh"]) == 0
    out = capsys.readouterr().out
    assert "no index built for good, front, jv" in out and "index_repo.py" in out
    assert "no matches in the index" in out and "not searched" in out


def test_m4_cli_endpoints_reports_skipped_repos(tmp_path, monkeypatch, capsys):
    import json as _json
    import lookup as cli
    _cli_env(tmp_path, monkeypatch)
    assert cli.main(["--endpoints", "--no-refresh"]) == 0
    cap = capsys.readouterr()
    assert _json.loads(cap.out) == [] and "skipped" in cap.err and "front" in cap.err
    assert cli.main(["--endpoints", "--no-refresh", "--json"]) == 0
    res = _json.loads(capsys.readouterr().out)
    assert res["pairs"] == [] and sorted(res["skipped"]) == ["front", "good", "jv"]


def test_m4_endpoints_backends_include_java(tmp_path, monkeypatch):
    import lookup as cli
    seen = {}
    monkeypatch.setattr(cli, "endpoint_pairs", lambda f, backs: seen.update(backs) or [])
    _cli_env(tmp_path, monkeypatch)
    for n in ("front", "jv", "good"):
        from qindex import db as qdb
        qdb.connect(cli.qpaths.index_path(n)).close()
    assert cli.main(["--endpoints", "--no-refresh"]) == 0
    assert set(seen) == {"good", "jv"}
