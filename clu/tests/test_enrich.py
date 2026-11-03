import json

import pytest

import enrich
import store


def _seed(c):
    ids = {
        "metric": store.upsert(c, "metric", {"name": "Rule 2 inflated price share", "definition": "list price > 2x"},
                               origin="clu"),
        "fact": store.upsert(c, "fact", {"statement": "Cart ids have two id spaces", "confidence": "verified"}, origin="clu"),
        "glossary": store.upsert(c, "glossary", {"term_fa": "بای‌باکس", "term_en": "Buybox", "meaning": "winner"}, origin="clu"),
        "lesson": store.upsert(c, "lesson", {"rule": "Keep max_threads=1"}, origin="clu"),
        "source": store.upsert(c, "source", {"kind": "mysql", "database": "d", "table_name": "orders"}, origin="clu"),
    }
    ids["done"] = store.upsert(c, "metric", {"name": "Already enriched", "definition": "x", "aliases": "a",
                                            "questions": "q?"}, origin="clu")
    ids["generated"] = store.upsert(c, "source", {"kind": "mysql", "database": "d", "table_name": "gen"},
                                    origin="schema-cache:x")
    ids["om"] = store.upsert(c, "metric", {"name": "OM metric", "definition": "x"}, origin="om:cert")
    ids["old"] = store.upsert(c, "fact", {"statement": "Superseded claim"}, origin="clu")
    store.supersede(c, ids["old"], ids["fact"])
    return ids


def test_worklist_has_only_curated_rows_lacking_enrichment(kbenv):
    c = store.connect()
    ids = _seed(c)
    rows = enrich.worklist(c)
    got = {r["id"] for r in rows}
    assert got == {ids["metric"], ids["fact"], ids["glossary"], ids["lesson"], ids["source"]}
    r = next(r for r in rows if r["id"] == ids["metric"])
    assert r["type"] == "metric" and r["title"] == "Rule 2 inflated price share"
    assert r["context"]["definition"] == "list price > 2x"
    assert r["aliases"] == "" and r["questions"] == ""


def test_worklist_filters_and_limit(kbenv):
    c = store.connect()
    ids = _seed(c)
    assert {r["type"] for r in enrich.worklist(c, types=["lesson", "glossary"])} == {"lesson", "glossary"}
    assert len(enrich.worklist(c, limit=2)) == 2
    with pytest.raises(ValueError):
        enrich.worklist(c, types=["run"])


def test_a_row_with_only_one_field_still_needs_the_other(kbenv):
    c = store.connect()
    eid = store.upsert(c, "lesson", {"rule": "r", "aliases": "alt"}, origin="clu")
    assert [r["id"] for r in enrich.worklist(c)] == [eid]
    assert enrich.worklist(c)[0]["aliases"] == "alt"


def test_cli_worklist_writes_jsonl_with_instructions(kbenv, capsys):
    c = store.connect()
    _seed(c)
    out = kbenv / "wl.jsonl"
    assert enrich.main(["worklist", "--out", str(out)]) == 0
    lines = [json.loads(l) for l in out.read_text().splitlines()]
    assert "_instructions" in lines[0] and len(lines) == 6
    assert "5 rows" in capsys.readouterr().out


def test_apply_writes_fields_updates_search_and_exports(kbenv, capsys):
    c = store.connect()
    ids = _seed(c)
    filled = kbenv / "filled.jsonl"
    filled.write_text("\n".join(json.dumps(x) for x in [
        {"_instructions": "ignored"},
        {"id": ids["metric"], "type": "metric", "aliases": ["inflated list price", "overpriced listing"],
         "questions": ["Which sellers inflate their list price?", "How many offers break Rule 2?"]},
        {"id": ids["lesson"], "aliases": "thread cap", "questions": "How many threads for ClickHouse?"},
        {"id": "met_doesnotexist", "aliases": "x", "questions": "y"},
        {"id": ids["glossary"], "aliases": "", "questions": ""},
    ]) + "\n")
    assert enrich.main(["apply", "--file", str(filled)]) == 0
    out = capsys.readouterr().out
    assert "applied 2" in out and "skipped 2" in out
    m = store.get(c, ids["metric"])
    assert m["aliases"] == "inflated list price; overpriced listing"
    assert m["questions"] == "Which sellers inflate their list price? | How many offers break Rule 2?"
    assert m["definition"] == "list price > 2x"  # the rest of the row is untouched
    assert store.search(c, "overpriced listing")[0]["id"] == ids["metric"]
    assert store.search(c, "ClickHouse threads")[0]["id"] == ids["lesson"]
    exported = [json.loads(l) for l in (kbenv / "export" / "metric.jsonl").read_text().splitlines()]
    assert any(r["id"] == ids["metric"] and r["aliases"] for r in exported)
    assert enrich.worklist(c, types=["metric"]) == []


def test_apply_does_not_overwrite_without_flag_and_dry_run_writes_nothing(kbenv, capsys):
    c = store.connect()
    eid = store.upsert(c, "lesson", {"rule": "r", "aliases": "keep me", "questions": "old?"}, origin="clu")
    f = kbenv / "f.jsonl"
    f.write_text(json.dumps({"id": eid, "aliases": "new", "questions": "new?"}) + "\n")
    enrich.main(["apply", "--file", str(f)])
    assert store.get(c, eid)["aliases"] == "keep me"
    enrich.main(["apply", "--file", str(f), "--overwrite", "--dry-run"])
    assert store.get(c, eid)["aliases"] == "keep me"
    enrich.main(["apply", "--file", str(f), "--overwrite"])
    assert store.get(c, eid)["aliases"] == "new"


def test_apply_rejects_oversized_and_bad_lines(kbenv, capsys):
    c = store.connect()
    eid = store.upsert(c, "lesson", {"rule": "r"}, origin="clu")
    f = kbenv / "f.jsonl"
    f.write_text("not json\n" + json.dumps({"id": eid, "aliases": "x" * 5000, "questions": "q?"}) + "\n")
    assert enrich.main(["apply", "--file", str(f)]) == 1
    err = capsys.readouterr()
    assert "line 1" in err.err and "applied 0" in err.out
    assert store.get(c, eid)["aliases"] in (None, "")


def test_no_network_imports():
    src = open(enrich.__file__).read()
    for bad in ("urllib", "requests", "http.client", "anthropic", "openai", "socket"):
        assert bad not in src
