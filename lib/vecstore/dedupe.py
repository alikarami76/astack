"""Near-duplicate clusters from stored vectors (cosine similarity). It only reports; it never merges anything."""
from __future__ import annotations

import numpy as np


def clusters(ids: list[str], mat, threshold: float = 0.92, block: int = 512) -> list[dict]:
    """Connected components of the graph "cosine >= threshold" over L2-normalised vectors, size 2 and up.
    Each cluster: ids (in input order), min_sim and max_sim over its member pairs, chain (True when some pair in
    the cluster is below the threshold, i.e. linked only through a third member). Largest clusters first."""
    n = len(ids)
    if n < 2 or mat is None:
        return []
    mat = np.asarray(mat, dtype=np.float32)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for s in range(0, n, block):
        sims = mat[s:s + block] @ mat.T
        for r in range(sims.shape[0]):
            i = s + r
            for j in np.flatnonzero(sims[r, i + 1:] >= threshold - 1e-6) + i + 1:
                parent[find(i)] = find(int(j))
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    out = []
    for g in groups.values():
        if len(g) < 2:
            continue
        sub = mat[g] @ mat[g].T
        iu = np.triu_indices(len(g), 1)
        lo, hi = float(sub[iu].min()), float(sub[iu].max())
        out.append({"ids": [ids[i] for i in g], "min_sim": round(lo, 4), "max_sim": round(hi, 4),
                    "chain": lo < threshold - 1e-6})
    return sorted(out, key=lambda c: (-len(c["ids"]), -c["max_sim"], c["ids"][0]))


def report(adapter, threshold: float = 0.92, etype: str | None = None) -> dict:
    """Clusters of near-duplicate entries in a store's vector file, with each member's type and title (from the
    adapter's hydrate()). Entries the adapter does not keep() (superseded rows) are left out."""
    from .vecfile import VecFile
    ids, mat = VecFile(adapter.vec_path).matrix(etype)
    if mat is None or not len(ids):
        return {"rows": 0, "threshold": threshold, "clusters": []}
    keep = [i for i, e in enumerate(ids) if adapter.keep(e)]
    out = []
    for c in clusters([ids[i] for i in keep], mat[keep], threshold):
        members = []
        for eid in c["ids"]:
            h = adapter.hydrate(eid) or {}
            members.append({"id": eid, "type": h.get("type"),
                            "title": " ".join(str(h.get("title") or "").split())[:100]})
        out.append({**c, "members": members})
    return {"rows": len(keep), "threshold": threshold, "clusters": out}


def render(rep: dict) -> str:
    lines = [f"{len(rep['clusters'])} cluster(s) of near-duplicates at cosine >= {rep['threshold']} among "
             f"{rep['rows']} rows (nothing is merged; review each and edit or supersede by hand)"]
    for n, x in enumerate(rep["clusters"], 1):
        flag = " (chain: some pairs are less similar than the threshold)" if x["chain"] else ""
        lines.append(f"\n#{n}  {len(x['ids'])} rows, similarity {x['min_sim']}-{x['max_sim']}{flag}")
        lines += [f"  {m['id']}  [{m['type']}] {m['title']}" for m in x["members"]]
    return "\n".join(lines)
