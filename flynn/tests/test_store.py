import json

import pytest
import store

TOKEN = {"system": "admin-panel", "name": "color.action.primary", "kind": "color", "level": "semantic",
         "value": "#0F766E", "tier": "verified", "source": "figma"}
BUTTON = {"system": "admin-panel", "name": "Button", "description": "دکمه اصلی، primary action trigger",
          "variants": {"size": ["sm", "md"]}, "props": {"label": {"type": "string", "required": True}},
          "states": ["default", "hover", "disabled"], "tier": "verified"}


def test_upsert_is_idempotent_and_merges():
    c = store.connect()
    a = store.upsert(c, "token", TOKEN, origin="test")
    b = store.upsert(c, "token", {**TOKEN, "notes": "brand teal"}, origin="test")
    assert a == b and a.startswith("tok_")
    row = store.get(c, a)
    assert row["value"] == "#0F766E" and row["notes"] == "brand teal" and row["type"] == "token"


@pytest.mark.parametrize("etype,row,msg", [
    ("token", {**TOKEN, "tier": "great"}, "tier"),
    ("token", {**TOKEN, "kind": "smell"}, "kind"),
    ("token", {k: v for k, v in TOKEN.items() if k != "name"}, "natural key"),
    ("component", {**BUTTON, "variants": {"size": "sm"}}, "variants"),
    ("component", {**BUTTON, "props": ["label"]}, "props"),
    ("reference", {"source_url": "https://x", "status": "maybe"}, "status"),
    ("nope", {"a": 1}, "unknown type"),
])
def test_validation_rejects_bad_rows(etype, row, msg):
    with pytest.raises(ValueError, match=msg):
        store.upsert(store.connect(), etype, row)


def test_components_parses_json_columns_and_token_names():
    c = store.connect()
    store.upsert(c, "component", BUTTON)
    store.upsert(c, "token", TOKEN)
    comps = store.components(c, "admin-panel")
    assert comps["Button"]["variants"] == {"size": ["sm", "md"]}
    assert comps["Button"]["props"]["label"]["required"] is True
    assert store.token_names(c, "admin-panel") == {"color.action.primary"}
    assert store.components(c, "other") == {} and store.token_names(c, "other") == set()


def test_search_finds_persian_and_english_words():
    c = store.connect()
    store.upsert(c, "component", BUTTON)
    assert [r["title"] for r in store.search(c, "دکمه")] == ["Button"]
    assert store.search(c, "primary action", etype="component")[0]["type"] == "component"


def test_export_rebuild_roundtrip(tmp_path):
    c = store.connect()
    eid = store.upsert(c, "component", BUTTON)
    store.export(c)
    c2 = store.rebuild(str(tmp_path / "fresh.sqlite"))
    assert store.get(c2, eid)["name"] == "Button"


def test_export_refuses_to_drop_rows():
    c = store.connect()
    store.upsert(c, "lesson", {"rule": "always show empty states", "why": "the user"})
    store.export(c)
    d = store.export_dir()
    with (d / "lesson.jsonl").open("a") as f:
        f.write(json.dumps({"id": "les_ghost", "rule": "x"}) + "\n")
    with pytest.raises(store.ExportRefused):
        store.export(c)
