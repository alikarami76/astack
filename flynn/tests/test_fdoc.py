import copy
import json

import fdoc
import pytest
import store

SYS = "test-sys"


@pytest.fixture
def conn():
    c = store.connect()
    for name, kind in [("color.surface", "color"), ("color.text.primary", "color"), ("space.md", "space"),
                       ("space.lg", "space"), ("radius.md", "radius"), ("type.body", "type")]:
        store.upsert(c, "token", {"system": SYS, "name": name, "kind": kind, "level": "semantic", "value": "x", "tier": "verified"})
    store.upsert(c, "component", {"system": SYS, "name": "Button", "tier": "verified",
                                  "variants": {"size": ["sm", "md"], "style": ["primary", "secondary"]},
                                  "props": {"label": {"type": "string", "required": True},
                                            "icon": {"type": "string"},
                                            "width": {"type": "enum", "values": ["hug", "fill"]}}})
    store.upsert(c, "component", {"system": SYS, "name": "Table", "tier": "drift"})
    store.upsert(c, "component", {"system": SYS, "name": "Chip", "tier": "proposed"})
    return c


DOC = {
    "flynn_doc": 1, "kind": "screen", "system": SYS, "title": "لیست سفارش‌ها", "dir": "rtl", "lang": "fa",
    "viewport": {"w": 1440, "h": 900},
    "root": {"id": "root", "component": "Frame",
             "layout": {"direction": "column", "gap": "space.md", "padding": ["space.lg", "space.md"]},
             "style": {"fill": "color.surface"},
             "children": [
                 {"id": "title", "component": "Text", "text": "سفارش‌ها",
                  "style": {"typography": "type.body", "color": "color.text.primary"}},
                 {"id": "cta", "component": "Button", "variant": {"size": "md", "style": "primary"},
                  "props": {"label": "ثبت سفارش", "width": "hug"}},
             ]},
}


def codes(issues, sev=None):
    return sorted(i["code"] for i in issues if sev is None or i["severity"] == sev)


def mutate(fn):
    d = copy.deepcopy(DOC)
    fn(d)
    return d


def test_valid_doc_has_no_issues(conn):
    assert fdoc.validate(DOC, conn) == []


@pytest.mark.parametrize("fn,code", [
    (lambda d: d.update(flynn_doc=2), "version"),
    (lambda d: d.update(dir="up"), "bad-field"),
    (lambda d: d["viewport"].update(w=0), "bad-field"),
    (lambda d: d.pop("root"), "no-root"),
    (lambda d: d["root"]["children"].append({"id": "x", "component": "Carousel"}), "unknown-component"),
    (lambda d: d["root"]["children"][1]["variant"].update(size="xl"), "unknown-variant-value"),
    (lambda d: d["root"]["children"][1]["variant"].update(tone="loud"), "unknown-variant-axis"),
    (lambda d: d["root"]["children"][1]["props"].pop("label"), "missing-prop"),
    (lambda d: d["root"]["children"][1]["props"].update(color="red"), "unknown-prop"),
    (lambda d: d["root"]["children"][1]["props"].update(width="huge"), "bad-prop-value"),
    (lambda d: d["root"]["style"].update(fill="#ff0000"), "off-system-value"),
    (lambda d: d["root"]["layout"].update(gap=13), "off-system-value"),
    (lambda d: d["root"]["layout"].update(direction="diagonal"), "bad-layout"),
    (lambda d: d["root"]["style"].update(shadow="big"), "unknown-style-key"),
    (lambda d: d["root"]["children"][0].update(id="root"), "duplicate-id"),
    (lambda d: d["root"]["children"][0].update(children=[{"id": "k", "component": "Text", "text": "a"}]), "leaf-children"),
    (lambda d: d["root"]["children"][0].pop("text"), "missing-field"),
    (lambda d: d["root"]["children"][0].update(props={"a": 1}), "primitive-has-props"),
    (lambda d: d["root"]["children"].append({"component": "Text", "text": "a"}), "bad-id"),
])
def test_errors(conn, fn, code):
    assert code in codes(fdoc.validate(mutate(fn), conn), "error")


def test_override_downgrades_off_system_to_warning(conn):
    d = mutate(lambda d: d["root"].update(style={"fill": "#ff0000"}, overrides=["fill"]))
    assert codes(fdoc.validate(d, conn)) == ["off-system-override"]


def test_drift_and_proposed_components_warn(conn):
    d = mutate(lambda d: d["root"]["children"].extend([{"id": "t", "component": "Table"}, {"id": "c", "component": "Chip"}]))
    issues = fdoc.validate(d, conn)
    assert codes(issues, "error") == [] and codes(issues, "warning") == ["tier", "tier"]


def test_empty_system_warns(conn):
    d = mutate(lambda d: d.update(system="nothing-here"))
    assert "empty-system" in codes(fdoc.validate(d, conn), "warning")


def test_cli_exit_codes(conn, tmp_path, capsys):
    good, bad = tmp_path / "good.json", tmp_path / "bad.json"
    good.write_text(json.dumps(DOC, ensure_ascii=False))
    bad.write_text(json.dumps(mutate(lambda d: d.update(flynn_doc=9))))
    assert fdoc.main(["validate", str(good)]) == 0
    assert fdoc.main(["validate", str(bad), "--json"]) == 1
    assert json.loads(capsys.readouterr().out.splitlines()[-1])[0]["code"] == "version"
