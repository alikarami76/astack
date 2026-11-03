"""Split markdown into heading sections small enough for the model (Qwen reads about 384 tokens per entry).
Each chunk's text starts with "<title> › <heading path>" so the context travels with the body."""
from __future__ import annotations

import re
from pathlib import Path

MAX_CHARS = 1200
_HEAD = re.compile(r"^(#{1,3})\s+(.+?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")


def slug(s: str) -> str:
    return re.sub(r"[^\w]+", "-", s.lower(), flags=re.UNICODE).strip("-") or "section"


def _split(body: str) -> list[str]:
    parts, cur = [], ""
    for para in re.split(r"\n\s*\n", body):
        para = para.strip()
        while len(para) > MAX_CHARS:  # one huge paragraph: hard split
            if cur:
                parts.append(cur)
                cur = ""
            parts.append(para[:MAX_CHARS])
            para = para[MAX_CHARS:]
        if cur and len(cur) + len(para) + 2 > MAX_CHARS:
            parts.append(cur)
            cur = ""
        cur = f"{cur}\n\n{para}" if cur else para
    if cur:
        parts.append(cur)
    return [p for p in parts if p.strip()]


def chunk_text(text: str, title: str) -> list[tuple[str, str]]:
    sections: list[tuple[list[str], list[str]]] = []
    stack: list[tuple[int, str]] = []
    lines: list[str] = []
    in_fence = False

    def flush():
        if any(l.strip() for l in lines):
            sections.append(([h for _, h in stack], list(lines)))
        lines.clear()

    for line in text.splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
        m = None if in_fence else _HEAD.match(line)
        if m:
            flush()
            level = len(m.group(1))
            stack = [(lv, h) for lv, h in stack if lv < level] + [(level, m.group(2).strip())]
        else:
            lines.append(line)
    flush()
    out = []
    for heads, body in sections:
        path = " › ".join(heads)
        prefix = f"{title} › {path}" if path else title
        for part in _split("\n".join(body)):
            out.append((path, f"{prefix}\n{part}"))
    return out


def chunk_file(path: Path, root: Path, text: str | None = None) -> list[tuple[str, str, str]]:
    """text overrides the file's contents (a store that cleans its files first, e.g. capping code blocks)."""
    rel = path.relative_to(root).as_posix()
    text = path.read_text(errors="replace") if text is None else text
    title = rel.rsplit(".", 1)[0] if path.suffix == ".md" else rel
    parts = chunk_text(text, title) if path.suffix == ".md" else [("", f"{title}\n{p}") for p in _split(text)]
    out, issued_ids = [], set()
    for heading, body in parts:
        base = slug(heading.rsplit(" › ", 1)[-1]) if heading else "top"
        eid = f"{rel}#{base}"
        if eid not in issued_ids:
            issued_ids.add(eid)
        else:
            # base slug already used; find next available number
            n = 2
            while f"{rel}#{base}-{n}" in issued_ids:
                n += 1
            eid = f"{rel}#{base}-{n}"
            issued_ids.add(eid)
        out.append((eid, heading, body))
    return out
