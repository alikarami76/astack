import eval_lookup as ev
import store
import tempfile
import json
import pytest
from pathlib import Path


def G(i, q, file, symbol=None, repo="pricing"):
    return {"id": i, "query": q, "phrasing": "en", "repos": [repo], "expect": [{"repo": repo, "file": file, **({"symbol": symbol} if symbol else {})}]}


def test_score_rank_and_rate():
    goldens = [G("g1", "a", "src/A.php", "A"), G("g2", "b", "src/B.php"), G("g3", "c", "src/C.php")]
    results = {"a": [{"repo": "pricing", "file": "src/X.php", "name": "X"}, {"repo": "pricing", "file": "src/A.php", "name": "A"}],
               "b": [{"repo": "pricing", "file": "src/Z.php", "name": "Z"}] * 5 + [{"repo": "pricing", "file": "src/B.php", "name": "B"}],
               "c": [{"repo": "pricing", "file": "src/C.php", "name": "Wrong"}]}
    res = ev.score(goldens, lambda g: results[g["query"]])
    assert [(i["id"], i["hit"], i["rank"]) for i in res["items"]] == [("g1", True, 2), ("g2", False, None), ("g3", True, 1)]
    assert abs(res["rate"] - 2 / 3) < 1e-9


def test_symbol_must_match_when_given():
    res = ev.score([G("g1", "a", "src/A.php", "A")], lambda g: [{"repo": "pricing", "file": "src/A.php", "name": "B"}])
    assert res["items"][0]["hit"] is False


def test_draft_from_concepts():
    conn = store.connect()
    store.upsert(conn, "concept", {"name_en": "featuredoffer winner", "names": {"en": ["featuredoffer winner"], "fa": ["برنده بای باکس"], "codename": []},
                                   "implementations": [{"repo": "pricing", "file": "src/W.php", "symbol": "W"}]})
    d = ev.draft(conn)
    assert {x["phrasing"] for x in d} == {"en", "fa"}
    assert all(x["expect"] == [{"repo": "pricing", "file": "src/W.php", "symbol": "W"}] for x in d)


def test_or_across_multiple_expect_entries():
    """Multiple expect entries: a hit matching any one counts."""
    goldens = [{"id": "g1", "query": "a", "phrasing": "en", "repos": ["pricing"], 
                "expect": [{"repo": "pricing", "file": "src/A.php", "symbol": "A"}, 
                          {"repo": "pricing", "file": "src/B.php", "symbol": "B"}]}]
    results = {"a": [{"repo": "pricing", "file": "src/B.php", "name": "B"}]}
    res = ev.score(goldens, lambda g: results[g["query"]])
    assert res["items"][0]["hit"] is True
    assert res["items"][0]["rank"] == 1


def test_grep_only_hit_never_satisfies_symbol():
    """A grep-only hit (name '') never satisfies a symbol golden."""
    goldens = [G("g1", "a", "src/A.php", "A")]
    results = {"a": [{"repo": "pricing", "file": "src/A.php", "name": ""}]}
    res = ev.score(goldens, lambda g: results[g["query"]])
    assert res["items"][0]["hit"] is False


def test_cli_exit_code_1_below_min(monkeypatch, tmp_path):
    """CLI exits 1 when rate is below --min."""
    repos_config = tmp_path / "repos.json"
    repos_config.write_text(json.dumps({"pricing": {"path": "/tmp/pricing", "ref": "HEAD"}}))
    monkeypatch.setenv("QUORRA_REPOS", str(repos_config))
    
    goldens_file = tmp_path / "goldens.jsonl"
    goldens_file.write_text('{"id": "g1", "query": "q", "phrasing": "en", "repos": ["pricing"], "expect": [{"repo": "pricing", "file": "src/F.php"}]}\n')
    
    # Mock _runner to return no hits
    original_runner = ev._runner
    ev._runner = lambda: lambda g: []
    
    try:
        code = ev.main(["run", "--goldens", str(goldens_file), "--min", "0.9"])
        assert code == 1
    finally:
        ev._runner = original_runner


def test_cli_exit_code_2_unknown_repo(monkeypatch, tmp_path):
    """CLI exits 2 when a golden has an unknown repo."""
    repos_config = tmp_path / "repos.json"
    repos_config.write_text(json.dumps({"pricing": {"path": "/tmp/pricing", "ref": "HEAD"}}))
    monkeypatch.setenv("QUORRA_REPOS", str(repos_config))
    
    goldens_file = tmp_path / "goldens.jsonl"
    goldens_file.write_text('{"id": "g1", "query": "q", "phrasing": "en", "repos": ["unknown_repo"], "expect": [{"repo": "unknown_repo", "file": "src/F.php"}]}\n')
    
    code = ev.main(["run", "--goldens", str(goldens_file)])
    assert code == 2


def test_cli_exit_code_2_empty_goldens(monkeypatch, tmp_path):
    """CLI exits 2 when goldens file is empty or missing."""
    repos_config = tmp_path / "repos.json"
    repos_config.write_text(json.dumps({"pricing": {"path": "/tmp/pricing", "ref": "HEAD"}}))
    monkeypatch.setenv("QUORRA_REPOS", str(repos_config))
    
    goldens_file = tmp_path / "goldens.jsonl"
    goldens_file.write_text("")
    
    code = ev.main(["run", "--goldens", str(goldens_file)])
    assert code == 2


def test_cli_exit_code_2_duplicate_id(monkeypatch, tmp_path):
    """CLI exits 2 when a golden has a duplicate id."""
    repos_config = tmp_path / "repos.json"
    repos_config.write_text(json.dumps({"pricing": {"path": "/tmp/pricing", "ref": "HEAD"}}))
    monkeypatch.setenv("QUORRA_REPOS", str(repos_config))
    
    goldens_file = tmp_path / "goldens.jsonl"
    goldens_file.write_text('{"id": "g1", "query": "q", "phrasing": "en", "repos": ["pricing"], "expect": [{"repo": "pricing", "file": "src/F.php"}]}\n')
    goldens_file.write_text(goldens_file.read_text() + '{"id": "g1", "query": "q2", "phrasing": "en", "repos": ["pricing"], "expect": [{"repo": "pricing", "file": "src/F.php"}]}\n')
    
    code = ev.main(["run", "--goldens", str(goldens_file)])
    assert code == 2


def test_stale_repo_truncates_to_top_5(monkeypatch, tmp_path):
    """R1: A stale result whose expected file is at rank 7 is a miss (top-5 cut applies)."""
    repos_config = tmp_path / "repos.json"
    repos_config.write_text(json.dumps({"pricing": {"path": "/tmp/pricing", "ref": "HEAD"}}))
    monkeypatch.setenv("QUORRA_REPOS", str(repos_config))
    
    goldens_file = tmp_path / "goldens.jsonl"
    goldens_file.write_text('{"id": "g1", "query": "q", "phrasing": "en", "repos": ["pricing"], "expect": [{"repo": "pricing", "file": "src/F.php", "symbol": "F"}]}\n')
    
    # Mock _runner to return hits with the expected file at rank 7, marked as stale
    def mock_runner():
        def run_one(g):
            hits = [{"repo": "pricing", "file": f"src/H{i}.php", "name": f"H{i}", "line": i} for i in range(1, 11)]
            hits[6] = {"repo": "pricing", "file": "src/F.php", "name": "F", "line": 7}  # rank 7
            return {"hits": hits, "stale": ["pricing"]}
        return run_one
    
    monkeypatch.setattr(ev, "_runner", mock_runner)
    code = ev.main(["run", "--goldens", str(goldens_file), "--out", str(tmp_path / "result.json")])
    assert code == 1  # miss, below min
    
    # Assert stale field is in output
    result_json = json.loads((tmp_path / "result.json").read_text())
    assert "stale" in result_json
    assert "pricing" in result_json["stale"]


def test_score_with_error_path():
    """Q2: Direct score() test with errored and non-errored goldens."""
    goldens = [
        {"id": "g1", "query": "q1", "phrasing": "en", "repos": ["pricing"], "expect": [{"repo": "pricing", "file": "src/A.php"}]},
        {"id": "g2", "query": "q2", "phrasing": "en", "repos": ["pricing"], "expect": [{"repo": "pricing", "file": "src/B.php"}]}
    ]
    
    def mock_run_one(g):
        if g["id"] == "g1":
            return {"error": "no index for pricing"}
        else:
            return [{"repo": "pricing", "file": "src/B.php", "name": "B", "line": 1}]
    
    res = ev.score(goldens, mock_run_one)
    
    # g1 should have error
    assert res["items"][0]["error"] == "g1: no index for pricing"
    # g2 should be a hit
    assert res["items"][1]["hit"] is True
    # Rate should be computed over non-errored goldens only (1/1)
    assert res["rate"] == 1.0
    # errors list should be present
    assert "errors" in res
    assert "g1: no index for pricing" in res["errors"]


def test_error_excluded_from_rate_cli(monkeypatch, tmp_path):
    """Q2: CLI test with errored golden excluded from rate, exit 2 precedence."""
    repos_config = tmp_path / "repos.json"
    repos_config.write_text(json.dumps({"pricing": {"path": "/tmp/pricing", "ref": "HEAD"}}))
    monkeypatch.setenv("QUORRA_REPOS", str(repos_config))
    
    goldens_file = tmp_path / "goldens.jsonl"
    goldens_file.write_text('{"id": "g1", "query": "q1", "phrasing": "en", "repos": ["pricing"], "expect": [{"repo": "pricing", "file": "src/A.php"}]}\n')
    goldens_file.write_text(goldens_file.read_text() + '{"id": "g2", "query": "q2", "phrasing": "en", "repos": ["pricing"], "expect": [{"repo": "pricing", "file": "src/B.php"}]}\n')
    
    # Mock _runner to return error for g1 and miss for g2
    def mock_runner():
        def run_one(g):
            if g["id"] == "g1":
                return {"error": "no index for pricing"}
            else:
                return []  # miss
        return run_one
    
    monkeypatch.setattr(ev, "_runner", mock_runner)
    
    out_file = tmp_path / "result.json"
    code = ev.main(["run", "--goldens", str(goldens_file), "--min", "0.9", "--out", str(out_file)])
    assert code == 2  # exit 2 (error) takes precedence over exit 1 (below min)
    
    # Check output JSON
    result_json = json.loads(out_file.read_text())
    assert result_json["items"][0]["error"] == "g1: no index for pricing"
    assert "errors" in result_json
    assert result_json["rate"] == 0.0  # only g2 (miss) counted


def test_stale_field_in_output(monkeypatch, tmp_path):
    """Q3: Stale test asserts the "stale" field in output JSON."""
    repos_config = tmp_path / "repos.json"
    repos_config.write_text(json.dumps({"pricing": {"path": "/tmp/pricing", "ref": "HEAD"}}))
    monkeypatch.setenv("QUORRA_REPOS", str(repos_config))
    
    goldens_file = tmp_path / "goldens.jsonl"
    goldens_file.write_text('{"id": "g1", "query": "q", "phrasing": "en", "repos": ["pricing"], "expect": [{"repo": "pricing", "file": "src/F.php", "symbol": "F"}]}\n')
    
    # Mock _runner to return a hit with stale
    def mock_runner():
        def run_one(g):
            return {"hits": [{"repo": "pricing", "file": "src/F.php", "name": "F", "line": 1}], "stale": ["pricing"]}
        return run_one
    
    monkeypatch.setattr(ev, "_runner", mock_runner)
    
    out_file = tmp_path / "result.json"
    code = ev.main(["run", "--goldens", str(goldens_file), "--out", str(out_file)])
    assert code == 0  # All hits, no errors
    
    result_json = json.loads(out_file.read_text())
    assert "stale" in result_json
    assert result_json["stale"] == ["pricing"]


def test_exit_0_all_hits_no_errors(monkeypatch, tmp_path):
    """R2(d): Exit 0 test (all hits, no errors)."""
    repos_config = tmp_path / "repos.json"
    repos_config.write_text(json.dumps({"pricing": {"path": "/tmp/pricing", "ref": "HEAD"}}))
    monkeypatch.setenv("QUORRA_REPOS", str(repos_config))
    
    goldens_file = tmp_path / "goldens.jsonl"
    goldens_file.write_text('{"id": "g1", "query": "q", "phrasing": "en", "repos": ["pricing"], "expect": [{"repo": "pricing", "file": "src/F.php", "symbol": "F"}]}\n')
    
    # Mock _runner to return a hit
    def mock_runner():
        def run_one(g):
            return [{"repo": "pricing", "file": "src/F.php", "name": "F", "line": 1}]
        return run_one
    
    monkeypatch.setattr(ev, "_runner", mock_runner)
    
    code = ev.main(["run", "--goldens", str(goldens_file)])
    assert code == 0  # All hits, no errors


def _raw_concept(conn, name_en, names_raw):
    """Insert a concept row bypassing store validation, as a legacy row would exist."""
    conn.execute("INSERT INTO concepts (id, name_en, names, implementations) VALUES (?, ?, ?, ?)",
                 (f"cpt_{name_en}", name_en, names_raw,
                  json.dumps([{"repo": "pricing", "file": "src/S.php"}])))
    conn.commit()


@pytest.mark.parametrize("raw", ["string concept", json.dumps("string concept")])
def test_draft_keeps_string_valued_names(raw):
    """R4 S2(a): a concept whose names is a string (bare text or a JSON string) still yields an 'en' draft."""
    conn = store.connect()
    _raw_concept(conn, "string concept", raw)
    d = ev.draft(conn)
    assert [(x["phrasing"], x["query"]) for x in d] == [("en", "string concept")]


def test_draft_skips_list_valued_names_and_warns_with_concept_name(capsys):
    """R4 S2(b): list-valued names are skipped; the warning names the concept."""
    conn = store.connect()
    _raw_concept(conn, "list concept", json.dumps(["a", "b"]))
    d = ev.draft(conn)
    assert d == []
    err = capsys.readouterr().err
    assert "warning: concept 'list concept' has non-dict names; skipped" in err


def _two_repo_runner(monkeypatch, tmp_path, lookup_result):
    repos_config = tmp_path / "repos.json"
    repos_config.write_text(json.dumps({"a": {"path": "/tmp/a", "ref": "HEAD"}, "b": {"path": "/tmp/b", "ref": "HEAD"}}))
    monkeypatch.setenv("QUORRA_REPOS", str(repos_config))
    import qindex.lookup as ql
    monkeypatch.setattr(ql, "lookup", lambda *args, **kw: lookup_result)
    return ev._runner()


def test_run_one_lists_every_unindexed_repo(monkeypatch, tmp_path):
    """R4 S2(c): lookup reports two missing repos; the error string names both."""
    run_one = _two_repo_runner(monkeypatch, tmp_path, {"hits": [], "missing": ["a", "b"]})
    res = run_one({"id": "g1", "query": "q", "repos": ["a", "b"], "expect": []})
    assert res == {"error": "no index for a, b"}


def test_run_one_lists_every_unknown_repo(monkeypatch, tmp_path):
    """R4 S2(c): two repos absent from repos.json are both named in one error string."""
    run_one = _two_repo_runner(monkeypatch, tmp_path, {"hits": []})
    res = run_one({"id": "g1", "query": "q", "repos": ["x", "y"], "expect": []})
    assert res == {"error": "no index for x, y"}
