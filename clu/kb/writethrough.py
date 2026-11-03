"""Mirror clu KB writes into the legacy skill memories. Never raises."""
from __future__ import annotations

import datetime as _dt
import os
import re
import subprocess
import sys
from pathlib import Path

import store

sys.path.insert(0, str(store.REPO / "hooks"))
from redact import redact as _redact  # noqa: E402

DBA_ENGINES = {e.strip() for e in os.environ.get("CLU_DBA_ENGINES", "warehouse").split(",") if e.strip()}  # engines whose notes are mirrored
HOME = Path.home()


def _enabled() -> bool:
    return os.environ.get("CLU_WRITETHROUGH", "1") != "0"


def _tool() -> Path:
    return Path(os.environ.get("CLU_DBA_MEMORY_TOOL", HOME / ".claude/skills/db-analyst/scripts/memory_tool.py"))


def _scales() -> Path:
    return Path(os.environ.get("CLU_SCALES_MEMORY", HOME / ".claude/skills/bi-analyst/memory"))


def _log(msg: str) -> None:
    try:
        p = Path(os.environ.get("CLU_KB_DB", store.REPO / "kb" / "kb.sqlite")).with_name("writethrough.log")
        with p.open("a") as f:
            f.write(f"{_dt.datetime.now().isoformat(timespec='seconds')} {msg}\n")
    except Exception:  # noqa: BLE001 - logging must never break KB writes
        pass


def _run(args: list[str]) -> bool:
    tool = _tool()
    if not tool.exists():
        _log(f"missing tool {tool}")
        return False
    r = subprocess.run([sys.executable, str(tool), *args], capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        stderr = _redact(r.stderr[-300:])
        _log(f"dba {args[0]} failed rc={r.returncode}: {stderr}")
    return r.returncode == 0


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60]


def mirror(conn, etype: str, eid: str) -> list[str]:
    if not _enabled() or etype not in ("fact", "recipe"):
        return []
    try:
        row = store.get(conn, eid)
        src = store.get(conn, row["source_id"]) if row and row.get("source_id") else None
        if not src:
            return []
        if src["kind"] == "mysql" and src.get("engine") in DBA_ENGINES:
            eng, tbl = src["engine"], src["table_name"]
            if etype == "fact":
                section = "Verified facts" if row.get("confidence") == "verified" else "Gotchas"
                args = ["note", "--engine", eng, "--table", tbl, "--section", section,
                        "--text", row["statement"], "--evidence", row.get("evidence") or f"clu {row.get('origin') or ''}".strip()]
                ok = _run(args)
                if not ok and _tool().exists():
                    _run(["stub", "--engine", eng, "--table", tbl, "--summary", src.get("grain") or tbl])
                    ok = _run(args)
                return [f"dba:note {eng}.{tbl}"] if ok else []
            args = ["recipe", "--slug", _slug(row["name"]), "--title", row["name"], "--summary", row.get("purpose") or "",
                    "--sql", row.get("sql") or "", "--engines", eng, "--tables", tbl, "--evidence", row.get("origin") or "clu"]
            return [f"dba:recipe {_slug(row['name'])}"] if _run(args) else []
        if src["kind"] == "metabase" and etype == "fact":
            p = _scales() / src["database"] / (src.get("schema_name") or "default") / "tables" / f"{src['table_name']}.md"
            p.parent.mkdir(parents=True, exist_ok=True)
            if not p.exists():
                p.write_text(f"# `{src['table_name']}`\n\n## Knowledge\n")
            elif any(row["statement"] in line for line in p.read_text().splitlines()):
                return []  # already mirrored: skip the duplicate append
            with p.open("a") as f:
                f.write(f"\n- {row['statement']} _(clu, {_dt.date.today().isoformat()}, {row.get('confidence') or 'observed'})_\n")
            return [f"scales:append {src['database']}/{src.get('schema_name') or 'default'}/{src['table_name']}"]
    except Exception as e:  # noqa: BLE001 - mirroring must never break a KB write
        _log(f"mirror {eid} error: {e!r}")
    return []
