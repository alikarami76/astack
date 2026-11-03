from redact import redact


def test_quoted_key_password():
    assert "hunter2" not in redact('{"password": "hunter2"}')


def test_short_p_flag():
    assert "hunter2" not in redact("mysql -phunter2 -u root")
    assert "-p***" in redact("mysql -phunter2 -u root")


def test_mysql_pwd_env():
    assert "hunter2" not in redact("MYSQL_PWD=hunter2 mysqldump")


def test_db_pass_env():
    assert "hunter2" not in redact("DB_PASS=hunter2 ./run.sh")


def test_url_creds():
    out = redact("connect to mysql://root:hunter2@10.0.0.1/db")
    assert "hunter2" not in out
    assert "mysql://root:***@10.0.0.1/db" in out


def test_bearer_and_basic_kept():
    assert "Bearer ***" in redact("Authorization: Bearer sk-live-abc123")
    out = redact("Authorization: Basic dXNlcjpwYXNz")
    assert "dXNlcjpwYXNz" not in out and "Basic ***" in out


def test_plain_token_colon():
    assert "abc123" not in redact("token: abc123")
