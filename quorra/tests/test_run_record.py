from pathlib import Path
import json

import run_record
import store


def test_new_append_close(tmp_path, monkeypatch):
    monkeypatch.setenv("QUORRA_KB_DB", str(tmp_path / "kb.sqlite"))
    monkeypatch.setenv("QUORRA_KB_EXPORT", str(tmp_path / "export"))
    d = run_record.new("research-api-app", "research", "research api-app conventions", ["api-app"], runs_root=tmp_path)
    d2 = run_record.new("research-api-app", "research", "again", ["api-app"], runs_root=tmp_path)
    assert d2.name.endswith("-2")
    run_record.append(d, "subagents", {"name": "quorra-scout", "model": "haiku", "purpose": "naming", "tokens": 12000})
    run_record.append(d, "questions", {"q": "include tests-legacy?", "answer": "no"})
    run = run_record.close(d, "12 rules verified")
    assert run["outcome"] == "12 rules verified" and run["subagents"][0]["model"] == "haiku"
    assert json.loads((d / "run.json").read_text())["closed_at"]
    assert store.stats(store.connect())["run"] == 1


def test_append_rejects_unknown_field(tmp_path):
    d = run_record.new("x", "lookup", "q", [], runs_root=tmp_path)
    import pytest
    with pytest.raises(KeyError):
        run_record.append(d, "nonsense", {})


def test_verify_result_is_last_entry_or_none(tmp_path):
    d = run_record.new("v", "lookup", "q", [], runs_root=tmp_path)
    run_record.close(d, "none")
    c = store.connect()
    assert c.execute("SELECT verify_result FROM runs_index").fetchone()[0] == "none"
    d = run_record.new("v2", "lookup", "q", [], runs_root=tmp_path)
    run_record.append(d, "verification", {"claim": "a", "ok": False})
    run_record.append(d, "verification", {"claim": "b", "ok": True})
    run_record.close(d, "done")
    got = c.execute("SELECT verify_result FROM runs_index WHERE run_id = ?", (d.name,)).fetchone()[0]
    assert json.loads(got) == {"claim": "b", "ok": True}


def test_new_rejects_path_slugs(tmp_path):
    import pytest
    for bad in ("a/b", "..", "x\\y", "a..b"):
        with pytest.raises(ValueError):
            run_record.new(bad, "lookup", "q", [], runs_root=tmp_path)


def test_new_records_mode_and_caller_cwd(tmp_path):
    d = run_record.new("m", "lookup", "q", [], runs_root=tmp_path)
    run = json.loads((d / "run.json").read_text())
    assert run["mode"] == "direct" and run["caller_cwd"] is None
    d = run_record.new("m2", "lookup", "q", [], runs_root=tmp_path, mode="teammate", caller_cwd="/some/where")
    run = json.loads((d / "run.json").read_text())
    assert run["mode"] == "teammate" and run["caller_cwd"] == "/some/where"


def test_cli_accepts_mode_and_caller_cwd(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(run_record.qpaths, "runs_dir", lambda: tmp_path)
    assert run_record.main(["new", "cli", "--type", "lookup", "--request", "q",
                            "--mode", "teammate", "--caller-cwd", "/x y"]) == 0
    d = Path(capsys.readouterr().out.strip())
    run = json.loads((d / "run.json").read_text())
    assert run["mode"] == "teammate" and run["caller_cwd"] == "/x y"


def test_new_rejects_invalid_mode(tmp_path):
    import pytest
    with pytest.raises(ValueError):
        run_record.new("bad-mode", "lookup", "q", [], runs_root=tmp_path, mode="nonsense")
