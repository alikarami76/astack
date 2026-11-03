# tests/test_ingest_rules.py
import json

import pytest

import ingest_rules
import store
from gitfix import make_repo

FILE = "<?php\nclass A {\n  public function t() {\n    return new TableSchema('a');\n  }\n}\n"


@pytest.fixture
def setup(tmp_path):
    repo, sha = make_repo(tmp_path, {"src/Entity/A.php": FILE, "src/Entity/B.php": FILE})
    return store.connect(), repo, sha


def doc(sha, rules):
    return {"repo": "pricing", "scope": "", "sha": sha, "run_id": "r1", "rules": rules}


def rule(**kw):
    base = {"area": "data-storage", "rule": "Tables via TableSchema", "tier": "dominant", "source": "code",
            "rationale": "all entities", "examples": [{"file": "src/Entity/A.php", "line": 4, "snippet": "new TableSchema('a')"},
                                                    {"file": "src/Entity/B.php", "line": 4, "snippet": "new TableSchema('a')"}],
            "counter_examples": [], "evidence": {"files_following": 2, "files_checked": 2, "recent_following": 1, "recent_checked": 1}}
    return {**base, **kw}


def test_ingest_valid_rule(setup):
    conn, repo, sha = setup
    res = ingest_rules.ingest(conn, doc(sha, [rule()]), repo)
    [eid] = res["ingested"]
    row = store.get(conn, eid)
    assert row["verified"] == 0 and row["checked_at_sha"] == sha and row["run_id"] == "r1"
    assert "Evidence: 2/2 files, 1/1 recent." in row["rationale"]


@pytest.mark.parametrize("bad,reason", [
    ({"tier": "mostly"}, "tier"),
    ({"examples": [{"file": "src/Entity/A.php", "line": 4, "snippet": "x"}]}, "2 examples"),
    ({"examples": [{"file": "src/Entity/A.php", "line": 99, "snippet": "x"},
                   {"file": "src/Entity/B.php", "line": 4, "snippet": "x"}]}, "line"),
    ({"examples": [{"file": "src/Nope.php", "line": 1, "snippet": "x"},
                   {"file": "src/Entity/B.php", "line": 4, "snippet": "x"}]}, "not found"),
])
def test_ingest_rejects_with_reason_and_keeps_others(setup, bad, reason):
    conn, repo, sha = setup
    res = ingest_rules.ingest(conn, doc(sha, [rule(**bad), rule(rule="Another rule")]), repo)
    assert len(res["ingested"]) == 1 and reason in res["rejected"][0]["reason"]


def test_snippet_is_scrubbed(setup):
    conn, repo, sha = setup
    r = rule(examples=[{"file": "src/Entity/A.php", "line": 4, "snippet": "redis://:hunter2hunter2@h"},
                       {"file": "src/Entity/B.php", "line": 4, "snippet": "x"}])
    [eid] = ingest_rules.ingest(conn, doc(sha, [r]), repo)["ingested"]
    assert "hunter2hunter2" not in store.get(conn, eid)["examples"]


def test_review_source_may_cite_reviews_jsonl(setup):
    conn, repo, sha = setup
    r = rule(source="review", examples=[{"file": "reviews.jsonl", "line": 3, "snippet": "use the repository"}])
    assert ingest_rules.ingest(conn, doc(sha, [r]), repo)["ingested"]


def test_verdicts_verify_refute_and_drop_after_two(setup):
    conn, repo, sha = setup
    [a, b] = ingest_rules.ingest(conn, doc(sha, [rule(), rule(rule="R2")]), repo)["ingested"]
    ingest_rules.apply_verdicts(conn, [{"id": a, "verdict": "sustained", "evidence": "ok"},
                                       {"id": b, "verdict": "refuted", "evidence": "no"}], "r1", round=1)
    assert store.get(conn, a)["verified"] == 1
    assert not store.get(conn, b)["superseded_by"]
    res = ingest_rules.apply_verdicts(conn, [{"id": b, "verdict": "refuted", "evidence": "still no"}], "r1", round=2)
    assert res["dropped"] == [b] and store.get(conn, b)["superseded_by"] == "dropped:r1"
    assert store.search(conn, "Dropped convention", etype="lesson")


def test_share_creates_shared_rule_and_supersedes_members(setup):
    conn, repo, sha = setup
    [a] = ingest_rules.ingest(conn, doc(sha, [rule()]), repo)["ingested"]
    d2 = doc(sha, [rule(tier="transitional")]); d2["repo"] = "api-app"
    [b] = ingest_rules.ingest(conn, d2, repo)["ingested"]
    [sid] = ingest_rules.share(conn, [{"rule": "Tables via TableSchema", "area": "data-storage", "members": [a, b]}], "r1")
    shared = store.get(conn, sid)
    assert shared["repo"] == "shared-rules" and shared["tier"] == "transitional"  # I1: weakest member tier (dominant + transitional)
    assert json.loads(shared["examples"])[0]["file"].startswith(("pricing:", "api-app:"))
    assert store.get(conn, a)["superseded_by"] == sid and store.get(conn, b)["superseded_by"] == sid


def test_search_returns_k_live_rows_despite_superseded(tmp_path):
    conn = store.connect()
    for i in range(12):
        store.upsert(conn, "convention", {"repo": "x", "area": "naming", "rule": f"old naming rule {i}", "tier": "dominant",
                                          "source": "code", "superseded_by": "conv_new"})
    for i in range(5):
        store.upsert(conn, "convention", {"repo": "x", "area": "naming", "rule": f"live naming rule {i}", "tier": "dominant",
                                          "source": "code"})
    assert len(store.search(conn, "naming rule", etype="convention", k=5)) == 5


def _two(setup):
    conn, repo, sha = setup
    return ingest_rules.ingest(conn, doc(sha, [rule(), rule(rule="R2")]), repo)["ingested"]


def test_unknown_or_malformed_verdicts_are_skipped_not_refutations(setup):
    conn, repo, sha = setup
    [a, b] = _two(setup)
    for run in ("r1", "r2"):
        res = ingest_rules.apply_verdicts(conn, [
            {"id": a, "verdict": "sustained", "evidence": "ok"},
            {"id": b, "verdict": "inconclusive", "evidence": "?"},
            {"id": b, "evidence": "no verdict"},
            {"id": "conv_doesnotexist", "verdict": "refuted"},
            {"id": "les_x", "verdict": "refuted"},
            "junk"], run)
        assert res["refuted"] == 0 and res["dropped"] == [] and len(res["skipped"]) == 5
    row = store.get(conn, b)
    assert not row["superseded_by"] and "refuted" not in row["tags"]
    assert store.get(conn, a)["verified"] == 1


def test_share_never_resurrects_dropped_rule_and_skips_non_conventions(setup):
    conn, repo, sha = setup
    [a, b] = _two(setup)
    ingest_rules.apply_verdicts(conn, [{"id": b, "verdict": "refuted", "evidence": "x"}], "r1", round=1)
    ingest_rules.apply_verdicts(conn, [{"id": b, "verdict": "refuted", "evidence": "y"}], "r1", round=2)
    g = {"rule": "Shared", "area": "data-storage", "members": [a, b, "les_abc"]}
    rep = ingest_rules.share_report(conn, [g], "r3")
    assert rep["shared"] == [] and len(rep["skipped"]) == 3
    assert store.get(conn, b)["superseded_by"] == "dropped:r1"
    assert not store.get(conn, a)["superseded_by"]


def test_malformed_rule_is_rejected_not_fatal(setup):
    conn, repo, sha = setup
    bad = [rule(examples=["oops", 5]), rule(examples="notalist"),
           rule(examples=[{"file": ["a"], "line": 1}, {"file": "src/Entity/B.php", "line": 4}]),
           rule(examples=[{"file": "src/Entity/A.php", "line": "4"}, {"file": "src/Entity/B.php", "line": 4}]),
           "notadict", {"area": "x"}, rule(evidence="text", rule="Good one")]
    res = ingest_rules.ingest(conn, doc(sha, bad), repo)
    assert len(res["ingested"]) == 1 and len(res["rejected"]) == 6


@pytest.mark.parametrize("patch", [{"sha": "abc"}, {"sha": "0" * 40}, {"repo": ""}, {"rules": "x"}])
def test_bad_document_raises_value_error(setup, patch):
    conn, repo, sha = setup
    with pytest.raises(ValueError):
        ingest_rules.ingest(conn, {**doc(sha, [rule()]), **patch}, repo)


def test_rationale_area_and_lesson_evidence_are_scrubbed(setup):
    conn, repo, sha = setup
    r = rule(rationale="connect via redis://:hunter2hunter2@cache:6379 always", area="storage redis://:pw9pw9pw9@h")
    [eid] = ingest_rules.ingest(conn, doc(sha, [r]), repo)["ingested"]
    row = store.get(conn, eid)
    assert "hunter2hunter2" not in row["rationale"] and "pw9pw9pw9" not in row["area"]
    [a, b] = _two(setup)
    ingest_rules.apply_verdicts(conn, [{"id": b, "verdict": "refuted", "evidence": "x"}], "r1", round=1)
    ingest_rules.apply_verdicts(conn, [{"id": b, "verdict": "refuted", "evidence": "see redis://:leak8leak8@h"}], "r1", round=2)
    [les] = store.search(conn, "Dropped convention", etype="lesson")
    assert "leak8leak8" not in store.get(conn, les["id"])["why"]


# ---- fix round 1: verified-preserving ingest, rounds, supersede, plan, retire ----

def test_c1_reingest_same_tier_keeps_verified_tier_change_resets(setup):
    conn, repo, sha = setup
    [a, b] = _two(setup)
    ingest_rules.apply_verdicts(conn, [{"id": a, "verdict": "sustained", "evidence": "ok"},
                                       {"id": b, "verdict": "sustained", "evidence": "ok"}], "r1")
    d = doc(sha, [rule(), rule(rule="R2", tier="transitional")]); d["run_id"] = "r2"
    ingest_rules.ingest(conn, d, repo)
    ra, rb = store.get(conn, a), store.get(conn, b)
    assert ra["verified"] == 1 and ra["run_id"] == "r2" and ra["checked_at_sha"] == sha
    assert rb["verified"] == 0 and rb["tier"] == "transitional"
    [c] = ingest_rules.ingest(conn, doc(sha, [rule(rule="Brand new")]), repo)["ingested"]
    assert store.get(conn, c)["verified"] == 0


def test_c2_rounds_count_distinct_refutations(setup):
    conn, repo, sha = setup
    [a, b] = _two(setup)
    v = [{"id": b, "verdict": "refuted", "evidence": "no"}]
    ingest_rules.apply_verdicts(conn, v, "r1", round=1)
    res = ingest_rules.apply_verdicts(conn, v, "r1", round=1)
    assert res["dropped"] == [] and not store.get(conn, b)["superseded_by"]
    res = ingest_rules.apply_verdicts(conn, v, "r1", round=2)
    assert res["dropped"] == [b] and store.get(conn, b)["superseded_by"] == "dropped:r1"
    assert store.search(conn, "Dropped convention", etype="lesson")


def test_c3_supersede(setup):
    conn, repo, sha = setup
    [a, b] = _two(setup)
    assert ingest_rules.supersede(conn, a, b) == {"superseded": a, "by": b}
    assert store.get(conn, a)["superseded_by"] == b
    other = doc(sha, [rule(rule="Elsewhere")]); other["repo"] = "api-app"
    [c] = ingest_rules.ingest(conn, other, repo)["ingested"]
    with pytest.raises(ValueError):
        ingest_rules.supersede(conn, b, c)  # different repo
    with pytest.raises(ValueError):
        ingest_rules.supersede(conn, "conv_nope", b)
    with pytest.raises(ValueError):
        ingest_rules.supersede(conn, b, a)  # a is no longer live


def test_c4_plan_is_scope_aware_and_writes_nothing(setup):
    conn, repo, sha = setup
    ingest_rules.ingest(conn, doc(sha, [rule(), rule(rule="R2"), rule(rule="R3")]), repo)
    scoped = doc(sha, [rule(rule="S1")]); scoped["scope"] = "src/Entity/"
    ingest_rules.ingest(conn, scoped, repo)
    before = conn.execute("select count(*), sum(verified) from conventions").fetchone()
    d = doc(sha, [rule(), rule(rule="R2", tier="transitional"), rule(rule="R4"), rule(rule="Bad", tier="mostly")])
    p = ingest_rules.plan(conn, d, repo)
    assert [r["rule"] for r in p["new"]] == ["R4"]
    assert p["tier_changed"] == [{"rule": "R2", "area": "data-storage", "old": "dominant", "new": "transitional"}]
    assert p["unchanged"] == 1 and [m["rule"] for m in p["missing"]] == ["R3"]  # scoped S1 is not compared
    assert len(p["rejected"]) == 1
    assert tuple(conn.execute("select count(*), sum(verified) from conventions").fetchone()) == tuple(before)
    sp = ingest_rules.plan(conn, {**scoped, "rules": [rule(rule="S2")]}, repo)
    assert [m["rule"] for m in sp["missing"]] == ["S1"]


def test_c5_retire_missing(setup):
    conn, repo, sha = setup
    [a, b] = _two(setup)
    d = doc(sha, [rule()]); d["run_id"] = "r2"
    res = ingest_rules.ingest(conn, d, repo, retire_missing=True)
    assert [m["id"] for m in res["retired"]] == [b]
    assert store.get(conn, b)["superseded_by"] == "retired:r2" and not store.get(conn, a)["superseded_by"]


# ---- fix round 2 ----

def test_d1_supersede_carries_refutations_and_refuses_self(setup):
    conn, repo, sha = setup
    [a, b] = _two(setup)
    ingest_rules.apply_verdicts(conn, [{"id": a, "verdict": "refuted", "evidence": "no"}], "r1", round=1)
    d = doc(sha, [rule(rule="Tables via TableSchema, reworded")])
    [new] = ingest_rules.ingest(conn, d, repo)["ingested"]
    ingest_rules.supersede(conn, a, new)
    assert "refuted:r1:1" in store.get(conn, new)["tags"]
    res = ingest_rules.apply_verdicts(conn, [{"id": new, "verdict": "refuted", "evidence": "still no"}], "r1", round=2)
    assert res["dropped"] == [new] and store.get(conn, new)["superseded_by"] == "dropped:r1"
    assert ingest_rules.supersede(conn, b, b) == {"superseded": False, "reason": "same id"}
    assert not store.get(conn, b)["superseded_by"]


def test_d3_share_missing_file_exits_2(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("QUORRA_KB_DB", str(tmp_path / "kb.sqlite"))
    monkeypatch.setenv("QUORRA_KB_EXPORT", str(tmp_path / "export"))
    assert ingest_rules.main(["share", str(tmp_path / "nope.json"), "--run-id", "r1"]) == 2
    assert "nope.json" in capsys.readouterr().err


def test_d4_refound_retired_or_dropped_rule_is_revived(setup):
    conn, repo, sha = setup
    [a, b, c] = ingest_rules.ingest(conn, doc(sha, [rule(), rule(rule="R2"), rule(rule="R3")]), repo)["ingested"]
    ingest_rules.apply_verdicts(conn, [{"id": a, "verdict": "sustained", "evidence": "ok"}], "r1")
    d = doc(sha, [rule(rule="R2")]); d["run_id"] = "r2"
    ingest_rules.ingest(conn, d, repo, retire_missing=True)
    assert store.get(conn, a)["superseded_by"] == "retired:r2" and store.get(conn, c)["superseded_by"] == "retired:r2"
    store.upsert(conn, "convention", {**store.get(conn, b), "superseded_by": "conv_shared"})
    p = ingest_rules.plan(conn, doc(sha, [rule(), rule(rule="R2")]), repo)
    assert [r["rule"] for r in p["revived"]] == ["Tables via TableSchema"] and p["unchanged"] == 1 and p["new"] == []
    ingest_rules.ingest(conn, doc(sha, [rule(), rule(rule="R2")]), repo)
    ra = store.get(conn, a)
    assert not ra["superseded_by"] and ra["verified"] == 0
    assert store.get(conn, b)["superseded_by"] == "conv_shared"  # shared ids stay superseded


def test_d5_comparisons_are_key_based_and_scope_aware(setup):
    conn, repo, sha = setup
    [a] = ingest_rules.ingest(conn, doc(sha, [rule()]), repo)["ingested"]
    p = ingest_rules.plan(conn, doc(sha, [rule(rule="  tables VIA TableSchema ")]), repo)
    assert p["unchanged"] == 1 and p["new"] == [] and p["missing"] == []
    bad = rule(rule="TABLES via tableschema", tier="mostly")  # rejected, but names the live rule
    res = ingest_rules.ingest(conn, doc(sha, [rule(rule="Other"), bad]), repo, retire_missing=True)
    assert res["retired"] == [] and not store.get(conn, a)["superseded_by"]
    scoped = doc(sha, [rule()]); scoped["scope"] = "src/Entity/"
    assert [r["rule"] for r in ingest_rules.plan(conn, scoped, repo)["new"]] == ["Tables via TableSchema"]


# ---- fix round 3 ----

def _refute(conn, ids, run, rnd):
    return ingest_rules.apply_verdicts(conn, [{"id": i, "verdict": "refuted", "evidence": "no"} for i in ids], run, round=rnd)


def test_e1_refutations_count_only_within_the_current_run(setup):
    conn, repo, sha = setup
    [a, b] = _two(setup)
    _refute(conn, [a], "r1", 1)
    assert _refute(conn, [a], "r2", 1)["dropped"] == []          # tag from another run does not count
    assert not store.get(conn, a)["superseded_by"]
    assert _refute(conn, [a], "r2", 2)["dropped"] == [a]         # r2 round 1 + r2 round 2
    # a revived rule starts clean: one later refutation does not drop it
    d = doc(sha, [rule(), rule(rule="R2")]); d["run_id"] = "r3"
    ingest_rules.ingest(conn, d, repo)
    assert not store.get(conn, a)["superseded_by"] and "refuted" not in store.get(conn, a)["tags"]
    assert _refute(conn, [a], "r4", 1)["dropped"] == []


def test_e2_refound_old_wording_is_unchanged_via_rewording(setup):
    conn, repo, sha = setup
    [a] = ingest_rules.ingest(conn, doc(sha, [rule(rule="Old wording")]), repo)["ingested"]
    [b] = ingest_rules.ingest(conn, doc(sha, [rule(rule="New wording")]), repo)["ingested"]
    ingest_rules.supersede(conn, a, b)
    d = doc(sha, [rule(rule="Old wording")]); d["run_id"] = "r2"
    p = ingest_rules.plan(conn, d, repo)
    assert [r["rule"] for r in p["unchanged_via_rewording"]] == ["Old wording"]
    assert p["missing"] == [] and p["revived"] == [] and p["unchanged"] == 0 and p["new"] == []
    res = ingest_rules.ingest(conn, d, repo, retire_missing=True)
    assert res["retired"] == []
    assert not store.get(conn, b)["superseded_by"] and store.get(conn, a)["superseded_by"] == b


def test_e3_scope_is_part_of_the_key(setup):
    conn, repo, sha = setup
    da, db = doc(sha, [rule(), rule(rule="A only")]), doc(sha, [rule()])
    da["scope"], db["scope"] = "src/A/", "src/B/"
    [a1, a2] = ingest_rules.ingest(conn, da, repo)["ingested"]
    [b1] = ingest_rules.ingest(conn, db, repo)["ingested"]
    assert len({a1, b1}) == 2 and store.get(conn, a1)["scope"] == "src/A/" and store.get(conn, b1)["scope"] == "src/B/"
    # refreshing module A with a smaller file retires only A's row
    da2 = doc(sha, [rule()]); da2["scope"] = "src/A/"; da2["run_id"] = "r2"
    res = ingest_rules.ingest(conn, da2, repo, retire_missing=True)
    assert [m["id"] for m in res["retired"]] == [a2]
    assert not store.get(conn, b1)["superseded_by"] and not store.get(conn, a1)["superseded_by"]
    # unscoped ids keep the old (repo, area, rule) hash
    [u] = ingest_rules.ingest(conn, doc(sha, [rule()]), repo)["ingested"]
    assert u == store.make_id("convention", {"repo": "pricing", "area": "data-storage", "rule": "Tables via TableSchema"}) and store.get(conn, u)["scope"] == ""


def test_e3_old_database_gets_scope_column_and_rebuild_tolerates_missing_scope(tmp_path):
    import sqlite3
    db = tmp_path / "old.sqlite"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE conventions (id TEXT PRIMARY KEY, repo TEXT, area TEXT, rule TEXT, tier TEXT, rationale TEXT, examples TEXT, "
              "counter_examples TEXT, source TEXT, checked_at_sha TEXT, verified INTEGER, superseded_by TEXT, run_id TEXT, "
              "tags TEXT DEFAULT '[]', origin TEXT, created_at TEXT, updated_at TEXT)")
    c.commit(); c.close()
    conn = store.connect(str(db))
    assert "scope" in [r[1] for r in conn.execute("PRAGMA table_info(conventions)")]
    exp = tmp_path / "export"; exp.mkdir()
    row = {"id": "conv_x", "repo": "r", "area": "a", "rule": "x", "tier": "dominant", "source": "code"}
    (exp / "convention.jsonl").write_text(json.dumps(row) + "\n")
    c2 = store.rebuild(str(tmp_path / "new.sqlite"), str(exp))
    assert store.get(c2, "conv_x")["scope"] == "" and not store.LAST_REBUILD_SKIPPED


def test_e3_render_and_list_show_scope(setup):
    import render_conventions
    conn, repo, sha = setup
    for scope in ("src/A/", "src/B/"):
        d = doc(sha, [rule()]); d["scope"] = scope
        ingest_rules.ingest(conn, d, repo)
    md = render_conventions.render(conn, "pricing")
    assert "## data-storage — src/A/" in md and "## data-storage — src/B/" in md
    assert "## data-storage — whole repo" not in md


# ---- plan 2 final review ----

QUOTED_FAKES = ("$dbPassword = 'fakeDbPw111'", "'api_key' => 'fakeApiKey222'", "define('API_KEY','fakeDefine333')")


def test_m1_quoted_secrets_in_rule_text_rationale_and_lesson_are_redacted(setup):
    conn, repo, sha = setup
    text = " and ".join(QUOTED_FAKES)
    r = rule(rule=f"Never hardcode {QUOTED_FAKES[0]}", rationale=f"seen: {text}")
    [eid] = ingest_rules.ingest(conn, doc(sha, [r]), repo)["ingested"]
    row = store.get(conn, eid)
    for leak in ("fakeDbPw111", "fakeApiKey222", "fakeDefine333"):
        assert leak not in row["rationale"] and leak not in row["rule"], leak
    [a, b] = _two(setup)
    _refute(conn, [b], "r1", 1)
    ingest_rules.apply_verdicts(conn, [{"id": b, "verdict": "refuted", "evidence": f"see {text}"}], "r1", round=2)
    [les] = store.search(conn, "Dropped convention", etype="lesson")
    why = store.get(conn, les["id"])["why"]
    for leak in ("fakeDbPw111", "fakeApiKey222", "fakeDefine333"):
        assert leak not in why, leak


def test_m2_reworded_rule_with_replaces_is_superseded_and_keeps_refutations(setup):
    conn, repo, sha = setup
    [a, b] = _two(setup)
    _refute(conn, [a], "r1", 1)
    res = ingest_rules.ingest(conn, doc(sha, [rule(rule="Tables via TableSchema, reworded", replaces=a)]), repo)
    [new] = res["ingested"]
    assert new != a and res["superseded"] == [[a, new]]
    assert store.get(conn, a)["superseded_by"] == new
    assert "refuted:r1:1" in store.get(conn, new)["tags"]
    assert _refute(conn, [new], "r1", 2)["dropped"] == [new]


def test_m2_same_wording_with_replaces_is_not_superseded_but_listed(setup):
    conn, repo, sha = setup
    [a, b] = _two(setup)
    _refute(conn, [a], "r1", 1)
    res = ingest_rules.ingest(conn, doc(sha, [rule(replaces=a)]), repo)
    assert res["ingested"] == [a] and res["superseded"] == []
    assert not store.get(conn, a)["superseded_by"]


def test_m2_replaces_unknown_id_is_rejected_and_others_ingest(setup):
    conn, repo, sha = setup
    [a, b] = _two(setup)
    res = ingest_rules.ingest(conn, doc(sha, [rule(rule="Reworded A", replaces="conv_doesnotexist"),
                                               rule(rule="Reworded B", replaces=b)]), repo)
    assert len(res["ingested"]) == 1 and res["superseded"] == [[b, res["ingested"][0]]]
    assert res["rejected"] == [{"rule": "Reworded A", "reason": "replaces conv_doesnotexist, which is not a known convention id"}]
    p = ingest_rules.plan(conn, doc(sha, [rule(rule="Reworded C", replaces="conv_nope")]), repo)
    assert p["new"] == [] and "not a known convention id" in p["rejected"][0]["reason"]


def test_m6_refound_wording_whose_replacement_died_is_revived(setup):
    conn, repo, sha = setup
    [a] = ingest_rules.ingest(conn, doc(sha, [rule(rule="Old wording")]), repo)["ingested"]
    _refute(conn, [a], "r1", 1)
    [b] = ingest_rules.ingest(conn, doc(sha, [rule(rule="New wording", replaces=a)]), repo)["ingested"]
    _refute(conn, [b], "r1", 2)  # b now carries r1:1 and r1:2 -> dropped
    assert store.get(conn, b)["superseded_by"] == "dropped:r1" and store.get(conn, a)["superseded_by"] == b
    d = doc(sha, [rule(rule="Old wording")]); d["run_id"] = "r2"
    p = ingest_rules.plan(conn, d, repo)
    assert [r["rule"] for r in p["revived"]] == ["Old wording"] and p["unchanged_via_rewording"] == [] and p["unchanged"] == 0
    assert [r for r in ingest_rules.ingest(conn, d, repo)["ingested"]] == [a]
    ra = store.get(conn, a)
    assert not ra["superseded_by"] and ra["verified"] == 0 and "refuted" not in ra["tags"]


def test_i2_drop_gives_up_a_rule_with_a_lesson(setup, tmp_path, monkeypatch, capsys):
    conn, repo, sha = setup
    [a, b] = _two(setup)
    _refute(conn, [a, b], "r1", 1)
    res = ingest_rules.ingest(conn, doc(sha, [{"drop": a, "reason": "no longer true: fakeKey = 'x'"}, rule(rule="R2", replaces=b)]), repo)
    assert res["dropped"] == [a] and res["ingested"] == [b]
    assert store.get(conn, a)["superseded_by"] == "dropped:r1"
    [les] = store.search(conn, "Dropped convention", etype="lesson")
    assert "no longer true" in store.get(conn, les["id"])["why"]
    p = ingest_rules.plan(conn, doc(sha, [{"drop": b, "reason": "x"}, {"drop": "conv_nope", "reason": "x"}]), repo)
    assert p["drop"] == [b] and p["rejected"] == [{"rule": None, "reason": "drop conv_nope: not a known convention id"}]
    assert not store.get(conn, b)["superseded_by"]
    with pytest.raises(ValueError):
        ingest_rules.drop(conn, a, "r1", "again")  # already dropped
    monkeypatch.setenv("QUORRA_KB_DB", str(tmp_path / "kb.sqlite"))
    monkeypatch.setenv("QUORRA_KB_EXPORT", str(tmp_path / "export"))
    c2 = store.connect()
    cid = store.upsert(c2, "convention", {"repo": "pricing", "area": "x", "rule": "cli rule", "tier": "dominant", "source": "code"})
    assert ingest_rules.main(["drop", cid, "--run-id", "r9", "--reason", "gave up"]) == 0
    assert json.loads(capsys.readouterr().out) == {"dropped": cid, "by": "dropped:r9"}
    assert store.get(store.connect(), cid)["superseded_by"] == "dropped:r9"


def test_i2_render_marks_refuted_once_apart_from_unverified(setup):
    import render_conventions
    conn, repo, sha = setup
    [a, b] = _two(setup)
    _refute(conn, [a], "r1", 1)
    md = render_conventions.render(conn, "pricing")
    sec_a = md.split("### Tables via TableSchema")[1].split("###")[0]
    sec_b = md.split("### R2")[1].split("###")[0]
    assert "_(refuted once)_" in sec_a and "_(unverified)_" not in sec_a
    assert "_(unverified)_" in sec_b and "refuted once" not in sec_b


def test_i5_scope_is_normalised_like_path_prefix(setup):
    conn, repo, sha = setup
    ids = set()
    for scope in ("src/Module", "src/Module/", "./src/Module", "/src/Module/"):
        d = doc(sha, [rule()]); d["scope"] = scope
        [eid] = ingest_rules.ingest(conn, d, repo)["ingested"]
        ids.add(eid)
        assert store.get(conn, eid)["scope"] == "src/Module/"
    assert len(ids) == 1
    d = doc(sha, [rule()]); d["scope"] = "/"
    [u] = ingest_rules.ingest(conn, d, repo)["ingested"]
    assert store.get(conn, u)["scope"] == ""


def test_m2_rewording_onto_a_dropped_wording_revives_then_supersedes(setup):
    conn, repo, sha = setup
    [a, b] = _two(setup)                        # a: "Tables via TableSchema", b: "R2"
    ingest_rules.drop(conn, b, "r0", "old")      # b dead from an earlier run
    _refute(conn, [a], "r1", 1)
    res = ingest_rules.ingest(conn, doc(sha, [rule(rule="R2", replaces=a)]), repo)
    assert res["ingested"] == [b] and res["superseded"] == [[a, b]]
    assert not store.get(conn, b)["superseded_by"] and store.get(conn, a)["superseded_by"] == b
    assert "refuted:r1:1" in store.get(conn, b)["tags"]


def test_m2_revision_back_to_an_older_wording_is_paired(setup):
    conn, repo, sha = setup
    [a] = ingest_rules.ingest(conn, doc(sha, [rule(rule="Old wording")]), repo)["ingested"]
    [b] = ingest_rules.ingest(conn, doc(sha, [rule(rule="New wording", replaces=a)]), repo)["ingested"]
    _refute(conn, [b], "r1", 1)
    d = doc(sha, [rule(rule="Old wording", replaces=b)])
    p = ingest_rules.plan(conn, d, repo)
    assert p["supersedes"] == [[b, a]] and p["unchanged_via_rewording"] == [] and [r["rule"] for r in p["revived"]] == ["Old wording"]
    res = ingest_rules.ingest(conn, d, repo)
    assert res["ingested"] == [a] and res["superseded"] == [[b, a]]
    assert not store.get(conn, a)["superseded_by"] and store.get(conn, b)["superseded_by"] == a
    assert "refuted:r1:1" in store.get(conn, a)["tags"]


# ---- plan 2 final review, round 2 ----

def _kb_state(conn):
    return ([tuple(r) for r in conn.execute("SELECT * FROM conventions ORDER BY id")],
            [tuple(r) for r in conn.execute("SELECT * FROM lessons ORDER BY id")])


def _setup_refuted_pair(setup):
    conn, repo, sha = setup
    [a, b] = _two(setup)
    _refute(conn, [a, b], "r1", 1)
    return conn, repo, sha, a, b


@pytest.mark.parametrize("case", ["split", "revise_and_drop", "duplicate_drop"])
def test_r1_conflicting_revision_entries_are_a_document_error_and_write_nothing(setup, case):
    conn, repo, sha, a, b = _setup_refuted_pair(setup)
    entries = {
        "split": [rule(rule="Split one", replaces=a), rule(rule="Split two", replaces=a)],
        "revise_and_drop": [rule(rule="Revised A", replaces=a), {"drop": a, "reason": "x"}],
        "duplicate_drop": [{"drop": b, "reason": "x"}, {"drop": b, "reason": "y"}],
    }[case]
    before = _kb_state(conn)
    with pytest.raises(ValueError) as e:
        ingest_rules.ingest(conn, doc(sha, [rule(rule="Unrelated new rule")] + entries), repo)
    msg = str(e.value)
    assert (a if case != "duplicate_drop" else b) in msg
    assert {"split": "without `replaces`", "revise_and_drop": "either", "duplicate_drop": "once"}[case] in msg
    with pytest.raises(ValueError):
        ingest_rules.plan(conn, doc(sha, entries), repo)
    assert _kb_state(conn) == before


def test_r1_cli_exits_1_on_a_split(setup, tmp_path, monkeypatch, capsys):
    conn, repo, sha, a, b = _setup_refuted_pair(setup)
    cfg = tmp_path / "repos.json"
    cfg.write_text(json.dumps({"pricing": {"path": str(repo), "gitlab": "g/p", "ref": "master", "lang": "php"}}))
    monkeypatch.setenv("QUORRA_REPOS", str(cfg))
    f = tmp_path / "rules-revised.json"
    f.write_text(json.dumps(doc(sha, [rule(rule="Split one", replaces=a), rule(rule="Split two", replaces=a)])))
    before = _kb_state(conn)
    assert ingest_rules.main(["ingest", str(f)]) == 1
    assert a in capsys.readouterr().err
    assert _kb_state(store.connect()) == before


def test_r2_evidence_values_must_be_integers(setup):
    conn, repo, sha = setup
    ok = rule(evidence={"files_following": "12", "files_checked": 15, "recent_following": " 3 ", "recent_checked": 4})
    bad = [rule(rule="Words", evidence={"files_following": "lots", "files_checked": 2}),
           rule(rule="Float", evidence={"files_following": 1.5, "files_checked": 2}),
           rule(rule="Bool", evidence={"files_following": True, "files_checked": 2}),
           rule(rule="Leak", evidence={"files_following": "$password = 'fakeEv555'", "files_checked": 2})]
    res = ingest_rules.ingest(conn, doc(sha, [ok] + bad), repo)
    [eid] = res["ingested"]
    assert "Evidence: 12/15 files, 3/4 recent." in store.get(conn, eid)["rationale"]
    assert [r["rule"] for r in res["rejected"]] == ["Words", "Float", "Bool", "Leak"]
    assert all("evidence files_following must be an integer" in r["reason"] for r in res["rejected"])
    assert "fakeEv555" not in json.dumps(res)


def test_r2_rejected_rule_text_is_scrubbed(setup):
    conn, repo, sha = setup
    res = ingest_rules.ingest(conn, doc(sha, [rule(rule="Never $dbPassword = 'fakeRej444'", tier="mostly")]), repo)
    assert len(res["rejected"]) == 1 and "fakeRej444" not in json.dumps(res)
    p = ingest_rules.plan(conn, doc(sha, [rule(rule="Never $dbPassword = 'fakeRej444'", tier="mostly")]), repo)
    assert "fakeRej444" not in json.dumps(p)


def test_r2_replaces_must_be_in_the_same_scope(setup):
    conn, repo, sha = setup
    [whole] = ingest_rules.ingest(conn, doc(sha, [rule()]), repo)["ingested"]
    d = doc(sha, [rule(rule="Module version", replaces=whole)]); d["scope"] = "src/Entity/"
    res = ingest_rules.ingest(conn, d, repo)
    assert res["ingested"] == [] and "scope" in res["rejected"][0]["reason"] and whole in res["rejected"][0]["reason"]
    assert not store.get(conn, whole)["superseded_by"]


def test_r2_scoped_documents_reject_whole_repo_sources_and_enforced(setup):
    conn, repo, sha = setup
    d = doc(sha, [rule(rule="T", source="tooling"), rule(rule="V", source="review"), rule(rule="E", tier="enforced"),
                  rule(rule="Fine")])
    d["scope"] = "src/Entity/"
    res = ingest_rules.ingest(conn, d, repo)
    assert len(res["ingested"]) == 1
    assert [r["rule"] for r in res["rejected"]] == ["T", "V", "E"]
    assert all("belongs to the whole-repo pass" in r["reason"] for r in res["rejected"])
    whole = ingest_rules.ingest(conn, doc(sha, [rule(rule="T", source="tooling"), rule(rule="E", tier="enforced")]), repo)
    assert len(whole["ingested"]) == 2 and whole["rejected"] == []


def test_r2_ingested_lists_only_live_ids(setup):
    conn, repo, sha = setup
    # moved to shared-rules: re-finding the old wording keeps it superseded
    [a] = ingest_rules.ingest(conn, doc(sha, [rule()]), repo)["ingested"]
    d2 = doc(sha, [rule()]); d2["repo"] = "api-app"
    [m] = ingest_rules.ingest(conn, d2, repo)["ingested"]
    [sid] = ingest_rules.share(conn, [{"rule": "Tables via TableSchema", "area": "data-storage", "members": [a, m]}], "r1")
    res = ingest_rules.ingest(conn, doc(sha, [rule()]), repo)
    assert res["ingested"] == [] and res["unchanged_via_rewording"] == [a]
    assert store.get(conn, a)["superseded_by"] == sid
    # a rule the same file rewords: its old id ends up superseded, so it is not listed as ingested
    [x] = ingest_rules.ingest(conn, doc(sha, [rule(rule="X old")]), repo)["ingested"]
    res = ingest_rules.ingest(conn, doc(sha, [rule(rule="X old"), rule(rule="X new", replaces=x)]), repo)
    live = [i for i in res["ingested"] if not store.get(conn, i)["superseded_by"]]
    assert res["ingested"] == live and x not in res["ingested"] and x in res["unchanged_via_rewording"]
