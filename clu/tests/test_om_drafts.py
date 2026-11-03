import json

import pytest

import om_drafts

SEM = '\n\n[semantics]\n{"semanticRole":"dimension_key","cardinalityClass":"high","canFilter":false,"canBreakdown":false}'


@pytest.fixture
def snap(tmp_path, monkeypatch):
    s = tmp_path / "om" / "2026-10-10"
    s.mkdir(parents=True)
    tables = [{"fullyQualifiedName": "bi.DW.sales.dim_promotion", "name": "dim_promotion",
               "description": "Promotions and campaigns. Use it to name a campaign.",
               "extension": {"querySource": "DW.sales.dim_promotion", "grainKey": "promotion_id",
                             "grain": "one promotion", "shape": "transaction"},
               "tags": [{"tagFQN": "AgentAccess.Readable"}, {"tagFQN": "Tier.Silver"}],
               "columns": [{"name": "promotion_id", "dataType": "BIGINT", "description": "Identity key of promotion." + SEM,
                            "tags": [{"tagFQN": "Semantic.dimension_key"}]}]},
              {"fullyQualifiedName": "bi.DW.general.dim_site", "name": "dim_site", "description": "Sites.",
               "extension": {"querySource": "q", "grainKey": "site_id", "grain": "one site"}, "columns": []}]
    metrics = [{"name": "sales_rrp_price", "fullyQualifiedName": "sales_rrp_price", "metricType": "AVERAGE",
                "unitOfMeasurement": "OTHER", "description": "Average price before discounts.",
                "extension": {"metricType": "base", "unit": "IRR", "defaultTimeDimension": "Conformed.dim_date",
                              "aggregation": "non_additive", "semanticRole": "driver", "baseTable": "x"},
                "tags": [{"tagFQN": "Metric.Base"}, {"tagFQN": "AgentAccess.Readable"}]}]
    terms = [{"name": "List Price", "fullyQualifiedName": "Shop.List Price", "description": "Price before discounts.",
              "synonyms": ["rrp", "قیمت قبل از تخفیف"], "tags": [{"tagFQN": "GlossaryKind.metric"}]}]
    tags = [{"fullyQualifiedName": x} for x in ("AgentAccess.Readable", "Tier.Silver", "Semantic.dimension_key",
                                                "Metric.Base", "GlossaryKind.metric", "Domain.Sales")]
    for n, rows in (("table", tables), ("metric", metrics), ("glossaryTerm", terms), ("tag", tags), ("domain", [])):
        (s / f"{n}.json").write_text(json.dumps(rows, ensure_ascii=False))
    (tmp_path / "om" / "state.json").write_text(json.dumps({"snapshot": str(s), "pulled_at": "2026-10-10T10:00:00"}))
    monkeypatch.setenv("CLU_OM_DIR", str(tmp_path / "om"))
    root = tmp_path / "kb-om"
    monkeypatch.setenv("CLU_OM_DRAFTS", str(root))
    return root


def good_table():
    return {"kind": "table", "status": "draft", "target_fqn": "shop_mysql.default.shop.item_prices",
            "proposed": ["Domain.Pricing", "service shop_mysql"], "evidence": ["features/x.md"],
            "entity": {"name": "item_prices", "description": "Every price a variant has had. Use it to read the live price.",
                       "extension": {"querySource": "shop.item_prices", "grainKey": "id", "grain": "one price row"},
                       "tags": ["AgentAccess.Readable", "Domain.Pricing"],
                       "columns": [{"name": "id", "dataType": "BIGINT", "description": "Identity key of the price row.",
                                    "semantics": {"semanticRole": "dimension_key", "cardinalityClass": "high",
                                                  "canFilter": False, "canBreakdown": False},
                                    "tags": ["Semantic.dimension_key"]}]}}


def test_conventions_derived_from_snapshot(snap):
    conv = om_drafts.conventions()
    assert "dimension_key" in conv["column_semantics"]["semanticRole"]
    assert conv["table_extension_keys"] >= ["grain", "grainKey", "querySource"] and "transaction" in conv["table_extension_values"]["shape"]
    assert conv["metric_extension_values"]["aggregation"] == ["non_additive"] and "AVERAGE" in conv["metricType"]
    assert "Tier.Silver" in conv["tags"]
    assert (snap / "CONVENTIONS.md").exists() and (snap / "conventions.json").exists()
    assert "semanticRole" in (snap / "CONVENTIONS.md").read_text()


def test_validate_good_table_passes(snap):
    om_drafts.conventions()
    errs, warns = om_drafts.validate(good_table())
    assert errs == []


def test_validate_catches_bad_values(snap):
    om_drafts.conventions()
    d = good_table()
    d["entity"]["columns"][0]["semantics"]["semanticRole"] = "primary"
    d["entity"]["tags"].append("Tier.Platinum")
    d["entity"]["extension"].pop("grain")
    d["entity"]["description"] = "TODO"
    errs, _ = om_drafts.validate(d)
    text = " | ".join(errs)
    assert "semanticRole" in text and "Tier.Platinum" in text and "grain" in text and "description" in text


def test_validate_metric_and_term(snap):
    om_drafts.conventions()
    m = {"kind": "metric", "status": "draft", "target_fqn": "clearance_share", "proposed": ["Domain.Pricing"],
         "entity": {"name": "clearance_share", "displayName": "Clearance Share", "metricType": "RATIO",
                    "unitOfMeasurement": "PERCENTAGE", "description": "Share of NMV sold under an Clearance offer.",
                    "metricExpression": {"code": "SUM(x)/SUM(y)", "language": "SQL"},
                    "extension": {"metricType": "derived", "unit": "%", "defaultTimeDimension": "Conformed.dim_date",
                                  "aggregation": "non_additive", "semanticRole": "outcome"},
                    "tags": ["AgentAccess.Readable", "Domain.Pricing", "Metric.Base"]}}
    errs, _ = om_drafts.validate(m)
    assert any("metricType RATIO" in e for e in errs)  # not seen in this snapshot
    assert any("outcome" in e for e in errs)
    t = {"kind": "glossaryTerm", "status": "draft", "target_fqn": "Shop.Clearance",
         "entity": {"glossary": "Shop", "name": "Clearance", "description": "The flagship time-boxed discount offer.",
                    "synonyms": ["incredible offer"], "tags": ["GlossaryKind.metric"]}}
    errs, _ = om_drafts.validate(t)
    assert any("Farsi" in e for e in errs)


def test_payload_merges_semantics_and_tags(snap):
    p = om_drafts.payload(good_table())
    col = p["columns"][0]
    assert col["description"].startswith("Identity key of the price row.\n\n[semantics]\n{")
    assert json.loads(col["description"].split("[semantics]\n", 1)[1])["semanticRole"] == "dimension_key"
    assert p["tags"][0] == {"tagFQN": "AgentAccess.Readable"} and "semantics" not in col


def test_save_render_and_status(snap):
    om_drafts.conventions()
    path = om_drafts.save(good_table())
    assert path == snap / "tables" / "item_prices.json"
    md = (snap / "tables" / "item_prices.md").read_text()
    assert "item_prices" in md and "dimension_key" in md and "Domain.Pricing" in md
    d = good_table()
    d["entity"]["name"] = "dim_promotion"
    d["target_fqn"] = "bi.DW.sales.dim_promotion"
    om_drafts.save(d)
    rows = om_drafts.status()
    st = {r["name"]: r["status"] for r in rows}
    assert st == {"dim_promotion": "loaded", "item_prices": "draft"}
    assert "loaded" in (snap / "STATUS.md").read_text()


def test_new_from_kb_prefills_columns(snap, kbenv):
    import store
    c = store.connect()
    sid = store.upsert(c, "source", {"kind": "mysql", "engine": "shop", "database": "shop",
                                     "table_name": "item_prices", "notes": "one row per price"})
    store.upsert(c, "column", {"source_id": sid, "name": "selling_price", "type": "bigint", "meaning": "price, Rial"})
    d = om_drafts.new("table", "item_prices", target="shop_mysql.default.shop.item_prices", from_kb=[sid])
    assert d["entity"]["columns"][0]["name"] == "selling_price" and d["entity"]["columns"][0]["dataType"] == "BIGINT"
    assert "price, Rial" in d["entity"]["columns"][0]["description"]
    assert d["evidence"] == [f"clu:{sid}"]
