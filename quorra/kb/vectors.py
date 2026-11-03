"""Local embeddings for the quorra KB: a thin shim over the shared vecstore library (agent-stack/lib/vecstore).

Embedding goes through the vecstore daemon (one model kept loaded for clu, flynn and quorra; nothing leaves the
machine), the vector file is vecstore's format, and hybrid search is vecstore's keyword + vector fusion with the
optional cross-encoder rerank. The quorra-specific parts (texts, filters, instruction) live in vec_adapter.py.
kb.py, store.search(mode="hybrid") and scripts/qindex/symvec.py keep calling the names below.
"""
from __future__ import annotations

import numpy as np

import vec_adapter
from vecstore import client
from vecstore import hybrid as _hy
from vecstore.vecfile import VecFile

INSTRUCT = vec_adapter.INSTRUCT
POOL = _hy.POOL
vec_path = vec_adapter.vec_path


def embed(texts: list[str], batch: int = 16) -> np.ndarray:
    return client.embed(list(texts))


def index(conn, batch: int = 16, progress: bool = False) -> dict:
    """Embed every curated row whose text changed since the last run; drop vectors of rows that are gone. A file
    built with another model is cleared and embedded again (VecFile.sync)."""
    a = vec_adapter.make(conn)
    return VecFile(a.vec_path).sync(a.entries(), lambda t: embed(t, batch), batch=batch, progress=progress)


def semantic(conn, query: str, etype: str | None = None, limit: int = POOL) -> list[str]:
    return _hy.semantic(vec_adapter.make(conn), query, etype, limit, embed_fn=lambda t: embed(t))


def hybrid(conn, query: str, etype: str | None = None, k: int = 10, rerank: bool = False) -> list[dict]:
    return _hy.hybrid(vec_adapter.make(conn), query, etype, k, embed_fn=lambda t: embed(t), rerank=rerank)


def want_rerank(conn, flag: str = "auto") -> bool:
    """on / off, or auto: rerank only when kb/eval/baseline.json shows hybrid + rerank at least as good as hybrid."""
    return _hy.resolve_rerank(vec_adapter.make(conn), flag)
