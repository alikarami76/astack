import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

FLYNN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FLYNN / "adapters" / "studio"))
sys.path.insert(0, str(FLYNN / "adapters" / "review"))
import studio_tools  # noqa: E402
import studio_mcp  # noqa: E402


class Conv:
    def __init__(self):
        self.events, self.view, self.asked, self.key = [], {"run": "r1", "option": "main"}, False, "run:r1"

    def emit(self, ev, gen=None):
        self.events.append(ev)


@pytest.fixture
def srv(tmp_path):
    run = tmp_path / "r1"
    (run / "page").mkdir(parents=True)
    (run / "page" / "index.html").write_text("<html><body>x</body></html>")
    calls = []
    return SimpleNamespace(run_path=lambda rid: run if rid == "r1" else (_ for _ in ()).throw(KeyError(rid)),
                           snapshot=lambda r, o, l: calls.append(("snap", l)) or "01-x",
                           export=lambda r, k, o: calls.append(("export", k)) or {"kind": k, "path": "exports/a"},
                           calls=calls)


def test_schemas_are_mcp_shaped():
    names = {t["name"] for t in studio_tools.schemas()}
    assert names == {"ask_user", "show_result", "studio_view", "studio_comments", "studio_snapshot", "studio_focus", "studio_export", "studio_edits", "studio_link", "propose_skill"}
    assert all("inputSchema" in t and t["description"] for t in studio_tools.schemas())


def test_ask_user_card_matches_desk(srv):
    c = Conv()
    r = studio_tools.call("ask_user", c, {"questions": [{"q": "Who?", "options": ["Sellers", "Admins"], "recommended": "Sellers"}]}, srv)
    card = c.events[0]["card"]
    assert card["kind"] == "needs_alignment" and card["questions"][0]["n"] == 1 and c.asked and r["shown"]


def test_view_focus_snapshot_export(srv):
    c = Conv()
    assert studio_tools.call("studio_view", c, {}, srv)["run"] == "r1"
    studio_tools.call("studio_focus", c, {"run": "r1", "option": "b"}, srv)
    assert c.events[-1] == {"t": "focus", "run": "r1", "option": "b", "version": None}
    assert studio_tools.call("studio_snapshot", c, {"run": "r1", "label": "v"}, srv)["version"] == "01-x"
    assert studio_tools.call("studio_export", c, {"run": "r1", "kind": "pdf"}, srv)["kind"] == "pdf"
    with pytest.raises(KeyError):
        studio_tools.call("studio_focus", c, {"run": "nope"}, srv)


def test_comments_roundtrip(srv):
    import review
    run = srv.run_path("r1")
    review.add_comment(run, "Make it bigger", {"selector": "h1"}, "the user", "page/index.html")
    c = Conv()
    lst = studio_tools.call("studio_comments", c, {"run": "r1", "action": "list"}, srv)
    assert lst["comments"][0]["n"] == 1
    studio_tools.call("studio_comments", c, {"run": "r1", "action": "resolve", "n": 1, "text": "Done"}, srv)
    assert review.load(run)["comments"][0]["status"] == "resolved"


def test_mcp_lists_and_forwards(monkeypatch):
    assert studio_mcp.handle({"id": 1, "method": "tools/list"})["result"]["tools"]
    monkeypatch.setattr(studio_mcp, "forward", lambda n, a: (json.dumps({"ok": n}), False))
    r = studio_mcp.handle({"id": 2, "method": "tools/call", "params": {"name": "studio_view", "arguments": {}}})
    assert r["result"]["content"][0]["text"] == '{"ok": "studio_view"}' and r["result"]["isError"] is False
    assert studio_mcp.handle({"method": "notifications/initialized"}) is None


def test_run_defaults_to_the_conversation_and_view_has_the_brief(srv):
    import review
    srv.summary = lambda run: {"request": "Voucher list empty state", "type": "ui", "options": [{"name": "main"}],
                               "versions": [], "decision": None, "comments_open": 1, "closed": False}
    review.add_comment(srv.run_path("r1"), "x", {"selector": "h1"}, "the user", "page/index.html")
    c = Conv()
    assert studio_tools.call("studio_comments", c, {"action": "list"}, srv)["comments"][0]["n"] == 1
    v = studio_tools.call("studio_view", c, {}, srv)
    assert v["about"]["brief"] == "Voucher list empty state" and v["about"]["open_comments"] == 1
    c.key, c.view = "home", {}
    with pytest.raises(KeyError):
        studio_tools.call("studio_comments", c, {"action": "list"}, srv)
