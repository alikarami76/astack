import json
import pathlib
import stat
import sys

import pytest

FLYNN = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FLYNN / "adapters/penpot/panel"))
import accounts_api as acc  # noqa: E402


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYNN_HOME", str(tmp_path))
    return tmp_path


def test_unknown_email_does_not_have_to_change():
    assert acc.me("Nobody@example.org") == {"email": "nobody@example.org", "must_change_password": False}


def test_mark_then_me_is_case_insensitive():
    assert acc.mark_must_change(["Maya@Example.org", " sam@example.org "]) == ["maya@example.org", "sam@example.org"]
    assert acc.me("MAYA@example.org")["must_change_password"] is True
    assert acc.me("sam@example.org")["must_change_password"] is True


def test_password_changed_clears_only_that_account():
    acc.mark_must_change(["a@x.com", "b@x.com"])
    assert acc.password_changed("A@x.com") is None
    assert acc.me("a@x.com")["must_change_password"] is False
    assert acc.me("b@x.com")["must_change_password"] is True


def test_password_changed_for_unknown_email_is_harmless(home):
    assert acc.password_changed("ghost@x.com") is None
    assert acc.password_changed("") is None
    assert not (home / "accounts.json").exists()


def test_marking_again_forces_a_new_change():
    acc.mark_must_change("a@x.com")
    acc.password_changed("a@x.com")
    acc.mark_must_change(["a@x.com"])
    assert acc.me("a@x.com")["must_change_password"] is True


def test_file_is_private_and_json(home):
    acc.mark_must_change(["a@x.com"])
    p = home / "accounts.json"
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert json.loads(p.read_text())["accounts"]["a@x.com"]["must_change_password"] is True


def test_corrupt_file_reads_as_empty_and_is_rewritten(home):
    (home / "accounts.json").write_text("{not json")
    assert acc.me("a@x.com")["must_change_password"] is False
    acc.mark_must_change(["a@x.com"])
    assert acc.me("a@x.com")["must_change_password"] is True


def test_empty_inputs():
    assert acc.mark_must_change([]) == []
    assert acc.mark_must_change(["", None]) == []
