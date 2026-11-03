"""Client for the embedding daemon. Starts it on demand; every failure surfaces as Unavailable."""
from __future__ import annotations

import base64
import json
import os
import socket
import subprocess
import time

import numpy as np

from . import config
from .errors import Unavailable


def _request(payload: dict, timeout: float) -> dict:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(str(config.sock_path()))
        s.sendall(json.dumps(payload).encode())
        s.shutdown(socket.SHUT_WR)
        buf = b"".join(iter(lambda: s.recv(1 << 16), b""))
    finally:
        s.close()
    resp = json.loads(buf)
    if "error" in resp:
        raise Unavailable(f"daemon error: {resp['error']}")
    return resp


def ping() -> dict | None:
    try:
        return _request({"op": "ping"}, 2.0)
    except (OSError, ValueError, Unavailable):
        return None


def stop() -> bool:
    try:
        return bool(_request({"op": "stop"}, 2.0).get("stopping"))
    except (OSError, ValueError, Unavailable):
        return False


def _stop_other_daemons() -> None:
    """Keep one embedding model in memory: before starting this model's daemon, ask any other model's daemon to
    exit (two models at once pushed the user's Mac into heavy swap on 2026-10-07)."""
    mine = config.sock_path()
    for sock in config.home().glob("embed*.sock"):
        if sock == mine:
            continue
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(2.0)
        try:
            s.connect(str(sock))
            s.sendall(json.dumps({"op": "stop"}).encode())
            s.shutdown(socket.SHUT_WR)
            s.recv(1 << 12)
        except OSError:
            pass
        finally:
            s.close()


def _spawn() -> subprocess.Popen:
    home = config.home()
    home.mkdir(parents=True, exist_ok=True)
    os.chmod(home, 0o700)
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(p for p in (str(config.LIB), os.environ.get("PYTHONPATH")) if p)}
    with open(config.log_path(), "a") as log:
        return subprocess.Popen([config.daemon_python(), "-m", "vecstore.daemon"], env=env, cwd=str(home),
                         stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)


def _log_tail() -> str:
    try:
        lines = [ln.strip() for ln in config.log_path().read_text(errors="replace").splitlines() if ln.strip()]
    except OSError:
        return ""
    return f" ({lines[-1][:200]})" if lines else ""


def _expected_model() -> str:
    return "fake-hash-64" if os.environ.get("VECSTORE_FAKE_MODEL") == "1" else config.model()


def ensure_daemon(wait: float = 40.0) -> None:
    if config.disabled():
        raise Unavailable("VECSTORE_DISABLE=1")
    if ping():
        return
    _stop_other_daemons()
    proc = _spawn()
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        time.sleep(0.25)
        if ping():
            return
        code = proc.poll()
        if code not in (None, 0):  # exit 0 means another copy holds the lock and is still loading
            raise Unavailable(f"daemon exited with code {code}; see {config.log_path()}{_log_tail()}")
    raise Unavailable(f"daemon did not start within {wait:.0f}s; see {config.log_path()}")


def embed(texts: list[str], timeout: float | None = None) -> np.ndarray:
    if config.disabled():
        raise Unavailable("VECSTORE_DISABLE=1")
    texts = list(texts)
    if not texts:
        return np.zeros((0, 0), dtype=np.float32)
    t = timeout or (10.0 if len(texts) <= 4 else 120.0)
    req = {"op": "embed", "texts": texts}
    try:
        try:
            resp = _request(req, t)
        except (FileNotFoundError, ConnectionRefusedError):
            ensure_daemon()
            resp = _request(req, t)
        expected = _expected_model()
        if resp.get("model") != expected:
            raise Unavailable(f"daemon serves {resp.get('model')!r}, expected {expected!r}")
        vecs = np.frombuffer(base64.b64decode(resp["vecs"]), dtype=np.float32)
        return vecs.reshape(len(texts), int(resp["dim"])).copy()
    except Unavailable:
        raise
    except Exception as e:  # noqa: BLE001 - callers only ever need to know "no vectors now"
        raise Unavailable(f"{type(e).__name__}: {e}") from e


def rerank(query: str, docs: list[str], timeout: float = 20.0) -> list[float]:
    """Cross-encoder scores of (query, doc) pairs from the daemon. The first call loads the reranker (about half a
    minute); a daemon without the rerank endpoint, or no daemon, raises Unavailable."""
    if config.disabled():
        raise Unavailable("VECSTORE_DISABLE=1")
    if not docs:
        return []
    try:
        resp = _request({"op": "rerank", "query": query, "docs": list(docs)}, timeout)
        scores = resp["scores"]
        if len(scores) != len(docs):
            raise Unavailable("daemon returned the wrong number of scores")
        return [float(x) for x in scores]
    except Unavailable:
        raise
    except Exception as e:  # noqa: BLE001
        raise Unavailable(f"{type(e).__name__}: {e}") from e
