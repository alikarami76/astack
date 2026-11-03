import sys

import pytest

from vecstore import client, config, daemon, procmem

from test_daemon_client import _serve_in_thread


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _slot(idle_s=600.0):
    loads, released = [], []
    clock = Clock()
    slot = daemon.RerankerSlot(loader=lambda: loads.append(1) or object(), idle_s=idle_s, clock=clock,
                               release=lambda: released.append(1))
    return slot, clock, loads, released


def test_reranker_loads_on_first_use_only():
    slot, clock, loads, _ = _slot()
    assert not slot.loaded
    a = slot.get()
    clock.t += 60
    assert slot.get() is a and loads == [1] and slot.loaded


def test_reranker_unloads_after_idle_and_reloads_on_demand():
    slot, clock, loads, released = _slot(idle_s=600)
    slot.get()
    clock.t += 599
    assert slot.maybe_unload() is False and slot.loaded
    clock.t += 2
    assert released == [1, 1]  # around the load
    assert slot.maybe_unload() is True and not slot.loaded and released == [1, 1, 1]
    assert slot.maybe_unload() is False  # nothing left to unload
    slot.get()
    assert loads == [1, 1] and slot.loaded


def test_each_use_restarts_the_idle_clock():
    slot, clock, _, _ = _slot(idle_s=600)
    slot.get()
    clock.t += 500
    slot.get()
    clock.t += 500
    assert slot.maybe_unload() is False
    clock.t += 101
    assert slot.maybe_unload() is True


def test_rerank_idle_minutes_switch(monkeypatch):
    assert config.rerank_idle_minutes() == 10
    monkeypatch.setenv("VECSTORE_RERANK_IDLE_MIN", "2.5")
    assert config.rerank_idle_minutes() == 2.5
    monkeypatch.setenv("VECSTORE_RERANK_IDLE_MIN", "junk")
    assert config.rerank_idle_minutes() == 10


def test_busy_daemon_still_unloads_an_idle_reranker(vhome, monkeypatch):
    """Embed traffic keeps the daemon alive; the reranker must still go once it has not been used for a while."""
    monkeypatch.setenv("VECSTORE_FAKE_MODEL", "1")
    monkeypatch.setattr(daemon, "_SLOT", daemon.RerankerSlot(loader=daemon.FakeReranker, idle_s=0.2))
    _serve_in_thread()
    assert client.rerank("buybox", ["buybox winner", "flash sale"]) and daemon._SLOT.loaded
    import time
    time.sleep(0.3)
    client.embed(["keeps the daemon busy"])
    assert not daemon._SLOT.loaded
    assert client.ping()["mem"]["reranker_loaded"] is False
    client.stop()


def test_ping_reports_memory(vhome, monkeypatch):
    monkeypatch.setenv("VECSTORE_FAKE_MODEL", "1")
    _serve_in_thread()
    mem = client.ping()["mem"]
    assert "reranker_loaded" in mem
    if sys.platform == "darwin":
        assert mem["footprint_mb"] > 0 and mem["peak_footprint_mb"] >= mem["footprint_mb"]
    client.stop()


@pytest.mark.skipif(sys.platform != "darwin", reason="libproc is macOS only")
def test_procmem_reads_this_process():
    m = procmem.usage()
    assert m["footprint_mb"] > 0 and m["peak_footprint_mb"] >= m["footprint_mb"]
    assert procmem.usage(999999999) is None  # no such process


def test_release_is_safe_without_torch():
    daemon.release_memory()  # torch not imported in the fake daemon: a no-op, never an error
