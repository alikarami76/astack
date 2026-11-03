import json
import pytest
import store


def test_upsert_is_idempotent_and_merges(kbenv):
    c = store.connect()
    a = store.upsert(c, "fact", {"statement": "B2B is 12% of GMV", "confidence": "observed"}, origin="t", tags=["gmv"])
    b = store.upsert(c, "fact", {"statement": "  b2b IS 12% of gmv ", "evidence": "q1"}, origin="t", tags=["b2b"])
    assert a == b
    row = store.get(c, a)
    assert row["evidence"] == "q1" and row["confidence"] == "observed"
    assert sorted(json.loads(row["tags"])) == ["b2b", "gmv"]


def test_search_english_and_farsi(kbenv):
    c = store.connect()
    store.upsert(c, "glossary", {"term_fa": "حراج‌ویژه", "term_en": "Flash sale", "meaning": "flash_offer promotion"})
    store.upsert(c, "metric", {"name": "AOV", "definition": "total_paid per order excluding B2B"})
    assert store.search(c, "AOV")[0]["type"] == "metric"
    assert store.search(c, "حراج")[0]["type"] == "glossary"
    assert store.search(c, "flash sale", etype="metric") == []


def test_verify_and_supersede(kbenv):
    c = store.connect()
    old = store.upsert(c, "fact", {"statement": "x uses tags", "confidence": "assumed"})
    new = store.upsert(c, "fact", {"statement": "x uses nature", "confidence": "observed"})
    store.verify(c, new, evidence="SELECT ... 2026-09-23")
    store.supersede(c, old, new)
    assert store.get(c, new)["confidence"] == "verified"
    assert store.get(c, old)["superseded_by"] == new
    assert all(r["id"] != old for r in store.search(c, "x uses"))


def test_export_rebuild_roundtrip(kbenv):
    c = store.connect()
    fid = store.upsert(c, "fact", {"statement": "cart ids have two id spaces"})
    sid = store.upsert(c, "source", {"kind": "mysql", "database": "warehouse", "table_name": "orders"})
    store.link(c, fid, sid)
    store.export(c)
    c.close()
    c2 = store.rebuild()
    assert store.get(c2, fid)["statement"] == "cart ids have two id spaces"
    assert store.search(c2, "orders")[0]["id"] == sid
    assert c2.execute("select count(*) from entity_links").fetchone()[0] == 1


def test_schema_cache_rows_not_exported(kbenv):
    c = store.connect()
    store.bulk_upsert(c, "source", [{"kind": "mysql", "database": "d", "table_name": "t"}], origin="schema-cache")
    store.upsert(c, "source", {"kind": "mysql", "database": "d", "table_name": "u"}, origin="manual")
    store.export(c, etype="source")
    lines = (kbenv / "export" / "source.jsonl").read_text().strip().splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["table_name"] == "u"


def test_curated_origin_overrides_schema_cache_origin(kbenv):
    c = store.connect()
    store.bulk_upsert(c, "source", [{"kind": "mysql", "database": "d", "table_name": "t"}], origin="schema-cache")
    store.upsert(c, "source", {"kind": "mysql", "database": "d", "table_name": "t", "notes": "curated"}, origin="dba:x")
    store.export(c, etype="source")
    c2 = store.rebuild()
    sid = store.make_id("source", {"kind": "mysql", "database": "d", "schema_name": "", "table_name": "t"})
    row = store.get(c2, sid)
    assert row["notes"] == "curated" and row["origin"] == "dba:x"


def test_curated_origin_first_then_schema_cache_keeps_curated(kbenv):
    c = store.connect()
    store.upsert(c, "source", {"kind": "mysql", "database": "d", "table_name": "t", "notes": "curated"}, origin="dba:x")
    store.bulk_upsert(c, "source", [{"kind": "mysql", "database": "d", "table_name": "t"}], origin="schema-cache")
    sid = store.make_id("source", {"kind": "mysql", "database": "d", "schema_name": "", "table_name": "t"})
    row = store.get(c, sid)
    assert row["origin"] == "dba:x"


def test_stats(kbenv):
    c = store.connect()
    store.upsert(c, "lesson", {"rule": "state time units on money"})
    assert store.stats(c)["lesson"] == 1


def test_upsert_rejects_empty_natural_key(kbenv):
    c = store.connect()
    with pytest.raises(ValueError, match="empty natural key for lesson"):
        store.upsert(c, "lesson", {"rule": "", "why": "no rule given"})
    with pytest.raises(ValueError, match="empty natural key for benchmark"):
        store.upsert(c, "benchmark", {"value": "12", "unit": "pct"})
    assert store.stats(c)["lesson"] == 0
    assert store.stats(c)["benchmark"] == 0


def test_bulk_upsert_skips_empty_natural_key_rows_without_counting(kbenv):
    c = store.connect()
    n = store.bulk_upsert(
        c,
        "lesson",
        [
            {"rule": "", "why": "blank rule"},
            {"rule": "state time units on money"},
        ],
        origin="curator",
    )
    assert n == 1
    assert store.stats(c)["lesson"] == 1


def test_kb_cli_errors_are_one_line_messages(kbenv, capsys, tmp_path):
    import kb
    c = store.connect()
    les = store.upsert(c, "lesson", {"rule": "state the currency"})
    fac = store.upsert(c, "fact", {"statement": "a fact"})
    assert kb.main(["supersede", les, "--by", fac]) == 1
    assert "only facts are superseded" in capsys.readouterr().err
    assert kb.main(["show", "fac_doesnotexist"]) == 1
    assert "no row with id" in capsys.readouterr().err
    assert kb.main(["link", fac, "src_nope"]) == 1
    batch = tmp_path / "b.json"
    batch.write_text(json.dumps([{"type": "fact", "row": {"statement": "ok row"}}, {"row": {"statement": "no type"}}]))
    assert kb.main(["add-many", "--file", str(batch)]) == 1
    out, err = capsys.readouterr()
    assert len(json.loads(out)) == 1 and "skipped item 1" in err


def test_recall_ends_with_standing_lessons(kbenv, capsys):
    import kb
    c = store.connect()
    store.upsert(c, "lesson", {"rule": "always state the currency", "applies_to": "step:report"})
    store.upsert(c, "lesson", {"rule": "chunk long clickhouse scans", "applies_to": "step:execute"})
    assert kb.main(["recall", "zzz unrelated"]) == 0
    out = capsys.readouterr().out
    standing = out.split("## standing lessons")[1]
    assert "always state the currency" in standing and "chunk long" not in standing
