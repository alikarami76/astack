import json
import sqlite3

import pytest

import store
import vec_lanes
from tests.test_search import _fake_embed


def _src(**kw):
    return {"kind": "mysql", "engine": "shop", "database": "shop", "schema_name": "", "table_name": "orders",
            **kw}


def test_doc_text_format_orders_columns_and_omits_empty_fields():
    cols = [{"name": "id", "type": "bigint"}, {"name": "payable_price", "type": "bigint", "meaning": "paid, no VAT"},
            {"name": "status", "type": "", "pitfalls": "canceled_system rows"}]
    t = vec_lanes.schema_doc_text(_src(grain="one row per order"), cols, ["orders hold 50k abandoned checkouts a day"])
    assert t.splitlines() == [
        "mysql shop shop.orders",
        "Grain: one row per order",
        "Columns: payable_price (bigint) — paid, no VAT; status — Pitfall: canceled_system rows; id (bigint)",
        "Facts: orders hold 50k abandoned checkouts a day"]
    assert vec_lanes.schema_doc_text({"kind": "ods", "database": "ODS", "schema_name": "dbo", "table_name": "t"}) \
        == "ods ODS.dbo.t"


def test_doc_text_is_cut_to_the_token_budget():
    cols = [{"name": f"column_number_{i}", "type": "varchar(255)"} for i in range(200)]
    t = vec_lanes.schema_doc_text(_src(notes="n"), cols, ["a fact " * 100])
    assert len(t) <= vec_lanes.DOC_CHARS
    assert "more" in t.splitlines()[2] and t.splitlines()[-1].startswith("Facts: ")


def test_cap_code_keeps_the_head_of_long_blocks():
    md = "# A\ntext\n```sql\n" + "\n".join(f"line {i}" for i in range(20)) + "\n```\nafter\n"
    out = vec_lanes.cap_code(md, keep=3)
    assert "line 2" in out and "line 3" not in out and "(17 more lines)" in out and "after" in out


@pytest.fixture
def lanes(kbenv, monkeypatch):
    import vectors
    monkeypatch.setattr(vectors, "embed", _fake_embed)
    c = store.connect()
    a = store.upsert(c, "source", _src(grain="one row per order"))
    b = store.upsert(c, "source", {"kind": "mysql", "engine": "dynamic_commission", "database": "dynamic_commission",
                                   "table_name": "commission_rules"}, origin="schema-cache")
    store.bulk_upsert(c, "column", [{"source_id": b, "name": "commission_rate", "type": "decimal"}],
                      origin="schema-cache", fts=False)
    store.upsert(c, "lesson", {"rule": "Keep ClickHouse queries at max_threads=1"})
    run = kbenv / "runs" / "2026-10-01-commission"
    run.mkdir(parents=True)
    (run / "run.json").write_text(json.dumps({"run_id": run.name, "question": "How much commission do sellers pay?"}))
    (run / "answer.md").write_text("# Answer\n\nSellers paid 12% commission on average.\n\n```sql\n"
                                   + "SELECT 1;\n" * 30 + "```\n")
    return vectors, c, a, b


def test_schema_and_runs_lanes_embed_search_and_leave_curated_alone(lanes, kbenv):
    vectors, c, a, b = lanes
    before = vectors.index(c)  # curated lane, unchanged behaviour
    res = vectors.index_lanes(c, ["schema", "runs"])
    assert res["schema"]["total"] == 2 and res["runs"]["total"] >= 2
    assert vectors.index(c)["embedded"] == 0 and before["total"] == vectors.index(c)["total"]
    assert vectors.index(c, lane="schema")["embedded"] == 0  # incremental by content hash
    hits = vectors.search_lane(c, "schema", "commission rate", k=2)
    assert hits[0]["id"] == b and hits[0]["title"] == "dynamic_commission.commission_rules"
    assert "commission_rate" in vec_lanes.why_table(c, b, "commission rate")
    runs = vectors.search_lane(c, "runs", "commission sellers pay", k=4)
    assert runs and {vec_lanes.run_id_of(r) for r in runs} == {"2026-10-01-commission"}
    assert all(r["path"].startswith("2026-10-01-commission/") for r in runs)
    body = sqlite3.connect(vec_lanes.runs_vec_path()).execute("SELECT group_concat(body) FROM chunks").fetchone()[0]
    assert body.count("SELECT 1;") == vec_lanes.CODE_LINES


def test_lanes_answer_by_keywords_without_vectors_or_daemon(lanes, monkeypatch, capsys):
    vectors, c, a, b = lanes
    monkeypatch.setattr(vectors, "embed", lambda *a, **k: (_ for _ in ()).throw(OSError("no daemon")))
    res = vectors.index_lanes(c, ["curated", "schema", "runs"])
    assert all("error" in v for v in res.values())
    assert vectors.search_lane(c, "schema", "commission", k=3)[0]["id"] == b  # docs index was still built
    import kb as kbcli
    assert kbcli.main(["embed", "--lane", "all", "--quiet"]) == 1  # reported, not raised
    capsys.readouterr()
    kbcli.main(["recall", "commission orders"])
    out = capsys.readouterr().out
    assert "## top" in out and "## Tables that may hold this" in out and "## Past run passages" in out
    assert "runs/2026-10-01-commission/" in out


def test_search_refuses_vectors_of_another_model(lanes, monkeypatch, capsys):
    vectors, c, a, b = lanes
    vectors.index_lanes(c, ["schema"])
    monkeypatch.setenv("VECSTORE_MODEL", "other/model")
    hits = vectors.search_lane(c, "schema", "commission rate", k=2)
    assert hits[0]["id"] == b and "built with" in capsys.readouterr().err
    r = vectors.index(c, lane="schema")  # the configured model differs: the whole file is embedded again
    assert r["embedded"] == r["total"] == 2 and r["model"] == "other/model"


def test_embed_lock_skips_a_second_embed(lanes):
    import fcntl
    vectors, c, a, b = lanes
    with open(vectors.lock_path(), "a+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        assert vectors.index_lanes(c, ["schema"]) == {"skipped": "another embed is running"}


def test_close_never_fails_because_of_vectors(kbenv, tmp_path, monkeypatch, capsys):
    import run_record
    import kb as kbcli
    d = run_record.new("x", "lookup", "q?", runs_dir=tmp_path / "runs", tier="quick")
    monkeypatch.setenv("CLU_BACKGROUND_EMBED", "1")
    monkeypatch.setattr(kbcli.subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(OSError("no fork")))
    assert run_record.close(d, "answered", commit=False) == d.name
    assert "background embed not started" in capsys.readouterr().err
    calls = []
    monkeypatch.setattr(kbcli.subprocess, "Popen", lambda cmd, **k: calls.append(cmd))
    assert "updating in the background" in kbcli.spawn_embed("all")
    assert calls[0][-4:] == ["embed", "--lane", "all", "--quiet"]
    assert (store.db_path().with_name("embed.log")).exists()


def test_schema_eval_matches_by_table_name(lanes, tmp_path):
    import eval_recall
    vectors, c, a, b = lanes
    f = tmp_path / "set.jsonl"
    f.write_text(json.dumps({"q": "commission rate", "expected": ["dynamic_commission.commission_rules"]}) + "\n"
                 + json.dumps({"query": "orders", "expected": [a]}) + "\n")
    items = eval_recall.load_schema_set(f)
    assert [i["query"] for i in items] == ["commission rate", "orders"]
    s = eval_recall.score_schema(c, items, "lexical")
    assert s["recall@5"] == 1.0 and s["queries"] == 2
    assert len(eval_recall.load_schema_set()) >= 3  # the placeholder set loads
