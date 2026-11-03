"""Local embeddings for the clu KB: a thin shim over the shared vecstore library (agent-stack/lib/vecstore).

The model, the vector file format and the fusion are unchanged; embedding now goes through the vecstore daemon,
which keeps the model loaded between searches. kb.py, store.search(mode="hybrid") and the tests keep calling the
names below. The clu-specific parts (texts, filters, instruction) live in vec_adapter.py.
"""
from __future__ import annotations

import fcntl

import numpy as np

import store
import vec_adapter
import vec_lanes
from vecstore import client, config
from vecstore import hybrid as _hy
from vecstore.config import MODEL  # noqa: F401  (kept for callers and eval scripts)
from vecstore.vecfile import VecFile

INSTRUCT = vec_adapter.INSTRUCT
POOL = _hy.POOL
vec_path = vec_adapter.vec_path


def embed(texts: list[str], batch: int = 16) -> np.ndarray:
    return client.embed(list(texts))


# The lambdas look `embed` up at call time, so tests can monkeypatch vectors.embed.
def index(conn, batch: int = 16, progress: bool = False, lane: str = "curated") -> dict:
    """Embed new or changed entries of one lane (by content hash). A vector file built with another model than the
    configured one is cleared and embedded again in full (VecFile.sync)."""
    a = vec_lanes.make(conn, lane)
    if hasattr(a, "refresh_index"):
        a.refresh_index()
    return VecFile(a.vec_path).sync(a.entries(), lambda t: embed(t, batch), batch=batch, progress=progress)


def lock_path():
    return store.db_path().with_name(store.db_path().stem + ".embed.lock")


def index_lanes(conn, lanes, progress: bool = False) -> dict:
    """Embed several lanes under one lock, so two background embeds (two runs closing) never overlap. A lane that
    fails reports its error and the others still run."""
    f = open(lock_path(), "a+")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        f.close()
        return {"skipped": "another embed is running"}
    out = {}
    try:
        for lane in lanes:
            try:
                out[lane] = index(conn, progress=progress, lane=lane)
            except Exception as e:  # noqa: BLE001 - no daemon, no model: report and go on
                out[lane] = {"error": f"{type(e).__name__}: {e}"[:300]}
    finally:
        fcntl.flock(f, fcntl.LOCK_UN)
        f.close()
    return out


def lane_mode(a, mode: str = "auto") -> str:
    """auto = hybrid when the lane's vector file holds vectors (of any model: a model mismatch is then reported on
    stderr and the search answers with keywords), else lexical."""
    if mode != "auto":
        return mode
    if config.disabled():
        return "lexical"
    try:
        return "hybrid" if VecFile(a.vec_path).file_model() else "lexical"
    except Exception:  # noqa: BLE001 - an unreadable vector file means keyword search
        return "lexical"


def search_lane(conn, lane: str, query: str, k: int = 8, mode: str = "auto", rerank: bool = False) -> list[dict]:
    """Search the schema or runs lane. Never raises for a missing daemon or vector file: keywords answer."""
    a = vec_lanes.make(conn, lane)
    if lane == "runs":
        a.refresh_index()  # cheap: only files whose mtime changed are chunked again
    if lane_mode(a, mode) == "hybrid":
        return _hy.hybrid(a, query, None, k, embed_fn=lambda t: embed(t), rerank=rerank)
    return a.lexical(query, None, k)


def semantic(conn, query: str, etype: str | None = None, limit: int = POOL) -> list[str]:
    return _hy.semantic(vec_adapter.make(conn), query, etype, limit, embed_fn=lambda t: embed(t))


def hybrid(conn, query: str, etype: str | None = None, k: int = 10, rerank: bool = False) -> list[dict]:
    return _hy.hybrid(vec_adapter.make(conn), query, etype, k, embed_fn=lambda t: embed(t), rerank=rerank)


def want_rerank(conn, lane: str = "curated", flag: str = "auto") -> bool:
    """Whether to rerank: on / off, or auto = the lane's eval baseline shows hybrid+rerank at least as good as
    hybrid (curated lane only; the other lanes have no gate and rerank only when asked)."""
    return _hy.resolve_rerank(vec_lanes.make(conn, lane), flag)
