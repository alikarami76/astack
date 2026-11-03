"""Identifier splitting and URL normalisation shared by extraction and lookup."""
from __future__ import annotations

import re

_CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+|[^\W\d_]+", re.UNICODE)


def split_words(name: str) -> list[str]:
    out: list[str] = []
    for chunk in re.split(r"[\s_\-.:\\/$]+", name):
        if not chunk:
            continue
        if chunk.isascii():
            out += [w.lower() for w in _CAMEL.findall(chunk)]
        else:
            out.append(chunk.lower())
    return out


def words_text(name: str) -> str:
    return " ".join(split_words(name))


def norm_url(url: str) -> str:
    u = url.strip().strip("'\"`")
    # Cut at first ? or # (query params and fragments)
    u = re.split(r"[?#]", u)[0]
    u = re.sub(r"^[a-z]+://[^/]+", "", u, flags=re.I)
    u = re.sub(r"\$\{[^}]*\}|\{[^}]*\}|(?<=/):\w+", "{}", u)
    u = "/" + u.lstrip("/")
    u = re.sub(r"^/api/v\d+(?=/|$)", "", u, flags=re.I)
    u = re.sub(r"/{2,}", "/", u).rstrip("/").lower()
    return u or "/"
