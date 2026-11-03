"""The flynn agent's outbox: events it can't deliver (a server restart, a laptop off the VPN) are saved on disk and
sent later in order, each batch once; and it records why it was offline so the server can tell the person."""
import importlib
import socket
import sys
import urllib.error
from pathlib import Path

FLYNN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FLYNN / "adapters" / "agent"))


class FlakyServer:
    url, token, ws = "https://studio.example.com", "fla_test", None

    def __init__(self):
        self.up, self.got, self.gone = False, [], set()

    def send_events(self, job, evs, bid, timeout=10):
        return None                                   # no live connection: the HTTPS path is used

    def post(self, path, body, **kw):
        if not self.up:
            raise urllib.error.URLError(socket.gaierror(8, "nodename nor servname provided"))
        if body["job"] in self.gone:
            raise urllib.error.HTTPError(path, 404, "gone", {}, None)
        if body["bid"] not in [b for b, _ in self.got]:
            self.got.append((body["bid"], body["events"]))
        return {"ok": True}


def _agent(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYNN_AGENT_HOME", str(tmp_path / "agent"))
    import flynn_agent as fa
    return importlib.reload(fa)


def test_saved_events_are_sent_in_order_when_the_server_is_back(tmp_path, monkeypatch):
    fa = _agent(tmp_path, monkeypatch)
    srv = FlakyServer()
    assert fa.deliver_events(srv, "j1", [{"t": "delta", "text": "a"}], "b1", tries=()) is False
    fa.outbox_add("j1", {"bid": "b1", "events": [{"t": "delta", "text": "a"}]})
    fa.outbox_add("j1", {"bid": "b2", "events": [{"t": "turn_end", "ok": True}]})
    assert fa.outbox_jobs() == ["j1"]
    assert fa.outbox_flush(srv) == 2                  # still offline: nothing lost, nothing sent
    srv.up = True
    assert fa.outbox_flush(srv) == 0
    assert [b for b, _ in srv.got] == ["b1", "b2"]
    assert fa.outbox_jobs() == []
    assert fa.outbox_flush(srv) == 0                  # nothing sent twice


def test_a_turn_the_server_forgot_is_kept_for_a_while_then_dropped(tmp_path, monkeypatch):
    fa = _agent(tmp_path, monkeypatch)
    srv = FlakyServer()
    srv.up, srv.gone = True, {"j2"}
    fa.outbox_add("j2", {"bid": "b1", "events": [{"t": "delta"}]})
    assert fa.outbox_flush(srv) == 1                  # the server may still be restarting: keep it
    fa.outbox_add("j3", {"bid": "x", "events": [{"t": "delta"}], "at": 1.0})
    srv.gone.add("j3")
    fa.outbox_flush(srv)
    assert "j3" not in fa.outbox_jobs()               # gone for longer than OUTBOX_KEEP: dropped


def test_offline_reason_and_outage_record(tmp_path, monkeypatch):
    fa = _agent(tmp_path, monkeypatch)
    dns = urllib.error.URLError(socket.gaierror(8, "nodename nor servname provided, or not known"))
    assert fa.why_offline(dns) == "dns"
    assert fa.why_offline(urllib.error.URLError(ConnectionRefusedError(61, "Connection refused"))) == "refused"
    assert fa.why_offline(urllib.error.HTTPError("u", 502, "bad gateway", {}, None)) == "server"
    assert fa.why_offline(TimeoutError("timed out")) == "timeout"
    fa.net_down(dns)
    fa.net_down(TimeoutError("timed out"))            # the first reason is kept for the outage
    fa.net_up()
    last = fa.NET["last"]
    assert last["reason"] == "dns" and "VPN" in last["text"] and last["since"] <= last["until"]

    class W:
        job = None
    fa.outbox_add("j9", {"bid": "b", "events": []})
    info = fa.link_info(W())
    assert info["outbox"] == "j9" and info["offline"]["reason"] == "dns" and info["busy"] == ""
