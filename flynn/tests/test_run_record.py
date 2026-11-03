import json

import pytest
import run_record
import store


def test_new_creates_folder_and_record():
    d = run_record.new("orders-page", "ui", "design the orders page", system="admin-panel",
                       mode="teammate", caller_cwd="/somewhere")
    run = json.loads((d / "run.json").read_text())
    assert run["mode"] == "teammate" and run["caller_cwd"] == "/somewhere" and run["system"] == "admin-panel"
    assert (d / "docs").is_dir() and (d / "shots").is_dir()
    assert run_record.new("orders-page", "ui", "again").name.endswith("-orders-page-2")


@pytest.mark.parametrize("kw", [dict(slug="../x"), dict(type_="essay"), dict(mode="solo")])
def test_new_rejects_bad_input(kw):
    args = dict(slug="ok", type_="ui", request="r") | kw
    with pytest.raises(ValueError):
        run_record.new(**args)


def test_append_and_close_writes_kb_row():
    d = run_record.new("deck", "deck", "q3 review deck")
    run_record.append(d, "audits", {"round": 1, "verdict": "revise"})
    run_record.append(d, "corrections", {"quote": "too busy"})
    with pytest.raises(KeyError):
        run_record.append(d, "outcome", "x")
    run = run_record.close(d, "delivered in Figma Slides")
    assert run["closed_at"]
    row = store.get(store.connect(), store.make_id("run", {"run_id": d.name}))
    assert row["corrections_count"] == 1 and "revise" in row["verdict"]


def test_cli_new_prints_path(capsys):
    assert run_record.main(["new", "x", "--type", "chart", "--request", "r", "--mode", "teammate", "--caller-cwd", "/c"]) == 0
    d = capsys.readouterr().out.strip()
    assert json.loads(open(f"{d}/run.json").read())["type"] == "chart"
