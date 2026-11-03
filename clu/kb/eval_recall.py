#!/usr/bin/env python3
"""Score KB search against the labelled recall set.

  eval_recall.py [--lane curated|schema] [--mode lexical|hybrid|both] [--show-misses] [--save-baseline] [--check]
Prints recall@5, recall@10 (share of expected ids found), hit@5 (any expected id in top 5) and MRR.
--save-baseline stores the scores in kb/eval/baseline.json; --check exits 1 when any mode's recall@5 or
recall@10 dropped more than TOLERANCE below the baseline. `both` also prints hybrid's gain over lexical,
which is the evidence that the vector index is worth keeping.

--lane schema scores the table lane (one document per table) against eval/schema_recall_set.jsonl, one
{"q": "...", "expected": ["<table name, database.table or source id>", ...]} per line, with its own baseline
(eval/schema_baseline.json). A hit matches an expected entry by source id, table name, database.table or the full
database.schema.table, ignoring case.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import store  # noqa: E402

SET = Path(__file__).with_name("eval") / "recall_set.jsonl"
BASELINE = Path(__file__).with_name("eval") / "baseline.json"
SCHEMA_SET = Path(__file__).with_name("eval") / "schema_recall_set.jsonl"
OM_SET = Path(__file__).with_name("eval") / "om_recall_set.jsonl"  # expected: metric names or glossary term_en/term_fa
OM_BASELINE = Path(__file__).with_name("eval") / "om_baseline.json"
SCHEMA_BASELINE = Path(__file__).with_name("eval") / "schema_baseline.json"
TOLERANCE = 0.03
METRICS = ("recall@5", "recall@10", "hit@5", "mrr")
RERANK_MODES = ("hybrid_rerank", "hybrid_rerank_order")  # rrf blend (0.7 rerank + 0.3 fused) and rerank order


def split_mode(mode: str) -> tuple[str, bool, str | None]:
    """(base mode for the search, rerank?, blend override) for a score mode name."""
    if mode == "hybrid_rerank":
        return "hybrid", True, "rrf"
    if mode == "hybrid_rerank_order":
        return "hybrid", True, "order"
    return mode, False, None


class _Blend:
    """Pin VECSTORE_RERANK_BLEND for one scoring pass."""
    def __init__(self, blend):
        self.blend, self.old = blend, os.environ.get("VECSTORE_RERANK_BLEND")

    def __enter__(self):
        if self.blend:
            os.environ["VECSTORE_RERANK_BLEND"] = self.blend

    def __exit__(self, *a):
        if self.old is None:
            os.environ.pop("VECSTORE_RERANK_BLEND", None)
        else:
            os.environ["VECSTORE_RERANK_BLEND"] = self.old


def cache_rerank():
    """Memoise client.rerank for the rest of the process, so scoring the rrf and the order blend on one query pays
    for the cross-encoder once (the second blend's latency is then not representative)."""
    from vecstore import client
    if getattr(client.rerank, "_cached", False):
        return
    orig, memo = client.rerank, {}

    def cached(query, docs, timeout=20.0):
        key = (query, tuple(docs))
        if key not in memo:
            memo[key] = orig(query, docs, timeout)
        return memo[key]
    cached._cached = True
    client.rerank = cached


def _finish(mode, n, r5, r10, h5, mrr, times, **extra):
    return {"mode": mode, **extra, "queries": n, "recall@5": round(r5 / (n or 1), 3), "recall@10": round(r10 / (n or 1), 3),
            "hit@5": round(h5 / (n or 1), 3), "mrr": round(mrr / (n or 1), 3),
            "ms_per_query": round(sum(times) * 1000 / (n or 1)),
            "p50_ms": round(statistics.median(times) * 1000) if times else 0}


def score(conn, mode: str, show_misses: bool = False) -> dict:
    items = [it for it in (json.loads(l) for l in SET.read_text().splitlines() if l.strip()) if it.get("expected")]
    base, rerank, blend = split_mode(mode)
    r5 = r10 = h5 = mrr = 0.0
    times = []
    with _Blend(blend):
        for it in items:
            t0 = time.perf_counter()
            ids = [r["id"] for r in store.search(conn, it["query"], k=10, mode=base, rerank=rerank)]
            times.append(time.perf_counter() - t0)
            exp = set(it["expected"])
            r5 += len(exp & set(ids[:5])) / len(exp)
            r10 += len(exp & set(ids[:10])) / len(exp)
            h5 += bool(exp & set(ids[:5]))
            mrr += next((1 / (i + 1) for i, x in enumerate(ids) if x in exp), 0)
            if show_misses and not exp <= set(ids[:10]):
                print(f"MISS {it['query']!r}: missing {sorted(exp - set(ids[:10]))}; got {ids[:5]}")
    return _finish(mode, len(items), r5, r10, h5, mrr, times)


def load_schema_set(path=None) -> list[dict]:
    """Items {"query", "expected"} from a schema set; lines may use "q" or "query"."""
    out = []
    for line in Path(path or SCHEMA_SET).read_text().splitlines():
        if line.strip():
            it = json.loads(line)
            q, exp = it.get("q") or it.get("query"), [str(x) for x in it.get("expected") or []]
            if q and exp:
                out.append({"query": q, "expected": exp})
    return out


def table_keys(conn, eid: str) -> set[str]:
    """Every name an expected entry may use for the source row eid."""
    r = conn.execute("SELECT database, schema_name, table_name FROM sources WHERE id = ?", (eid,)).fetchone()
    keys = {eid.lower()}
    if r:
        db, sc, t = (x or "" for x in (r["database"], r["schema_name"], r["table_name"]))
        keys |= {k.lower() for k in (t, f"{db}.{t}", ".".join(x for x in (db, sc, t) if x),
                                     ".".join(x for x in (sc, t) if x)) if k}
    return keys


def om_keys(conn, eid: str) -> set[str]:
    """Every name an expected entry may use for an OpenMetadata metric or glossary row."""
    keys = {eid.lower()}
    for sql in ("SELECT name FROM metrics WHERE id = ?", "SELECT term_en, term_fa FROM glossary WHERE id = ?"):
        r = conn.execute(sql, (eid,)).fetchone()
        if r:
            keys |= {str(x).lower() for x in r if x}
    return keys


def score_schema(conn, items: list[dict], mode: str, show_misses: bool = False, lane: str = "schema",
                 keyfn=table_keys) -> dict:
    import vectors
    base, rerank, blend = split_mode(mode)
    r5 = r10 = h5 = mrr = 0.0
    times = []
    with _Blend(blend):
        for it in items:
            t0 = time.perf_counter()
            hits = vectors.search_lane(conn, lane, it["query"], k=10, mode=base, rerank=rerank)
            times.append(time.perf_counter() - t0)
            ranked = [keyfn(conn, r["id"]) for r in hits]
            exp = [e.lower() for e in it["expected"]]

            def found(n):
                return {e for e in exp if any(e in keys for keys in ranked[:n])}
            r5 += len(found(5)) / len(exp)
            r10 += len(found(10)) / len(exp)
            h5 += bool(found(5))
            mrr += next((1 / (i + 1) for i, keys in enumerate(ranked) if keys & set(exp)), 0)
            if show_misses and len(found(10)) < len(exp):
                print(f"MISS {it['query']!r}: missing {sorted(set(exp) - found(10))}")
    return _finish(mode, len(items), r5, r10, h5, mrr, times, lane=lane)


def regressions(scores: dict, baseline: dict) -> list[str]:
    out = []
    for mode, s in scores.items():
        b = (baseline.get("modes") or {}).get(mode)
        if not b:
            continue
        if baseline.get("queries") and s.get("queries") != baseline["queries"]:
            out.append(f"note: the recall set has {s.get('queries')} queries, the baseline {baseline['queries']}; "
                       "re-save the baseline after reviewing (--save-baseline)")
            continue
        for m in ("recall@5", "recall@10"):
            if s[m] < b[m] - TOLERANCE:
                out.append(f"{mode} {m} {s[m]} is below the baseline {b[m]} (tolerance {TOLERANCE})")
    return out


# Cumulative upgrade steps for --matrix: each keeps the earlier ones. norm = Farsi/Arabic normalisation of index and
# queries; header = the one-line context header on the vector text (aliases/questions are indexed in every column
# but stay empty until kb/enrich.py has been run); rerank = cross-encoder over the fused top 30.
CONFIGS = (
    ("baseline (as main)", {"norm": "0", "header": "0", "modes": ("lexical", "hybrid")}),
    ("+ normalisation", {"norm": "1", "header": "0", "modes": ("lexical", "hybrid")}),
    ("+ context header (tried, left off)", {"norm": "1", "header": "1", "modes": ("lexical", "hybrid")}),
    ("+ rerank (on normalisation, no header)", {"norm": "1", "header": "0", "modes": RERANK_MODES}),
)
SETS = {"curated": "recall_set (curated rows)", "schema": "schema_recall_set (tables)", "om": "om_recall_set (OpenMetadata)"}


def _set_env(**kv):
    old = {k: os.environ.get(k) for k in kv}
    for k, v in kv.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    return old


def run_matrix(workdir: Path, lanes=("curated", "schema", "om"), progress=True) -> dict:
    """Build one index per setting under workdir and score every step on every set. Returns
    {step: {lane: {mode: scores}}}. Needs the vecstore daemon (embeddings, and the reranker for the last step)."""
    import vectors
    from vecstore import client
    from vecstore.errors import Unavailable
    workdir.mkdir(parents=True, exist_ok=True)
    cache_rerank()
    out: dict = {}
    built: set[str] = set()
    for name, cfg in CONFIGS:
        db = workdir / f"n{cfg['norm']}.sqlite"
        old = _set_env(VECSTORE_NORMALIZE=cfg["norm"], CLU_KB_HEADER=cfg["header"], CLU_KB_DB=str(db),
                       CLU_KB_VECTORS=str(workdir / f"n{cfg['norm']}h{cfg['header']}.vectors.sqlite"),
                       CLU_KB_SCHEMA_VECTORS=None, CLU_KB_OM_VECTORS=None, CLU_KB_RUNS_VECTORS=None)
        try:
            if cfg["norm"] in built:
                conn = store.connect(str(db))
            else:
                conn = store.rebuild(str(db))  # a fresh index under this normalisation setting
                built.add(cfg["norm"])
            for lane in lanes:
                vectors.index(conn, lane=lane)
            if any(m in RERANK_MODES for m in cfg["modes"]):
                try:
                    client.rerank("warm up", ["warm up the reranker"], 600.0)
                except Unavailable as e:
                    print(f"matrix: reranker unavailable ({e}); skipping the rerank step", file=sys.stderr)
                    continue
            res: dict = {}
            for lane in lanes:
                if lane == "curated":
                    res[lane] = {m: score(conn, m) for m in cfg["modes"]}
                else:
                    import vec_lanes  # noqa: F401
                    items = load_schema_set(None if lane == "schema" else OM_SET)
                    kw = {"lane": lane} if lane == "om" else {}
                    if lane == "om":
                        kw["keyfn"] = om_keys
                    res[lane] = {m: score_schema(conn, items, m, **kw) for m in cfg["modes"]}
            out[name] = res
            if progress:
                print(f"matrix: scored {name}", file=sys.stderr)
        finally:
            _set_env(**old)
    return out


def render_matrix(results: dict) -> str:
    """Markdown table: one row per step and mode, recall@5 and MRR for each set."""
    lanes = [l for l in SETS if any(l in v for v in results.values())]
    head = "| step | mode | " + " | ".join(f"{SETS[l].split(' ')[0]} R@5 | {l} MRR" for l in lanes) + " | p50 ms (curated) |"
    rows = [head, "|" + "---|" * (2 + 2 * len(lanes) + 1)]
    for name, res in results.items():
        for mode in next(iter(res.values())):
            cells = []
            for l in lanes:
                sc = res.get(l, {}).get(mode)
                cells += [f"{sc['recall@5']:.3f}", f"{sc['mrr']:.3f}"] if sc else ["", ""]
            p50 = res.get("curated", {}).get(mode, {}).get("p50_ms", "")
            rows.append(f"| {name} | {mode} | " + " | ".join(cells) + f" | {p50} |")
    return "\n".join(rows)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--matrix", metavar="WORKDIR", help="build an index per upgrade step under WORKDIR, score every "
                    "step on every set and print JSON plus a markdown table (needs the vecstore daemon)")
    ap.add_argument("--matrix-out", help="also write the matrix JSON here")
    ap.add_argument("--mode", default="lexical", choices=["lexical", "hybrid", "hybrid_rerank", "hybrid_rerank_order",
                                                          "both", "all"])
    ap.add_argument("--show-misses", action="store_true")
    ap.add_argument("--save-baseline", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--lane", default="curated", choices=["curated", "schema", "om"])
    ap.add_argument("--set", help="schema lane: an alternative set file (default eval/schema_recall_set.jsonl)")
    a = ap.parse_args(argv)
    if a.matrix:
        res = run_matrix(Path(a.matrix))
        if a.matrix_out:
            Path(a.matrix_out).write_text(json.dumps(res, indent=1, ensure_ascii=False) + "\n")
        print(render_matrix(res))
        return 0
    conn = store.connect()
    global BASELINE
    modes = ["lexical", "hybrid"] if a.mode == "both" else ["lexical", "hybrid", "hybrid_rerank"] if a.mode == "all" \
        or a.save_baseline else [a.mode]
    if "hybrid_rerank" in modes or "hybrid_rerank_order" in modes:
        try:
            from vecstore import client
            client.rerank("probe", ["probe"])
        except Exception as e:  # noqa: BLE001 - no daemon or no reranker: score without it
            print(f"eval_recall: reranker unavailable ({type(e).__name__}); skipping the rerank modes", file=sys.stderr)
            modes = [m for m in modes if m not in RERANK_MODES]
    if a.lane == "schema":
        import vec_lanes
        from vecstore.vecfile import VecFile
        BASELINE = SCHEMA_BASELINE
        if any(m.startswith("hybrid") for m in modes) and not VecFile(vec_lanes.schema_vec_path()).count():
            print("eval_recall: no schema vectors (run `kb.py embed --lane schema`); scoring lexical only",
                  file=sys.stderr)
            modes = ["lexical"]
        items = load_schema_set(a.set)
        scores = {m: score_schema(conn, items, m, a.show_misses) for m in modes}
    elif a.lane == "om":
        import vec_lanes
        from vecstore.vecfile import VecFile
        BASELINE = OM_BASELINE
        if any(m.startswith("hybrid") for m in modes) and not VecFile(vec_lanes.om_vec_path()).count():
            print("eval_recall: no om vectors (run `kb.py embed --lane om`); scoring lexical only", file=sys.stderr)
            modes = ["lexical"]
        items = load_schema_set(a.set or OM_SET)
        scores = {m: score_schema(conn, items, m, a.show_misses, lane="om", keyfn=om_keys) for m in modes}
    else:
        if any(m.startswith("hybrid") for m in modes) and not store.vectors_ready():
            print("eval_recall: no vector index (run `kb.py embed`); scoring lexical only", file=sys.stderr)
            modes = ["lexical"]
        scores = {m: score(conn, m, a.show_misses) for m in modes}
    for s in scores.values():
        print(json.dumps(s))
    if {"hybrid", "hybrid_rerank"} <= set(scores):
        print(json.dumps({"rerank_gain": {m: round(scores["hybrid_rerank"][m] - scores["hybrid"][m], 3) for m in METRICS}}))
    if {"lexical", "hybrid"} <= set(scores):
        print(json.dumps({"hybrid_gain": {m: round(scores["hybrid"][m] - scores["lexical"][m], 3) for m in METRICS}}))
    if a.save_baseline:
        import datetime as _dt
        BASELINE.write_text(json.dumps({"date": _dt.date.today().isoformat(), "queries": scores[modes[0]]["queries"],
                                        "modes": {m: {k: s[k] for k in METRICS} for m, s in scores.items()}},
                                       indent=2) + "\n")
        print(f"saved {BASELINE}", file=sys.stderr)
    if a.check:
        if not BASELINE.exists():
            print("eval_recall: no baseline yet; run with --save-baseline", file=sys.stderr)
            return 1
        bad = regressions(scores, json.loads(BASELINE.read_text()))
        for b in bad:
            print(f"REGRESSION {b}", file=sys.stderr)
        return 1 if bad else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
