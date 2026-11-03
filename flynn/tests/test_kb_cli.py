import json

import kb


def test_add_list_search_show(capsys):
    row = {"system": "sc", "name": "Chip", "description": "filter chip", "tier": "proposed"}
    assert kb.main(["add", "component", "--json", json.dumps(row)]) == 0
    eid = capsys.readouterr().out.strip()
    assert kb.main(["list", "--type", "component", "--system", "sc"]) == 0
    assert "Chip" in capsys.readouterr().out
    assert kb.main(["search", "filter", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["id"] == eid
    assert kb.main(["show", eid]) == 0
    assert json.loads(capsys.readouterr().out)["tier"] == "proposed"


def test_add_invalid_returns_1(capsys):
    assert kb.main(["add", "token", "--json", '{"system": "sc", "name": "x"}']) == 1
    assert "kind" in capsys.readouterr().err


def test_stats(capsys):
    assert kb.main(["stats"]) == 0
    assert json.loads(capsys.readouterr().out)["component"] == 0
