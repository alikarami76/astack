"""Shared secret redaction for clu. Single source of truth for both
hooks/telemetry.py and kb/writethrough.py.

Redacts:
- key/value secrets: password|passwd|pwd|pass|token|secret|api[_-]?key|
  authorization|bearer, with an optional quote before the separator,
  keeping a leading Bearer/Basic scheme word;
- `-p<value>` short flags (e.g. mysql -phunter2);
- credentials embedded in a URL (scheme://user:pass@host).
"""
from __future__ import annotations

import re

_KEYWORD = r"(?i)(password|passwd|pwd|pass|token|secret|api[_-]?key|authorization|bearer)"
_SEP = r"(\s*[\"']?\s*[=:]\s*[\"']?|\s+)"
_SCHEME = r"((?:bearer|basic)\s+)?"
SECRET = re.compile(_KEYWORD + _SEP + _SCHEME + r"(\S+)")
SHORT_P_FLAG = re.compile(r"(?<=\s)-p\S+")
URL_CREDS = re.compile(r"://([^:/\s]+):[^@\s]+@")


def redact(s: str) -> str:
    if not s:
        return s
    s = SECRET.sub(lambda m: f"{m.group(1)}{m.group(2)}{m.group(3) or ''}***", s)
    s = SHORT_P_FLAG.sub("-p***", s)
    s = URL_CREDS.sub(r"://\1:***@", s)
    return s
