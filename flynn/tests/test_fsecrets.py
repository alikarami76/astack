"""fsecrets: env first, then a secrets folder, then the macOS keychain (never touched here)."""
import stat

import pytest

import fsecrets


@pytest.fixture
def no_keychain(monkeypatch):
    calls = []
    monkeypatch.setattr(fsecrets, "_keychain_get", lambda a: calls.append(("get", a)) or "from-keychain")
    monkeypatch.setattr(fsecrets, "_keychain_set", lambda a, v: calls.append(("set", a)))
    monkeypatch.delenv("FLYNN_SECRETS_DIR", raising=False)
    monkeypatch.delenv("FLYNN_SECRET_MCP_KEY", raising=False)
    return calls


def test_env_wins(monkeypatch, no_keychain):
    monkeypatch.setenv("FLYNN_SECRET_MCP_KEY", "from-env")
    assert fsecrets.get("mcp-key") == "from-env" and no_keychain == []


def test_folder_then_keychain(tmp_path, monkeypatch, no_keychain):
    monkeypatch.setenv("FLYNN_SECRETS_DIR", str(tmp_path / "secrets"))
    assert fsecrets.get("mcp-key") == "from-keychain"               # nothing in the folder yet
    fsecrets.set("mcp-key", "s3cret\n")
    f = tmp_path / "secrets" / "mcp-key"
    assert f.read_text() == "s3cret\n" and stat.S_IMODE(f.stat().st_mode) == 0o600
    assert fsecrets.get("mcp-key") == "s3cret"
    assert ("set", "mcp-key") not in no_keychain


def test_set_without_a_folder_uses_the_keychain(no_keychain):
    fsecrets.set("password", "x")
    assert no_keychain == [("set", "password")]


def test_account_names_are_checked(no_keychain):
    with pytest.raises(ValueError):
        fsecrets.get("../etc/passwd")
