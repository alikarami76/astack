#!/usr/bin/env python3
"""Rinzler's safety rules in code, so every script enforces them the same way.

  safety.py target <url> --purpose read|write|load [--approval <text>]   exit 0 allowed, 3 needs approval, 4 refused
  safety.py path <path>                                                  exit 4 if Rinzler may not write there
  safety.py sql <file or ->                                              exit 4 unless the SQL is read-only
  safety.py redact <file or ->                                           prints the text with secrets masked

Rules (docs/architecture.md, "Safety"):
- never write under ~/code; Rinzler builds in its own work folder;
- never read secret files into evidence (.env*, .npmrc, login state, credentials);
- local targets are free; demo and stage are fine for reading and functional writes, but load tests there need the
  owner's approval because the environments are shared; production is never written to, and reading or load-testing
  it needs the owner's explicit approval;
- database access is SELECT-only.
"""
from __future__ import annotations

import argparse
import fnmatch
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rpaths  # noqa: E402

LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "host.docker.internal"}
PURPOSES = ("read", "write", "load")
SECRET_GLOBS = (".env", ".env.*", "*.env", ".npmrc", ".yarnrc", "auth-state.json", "*credentials*", "*.pem", "*.key",
                "id_rsa*", "*secret*", "*token*", "auth.json", "storageState*.json")
SECRET_ALLOW = (".env.example", "auth-state.example.json")


class Refused(Exception):
    """The action breaks a safety rule."""


class NeedsApproval(Exception):
    """The action is allowed only with the owner's explicit approval."""


def classify_host(url: str) -> str:
    """local, demo, stage, test or production. Anything unknown counts as production."""
    host = (urlparse(url if "//" in url else f"//{url}").hostname or "").lower()
    if host in LOCAL_HOSTS or host.endswith(".local") or host.endswith(".localhost"):
        return "local"
    labels = re.split(r"[.-]", host)
    if "demo" in labels:
        return "demo"
    if "stage" in labels or "staging" in labels or "stg" in labels:
        return "stage"
    if host.startswith("backend-") or "test" in labels:  # the backend TEST environment (backend-*.example.com)
        return "test"
    return "production"


def check_target(url: str, purpose: str, approval: str | None = None) -> str:
    """Return the environment when allowed; raise NeedsApproval or Refused otherwise."""
    if purpose not in PURPOSES:
        raise ValueError(f"purpose must be one of {PURPOSES}")
    env = classify_host(url)
    approved = bool(approval and approval.strip())
    if env == "local":
        return env
    if env in ("demo", "stage", "test"):
        if purpose == "load" and not approved:
            raise NeedsApproval(f"load test on the shared {env} environment ({url}) needs the owner's approval")
        return env
    if purpose == "write":
        raise Refused(f"{url} looks like production; Rinzler never writes to production")
    if not approved:
        raise NeedsApproval(f"{url} looks like production; {purpose} needs the owner's explicit approval")
    return env


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def check_writable(path: str | Path) -> Path:
    """Refuse a write under the owner's your company clones."""
    p = Path(path).expanduser()
    if _inside(p, rpaths.dk_code()):
        raise Refused(f"{p} is inside {rpaths.dk_code()}; Rinzler never writes to your company repos (use {rpaths.work_dir()})")
    return p


def is_secret_path(path: str | Path) -> bool:
    name = Path(path).name
    if name in SECRET_ALLOW:
        return False
    if any(part in ("secrets", ".secrets") for part in Path(path).parts):
        return True
    return any(fnmatch.fnmatch(name.lower(), g.lower()) for g in SECRET_GLOBS)


_REDACTIONS = [
    (re.compile(r"(?i)(authorization\s*[:=]\s*)(bearer|basic|token)?\s*[^\s\"',;]+"), r"\1\2 <redacted>"),
    (re.compile(r"(?i)((?:set-)?cookie\s*[:=]\s*)[^\n\"]+"), r"\1<redacted>"),
    (re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), "<jwt-redacted>"),
    (re.compile(r"(?i)(\"?(?:password|passwd|secret|token|api[_-]?key|access[_-]?key|client[_-]?secret|otp)\"?\s*[:=]\s*\"?)"
                r"[^\s\"',;&]+"), r"\1<redacted>"),
    (re.compile(r"(?<!\d)(98|0)(9\d{2})\d{5}(\d{2})(?!\d)"), r"\1\2*****\3"),  # Iranian mobile numbers
    (re.compile(r"\b([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b"), r"\1***@\2"),
]


def redact(text: str) -> str:
    for pattern, repl in _REDACTIONS:
        text = pattern.sub(repl, text)
    return text


_SQL_COMMENT = re.compile(r"(--[^\n]*|#[^\n]*|/\*.*?\*/)", re.S)
_SQL_STRING = re.compile(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"")
_SQL_OK = ("select", "with", "explain", "show", "describe", "desc")
_SQL_BAD = re.compile(r"\b(insert|update|delete|replace|merge|drop|alter|create|truncate|rename|grant|revoke|call|"
                      r"lock|unlock|set|load|handler|do|outfile|dumpfile|sleep|benchmark|kill|shutdown)\b", re.I)


def sql_is_readonly(sql: str) -> tuple[bool, str]:
    body = _SQL_STRING.sub("''", _SQL_COMMENT.sub(" ", sql))
    stmts = [s.strip() for s in body.split(";") if s.strip()]
    if not stmts:
        return False, "no statement"
    for s in stmts:
        first = s.split(None, 1)[0].lower()
        if first not in _SQL_OK:
            return False, f"statement starts with {first!r}; only SELECT, WITH, EXPLAIN, SHOW and DESCRIBE are allowed"
        bad = _SQL_BAD.search(s)
        if bad:
            return False, f"forbidden keyword {bad.group(1)!r}"
        if re.search(r"\bfor\s+update\b", s, re.I):
            return False, "SELECT ... FOR UPDATE takes locks"
    return True, "ok"


def _read(arg: str) -> str:
    return sys.stdin.read() if arg == "-" else Path(arg).read_text(encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="safety.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("target")
    t.add_argument("url")
    t.add_argument("--purpose", choices=PURPOSES, required=True)
    t.add_argument("--approval")
    p = sub.add_parser("path")
    p.add_argument("path")
    s = sub.add_parser("sql")
    s.add_argument("file")
    r = sub.add_parser("redact")
    r.add_argument("file")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "target":
            print(check_target(a.url, a.purpose, a.approval))
        elif a.cmd == "path":
            print(check_writable(a.path))
        elif a.cmd == "sql":
            ok, why = sql_is_readonly(_read(a.file))
            print(why)
            return 0 if ok else 4
        elif a.cmd == "redact":
            if a.file != "-" and is_secret_path(a.file):
                raise Refused(f"{a.file} is a secret file; Rinzler does not read it")
            sys.stdout.write(redact(_read(a.file)))
    except NeedsApproval as e:
        print(f"NEEDS APPROVAL: {e}", file=sys.stderr)
        return 3
    except Refused as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
