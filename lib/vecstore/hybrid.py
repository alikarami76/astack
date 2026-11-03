"""Merge a store's keyword ranking with the vector ranking (reciprocal rank fusion). The keyword ranking is the
store's own and is never changed; when the vector layer fails, the keyword results come back on their own."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from . import client, config, evalkit, rerank as _rr
from .normalize import normalize
from .errors import Unavailable
from .fuse import rrf
from .vecfile import VecFile

POOL = 50  # candidates taken from each ranker before fusion


class BaseAdapter:
    """What a store tells vecstore about itself. Subclass it in the store's vec_adapter.py."""
    name: str = ""
    instruct: str = ""
    vec_path: Path
    eval_dir: Path | None = None

    def entries(self):
        """Yield (eid, etype, text) for every entry to embed. Leave auto-generated entries out."""
        raise NotImplementedError

    def lexical(self, query: str, etype: str | None, limit: int) -> list[dict]:
        """The store's existing keyword ranker; each result dict carries "id"."""
        raise NotImplementedError

    def hydrate(self, eid: str) -> dict | None:
        return None

    def keep(self, eid: str) -> bool:
        return True

    def prepare_query(self, query: str) -> str:
        return query

    def rerank_text(self, eid: str) -> str | None:
        """The text the cross-encoder reads for one entry. None: use the title and snippet from hydrate()."""
        return None


def report_unavailable(e: Exception) -> None:
    reason = str(e) if isinstance(e, Unavailable) else type(e).__name__
    print(f"[vecstore] semantic search unavailable ({reason}); keyword results only", file=sys.stderr)


def check_model(vf: VecFile) -> None:
    """Refuse to rank with vectors from another model than the configured one (the query would be embedded by a
    different model, so the scores would mean nothing)."""
    built_with = vf.file_model()
    if built_with and built_with != vf.model:
        raise Unavailable(f"{vf.path.name} was built with {built_with}, the configured model is {vf.model}; "
                          "re-embed it to use vectors")


def semantic(adapter, query: str, etype: str | None = None, limit: int = POOL, embed_fn=None) -> list[str]:
    vf = VecFile(adapter.vec_path)
    check_model(vf)
    ids, mat = vf.matrix(etype)
    if mat is None:
        return []
    text = adapter.prepare_query(query)
    q = (embed_fn or client.embed)([config.query_prefix(adapter.instruct) + (normalize(text) if config.normalizing()
                                                                           else text)])[0]
    out = []
    for i in np.argsort(-(mat @ np.asarray(q, dtype=np.float32))):
        if adapter.keep(ids[i]):
            out.append(ids[i])
            if len(out) >= limit:
                break
    return out


def _candidate_text(adapter, eid: str, hit: dict | None) -> str:
    t = adapter.rerank_text(eid)
    if t:
        return t
    h = hit or adapter.hydrate(eid) or {}
    return " ".join(str(h.get(k) or "") for k in ("title", "heading", "snippet")).strip()


def hybrid(adapter, query: str, etype: str | None = None, k: int = 10, embed_fn=None, rerank: bool = False,
           rerank_fn=None) -> list[dict]:
    """Keyword and vector rankings fused with RRF. With rerank=True the fused top N (config.rerank_top) is reordered
    by the cross-encoder; when the reranker is unavailable the fused order stands (silently)."""
    lex = adapter.lexical(query, etype, POOL)
    try:
        sem = semantic(adapter, query, etype, POOL, embed_fn)
    except Exception as e:  # noqa: BLE001 - no daemon, no model, no torch: keyword search still answers
        report_unavailable(e)
        return lex[:k]
    by_id = {r["id"]: r for r in lex}
    want = max(k, config.rerank_top()) if rerank else k
    out = []
    for eid in rrf([[r["id"] for r in lex], sem]):
        r = by_id.get(eid) or adapter.hydrate(eid)
        if r:
            out.append(r)
            if len(out) >= want:
                break
    if rerank and len(out) > 1:
        order = _rr.rerank_ids(query, [(r["id"], _candidate_text(adapter, r["id"], r)) for r in out],
                               rerank_fn=rerank_fn)
        if order:
            pos = {r["id"]: r for r in out}
            out = [pos[i] for i in order]
    return out[:k]


def resolve_mode(adapter, mode: str = "auto") -> str:
    """auto means hybrid only when the store has vectors and its recall baseline shows hybrid at least matching
    keyword search (spec section 8)."""
    if mode != "auto":
        return mode
    try:
        if config.disabled() or VecFile(adapter.vec_path).count() == 0:
            return "lexical"
    except Exception:  # noqa: BLE001 - an unreadable vector file means keyword search, never a crash
        return "lexical"
    ok = adapter.eval_dir is not None and evalkit.gate(Path(adapter.eval_dir) / "baseline.json")
    return "hybrid" if ok else "lexical"


def resolve_rerank(adapter, flag: str = "auto") -> bool:
    """on / off are explicit (VECSTORE_DISABLE=1 still wins). auto: the env flag VECSTORE_RERANK decides if it says
    on or off; otherwise rerank only when the store's baseline shows hybrid+rerank at least as good as hybrid."""
    if config.disabled():
        return False
    if flag == "auto":
        flag = config.rerank_flag()
    if flag != "auto":
        return flag == "on"
    return adapter.eval_dir is not None and evalkit.gate_rerank(Path(adapter.eval_dir) / "baseline.json")


def search(adapter, query: str, etype: str | None = None, k: int = 10, mode: str = "auto", embed_fn=None,
           rerank: str = "auto", rerank_fn=None):
    if resolve_mode(adapter, mode) == "hybrid":
        return hybrid(adapter, query, etype, k, embed_fn, rerank=resolve_rerank(adapter, rerank), rerank_fn=rerank_fn)
    return adapter.lexical(query, etype, k)
