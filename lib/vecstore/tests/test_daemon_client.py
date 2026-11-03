import os
import subprocess
import sys
import threading
import time

import numpy as np
import pytest

from vecstore import Unavailable, client, config, daemon


def _serve_in_thread(idle_s=5.0):
    enc = daemon.FakeEncoder()
    config.home().mkdir(parents=True, exist_ok=True)
    t = threading.Thread(target=daemon.serve, args=(enc, config.sock_path(), idle_s), daemon=True)
    t.start()
    for _ in range(100):
        if client.ping():
            return t
        time.sleep(0.02)
    raise AssertionError("daemon thread did not come up")


def test_round_trip_matches_the_encoder(vhome, monkeypatch):
    monkeypatch.setenv("VECSTORE_FAKE_MODEL", "1")
    t = _serve_in_thread()
    vecs = client.embed(["buybox winner", "flash-sale share"])
    assert vecs.shape == (2, 64) and vecs.dtype == np.float32
    assert np.allclose(vecs, daemon.FakeEncoder().encode(["buybox winner", "flash-sale share"]))
    assert client.ping()["model"] == "fake-hash-64"
    assert client.stop()
    t.join(2)
    assert not t.is_alive() and not config.sock_path().exists()


def test_idle_exit(vhome):
    t = _serve_in_thread(idle_s=0.3)
    t.join(3)
    assert not t.is_alive()


def test_socket_is_private(vhome):
    _serve_in_thread()
    assert oct(config.sock_path().stat().st_mode & 0o777) == "0o600"
    client.stop()


def test_bad_request_returns_error_not_crash(vhome):
    _serve_in_thread()
    with pytest.raises(Unavailable, match="unknown op"):
        client._request({"op": "nope"}, 2.0)
    assert client.ping()  # still serving
    client.stop()


def test_disabled_raises_unavailable(vhome, monkeypatch):
    monkeypatch.setenv("VECSTORE_DISABLE", "1")
    with pytest.raises(Unavailable):
        client.embed(["x"])


def test_client_spawns_daemon_on_demand_and_second_copy_exits(vhome, monkeypatch):
    monkeypatch.setenv("VECSTORE_FAKE_MODEL", "1")
    monkeypatch.setenv("VECSTORE_PYTHON", sys.executable)
    monkeypatch.setenv("VECSTORE_IDLE_MIN", "0.1")
    assert client.embed(["hello world"]).shape == (1, 64)
    env = {**os.environ, "PYTHONPATH": str(config.LIB)}
    second = subprocess.run([sys.executable, "-m", "vecstore.daemon"], env=env, timeout=10)
    assert second.returncode == 0  # lock held by the first daemon: exits at once
    assert client.ping() and client.stop()
    log = config.log_path().read_text()
    assert "hello world" not in log and "embed" in log


def test_client_rejects_wrong_model(vhome, monkeypatch):
    monkeypatch.delenv("VECSTORE_FAKE_MODEL", raising=False)
    _serve_in_thread()
    with pytest.raises(Unavailable, match="expected"):
        client.embed(["buybox winner"])
    client.stop()


def test_crashed_daemon_fails_fast(vhome, monkeypatch):
    monkeypatch.setenv("VECSTORE_FAKE_MODEL", "1")
    monkeypatch.setenv("VECSTORE_PYTHON", sys.executable)
    monkeypatch.setenv("VECSTORE_IDLE_MIN", "not-a-number")
    t0 = time.monotonic()
    with pytest.raises(Unavailable, match="exited"):
        client.embed(["hello"])
    assert time.monotonic() - t0 < 10
