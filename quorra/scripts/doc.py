#!/usr/bin/env python3
"""quorra documentation output: draft from a template, check, plan, publish to Confluence (one configured space only) after the user's fingerprint.
  doc.py new <run-dir> <trd|contract|decision> <slug>
  doc.py check <file>
  doc.py plan <file> --space DOCS [--parent ID] [--page ID]
  doc.py publish <file> --approve FINGERPRINT
  doc.py diff <doc_id>
  doc.py status
quorra only creates pages and updates pages it created (state/pages.json) while their live version is the one it last wrote.
There is no delete and no move. Errors go to stderr as {"error": ...} with exit 2."""
from __future__ import annotations

import argparse
import datetime as _dt
import difflib
import hashlib
import os
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import confluence_client  # noqa: E402
import doc_check  # noqa: E402
import gitsrc  # noqa: E402
import jira_ops  # noqa: E402
import kb_cite  # noqa: E402
import qpaths  # noqa: E402
import run_record  # noqa: E402
from work import _safe  # noqa: E402

ALLOWED_SPACE = os.environ.get("QUORRA_CONFLUENCE_SPACE", "DOCS")
TEMPLATES = HERE.parent / "templates"
TYPES = ("trd", "contract", "decision")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class DocError(Exception):
    pass


def _pages_path() -> Path:
    return qpaths.state_dir() / "pages.json"


def _registry() -> dict:
    p = _pages_path()
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"pages": {}}


def _save_registry(reg: dict) -> None:
    p = _pages_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(reg, indent=1, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def _client(client):
    return client or confluence_client.Confluence()


def _author() -> str:
    """Author name: ASTACK_FULL_NAME in ~/.claude/astack/profile.env, else `git config user.name`, else a visible placeholder."""
    try:
        for line in (Path.home() / ".claude" / "astack" / "profile.env").read_text(encoding="utf-8").splitlines():
            key, _, val = line.strip().partition("=")
            if key.strip() == "ASTACK_FULL_NAME" and val.strip().strip("'\""):
                return val.strip().strip("'\"")
    except OSError:
        pass
    try:
        name = subprocess.run(["git", "config", "user.name"], capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        name = ""
    return name or "<your name>"


def new(run: Path, type_: str, slug: str) -> Path:
    run = Path(run)
    if type_ not in TYPES:
        raise DocError(f"type must be one of {', '.join(TYPES)}")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", slug or ""):
        raise DocError("slug must be lowercase letters, digits and hyphens")
    dest = run / "docs" / f"{type_}-{slug}.md"
    if dest.exists():
        raise DocError(f"{dest} already exists")
    text = (TEMPLATES / f"{type_}.md").read_text(encoding="utf-8")
    text = text.replace("doc_id=<id>", f"doc_id={type_}-{slug}", 1).replace("run=<run>", f"run={run.name}", 1)
    text = text.replace("# <title>", "# " + slug.replace("-", " "), 1).replace("<date>", _dt.date.today().isoformat())
    text = text.replace("<author>", _author())
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")
    return dest


def _body(text: str) -> tuple[str, str]:
    lines = text.split("\n")
    idx = next((i for i, ln in enumerate(lines[:5]) if doc_check.META_RE.match(ln.strip())), None)
    if idx is None:
        raise DocError("the draft has no quorra-doc metadata line")
    meta_line = lines.pop(idx).strip()
    title_ix = next((i for i, ln in enumerate(lines) if ln.startswith("# ")), None)
    if title_ix is None or not lines[title_ix][2:].strip():
        raise DocError("the draft has no '# ' title line")
    title = lines.pop(title_ix)[2:].strip()
    md = doc_check.COMMENT_RE.sub("", "\n".join(lines))
    html = confluence_client.md_to_storage(md.strip("\n"))
    if (doc_check.parse_meta(text).get("type")) == "decision":
        html = f'<div dir="rtl">{html}</div>'
    return title, html + "\n" + meta_line


def _plan_path(path: Path) -> Path:
    path = Path(path)
    return path.with_name(path.name + ".plan.json")


def _space_key(page: dict) -> str | None:
    return (page.get("space") or {}).get("key")


def _build(path: Path, space: str, parent: str | None, page: str | None, client=None) -> tuple[dict, str, str, dict]:
    """The plan, the title, the exact body the plan fingerprints, and the meta. Writes nothing."""
    if space != ALLOWED_SPACE:
        raise DocError(f"quorra publishes only to space {ALLOWED_SPACE}, not {space!r}")
    text = Path(path).read_text(encoding="utf-8")
    meta = doc_check.parse_meta(text)
    cited = {c["repo"] for c in kb_cite.parse_text("\n".join(doc_check._strip_comments(text)), "", set(qpaths.load_repos()))
             if c["form"] == "repo_path"}
    for repo in sorted(cited - set(meta.get("shas") or {})):
        raise DocError(f"no commit recorded for {repo} in the meta line")
    problems = doc_check.check(Path(path))
    if problems:
        raise DocError("the draft has problems: " + "; ".join(f"line {p['line']}: {p['reason']}" for p in problems))
    doc_id = meta.get("doc_id", "")
    if not ID_RE.match(doc_id) or not ID_RE.match(meta.get("run", "")):
        raise DocError("doc_id and run in the metadata line must match ^[a-z0-9][a-z0-9-]*$")
    title, body = _body(text)
    entry = _registry()["pages"].get(doc_id)
    if entry is None:
        if page:
            raise DocError("quorra only updates pages it created; this doc_id is not in the registry")
        if not parent:
            raise DocError("a new page needs --parent")
        pk = _space_key(_client(client).fetch(parent))
        if pk != ALLOWED_SPACE:
            raise DocError(f"the parent page {parent} is in space {pk!r}, not {ALLOWED_SPACE}")
        mode, page_id, version = "create", None, None
    else:
        if page and str(page) != str(entry["page_id"]):
            raise DocError(f"--page {page} differs from the registered page {entry['page_id']}")
        mode, page_id, version = "update", str(entry["page_id"]), entry["version"]
        live_page = _client(client).fetch(page_id)
        if _space_key(live_page) != ALLOWED_SPACE:
            raise DocError(f"page {page_id} is in space {_space_key(live_page)!r}, not {ALLOWED_SPACE}")
        live = live_page["version"]["number"]
        if live != version:
            raise DocError(f"the page was edited since quorra last wrote it (v{live} vs v{version}); run doc.py diff {doc_id}")
    out = {"doc_id": doc_id, "mode": mode, "space": space, "parent": parent, "page_id": page_id, "title": title,
           "version": version, "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest()}
    out["fingerprint"] = jira_ops._fp(out)
    return out, title, body, meta


def plan(path: Path, space: str, parent: str | None, page: str | None, client=None) -> dict:
    out, _t, _b, _m = _build(path, space, parent, page, client)
    _plan_path(path).write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    return out


def publish(path: Path, approve: str, client=None) -> dict:
    pp = _plan_path(path)
    if not pp.exists():
        raise DocError("no saved plan; run doc.py plan first")
    saved = json.loads(pp.read_text(encoding="utf-8"))
    client = _client(client)
    fresh, title, body, meta = _build(path, saved["space"], saved["parent"], saved["page_id"], client)
    if not (fresh["fingerprint"] == approve == saved["fingerprint"]):
        raise DocError(f"fingerprint mismatch: the approved plan {approve} no longer matches the draft or the page ({fresh['fingerprint']}); re-plan and re-approve")
    if fresh["mode"] == "create":
        try:
            resp = client.create(fresh["space"], title, body, fresh["parent"])
        except confluence_client.ConfluenceError as e:
            raise confluence_client.ConfluenceError(_safe(f"{e}; the page may exist already; check {ALLOWED_SPACE} for '{title}' before retrying")) from None
    else:
        resp = client.update(fresh["page_id"], title, body, fresh["version"])
    doc_id = fresh["doc_id"]
    page_id, url = str((resp or {}).get("id") or fresh["page_id"] or ""), ""
    try:
        url = client.page_url(resp)
        version = ((resp.get("version") or {}).get("number"))
        reg = _registry()
        reg["pages"][doc_id] = {"page_id": page_id, "space": fresh["space"], "title": title, "url": url, "run": meta["run"],
                                "version": version, "updated": _dt.datetime.now().isoformat(timespec="seconds")}
        _save_registry(reg)
        saved_body = qpaths.state_dir() / "pages" / f"{doc_id}.html"
        saved_body.parent.mkdir(parents=True, exist_ok=True)
        saved_body.write_text(body, encoding="utf-8")
        if version is None:
            raise ValueError("the response had no version number")
        run_dir = qpaths.runs_dir() / meta["run"]
        if (run_dir / "run.json").exists():
            docs = json.loads((run_dir / "run.json").read_text(encoding="utf-8")).get("docs", [])
            run_record.set_(run_dir, "docs", docs + [{"doc_id": doc_id, "type": meta["type"], "file": str(Path(path)), "url": url, "version": version}])
    except Exception as e:
        raise DocError(_safe(f"page {page_id} was written at {url} but quorra could not record it: {e}")) from None
    return {"doc_id": doc_id, "url": url, "version": version, "mode": fresh["mode"]}


def _entry(doc_id: str) -> dict:
    entry = _registry()["pages"].get(doc_id)
    if entry is None:
        raise DocError(f"{doc_id} is not in quorra's page registry")
    return entry


def diff(doc_id: str, client=None) -> str:
    entry = _entry(doc_id)
    saved = qpaths.state_dir() / "pages" / f"{doc_id}.html"
    ours = saved.read_text(encoding="utf-8") if saved.exists() else ""
    live = _client(client).fetch(entry["page_id"])["body"]["storage"]["value"]
    return "".join(difflib.unified_diff(ours.splitlines(True), live.splitlines(True), "quorra-last", "confluence-live"))


def _stale_citations(entry: dict, doc_id: str) -> int:
    draft = qpaths.runs_dir() / entry["run"] / "docs" / f"{doc_id}.md"
    if not draft.exists():
        return 0
    text = draft.read_text(encoding="utf-8")
    shas = doc_check.parse_meta(text).get("shas", {})
    repos = set(qpaths.load_repos())
    changed: dict[str, set[str]] = {}
    count = 0
    for c in kb_cite.parse_text(text, doc_id, repos):
        repo, old = c.get("repo"), shas.get(c.get("repo") or "")
        if not repo or not old or not c.get("path"):
            continue
        new = kb_cite.indexed_sha(repo)
        if not new or new == old:
            continue
        if repo not in changed:
            changed[repo] = set()
            cands = [Path(qpaths.repo_cfg(repo)["path"]), qpaths.work_dir() / repo]
            for cl in cands:
                try:
                    if cl.exists() and gitsrc.has_commit(cl, old) and gitsrc.has_commit(cl, new):
                        changed[repo] = {p for _s, p in gitsrc.changed_files(cl, old, new)}
                        break
                except Exception:
                    continue
        if c["path"] in changed[repo]:
            count += 1
    return count


def status(client=None) -> list[dict]:
    out = []
    for doc_id, e in sorted(_registry()["pages"].items()):
        try:
            live = _client(client).fetch(e["page_id"])["version"]["number"]
        except Exception as ex:
            out.append({"doc_id": doc_id, "page_id": e["page_id"], "error": _safe(str(ex))})
            continue
        out.append({"doc_id": doc_id, "page_id": e["page_id"], "url": e["url"], "our_version": e["version"], "live_version": live,
                    "edited_by_others": live != e["version"], "stale_citations": _stale_citations(e, doc_id)})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="doc.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("new"); n.add_argument("run"); n.add_argument("type", choices=TYPES); n.add_argument("slug")
    c = sub.add_parser("check"); c.add_argument("file")
    p = sub.add_parser("plan"); p.add_argument("file"); p.add_argument("--space", required=True)
    p.add_argument("--parent"); p.add_argument("--page")
    b = sub.add_parser("publish"); b.add_argument("file"); b.add_argument("--approve", required=True)
    d = sub.add_parser("diff"); d.add_argument("doc_id")
    sub.add_parser("status")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "new":
            print(new(Path(a.run), a.type, a.slug))
        elif a.cmd == "check":
            probs = doc_check.check(Path(a.file))
            for q in probs:
                print(f"line {q['line']}: {q['reason']}")
            return 1 if probs else 0
        elif a.cmd == "plan":
            print(json.dumps(plan(Path(a.file), a.space, a.parent, a.page), ensure_ascii=False, indent=1))
        elif a.cmd == "publish":
            print(json.dumps(publish(Path(a.file), a.approve), ensure_ascii=False))
        elif a.cmd == "diff":
            print(diff(a.doc_id), end="")
        elif a.cmd == "status":
            print(json.dumps(status(), ensure_ascii=False, indent=1))
    except (DocError, confluence_client.ConfluenceError, OSError, KeyError, ValueError) as e:
        print(json.dumps({"error": _safe(str(e))}, ensure_ascii=False), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
