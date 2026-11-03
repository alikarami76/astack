import json
import sqlite3

import store


def test_arabic_yeh_kaf_query_finds_persian_row(kbenv):
    c = store.connect()
    store.upsert(c, "glossary", {"term_fa": "کیفیت کالا", "term_en": "Product quality", "meaning": "کتاب و کیف"})
    assert store.search(c, "كيفيت")[0]["type"] == "glossary"  # Arabic yeh and kaf typed by a keyboard
    assert store.search(c, "كتاب")[0]["type"] == "glossary"


def test_index_side_is_normalised_too(kbenv):
    c = store.connect()
    store.upsert(c, "fact", {"statement": "نرخ كميسيون برای ۱۲۰ فروشنده"})  # Arabic kaf and yeh, Persian digits
    body = c.execute("SELECT body FROM fts").fetchone()[0]
    assert "کمیسیون" in body and "120" in body and "ك" not in body
    assert store.search(c, "کمیسیون 120")
    assert store.search(c, "كميسيون ١٢٠")  # Arabic-Indic digits in the query


def test_persian_digits_in_query_match_ascii_rows(kbenv):
    c = store.connect()
    store.upsert(c, "metric", {"name": "Rule 2 inflated list price", "definition": "list price above 2x"})
    assert store.search(c, "رول ۲ Rule ۲")[0]["type"] == "metric"


def test_zwnj_is_a_token_break_in_queries_and_documents(kbenv):
    c = store.connect()
    store.upsert(c, "glossary", {"term_fa": "بای‌باکس", "term_en": "Buybox", "meaning": "برنده می‌شود"})
    assert store.search(c, "بای باکس")  # space where the row has a ZWNJ
    assert store.search(c, "می ‌ شود")  # stray spaces around the ZWNJ
    assert store._fts_query("می‌خواهم") == '"می"* OR "خواهم"*' or store._fts_query("می‌خواهم").count("OR") == 1


def test_normalisation_can_be_switched_off(kbenv, monkeypatch):
    monkeypatch.setenv("VECSTORE_NORMALIZE", "0")
    c = store.connect()
    store.upsert(c, "fact", {"statement": "نرخ كميسيون"})
    assert store.search(c, "كميسيون")
    assert not store.search(c, "کمیسیون")  # raw index: the Persian spelling does not meet the Arabic one


def test_glossary_expansion_survives_variant_spelling(kbenv):
    c = store.connect()
    store.upsert(c, "glossary", {"term_fa": "ریزش فروشنده", "term_en": "Seller churn", "meaning": "x"})
    store.upsert(c, "metric", {"name": "Seller churn rate", "definition": "stopped selling"})
    assert "Seller churn" in store.expand_query(c, "ريزش فروشنده")


def test_enrichment_columns_are_indexed(kbenv):
    c = store.connect()
    eid = store.upsert(c, "metric", {"name": "Rule 2 metric", "definition": "d", "aliases": "inflated list price",
                                    "questions": "which sellers inflate the list price?"})
    assert store.search(c, "inflate sellers")[0]["id"] == eid
    for etype, row in (("fact", {"statement": "s1"}), ("glossary", {"term_fa": "ف", "term_en": "e"}),
                       ("lesson", {"rule": "r"}), ("source", {"kind": "mysql", "table_name": "t"})):
        i = store.upsert(c, etype, {**row, "aliases": "zzalias", "questions": "zzquestion text"})
        assert store.get(c, i)["questions"] == "zzquestion text"
    assert len(store.search(c, "zzalias", k=10)) == 4
    assert len(store.search(c, "zzquestion", k=10)) == 4


def test_old_database_gets_the_new_columns(kbenv):
    p = kbenv / "old.sqlite"
    db = sqlite3.connect(p)
    db.execute("CREATE TABLE facts (id TEXT PRIMARY KEY, statement TEXT, topic TEXT, source_id TEXT, confidence TEXT,"
               " evidence TEXT, run_id TEXT, last_verified TEXT, superseded_by TEXT, tags TEXT DEFAULT '[]',"
               " origin TEXT, created_at TEXT, updated_at TEXT)")
    db.commit()
    db.close()
    c = store.connect(str(p))
    cols = {r[1] for r in c.execute("PRAGMA table_info(facts)")}
    assert {"aliases", "questions"} <= cols
    store.upsert(c, "fact", {"statement": "old db still works", "questions": "does it?"})


def test_rebuild_reindexes_with_normalisation(kbenv):
    c = store.connect()
    store.upsert(c, "fact", {"statement": "نرخ كميسيون"})
    store.export(c)
    c2 = store.rebuild()
    assert "کمیسیون" in c2.execute("SELECT body FROM fts").fetchone()[0]


def test_reindex_rewrites_the_fts_without_touching_rows(kbenv, monkeypatch):
    monkeypatch.setenv("VECSTORE_NORMALIZE", "0")
    c = store.connect()
    store.upsert(c, "fact", {"statement": "نرخ كميسيون"})
    monkeypatch.setenv("VECSTORE_NORMALIZE", "1")
    assert store.reindex(c) == 1
    assert store.search(c, "کمیسیون")
