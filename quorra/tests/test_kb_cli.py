import json

import kb
import store


def _rule(repo, area, rule, **kw):
    return {"repo": repo, "area": area, "rule": rule, "tier": "dominant", "source": "code",
            "rationale": "r", "examples": [], "counter_examples": [], "checked_at_sha": "a" * 40,
            "verified": 1} | kw


def test_list_returns_ids_tiers_and_filters_live(tmp_path, capsys):
    c = store.connect()
    a = store.upsert(c, "convention", _rule("api-app", "naming", "use camelCase"))
    b = store.upsert(c, "convention", _rule("api-app", "naming", "old style", superseded_by=a))
    store.upsert(c, "convention", _rule("admin", "naming", "other repo"))
    c.close()
    assert kb.main(["list", "--type", "convention", "--repo", "api-app", "--live", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert [r["id"] for r in rows] == [a]
    assert {"id", "repo", "area", "rule", "tier", "source", "verified", "superseded_by", "checked_at_sha"} <= set(rows[0])
    assert rows[0]["tier"] == "dominant"
    kb.main(["list", "--repo", "api-app", "--json"])
    assert {r["id"] for r in json.loads(capsys.readouterr().out)} == {a, b}
    kb.main(["list", "--area", "naming", "--json"])
    assert len(json.loads(capsys.readouterr().out)) == 3


def test_i3_add_with_tags_marks_refuted_in_use_and_status_sees_it(tmp_path, capsys):
    import research_tools as rt
    from gitfix import make_repo
    repo, sha = make_repo(tmp_path, {"a.php": "1"})
    c = store.connect()
    a = store.upsert(c, "convention", _rule("pricing", "naming", "use camelCase", checked_at_sha=sha, tags="[]"))
    s = store.upsert(c, "convention", _rule("pricing", "naming", "module rule", checked_at_sha=sha, scope="src/A/"))
    c.close()
    for row in ({"repo": "pricing", "area": "naming", "rule": "use camelCase", "tier": "dominant", "source": "code"},
                {"repo": "pricing", "scope": "src/A/", "area": "naming", "rule": "module rule", "tier": "dominant", "source": "code"}):
        assert kb.main(["add", "convention", "--json", json.dumps(row), "--tags", "refuted-in-use"]) == 0
    assert capsys.readouterr().out.split() == [a, s]  # same key row: the existing rows were updated, not duplicated
    c = store.connect()
    for i in (a, s):
        r = store.get(c, i)
        assert "refuted-in-use" in json.loads(r["tags"]) and r["verified"] == 1 and r["checked_at_sha"] == sha
    [st] = rt.status(c, {"pricing": {"path": repo, "ref": "master"}})
    assert st["refuted_in_use"] == 2 and st["suggest_rerun"] is True
