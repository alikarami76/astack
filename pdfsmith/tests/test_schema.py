import datetime

import pytest

from pdfsmith.errors import InputError
from pdfsmith.schema import load_doc


def test_defaults_en(make_doc):
    d = load_doc(make_doc({"meta": {"title": "T"}}, content="# T"))
    assert (d["theme"], d["dir"], d["digits"], d["calendar"], d["pdf_lang"]) == ("neutral", "ltr", "latin", "gregorian", "en-US")
    assert d["name"] == "d" and d["page"]["size"] == "A4" and d["checks"]["allow_overflow"] == []
    assert d["meta"]["date"] == datetime.date.today()


def test_defaults_fa(make_doc):
    d = load_doc(make_doc({"lang": "fa"}, content="# ت"))
    assert (d["theme"], d["dir"], d["digits"], d["calendar"], d["pdf_lang"]) == ("neutral-fa", "rtl", "fa", "jalali", "fa-IR")


def test_mixed_defaults(make_doc):
    d = load_doc(make_doc({"lang": "mixed"}, content="x"))
    assert (d["dir"], d["theme"], d["pdf_lang"]) == ("auto", "neutral-fa", "en-US")


def test_unknown_key(make_doc):
    with pytest.raises(InputError, match="colour"):
        load_doc(make_doc({"colour": "red"}, content="x"))


def test_bad_enum_names_the_field(make_doc):
    with pytest.raises(InputError, match="lang"):
        load_doc(make_doc({"lang": "de"}, content="x"))


def test_needs_content(make_doc):
    with pytest.raises(InputError, match="content"):
        load_doc(make_doc({}))


def test_only_one_source(make_doc):
    with pytest.raises(InputError, match="only one"):
        load_doc(make_doc({}, content="x", html="<p>x</p>"))


def test_missing_content_file(make_doc):
    with pytest.raises(InputError, match="nope.md"):
        load_doc(make_doc({"content": "nope.md"}))


def test_missing_doc_yaml(tmp_path):
    with pytest.raises(InputError, match="doc.yaml"):
        load_doc(tmp_path)


def test_date_string_parsed(make_doc):
    d = load_doc(make_doc({"meta": {"date": "2026-09-30"}}, content="x"))
    assert d["meta"]["date"] == datetime.date(2026, 9, 30)


def test_bad_date(make_doc):
    with pytest.raises(InputError, match="meta.date"):
        load_doc(make_doc({"meta": {"date": "30/09/2026"}}, content="x"))


def test_name_sanitised(make_doc):
    d = load_doc(make_doc({}, content="x", name="My Report"))
    assert d["name"] == "My-Report"


NOT_YET = [
    {"watermark": "DRAFT"},
    {"confidential": True},
    {"page": {"mirror": True}},
    {"features": {"pdfa": True}},
    {"features": {"encrypt": {"owner": "env:PDFSMITH_TEST_OWNER"}}},
    {"features": {"merge": {"after": ["content.md"]}}},
]


@pytest.mark.parametrize("extra", NOT_YET)
def test_later_plan_features_fail_loudly(make_doc, monkeypatch, extra):
    monkeypatch.setenv("PDFSMITH_TEST_OWNER", "owner-secret")
    with pytest.raises(InputError, match="not implemented yet"):
        load_doc(make_doc(extra, content="x"))


@pytest.mark.parametrize("value", [20260930, True, ["2026-09-30"]])
def test_non_date_value_is_input_error(make_doc, value):
    with pytest.raises(InputError, match="meta.date: .* is not a YYYY-MM-DD date"):
        load_doc(make_doc({"meta": {"date": value}}, content="x"))


def test_yaml_date_accepted(make_doc):
    d = load_doc(make_doc({"meta": {"date": datetime.date(2026, 9, 30)}}, content="x"))
    assert d["meta"]["date"] == datetime.date(2026, 9, 30)


def test_checks_pages_reversed_range(make_doc):
    with pytest.raises(InputError, match="checks.pages"):
        load_doc(make_doc({"checks": {"pages": [5, 2]}}, content="x"))
    assert load_doc(make_doc({"checks": {"pages": [2, 2]}}, content="x", name="e"))["checks"]["pages"] == [2, 2]


def test_feature_defaults(make_doc):
    f = load_doc(make_doc({}, content="x"))["features"]
    assert f == {"attachments": [], "forms": "auto", "encrypt": None, "merge": {"before": [], "after": []}, "pdfa": False}


@pytest.mark.parametrize(
    "features, where",
    [
        ({"sign": True}, "features"),
        ({"forms": "yes"}, "features.forms"),
        ({"attachments": "a.sql"}, "features.attachments"),
        ({"encrypt": {"owner": "hunter2"}}, "features.encrypt.owner"),
        ({"encrypt": {"user": "env:X"}}, "features.encrypt"),
        ({"encrypt": {"owner": "env:X", "allow": ["print", "fly"]}}, "features.encrypt.allow.1"),
        ({"merge": {"middle": []}}, "features.merge"),
    ],
)
def test_bad_features_name_the_field(make_doc, features, where):
    with pytest.raises(InputError, match=f"doc.yaml: {where}: "):
        load_doc(make_doc({"features": features}, content="x"))


@pytest.fixture
def implemented(monkeypatch):
    """Lets the feature validators run: the not-implemented guards now fire first, and they hide these errors."""
    monkeypatch.setattr("pdfsmith.schema._not_yet", lambda key: None)


def test_unimplemented_feature_is_reported_before_other_feature_errors(make_doc, monkeypatch):
    monkeypatch.delenv("PDFSMITH_TEST_OWNER", raising=False)
    with pytest.raises(InputError, match="features.encrypt is not implemented yet") as exc:
        load_doc(make_doc({"features": {"encrypt": {"owner": "env:PDFSMITH_TEST_OWNER"}, "pdfa": True}}, content="x"))
    assert str(exc.value).count("/") > 2 and "(see /" in str(exc.value)


def test_feature_files_must_exist(make_doc, implemented):
    with pytest.raises(InputError, match="features.attachments: file 'q.sql' not found"):
        load_doc(make_doc({"features": {"attachments": ["q.sql"]}}, content="x"))
    with pytest.raises(InputError, match="features.merge.after: file 'app.pdf' not found"):
        load_doc(make_doc({"features": {"merge": {"after": ["app.pdf"]}}}, content="x", name="e"))


def test_encrypt_env_var_must_be_set(make_doc, monkeypatch, implemented):
    monkeypatch.delenv("PDFSMITH_TEST_OWNER", raising=False)
    with pytest.raises(InputError, match="features.encrypt.owner: environment variable PDFSMITH_TEST_OWNER is not set"):
        load_doc(make_doc({"features": {"encrypt": {"owner": "env:PDFSMITH_TEST_OWNER"}}}, content="x"))


@pytest.mark.parametrize("features", [{"encrypt": {"owner": "env:PDFSMITH_TEST_OWNER"}}, {"merge": {"before": ["content.md"]}}])
def test_pdfa_conflicts(make_doc, monkeypatch, features, implemented):
    monkeypatch.setenv("PDFSMITH_TEST_OWNER", "owner-secret")
    with pytest.raises(InputError, match="features.pdfa: "):
        load_doc(make_doc({"features": dict(features, pdfa=True)}, content="x"))


def test_resolve_env(monkeypatch):
    from pdfsmith.schema import resolve_env

    monkeypatch.setenv("PDFSMITH_TEST_PW", "s3cret")
    assert resolve_env("env:PDFSMITH_TEST_PW", "features.encrypt.user") == "s3cret"
    monkeypatch.setenv("PDFSMITH_TEST_PW", "")
    with pytest.raises(InputError, match="PDFSMITH_TEST_PW is not set or is empty"):
        resolve_env("env:PDFSMITH_TEST_PW", "features.encrypt.user")


@pytest.mark.parametrize("field", ["owner", "user"])
def test_plaintext_password_is_never_echoed(make_doc, field):
    encrypt = {"owner": "env:X", field: "hunter2"}
    with pytest.raises(InputError, match=f"features.encrypt.{field}") as exc:
        load_doc(make_doc({"features": {"encrypt": encrypt}}, content="x"))
    assert "hunter2" not in str(exc.value)


@pytest.mark.parametrize(
    "encrypt, where",
    [("hunter2", "features.encrypt"), ({"owner": "env:X", "allow": ["hunter2"]}, "features.encrypt.allow.0")],
)
def test_other_encrypt_errors_never_echo_values(make_doc, encrypt, where):
    with pytest.raises(InputError, match=f"doc.yaml: {where}: ") as exc:
        load_doc(make_doc({"features": {"encrypt": encrypt}}, content="x"))
    assert "hunter2" not in str(exc.value)
