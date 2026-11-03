#!/usr/bin/env python3
"""Cache reviewer discussions from recently merged MRs. GitLab GET requests only.
  fetch_mr_reviews.py api-app --run runs/2026-09-30-research-api-app --limit 100
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import qpaths  # noqa: E402
from qindex.redact import scrub  # noqa: E402
from gitlab_client import SYSTEM_CA, _SameHostRedirect, make_ssl_context  # noqa: E402,F401

CREDENTIALS = Path(os.environ.get("QUORRA_GITLAB_READ_CREDENTIALS") or Path.home() / ".config/quorra/gitlab-read.json")
TRIVIAL = re.compile(r"^\s*(lgtm|\+1|👍|ok|done|thanks?|fixed|approved( this merge request)?|resolved)\W*\s*$", re.I)
BOT = re.compile(r"^(?:\w+[-_])?(?:bot|danger|sonar(?:qube|cloud)?|gitlab[-_]?ci|renovate)(?:[-_]\w+)?$|^project_\d+_bot\w*$", re.I)


def is_substantive(body: str) -> bool:
    return len(body.strip()) >= 20 and not TRIVIAL.match(body)


def http_get_json(endpoint: str, params: dict, cfg: dict | None = None) -> object:
    cfg = cfg if cfg is not None else json.loads(CREDENTIALS.read_text())
    if urllib.parse.urlsplit(cfg["base_url"]).scheme != "https":
        raise ValueError("GitLab base_url must be https: the access token is never sent over plain http")
    url = f"{cfg['base_url']}/api/v4{endpoint}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"PRIVATE-TOKEN": cfg["personal_access_token"], "Accept": "application/json"},
                                 method="GET")
    opener = urllib.request.build_opener(_SameHostRedirect, urllib.request.HTTPSHandler(context=make_ssl_context()))
    with opener.open(req, timeout=60) as r:
        return json.loads(r.read())


def is_bot(author: dict) -> bool:
    return author.get("bot") is True or bool(BOT.match(author.get("username") or ""))


def mr_notes(pid: str, iid: int, get_json=http_get_json) -> list[dict]:
    """Human (non-system, non-bot) notes of one MR, with discussions paginated."""
    discussions, page = [], 1
    while True:
        chunk = get_json(f"/projects/{pid}/merge_requests/{iid}/discussions", {"per_page": 100, "page": page})
        discussions += chunk or []
        if not chunk or len(chunk) < 100:
            break
        page += 1
    return [n for d in discussions for n in d.get("notes", [])
            if not n.get("system") and not is_bot(n.get("author") or {})]


def fetch(project: str, ref: str, limit: int, get_json=http_get_json, stats: dict | None = None) -> list[dict]:
    pid = urllib.parse.quote(project, safe="")
    mrs, page = [], 1
    while len(mrs) < limit:
        batch = get_json(f"/projects/{pid}/merge_requests", {"state": "merged", "target_branch": ref, "order_by": "updated_at",
                                                              "sort": "desc", "per_page": min(100, limit), "page": page})
        if not batch:
            break
        mrs += batch
        if len(batch) < min(100, limit):
            break
        page += 1
    mrs.sort(key=lambda m: m.get("merged_at") or "", reverse=True)
    mrs = mrs[:limit]
    rows = []
    if stats is not None:
        stats["mrs_fetched"] = len(mrs)
    for mr in mrs:
        for n in mr_notes(pid, mr["iid"], get_json):
            author = n.get("author") or {}
            pos = n.get("position") or {}
            rows.append({"mr_iid": mr["iid"], "mr_title": scrub(mr.get("title") or ""), "merged_at": mr.get("merged_at"),
                         "mr_author": (mr.get("author") or {}).get("username"), "note_author": author.get("username"),
                         "body": scrub(n.get("body") or ""), "file": pos.get("new_path") or pos.get("old_path"),
                         "line": pos.get("new_line") or pos.get("old_line"), "resolved": n.get("resolved")})
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="fetch_mr_reviews.py")
    ap.add_argument("name")
    ap.add_argument("--run", required=True)
    ap.add_argument("--limit", type=int, default=100)
    a = ap.parse_args(argv)
    cfg = qpaths.repo_cfg(a.name)
    st: dict = {}
    rows = fetch(cfg["gitlab"], cfg["ref"], a.limit, stats=st)
    out = Path(a.run) / a.name
    out.mkdir(parents=True, exist_ok=True)
    (out / "reviews.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    subst = sum(is_substantive(r["body"]) for r in rows)
    print(json.dumps({"mrs_fetched": st["mrs_fetched"], "mrs": len({r["mr_iid"] for r in rows}), "notes": len(rows), "substantive": subst, "low_evidence": subst < 20}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
