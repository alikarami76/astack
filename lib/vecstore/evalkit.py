"""Recall eval for any store, generalised from clu/kb/eval_recall.py. A store's eval_dir holds recall_set.jsonl
(one {"query", "expected": [ids]} per line) and baseline.json; gate() decides whether auto mode uses vectors."""
from __future__ import annotations

import datetime as _dt
import json
import time
from pathlib import Path

TOLERANCE = 0.03
METRICS = ("recall@5", "recall@10", "hit@5", "mrr")


def load_set(path) -> list[dict]:
    lines = Path(path).read_text().splitlines()
    return [it for it in (json.loads(l) for l in lines if l.strip()) if it.get("expected")]


def rank_all(search_ids, items: list[dict]) -> list[tuple[list[str], float]]:
    """(ranked ids, milliseconds) for every query."""
    out = []
    for it in items:
        t0 = time.perf_counter()
        ids = list(search_ids(it["query"]))
        out.append((ids, (time.perf_counter() - t0) * 1000))
    return out


def score_ranked(items: list[dict], ranked: list[tuple[list[str], float]]) -> dict:
    r5 = r10 = h5 = mrr = 0.0
    for it, (ids, _) in zip(items, ranked):
        exp = set(it["expected"])
        r5 += len(exp & set(ids[:5])) / len(exp)
        r10 += len(exp & set(ids[:10])) / len(exp)
        h5 += bool(exp & set(ids[:5]))
        mrr += next((1 / (i + 1) for i, x in enumerate(ids) if x in exp), 0)
    n = len(items) or 1
    ms = sorted(t for _, t in ranked) or [0.0]
    return {"queries": len(items), "recall@5": round(r5 / n, 3), "recall@10": round(r10 / n, 3),
            "hit@5": round(h5 / n, 3), "mrr": round(mrr / n, 3), "ms_per_query": round(sum(ms) / n),
            "p50_ms": round(ms[len(ms) // 2])}


def score(search_ids, items: list[dict]) -> dict:
    return score_ranked(items, rank_all(search_ids, items))


# Cumulative steps of the retrieval upgrade, scored by matrix(): (name, VECSTORE_NORMALIZE, modes).
STEPS = (
    ("baseline", "0", ("lexical", "hybrid")),
    ("+ normalisation", "1", ("lexical", "hybrid")),
    ("+ rerank (rrf blend)", "1", ("hybrid_rerank",)),
)


def matrix(build, items: list[dict], steps=STEPS, embed_fn=None, rerank_fn=None, progress: bool = True) -> dict:
    """Score every upgrade step on one recall set. build(norm) returns an adapter whose keyword index was built under
    VECSTORE_NORMALIZE=norm (it is called with that variable already set) and whose vec_path is private to that
    setting; matrix() embeds it. Items may carry a "tag" (e.g. farsi, paraphrase); each tag is also scored alone.
    Returns {step: {mode: scores, mode + "/" + tag: scores}}."""
    import os
    import sys
    from . import client
    from .hybrid import hybrid
    from .vecfile import VecFile
    embed_fn = embed_fn or client.embed
    out: dict = {}
    built: dict = {}
    tags = sorted({it["tag"] for it in items if it.get("tag")})
    for name, norm, modes in steps:
        old = os.environ.get("VECSTORE_NORMALIZE")
        os.environ["VECSTORE_NORMALIZE"] = norm
        try:
            if norm not in built:
                ad = build(norm)
                VecFile(ad.vec_path).sync(ad.entries(), embed_fn)
                built[norm] = ad
            ad = built[norm]
            res = {}
            for mode in modes:
                if mode == "lexical":
                    fn = lambda q: [r["id"] for r in ad.lexical(q, None, 10)]  # noqa: E731
                else:
                    rr = mode == "hybrid_rerank"
                    fn = lambda q, rr=rr: [r["id"] for r in hybrid(ad, q, k=10, embed_fn=embed_fn, rerank=rr,  # noqa: E731
                                                                   rerank_fn=rerank_fn)]
                ranked = rank_all(fn, items)
                res[mode] = score_ranked(items, ranked)
                for t in tags:
                    sub = [(it, r) for it, r in zip(items, ranked) if it.get("tag") == t]
                    res[f"{mode}/{t}"] = score_ranked([x for x, _ in sub], [r for _, r in sub])
            out[name] = res
            if progress:
                print(f"matrix: scored {name}", file=sys.stderr)
        finally:
            if old is None:
                os.environ.pop("VECSTORE_NORMALIZE", None)
            else:
                os.environ["VECSTORE_NORMALIZE"] = old
    return out


def render_matrix(results: dict, tags: tuple = ()) -> str:
    """Markdown table in the phase A report format: one row per step and mode, recall@5 / MRR overall and per tag,
    and the median latency."""
    head = "| step | mode | R@5 | MRR | " + "".join(f"{t} R@5 | {t} MRR | " for t in tags) + "p50 ms |"
    rows = [head, "|" + "---|" * (5 + 2 * len(tags))]
    for name, res in results.items():
        for mode, sc in res.items():
            if "/" in mode:
                continue
            cells = [f"{sc['recall@5']:.3f}", f"{sc['mrr']:.3f}"]
            for t in tags:
                st = res.get(f"{mode}/{t}")
                cells += [f"{st['recall@5']:.3f}", f"{st['mrr']:.3f}"] if st else ["", ""]
            rows.append(f"| {name} | {mode} | " + " | ".join(cells) + f" | {sc['p50_ms']} |")
    return "\n".join(rows)


def regressions(scores: dict, baseline: dict, tolerance: float = TOLERANCE) -> list[str]:
    out = []
    for mode, s in scores.items():
        b = (baseline.get("modes") or {}).get(mode)
        if not b:
            continue
        if baseline.get("queries") and s.get("queries") != baseline["queries"]:
            out.append(f"note: the recall set has {s.get('queries')} queries, the baseline {baseline['queries']}; "
                       "re-save the baseline after reviewing")
            continue
        for m in ("recall@5", "recall@10"):
            if s[m] < b[m] - tolerance:
                out.append(f"{mode} {m} {s[m]} is below the baseline {b[m]} (tolerance {tolerance})")
    return out


def gate(baseline_path) -> bool:
    try:
        m = json.loads(Path(baseline_path).read_text())["modes"]
        return m["hybrid"]["recall@5"] >= m["lexical"]["recall@5"]
    except (OSError, ValueError, KeyError, TypeError):
        return False


def gate_rerank(baseline_path) -> bool:
    """Rerank is used only when the eval shows hybrid+rerank at least as good as hybrid on recall@5 and MRR."""
    try:
        m = json.loads(Path(baseline_path).read_text())["modes"]
        h, r = m["hybrid"], m["hybrid_rerank"]
        return r["recall@5"] >= h["recall@5"] and r["mrr"] >= h["mrr"]
    except (OSError, ValueError, KeyError, TypeError):
        return False


def save_baseline(path, scores: dict) -> None:
    first = next(iter(scores.values()))
    Path(path).write_text(json.dumps({"date": _dt.date.today().isoformat(), "queries": first["queries"],
                                      "modes": {m: {k: s[k] for k in METRICS} for m, s in scores.items()}},
                                     indent=2) + "\n")


def run(adapter, embed_fn=None, rerank: bool = False, rerank_fn=None) -> dict:
    import sys
    from . import client
    from .errors import Unavailable
    from .hybrid import hybrid
    from .vecfile import VecFile
    items = load_set(Path(adapter.eval_dir) / "recall_set.jsonl")
    scores = {"lexical": score(lambda q: [r["id"] for r in adapter.lexical(q, None, 10)], items)}
    if VecFile(adapter.vec_path).count():
        try:
            (embed_fn or client.embed)([adapter.instruct + "probe"])
        except Exception as e:
            reason = str(e) if isinstance(e, Unavailable) else type(e).__name__
            print(f"vecstore eval: vectors unavailable ({reason}); scoring lexical only", file=sys.stderr)
            return scores
        scores["hybrid"] = score(lambda q: [r["id"] for r in hybrid(adapter, q, k=10, embed_fn=embed_fn)], items)
    if rerank or rerank_fn:
        try:
            (rerank_fn or client.rerank)("probe", ["probe"])
        except Exception as e:  # noqa: BLE001
            reason = str(e) if isinstance(e, Unavailable) else type(e).__name__
            print(f"vecstore eval: reranker unavailable ({reason}); skipping hybrid_rerank", file=sys.stderr)
            return scores
        scores["hybrid_rerank"] = score(lambda q: [r["id"] for r in hybrid(adapter, q, k=10, embed_fn=embed_fn,
                                                                          rerank=True, rerank_fn=rerank_fn)], items)
    return scores
