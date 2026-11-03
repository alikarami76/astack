import json

import pytest

from vecstore import cli


@pytest.fixture
def reg(tmp_path, monkeypatch, vhome):
    root = tmp_path / "notes"
    root.mkdir()
    (root / "a.md").write_text("## Buybox\nwinner share per seller\n")
    (tmp_path / "eval").mkdir()
    (tmp_path / "eval" / "recall_set.jsonl").write_text('{"query": "winner", "expected": ["a.md#buybox"]}\n')
    adapter = tmp_path / "toy_adapter.py"
    adapter.write_text(
        "from pathlib import Path\n"
        "from vecstore.mdstore import MdStore\n"
        f"HERE = Path({str(tmp_path)!r})\n"
        "def make():\n"
        "    return MdStore('toy', HERE / 'notes', HERE / 'toy.vectors.sqlite', 'Instruct: t\\nQuery: ',"
        " eval_dir=HERE / 'eval')\n")
    toml = tmp_path / "stores.toml"
    toml.write_text(f'[toy]\nadapter = "{adapter}"\n')
    monkeypatch.setenv("VECSTORE_REGISTRY", str(toml))
    monkeypatch.setenv("VECSTORE_FAKE_MODEL", "1")
    monkeypatch.setenv("VECSTORE_IDLE_MIN", "0.2")
    import sys
    monkeypatch.setenv("VECSTORE_PYTHON", sys.executable)
    return tmp_path


def test_unknown_store_lists_known(reg, capsys):
    assert cli.main(["search", "nope", "x"]) == 1
    assert "toy" in capsys.readouterr().err


def test_index_search_eval_status(reg, capsys):
    assert cli.main(["index", "toy", "--quiet"]) == 0
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["embedded"] == 1
    assert cli.main(["search", "toy", "winner", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["id"] == "a.md#buybox"
    assert cli.main(["eval", "toy", "--save-baseline"]) == 0
    assert (reg / "eval" / "baseline.json").exists()
    capsys.readouterr()
    assert cli.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "toy" in out and "vectors=1" in out and "auto=hybrid" in out
    assert cli.main(["daemon", "stop"]) == 0


def test_missing_adapter_file_names_the_path(reg, monkeypatch, capsys):
    toml = reg / "stores.toml"
    toml.write_text('[broken]\nadapter = "/nonexistent/path/adapter.py"\n')
    monkeypatch.setenv("VECSTORE_REGISTRY", str(toml))
    assert cli.main(["search", "broken", "x"]) == 1
    err = capsys.readouterr().err
    assert "adapter file not found" in err
    assert "/nonexistent/path/adapter.py" in err


def test_status_reports_broken_store_and_keeps_going(reg, monkeypatch, capsys):
    toml = reg / "stores.toml"
    toml.write_text(
        f'[toy]\nadapter = "{reg / "toy_adapter.py"}"\n'
        '[broken]\nadapter = "/nonexistent/path/adapter.py"\n'
    )
    monkeypatch.setenv("VECSTORE_REGISTRY", str(toml))
    assert cli.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "toy:" in out and "vectors=" in out
    assert "broken: error" in out


def test_bad_registry_is_one_line_error(reg, monkeypatch, capsys):
    toml = reg / "stores.toml"
    toml.write_text("this is [[ not valid toml")
    monkeypatch.setenv("VECSTORE_REGISTRY", str(toml))
    assert cli.main(["search", "toy", "x"]) == 1
    err = capsys.readouterr().err
    assert "cannot read registry" in err
    assert "Traceback" not in err  # no traceback, just one error line


def test_eval_check_without_baseline_fails(reg, capsys):
    assert cli.main(["eval", "toy", "--check"]) == 1
