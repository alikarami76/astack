import json
import sys
import store
import writethrough


def fake_tool(tmp_path):
    log = tmp_path / "calls.jsonl"
    tool = tmp_path / "memory_tool.py"
    tool.write_text(
        "import sys, json\n"
        f"open({str(log)!r}, 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n"
    )
    return tool, log


def test_mysql_fact_goes_to_dba_note(kbenv, tmp_path, monkeypatch):
    tool, log = fake_tool(tmp_path)
    monkeypatch.setenv("CLU_WRITETHROUGH", "1")
    monkeypatch.setenv("CLU_DBA_MEMORY_TOOL", str(tool))
    c = store.connect()
    sid = store.upsert(c, "source", {"kind": "mysql", "engine": "warehouse", "database": "warehouse", "table_name": "orders"})
    fid = store.upsert(c, "fact", {"statement": "cancelled_test is abandoned checkout noise", "source_id": sid, "confidence": "verified", "evidence": "q"})
    acts = writethrough.mirror(c, "fact", fid)
    call = json.loads(log.read_text().splitlines()[0])
    assert call[:5] == ["note", "--engine", "warehouse", "--table", "orders"]
    assert "Verified facts" in call and acts == ["dba:note warehouse.orders"]


def test_metabase_fact_appends_to_scales_tree(kbenv, tmp_path, monkeypatch):
    mem = tmp_path / "scales"
    monkeypatch.setenv("CLU_WRITETHROUGH", "1")
    monkeypatch.setenv("CLU_SCALES_MEMORY", str(mem))
    c = store.connect()
    sid = store.upsert(c, "source", {"kind": "metabase", "database": "Analytics", "schema_name": "mv", "table_name": "daily_orders"})
    fid = store.upsert(c, "fact", {"statement": "runs 0.5-2.8% below MySQL", "source_id": sid, "confidence": "observed"})
    writethrough.mirror(c, "fact", fid)
    text = (mem / "Analytics" / "mv" / "tables" / "daily_orders.md").read_text()
    assert "## Knowledge" in text and "runs 0.5-2.8% below MySQL" in text


def test_metabase_fact_skips_duplicate_statement(kbenv, tmp_path, monkeypatch):
    mem = tmp_path / "scales"
    monkeypatch.setenv("CLU_WRITETHROUGH", "1")
    monkeypatch.setenv("CLU_SCALES_MEMORY", str(mem))
    c = store.connect()
    sid = store.upsert(c, "source", {"kind": "metabase", "database": "Analytics", "schema_name": "mv", "table_name": "daily_orders"})
    fid = store.upsert(c, "fact", {"statement": "runs 0.5-2.8% below MySQL", "source_id": sid, "confidence": "observed"})
    acts1 = writethrough.mirror(c, "fact", fid)
    p = mem / "Analytics" / "mv" / "tables" / "daily_orders.md"
    lines_after_first = p.read_text().count("runs 0.5-2.8% below MySQL")
    acts2 = writethrough.mirror(c, "fact", fid)
    lines_after_second = p.read_text().count("runs 0.5-2.8% below MySQL")
    assert acts1 == [f"scales:append Analytics/mv/daily_orders"]
    assert acts2 == []
    assert lines_after_first == 1 and lines_after_second == 1


def test_disabled_and_failures_never_raise(kbenv, tmp_path, monkeypatch):
    c = store.connect()
    fid = store.upsert(c, "fact", {"statement": "no source"})
    assert writethrough.mirror(c, "fact", fid) == []
    monkeypatch.setenv("CLU_WRITETHROUGH", "1")
    monkeypatch.setenv("CLU_DBA_MEMORY_TOOL", str(tmp_path / "missing.py"))
    sid = store.upsert(c, "source", {"kind": "mysql", "engine": "warehouse", "database": "warehouse", "table_name": "x"})
    fid2 = store.upsert(c, "fact", {"statement": "y", "source_id": sid})
    assert writethrough.mirror(c, "fact", fid2) == []


def test_cli_add_and_search(kbenv, capsys):
    import kb
    kb.main(["add", "metric", "--json", json.dumps({"name": "AOV", "definition": "total_paid per order"}), "--tags", "sales"])
    eid = capsys.readouterr().out.strip()
    assert eid.startswith("met_")
    kb.main(["search", "AOV", "--json"])
    assert json.loads(capsys.readouterr().out)[0]["id"] == eid
    assert (kbenv / "export" / "metric.jsonl").exists()


def test_cli_add_empty_natural_key_exits_1_with_stderr_message(kbenv, capsys):
    import kb
    rc = kb.main(["add", "lesson", "--json", json.dumps({"rule": ""})])
    out, err = capsys.readouterr()
    assert rc == 1
    assert out == ""
    assert "empty natural key for lesson" in err


def test_mysql_recipe_goes_to_dba_recipe(kbenv, tmp_path, monkeypatch):
    tool, log = fake_tool(tmp_path)
    monkeypatch.setenv("CLU_WRITETHROUGH", "1")
    monkeypatch.setenv("CLU_DBA_MEMORY_TOOL", str(tool))
    c = store.connect()
    sid = store.upsert(c, "source", {"kind": "mysql", "engine": "warehouse", "database": "warehouse", "table_name": "orders"})
    rid = store.upsert(c, "recipe", {"name": "Daily Orders Report", "purpose": "summarize orders", "source_id": sid, "sql": "SELECT * FROM orders"})
    acts = writethrough.mirror(c, "recipe", rid)
    call = json.loads(log.read_text().splitlines()[0])
    assert call[:2] == ["recipe", "--slug"]
    assert "--engines" in call and "warehouse" in call
    assert "--tables" in call and "orders" in call
    assert acts == ["dba:recipe daily-orders-report"]


def test_stub_retry_path(kbenv, tmp_path, monkeypatch):
    # Create a tool that fails once then succeeds
    counter = tmp_path / "counter.txt"
    counter.write_text("0")
    log = tmp_path / "calls.jsonl"
    tool = tmp_path / "memory_tool.py"
    tool.write_text(
        "import sys, json\n"
        f"counter_file = {str(counter)!r}\n"
        "count = int(open(counter_file).read().strip() or '0')\n"
        "open(counter_file, 'w').write(str(count + 1))\n"
        f"open({str(log)!r}, 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "if sys.argv[1] == 'note' and count == 0:\n"
        "    sys.exit(1)\n"
        "sys.exit(0)\n"
    )
    monkeypatch.setenv("CLU_WRITETHROUGH", "1")
    monkeypatch.setenv("CLU_DBA_MEMORY_TOOL", str(tool))
    c = store.connect()
    sid = store.upsert(c, "source", {"kind": "mysql", "engine": "warehouse", "database": "warehouse", "table_name": "orders"})
    fid = store.upsert(c, "fact", {"statement": "test fact", "source_id": sid})
    acts = writethrough.mirror(c, "fact", fid)
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert len(calls) == 3
    assert calls[0][0] == "note"
    assert calls[1][0] == "stub"
    assert calls[2][0] == "note"
    assert acts == ["dba:note warehouse.orders"]


def test_stderr_password_redacted_in_log(kbenv, tmp_path, monkeypatch):
    log = tmp_path / "calls.jsonl"
    tool = tmp_path / "memory_tool.py"
    tool.write_text(
        "import sys\n"
        "sys.stderr.write('ERROR: password=hunter2 and token=secret123\\n')\n"
        "sys.exit(1)\n"
    )
    monkeypatch.setenv("CLU_WRITETHROUGH", "1")
    monkeypatch.setenv("CLU_DBA_MEMORY_TOOL", str(tool))
    c = store.connect()
    sid = store.upsert(c, "source", {"kind": "mysql", "engine": "warehouse", "database": "warehouse", "table_name": "orders"})
    fid = store.upsert(c, "fact", {"statement": "test", "source_id": sid})
    acts = writethrough.mirror(c, "fact", fid)
    assert acts == []
    log_path = tmp_path / "kb.sqlite".replace("kb.sqlite", "writethrough.log")
    # Find writethrough.log in the kbenv directory
    from pathlib import Path
    import os
    log_file = Path(os.environ.get("CLU_KB_DB")).parent / "writethrough.log"
    log_contents = log_file.read_text()
    assert "password" not in log_contents or "***" in log_contents
    assert "hunter2" not in log_contents
    assert "token" not in log_contents or "***" in log_contents
    assert "secret123" not in log_contents


def test_stderr_redaction_forms_in_log(kbenv, tmp_path, monkeypatch):
    cases = [
        ('{"password": "hunter2"}', "hunter2"),
        ("mysql -phunter2 -u root", "hunter2"),
        ("MYSQL_PWD=hunter2 mysqldump", "hunter2"),
        ("DB_PASS=hunter2 ./run.sh", "hunter2"),
        ("connect to mysql://root:hunter2@10.0.0.1/db", "hunter2"),
    ]
    import os
    from pathlib import Path
    log_file = Path(os.environ.get("CLU_KB_DB")).parent / "writethrough.log"
    for i, (text, secret) in enumerate(cases):
        tool = tmp_path / f"memory_tool_{i}.py"
        tool.write_text(
            "import sys\n"
            f"sys.stderr.write({text!r} + '\\n')\n"
            "sys.exit(1)\n"
        )
        monkeypatch.setenv("CLU_WRITETHROUGH", "1")
        monkeypatch.setenv("CLU_DBA_MEMORY_TOOL", str(tool))
        c = store.connect()
        sid = store.upsert(c, "source", {"kind": "mysql", "engine": "warehouse", "database": "warehouse", "table_name": f"t{i}"})
        fid = store.upsert(c, "fact", {"statement": f"test {i}", "source_id": sid})
        acts = writethrough.mirror(c, "fact", fid)
        assert acts == []
    log_contents = log_file.read_text()
    for text, secret in cases:
        assert secret not in log_contents, f"{secret!r} leaked (from {text!r})"


def test_stderr_bearer_token_redacted_in_log(kbenv, tmp_path, monkeypatch):
    tool = tmp_path / "memory_tool.py"
    tool.write_text(
        "import sys\n"
        "sys.stderr.write('Authorization: Bearer sk-live-abc123\\n')\n"
        "sys.exit(1)\n"
    )
    monkeypatch.setenv("CLU_WRITETHROUGH", "1")
    monkeypatch.setenv("CLU_DBA_MEMORY_TOOL", str(tool))
    c = store.connect()
    sid = store.upsert(c, "source", {"kind": "mysql", "engine": "warehouse", "database": "warehouse", "table_name": "orders"})
    fid = store.upsert(c, "fact", {"statement": "test", "source_id": sid})
    acts = writethrough.mirror(c, "fact", fid)
    assert acts == []
    from pathlib import Path
    import os
    log_file = Path(os.environ.get("CLU_KB_DB")).parent / "writethrough.log"
    log_contents = log_file.read_text()
    assert "sk-live-abc123" not in log_contents
    assert "Bearer ***" in log_contents


def test_cli_add_many_and_brief_search(kbenv, tmp_path, capsys):
    import kb
    f = tmp_path / "batch.json"
    f.write_text(json.dumps([
        {"type": "fact", "row": {"statement": "roster grew to 1,066 on 2026-09-15"}, "tags": ["ai"]},
        {"type": "lesson", "row": {"rule": ""}},
        {"type": "metric", "row": {"name": "AI adoption", "definition": "successful sellers / entitled"}},
    ]))
    assert kb.main(["add-many", "--file", str(f), "--origin", "run:r1"]) == 1  # good rows written, skip reported
    captured = capsys.readouterr()
    ids = json.loads(captured.out)
    assert len(ids) == 2 and "skipped item" in captured.err
    kb.main(["search", "AI adoption", "--brief"])
    line = capsys.readouterr().out.splitlines()[0]
    assert line.startswith("met_") and "—" not in line
