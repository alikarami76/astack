import store


def test_experiment_type_roundtrips_and_is_searchable(kbenv):
    c = store.connect()
    eid = store.upsert(c, "experiment", {
        "name": "Promo badge on listing page (2026-09)", "kind": "rct", "status": "running", "growthbook_id": "exp_1",
        "unit": "seller_id", "window": "2026-09-01 to 2026-09-28", "design": "50/50 seller-level split", "estimand": "ITT on seller GMV",
        "primary_metric": "GMV per seller", "result": "+1.2%", "ci": "[-0.4%, 2.8%]", "decision": "extend",
        "lessons": "seller-level split leaks through shared buyers"}, origin="t")
    assert eid.startswith("exp_")
    assert store.search(c, "badge PLP")[0]["id"] == eid
    store.export(c)
    c2 = store.rebuild()
    assert store.get(c2, eid)["growthbook_id"] == "exp_1"
    assert store.get(c2, eid)["window"] == "2026-09-01 to 2026-09-28"


def test_recall_prints_experiments_group(kbenv, capsys):
    import kb as kbcli
    c = store.connect()
    store.upsert(c, "experiment", {"name": "Daily deal auto vs manual rotation", "kind": "quasi-did",
                                   "design": "odd/even day rotation", "estimand": "effect on daily-deal GMV"})
    kbcli.main(["recall", "Daily deal rotation"])
    out = capsys.readouterr().out
    assert "## experiments" in out and "Daily deal auto vs manual rotation" in out
