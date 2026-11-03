import pytest

import safety


@pytest.mark.parametrize("url,env", [
    ("http://localhost:3333/pwa", "local"), ("127.0.0.1:8787", "local"),
    ("https://demo-api.example.com/x", "demo"), ("https://demo.example.com", "demo"),
    ("https://stage.example.com", "stage"), ("https://backend-api.example.com", "test"),
    ("https://seller.example.com", "production"), ("https://api.example.com", "production"),
    ("https://example.com", "production"),
])
def test_classify_host(url, env):
    assert safety.classify_host(url) == env


def test_targets():
    assert safety.check_target("http://localhost:3333", "load") == "local"
    assert safety.check_target("https://demo-api.example.com", "write") == "demo"
    with pytest.raises(safety.NeedsApproval):
        safety.check_target("https://demo-api.example.com", "load")
    assert safety.check_target("https://demo-api.example.com", "load", "the user: yes, 10 minutes") == "demo"
    with pytest.raises(safety.Refused):
        safety.check_target("https://seller.example.com", "write", "approved")
    with pytest.raises(safety.NeedsApproval):
        safety.check_target("https://seller.example.com", "read")


def test_never_writes_in_dk_code(tmp_path):
    with pytest.raises(safety.Refused):
        safety.check_writable(tmp_path / "code" / "web-app" / "x.txt")
    assert safety.check_writable(tmp_path / "work" / "x.txt")


@pytest.mark.parametrize("name,secret", [(".env", True), (".env.development", True), (".npmrc", True),
                                         ("auth-state.json", True), ("auth-state.example.json", False),
                                         ("report.md", False), ("secrets/x.txt", True)])
def test_secret_paths(name, secret):
    assert safety.is_secret_path(name) is secret


def test_redact():
    text = ('Authorization: Bearer abc.def.ghi\ncookie: sid=123; x=1\n{"token": "s3cr3t", "password": "p"}\n'
            'phone 09121234567 and 989121234567 mail jane.doe@example.com '
            'jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijk')
    out = safety.redact(text)
    for s in ("abc.def.ghi", "sid=123", "s3cr3t", '"p"', "1234567", "jane.doe@", "eyJhbGciOiJIUzI1NiJ9"):
        assert s not in out, s
    assert "0912*****67" in out


@pytest.mark.parametrize("sql,ok", [
    ("SELECT * FROM t WHERE id = 1", True), ("with a as (select 1) select * from a;", True),
    ("EXPLAIN SELECT 1", True), ("UPDATE t SET x = 1", False), ("SELECT 1; DROP TABLE t", False),
    ("SELECT * FROM t FOR UPDATE", False), ("SELECT * INTO OUTFILE '/tmp/x' FROM t", False),
    ("-- delete everything\nSELECT 'delete' FROM t", True), ("", False), ("SELECT SLEEP(10)", False),
])
def test_sql_readonly(sql, ok):
    assert safety.sql_is_readonly(sql)[0] is ok


def test_cli_exit_codes(tmp_path):
    assert safety.main(["target", "https://seller.example.com", "--purpose", "write"]) == 4
    assert safety.main(["target", "https://demo.example.com", "--purpose", "load"]) == 3
    assert safety.main(["target", "http://localhost", "--purpose", "load"]) == 0
    f = tmp_path / ".env"
    f.write_text("A=1")
    assert safety.main(["redact", str(f)]) == 4
