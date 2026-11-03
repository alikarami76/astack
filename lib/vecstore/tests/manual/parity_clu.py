"""Manual check (loads the real model twice): the vecstore daemon must encode exactly like the original
clu/kb/vectors.py. Run from agent-stack: python3 lib/vecstore/tests/manual/parity_clu.py

It verified parity before the migration (2026-09-30, cosine 1.0). It relies on the pre-migration vectors.py, so it
must be run against that module, e.g. `git show 13bec4d:clu/kb/vectors.py > /tmp/vectors_old.py` and importing that."""
import sqlite3
import sys
from pathlib import Path

import numpy as np

ASTACK = Path(__file__).resolve().parents[4]
sys.path[:0] = [str(ASTACK / "clu" / "kb"), str(ASTACK / "lib")]
import store  # noqa: E402
import vectors  # noqa: E402  (the original, pre-migration module)
from vecstore import client  # noqa: E402

c = store.connect()
rows = c.execute("SELECT eid, title, body FROM fts WHERE etype IN ('metric', 'lesson', 'fact') LIMIT 6").fetchall()
docs = [vectors._doc_text(r["title"] or "", r["body"] or "") for r in rows]
queries = [vectors.INSTRUCT + store.expand_query(c, q) for q in
           ("buybox winner price", "سهم فروش ویژه از NMV", "why is clickhouse slow")]
old = vectors.embed(docs + queries)
new = client.embed(docs + queries, timeout=120)
cos = (old * new).sum(axis=1)
print("cosine min", round(float(cos.min()), 5), "per row", np.round(cos, 5).tolist())
stored = dict(sqlite3.connect(vectors.vec_path()).execute("SELECT eid, vec FROM vecs"))
for r, v in zip(rows, new[:len(docs)]):
    if r["eid"] in stored:
        sv = np.frombuffer(stored[r["eid"]], dtype=np.float16).astype(np.float32)
        print(r["eid"], "vs stored", round(float(sv @ v), 5))
ok = cos.min() > 0.999
print("PARITY OK" if ok else "PARITY FAILED")
raise SystemExit(0 if ok else 1)
