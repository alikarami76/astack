# scripts/qindex/symvec.py
"""Optional semantic search over index symbols, reusing the KB's local embedding model (kb/vectors.py)."""
from __future__ import annotations

import hashlib
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "kb"))

INSTRUCT = "Instruct: Given a developer's description of some logic, retrieve the code symbol that implements it\nQuery: "
EMBED_KINDS = ("entity", "service", "repository", "usecase", "method", "table", "route", "component", "hook",
               "api_client", "console_command", "consumer", "controller", "view", "form")


def _vpath(db_path) -> Path:
    return Path(str(db_path).replace(".sqlite", ".vectors.sqlite"))


def _text(r) -> str:
    return f"{r['kind']} {r['name']}\n{r['fqname']}\n{r['signature']}\n{r['file']}"[:600]


def index(db_path) -> dict:
    import numpy as np
    import vectors  # kb/vectors.py: optional numpy + torch + transformers
    src = sqlite3.connect(db_path)
    src.row_factory = sqlite3.Row
    vc = sqlite3.connect(_vpath(db_path))
    vc.execute("CREATE TABLE IF NOT EXISTS vecs (h TEXT PRIMARY KEY, sym_id INTEGER, vec BLOB)")
    have = {h for (h,) in vc.execute("SELECT h FROM vecs")}
    rows = src.execute(f"SELECT * FROM symbols WHERE kind IN ({','.join('?' * len(EMBED_KINDS))})", EMBED_KINDS).fetchall()
    live, todo = {}, []
    for r in rows:
        h = hashlib.sha1(_text(r).encode()).hexdigest()[:16]
        live[h] = r["id"]
        if h not in have:
            todo.append((h, r["id"], _text(r)))
    for i in range(0, len(todo), 128):
        chunk = todo[i:i + 128]
        vecs = vectors.embed([t[2] for t in chunk])
        vc.executemany("INSERT OR REPLACE INTO vecs VALUES (?,?,?)", [(h, sid, v.astype(np.float16).tobytes()) for (h, sid, _), v in zip(chunk, vecs)])
        vc.commit()
    vc.executemany("UPDATE vecs SET sym_id = ? WHERE h = ?", [(sid, h) for h, sid in live.items()])
    stale = [h for h in have if h not in live]
    vc.executemany("DELETE FROM vecs WHERE h = ?", [(h,) for h in stale])
    vc.commit()
    return {"embedded": len(todo), "removed": len(stale), "total": len(live)}


def semantic(db_path, query: str, limit: int = 50) -> list[int]:
    p = _vpath(db_path)
    if not p.exists():
        raise FileNotFoundError(p)
    vc = sqlite3.connect(p)
    try:
        rows = vc.execute("SELECT sym_id, vec FROM vecs").fetchall()
    finally:
        vc.close()
    if not rows:
        return []
    import numpy as np
    import vectors
    mat = np.frombuffer(b"".join(r[1] for r in rows), dtype=np.float16).reshape(len(rows), -1).astype(np.float32)
    q = vectors.embed([INSTRUCT + query])[0].astype(np.float32)
    return [rows[i][0] for i in np.argsort(-(mat @ q))[:limit]]
