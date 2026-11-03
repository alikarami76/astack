"""Optional cross-encoder rerank of the fused top candidates. The daemon serves BAAI/bge-reranker-v2-m3 (lazily
loaded on the first request); any failure here returns None and the caller keeps the fused order, silently."""
from __future__ import annotations

from typing import Callable

from . import client, config
from .normalize import normalize

DOC_CHARS = 1000  # the cross-encoder reads config.rerank_max_tokens() tokens (256); more text only costs time


def _rank(scores: list[float]) -> list[int]:
    """1-based rank of each position by descending score; ties keep the earlier position first."""
    order = sorted(range(len(scores)), key=lambda i: (-scores[i], i))
    r = [0] * len(scores)
    for pos, i in enumerate(order):
        r[i] = pos + 1
    return r


def blend(fused_ids: list[str], scores: list[float], mode: str = "rrf", w: float = config.RERANK_WEIGHT) -> list[str]:
    """Order fused_ids (best first) using the reranker scores (same positions).
    order: the reranker's order. rrf: final = w / rerank_rank + (1 - w) / fused_rank."""
    if len(fused_ids) != len(scores):
        raise ValueError("one score per candidate")
    if mode == "order":
        return [fused_ids[i] for i in sorted(range(len(scores)), key=lambda i: (-scores[i], i))]
    rr = _rank(scores)
    final = [w / rr[i] + (1 - w) / (i + 1) for i in range(len(fused_ids))]
    return [fused_ids[i] for i in sorted(range(len(final)), key=lambda i: (-final[i], i))]


def rerank_ids(query: str, items: list[tuple[str, str]], top: int | None = None, mode: str | None = None,
               rerank_fn: Callable | None = None) -> list[str] | None:
    """items: (id, text) best-first. The first `top` are reranked and blended; the rest follow in their order.
    None when the reranker is unavailable or misbehaves."""
    top = top or config.rerank_top()
    head, tail = items[:top], [i for i, _ in items[top:]]
    if not head:
        return [i for i, _ in items]
    q = normalize(query) if config.normalizing() else query
    docs = [(normalize(t) if config.normalizing() else t)[:DOC_CHARS] for _, t in head]
    try:
        scores = [float(s) for s in (rerank_fn or client.rerank)(q, docs)]
        if len(scores) != len(head):
            return None
        return blend([i for i, _ in head], scores, mode or config.rerank_blend()) + tail
    except Exception:  # noqa: BLE001 - no daemon, no model, no torch: the fused order answers
        return None
