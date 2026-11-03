import json

import plan


def fake_map(tmp_path):
    d = tmp_path / "scouts"
    d.mkdir()
    (d / "x.json").write_text(json.dumps({"features": [{
        "name": "Deal", "name_fa": "معامله", "description": "d", "screens": ["/deal"], "apis": ["GET /deal"],
        "business_rules": ["Discount must end with 00 and price at least 1,000 rials", "Shows a badge"],
        "evidence": ["src/pages/deal/constants.ts:3"], "tier": "code-only"}],
        "flows": [{"name": "Do deal", "screens": ["/deal"], "edge_cases": ["Wallet empty: request not entered"]}]}))
    return d


def test_score_levels():
    assert plan.score("price after discount must end with 00, at least 1000")["level"] == "high"
    assert plan.score("Shows a badge")["level"] == "low"


def test_extract_plan_charters_regress(tmp_path):
    d = fake_map(tmp_path)
    reqs = plan.extract_feature("deal", d)
    assert [r["id"] for r in reqs["requirements"]] == ["R1", "R2", "R3"]
    assert reqs["requirements"][2]["kind"] == "edge-case"
    text = plan.render_plan(reqs)
    assert "## Risks, highest first" in text and "| R1 |" in text and "Never production" in text
    assert "## C1 (R1)" in plan.render_charters(reqs)
    suites = [{"feature": "Deal", "cases": [{"id": "D1"}, {"id": "D2", "status": "retired"}]},
              {"feature": "Other", "cases": [{"id": "O1", "tags": ["smoke"]}, {"id": "O2"}]}]
    sel = plan.regress(["src/pages/deal/hooks/useDeal.ts", "README.md"], suites, d)
    assert sel["selected_cases"] == ["D1"] and sel["smoke_cases"] == ["O1"] and "Deal" in sel["touched_features"]


def test_extract_prd(tmp_path):
    prd = tmp_path / "prd.md"
    prd.write_text("# PRD\n- FR-1: The seller can set a discount between 5 and 50 percent\n- short\n"
                   "1. The voucher code must be unique per seller\n")
    reqs = plan.extract_prd(str(prd), "Voucher")
    assert [r["id"] for r in reqs["requirements"]] == ["FR-1", "R2"]
