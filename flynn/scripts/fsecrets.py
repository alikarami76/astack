"""flynn desk's secrets (canvas passwords, connector keys). Never print a value.

get(account) looks, in order:
  1. env FLYNN_SECRET_<ACCOUNT> (upper case, "-" becomes "_"), for containers that inject secrets;
  2. the secrets folder $FLYNN_SECRETS_DIR/<account> (files mode 600), for a container's mounted volume;
  3. the macOS keychain, service flynn-penpot, as on the user's Mac.
set(account, value) writes to the secrets folder when FLYNN_SECRETS_DIR is set, else to the keychain.
Stdlib only.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

SERVICE = "flynn-penpot"
ACCOUNT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def _check(account: str) -> str:
    if not ACCOUNT.match(account or "") or ".." in account:
        raise ValueError("bad secret account name")
    return account


def env_name(account: str) -> str:
    return "FLYNN_SECRET_" + _check(account).upper().replace("-", "_").replace(".", "_")


def _folder() -> Path | None:
    d = os.environ.get("FLYNN_SECRETS_DIR")
    return Path(d) if d else None


def _keychain_get(account: str) -> str | None:
    if not shutil.which("security"):                     # not a Mac (a container): there is no keychain
        return None
    r = subprocess.run(["security", "find-generic-password", "-s", SERVICE, "-a", account, "-w"],
                       capture_output=True, text=True)
    return r.stdout.strip() or None


def _keychain_set(account: str, value: str) -> None:
    subprocess.run(["security", "add-generic-password", "-U", "-s", SERVICE, "-a", account, "-w", value],
                   check=True, capture_output=True)


def get(account: str) -> str | None:
    v = os.environ.get(env_name(account))
    if v:
        return v
    d = _folder()
    if d:
        try:
            v = (d / account).read_text(encoding="utf-8").strip()
        except OSError:
            v = ""
        if v:
            return v
    return _keychain_get(account)


def set(account: str, value: str) -> None:  # noqa: A001 - fsecrets.set reads best
    _check(account)
    d = _folder()
    if not d:
        _keychain_set(account, value)
        return
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = d / f".{account}.tmp"
    tmp.unlink(missing_ok=True)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)   # 600 from the start, never readable by others
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(value)
    tmp.replace(d / account)
