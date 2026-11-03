#!/usr/bin/env python3
"""Code citations in product knowledge base docs: extract them, check which went stale, stamp the verified commits.
  kb_cite.py extract <feature> [--kb DIR] [--kb-ref REF] [--out FILE]
  kb_cite.py stale <feature> [--kb DIR] [--kb-ref REF] [--sha repo=<sha> ...] [--out FILE]
  kb_cite.py stamp <doc.md> --repo repo=<sha> [--repo ...] [--date YYYY-MM-DD]
Read-only on the clones (ls-tree, cat-file, rev-parse, log through gitsrc). stamp writes only the doc it is given."""
from __future__ import annotations

import datetime as _dt
import json
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gitsrc
import qpaths

EXT = r"(?:php|java|ts|tsx|js|py|ya?ml|xml|json|neon)"
LINE = r"(?P<line>\d+)(?:-(?P<end>\d+))?"
PATTERNS = [
    ("repo_path", re.compile(rf"(?<![\w/.-])(?P<repo>[a-z][a-z0-9-]*):(?P<path>[\w./-]+\.{EXT}):{LINE}")),
    ("path", re.compile(rf"(?<![\w/.-])(?P<path>(?:[\w.-]+/)+[\w.-]+\.{EXT})(?::{LINE})?(?![\w/])")),
    ("file", re.compile(rf"(?<![\w/.:-])(?P<base>[A-Za-z_]\w*\.{EXT}):{LINE}")),
    ("method", re.compile(r"(?<![\w\\])(?P<cls>[A-Z][A-Za-z0-9_]+)::(?P<meth>[a-z_]\w*)\(\)")),
    ("class_line", re.compile(rf"(?<![\w\\/.])(?P<cls>[A-Z][A-Za-z0-9_]*[a-z][A-Za-z0-9_]*):{LINE}\b")),
]
FOOTER_RE = re.compile(r"<!-- quorra-verified: (?P<body>[^>]*?) on (?P<date>\d{4}-\d{2}-\d{2}) -->")
KEYS = ("repo", "path", "base", "cls", "meth", "line", "end")


def parse_text(text: str, doc: str, known_repos: set[str]) -> list[dict]:
    out = []
    for n, line_text in enumerate(text.split("\n"), 1):
        taken: list[tuple[int, int]] = []
        found = []
        for form, rx in PATTERNS:
            for m in rx.finditer(line_text):
                if any(m.start() < e and s < m.end() for s, e in taken):
                    continue
                g = m.groupdict()
                if form == "repo_path" and g["repo"] not in known_repos:
                    continue
                taken.append((m.start(), m.end()))
                c = {"doc": doc, "doc_line": n, "raw": m.group(0), "form": form, **{k: g.get(k) for k in KEYS}}
                for k in ("line", "end"):
                    c[k] = int(c[k]) if c[k] else None
                found.append((m.start(), c))
        out.extend(c for _s, c in sorted(found, key=lambda x: x[0]))
    return out


def parse_footer(text: str) -> dict[str, str]:
    m = None
    for m in FOOTER_RE.finditer(text):
        pass
    if not m:
        return {}
    return dict(tok.split("@", 1) for tok in m.group("body").split() if "@" in tok)


CODE_EXTS = (".php", ".java", ".ts", ".tsx")


WINDOW = 5
METHOD_START = re.compile(r"^\s*(?:(?:public|private|protected|static|final|abstract|async)\s+)*function\s+\w+\s*\("
                          r"|^\s*(?:public|private|protected)\s+[\w<>\[\], ?]+\s+\w+\s*\(")


def _window(lines: list[str], line: int, end: int | None = None) -> list[str]:
    return [x.strip() for x in lines[max(0, line - 1 - WINDOW): (end or line) + WINDOW]]


def _method_body(lines: list[str], meth: str) -> str | None:
    start = re.compile(rf"\bfunction\s+{re.escape(meth)}\s*\(|^\s*(?:public|private|protected)\s+[\w<>\[\], ?]+\s+{re.escape(meth)}\s*\(")
    for i, x in enumerate(lines):
        if start.search(x):
            j = next((k for k in range(i + 1, len(lines)) if METHOD_START.search(lines[k])), len(lines))
            return "\n".join(y.strip() for y in lines[i:j]).strip()
    return None


def classify(repo_path: Path, old: str, new: str, file: str, line: int | None, meth: str | None,
             end: int | None = None) -> dict:
    if old == new:
        return {"status": "unchanged"}
    new_txt = gitsrc.blob_text(repo_path, new, file)
    if new_txt is None:
        return {"status": "gone", "commit": gitsrc.deleting_commit(repo_path, old, new, file)}
    old_txt = gitsrc.blob_text(repo_path, old, file)
    if old_txt is None:
        return {"status": "unresolved", "reason": "file not in the baseline commit"}
    if old_txt == new_txt:
        return {"status": "unchanged"}
    ol, nl = old_txt.split("\n"), new_txt.split("\n")
    touched = gitsrc.last_commit(repo_path, old, new, file)
    if line is None:
        if meth:
            ob, nb = _method_body(ol, meth), _method_body(nl, meth)
            if nb is None and ob is None:
                return {"status": "unresolved", "reason": "method not defined in file"}
            if nb is None:
                return {"status": "gone", "commit": touched}
            return {"status": "unchanged"} if ob == nb else {"status": "changed", "commit": touched}
        return {"status": "changed", "commit": touched}
    if line > len(ol):
        return {"status": "unresolved", "reason": "line beyond the end of the file at the baseline"}
    end = end if end and end >= line else None
    span = (end - line) if end else 0
    if line <= len(nl) and _window(ol, line, end) == _window(nl, line, end):
        return {"status": "unchanged"}
    target = ol[line - 1].strip()
    spots = [i + 1 for i, x in enumerate(nl) if target and x.strip() == target]
    matched_spots = [s for s in spots if _window(ol, line, end) == _window(nl, s, s + span if end else None)]
    if len(matched_spots) == 1:
        return {"status": "moved", "new_line": matched_spots[0]}
    at = spots[0] if len(spots) == 1 else (line if line <= len(nl) and nl[line - 1].strip() == target else None)
    return {"status": "changed", "new_line": at, "commit": touched}


STATUSES = ("unchanged", "moved", "changed", "gone", "unresolved", "no_baseline")


def indexed_sha(repo: str) -> str | None:
    p = qpaths.index_path(repo)
    if not p.exists():
        return None
    con = sqlite3.connect(p)
    try:
        row = con.execute("SELECT value FROM meta WHERE key = 'sha'").fetchone()
    finally:
        con.close()
    return row[0] if row else None


class Resolver:
    def __init__(self, repos: dict):
        self.repos = repos
        self._files: dict[tuple[str, str], list[str]] = {}

    def full_sha(self, repo: str, sha: str) -> str:
        return gitsrc.resolve(self.repos[repo]["path"], sha)

    def files(self, repo: str, sha: str) -> list[str]:
        key = (repo, sha)
        if key not in self._files:
            self._files[key] = gitsrc.list_files(self.repos[repo]["path"], sha)
        return self._files[key]

    def _match(self, repos_shas, test) -> list[tuple[str, str]]:
        return [(r, f) for r, s in repos_shas for f in self.files(r, s) if test(f)]

    def resolve(self, c: dict, shas: dict[str, str]) -> dict:
        pool = [(r, shas.get(r) or indexed_sha(r)) for r in self.repos]
        pool = [(r, s) for r, s in pool if s]
        if c["repo"]:
            pool = [(r, s) for r, s in pool if r == c["repo"]]
        if c["path"]:
            p = c["path"].removeprefix("./")
            by = "path"
            hits = self._match(pool, lambda f: f == p or f.endswith("/" + p))
        elif c["base"]:
            by = "basename"
            hits = self._match(pool, lambda f: f.rsplit("/", 1)[-1] == c["base"])
        else:
            by = "class"
            names = {c["cls"] + e for e in CODE_EXTS}
            hits = self._match(pool, lambda f: f.rsplit("/", 1)[-1] in names)
        if len(hits) == 1:
            c["resolved"] = {"repo": hits[0][0], "file": hits[0][1], "resolved_by": by}
        else:
            c["unresolved"] = {"reason": "no match" if not hits else "ambiguous",
                               "candidates": [f"{r}:{f}" for r, f in hits[:5]]}
        return c


class KbCiteError(RuntimeError):
    pass


def read_docs(kb: Path, feature: str, ref: str | None) -> list[tuple[str, str]]:
    prefix = f"features/{feature}/"
    if ref:
        names = [f for f in gitsrc.list_files(kb, ref) if f.startswith(prefix) and f.endswith(".md")]
        if not names:
            raise KbCiteError(f"no feature folder {prefix} in {kb} at {ref}")
        return [(n, (d or b"").decode("utf-8", "replace")) for n, d in gitsrc.read_files(kb, ref, sorted(names))]
    root = kb / prefix
    if not root.is_dir():
        raise KbCiteError(f"no feature folder {prefix} in {kb}")
    return [(str(p.relative_to(kb)), p.read_text(encoding="utf-8")) for p in sorted(root.rglob("*.md"))]


def extract(feature: str, kb: Path, ref: str | None = None, shas: dict | None = None) -> dict:
    repos = qpaths.load_repos()
    r = Resolver(repos)
    cits = []
    for doc, text in read_docs(kb, feature, ref):
        footer, bad = {}, set()
        for k, v in parse_footer(text).items():
            if k not in repos:
                continue
            try:
                footer[k] = r.full_sha(k, v)
            except gitsrc.GitError:
                bad.add(k)
        base = {**(shas or {}), **footer}  # the doc's own footer wins; --sha only fills repos it lacks
        for c in parse_text(text, doc, set(repos)):
            c["baseline"] = {k: v for k, v in base.items()}
            r.resolve(c, base)
            res = c.get("resolved")
            if res and res["repo"] in bad and res["repo"] not in base:
                del c["resolved"]
                c["unresolved"] = {"reason": "baseline sha not found", "candidates": []}
            cits.append(c)
    return {"feature": feature, "citations": cits, "unresolved": [c for c in cits if "unresolved" in c]}


def stale(feature: str, kb: Path, ref: str | None = None, shas: dict | None = None) -> dict:
    repos = qpaths.load_repos()
    items = []
    for c in extract(feature, kb, ref, shas)["citations"]:
        item = {k: c[k] for k in ("doc", "doc_line", "raw", "form", "line", "end", "meth")}
        if "unresolved" in c:
            items.append({**item, "status": "unresolved", "reason": c["unresolved"]["reason"],
                          "candidates": c["unresolved"]["candidates"]})
            continue
        repo, file = c["resolved"]["repo"], c["resolved"]["file"]
        old, new = c["baseline"].get(repo), indexed_sha(repo)
        item.update(repo=repo, file=file, resolved_by=c["resolved"]["resolved_by"])
        if not old or not new:
            items.append({**item, "status": "no_baseline" if not old else "unresolved",
                          **({} if not old else {"reason": "repo has no index"})})
            continue
        items.append({**item, "old": old, "new": new, **classify(repos[repo]["path"], old, new, file, c["line"], c["meth"], c["end"])})
    summary = {s: sum(1 for i in items if i["status"] == s) for s in STATUSES}
    summary["stale"] = sum(1 for i in items if i["status"] != "unchanged")
    return {"feature": feature, "summary": summary, "items": items}


def stamp(doc: Path, shas: dict[str, str], date: str | None) -> None:
    if not shas:
        raise ValueError("shas cannot be empty")
    date = date or _dt.date.today().isoformat()
    footer = "<!-- quorra-verified: " + " ".join(f"{r}@{s[:7]}" for r, s in sorted(shas.items())) + f" on {date} -->"
    text = doc.read_text(encoding="utf-8")
    if FOOTER_RE.search(text):
        text = FOOTER_RE.sub("", text)  # Remove all existing footers first
        text = text.rstrip("\n") + "\n\n" + footer
    else:
        text = text.rstrip("\n") + "\n\n" + footer
    doc.write_text(text.rstrip("\n") + "\n", encoding="utf-8")


def _shas(pairs: list[str] | None) -> dict[str, str]:
    out = {}
    for p in pairs or []:
        repo, _, sha = p.partition("=")
        if not sha:
            raise SystemExit(f"--sha needs repo=<sha>, got {p!r}")
        out[repo] = sha
    return out


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("extract", "stale"):
        s = sub.add_parser(name)
        s.add_argument("feature")
        s.add_argument("--kb", default=None)
        s.add_argument("--kb-ref", default=None)
        s.add_argument("--sha", action="append", metavar="REPO=SHA",
                       help="baseline for a repo the doc's own footer does not name (a fallback: a footer sha always wins)")
        s.add_argument("--out", default=None)
    st = sub.add_parser("stamp")
    st.add_argument("doc")
    st.add_argument("--repo", action="append", required=True)
    st.add_argument("--date", default=None)
    a = ap.parse_args(argv)
    if a.cmd == "stamp":
        stamp(Path(a.doc), _shas(a.repo), a.date)
        print(json.dumps({"stamped": a.doc}))
        return 0
    kb = Path(a.kb) if a.kb else qpaths.feature_kb_path()
    repos = qpaths.load_repos()
    shas = {}
    for k, v in _shas(a.sha).items():
        if k not in repos:
            print(f"--sha: unknown repo {k!r}; known repos: {', '.join(sorted(repos))}", file=sys.stderr)
            return 2
        try:
            shas[k] = Resolver(repos).full_sha(k, v)
        except gitsrc.GitError:
            print(f"--sha: cannot resolve {v!r} in repo {k!r}", file=sys.stderr)
            return 2
    try:
        res = extract(a.feature, kb, a.kb_ref, shas) if a.cmd == "extract" else stale(a.feature, kb, a.kb_ref, shas)
    except KbCiteError as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        return 2
    text = json.dumps(res, ensure_ascii=False, indent=1)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
        print(json.dumps(res.get("summary") or {"citations": len(res["citations"]), "unresolved": len(res["unresolved"])}))
    else:
        print(text)
    return 1 if a.cmd == "stale" and res["summary"]["stale"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
