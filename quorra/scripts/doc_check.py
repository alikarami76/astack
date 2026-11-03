#!/usr/bin/env python3
"""Checks a quorra document draft (TRD, API contract, decision report) against its template rules and the code it cites.
  doc_check.py <draft.md> [--runs-root DIR]
Read-only: reads the draft, the run record and git blobs at recorded commits through gitsrc. Prints one line per problem."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gitsrc
import jira_ops
import kb_cite
import qpaths
from work import _safe

Problem = dict  # {line: int, reason: str}

META_RE = re.compile(r"^<!--\s*quorra-doc:\s*(?P<body>.*?)\s*-->\s*$")
MARKER_RE = re.compile(r"<!--\s*section:\s*(?P<name>[^;]+?)\s*;\s*cite:\s*(?P<cite>required|none)\s*-->")
HEADING_RE = re.compile(r"^##\s+(?P<title>\S.*)$")
COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
HEADER_SECTIONS = {"sign-off", "links", "change-log"}
TYPES = {"trd", "contract", "decision"}
RUN_CITE = re.compile(r"(?<![\w:])run:(?P<field>[A-Za-z_]\w*(?:\.\w+)*)")
GENERIC_REPO_CITE = kb_cite.PATTERNS[0][1]
BACKTICK = re.compile(r"`([^`\n]+)`")
CLAIM_PATTERNS = [
    re.compile(r"^[A-Za-z_][\w\\]*::\w+\(\)$"),       # Class::method()
    re.compile(r"^[A-Za-z_]\w*\\[\w\\]+$"),            # Namespaced\Class
    re.compile(r"^[a-z_]+\.[a-z_]+$"),                 # table.column
    re.compile(r"^(GET|POST|PUT|PATCH|DELETE) /"),     # endpoint
    re.compile(r"^[A-Za-z_]\w*[a-z]_[a-z]\w*$"),       # snake_case
    re.compile(r"^[A-Za-z_]\w*[a-z][A-Z]\w*$"),        # camelCase / PascalCase with an inner capital
    re.compile(r"^[A-Za-z_]\w*\(\)$"),                 # name()
    re.compile(r"^[A-Za-z_]\w*::\w+(\(\))?$"),          # Class::method with or without ()
    re.compile(r"^[A-Z][A-Z0-9]*(_[A-Z0-9]+)+$"),      # UPPER_SNAKE, 2+ segments
]
DOTTED = CLAIM_PATTERNS[2]
LIST_ITEM = re.compile(r"^(?:[-*]|\d+\.)\s")
PLACEHOLDER = re.compile(r"<[A-Za-z][^<>\n]*>")
TODO_RE = re.compile(r"\b(TODO|TBD)\b")
SEP_ROW = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
LABEL_ONLY = re.compile(r"^\*\*[^*]+\*\*:?\s*$")
PERSIAN_CITE = re.compile(rf"\.{kb_cite.EXT}:[۰-۹]+")
EMPTY_FIELD = {"none", "ندارد", "-", "—"}
PROPOSED = "new (design)"
STATUS_RE = re.compile(r"^\|\s*Status\s*\|\s*(?P<v>[^|]*?)\s*\|", re.I)


def clone_for(repo: str, sha: str) -> Path | None:
    """The clone that holds commit `sha`: the user's clone first, then quorra's work clone (QUORRA_WORK_DIR/<repo>)."""
    cfg = qpaths.load_repos().get(repo)
    cands = ([Path(cfg["path"])] if cfg else []) + [qpaths.work_dir() / repo]
    return next((c for c in cands if c.exists() and gitsrc.has_commit(c, sha)), None)


def parse_meta(text: str) -> dict:
    """The quorra-doc line as {doc_id, type, run, shas}; {} when the draft has none."""
    for raw in text.split("\n")[:5]:
        m = META_RE.match(raw.strip())
        if not m:
            continue
        kv = dict(tok.split("=", 1) for tok in m.group("body").split() if "=" in tok)
        shas = dict(t.split("@", 1) for t in kv.get("shas", "").split(",") if "@" in t)
        return {"doc_id": kv.get("doc_id", ""), "type": kv.get("type", ""), "run": kv.get("run", ""), "shas": shas}
    return {}


def _fence_mask(lines: list[str]) -> list[bool]:
    mask, inside = [], False
    for ln in lines:
        if ln.lstrip().startswith("```"):
            mask.append(True)
            inside = not inside
        else:
            mask.append(inside)
    return mask


def _strip_comments(text: str) -> list[str]:
    """Lines of the text with HTML comments blanked out; line numbers stay the same."""
    return COMMENT_RE.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text).split("\n")


def sections(text: str) -> list[dict]:
    """Every `## ` section: name and cite from its marker comment, 1-based start (heading) and end lines, body lines."""
    lines = text.split("\n")
    fence = _fence_mask(lines)
    starts = [i for i, ln in enumerate(lines) if not fence[i] and HEADING_RE.match(ln)]
    out = []
    for k, i in enumerate(starts):
        j = starts[k + 1] if k + 1 < len(starts) else len(lines)
        while j > i + 1 and not lines[j - 1].strip():
            j -= 1
        body = lines[i + 1:j]
        m = MARKER_RE.search(body[0]) if body else None
        name = m.group("name") if m else HEADING_RE.match(lines[i]).group("title")
        out.append({"name": name, "cite": m.group("cite") if m else "none", "start": i + 1, "end": j,
                    "body_lines": body, "marked": bool(m)})
    return out


def _is_empty(sec: dict) -> bool:
    body = _strip_comments("\n".join(sec["body_lines"]))
    kept = []
    for i, ln in enumerate(body):
        s = ln.strip()
        nxt = body[i + 1].strip() if i + 1 < len(body) else ""
        if not s or s.startswith("#") or s == "```" or LABEL_ONLY.match(s) or SEP_ROW.match(s):
            continue
        if s.startswith("|") and SEP_ROW.match(nxt):   # table header row
            continue
        kept.append(s)
    return not kept or any(TODO_RE.search(s) or s == "..." for s in kept)


def _units(lines: list[str], fence: list[bool], first: int, last: int):
    """(first line number, text) of each paragraph and each table row in lines[first:last]; headings and code are skipped."""
    para: list[tuple[int, str]] = []

    def flush():
        if para:
            yield para[0][0], "\n".join(t for _n, t in para)
            para.clear()
    for i in range(first, last):
        s = lines[i].strip()
        if fence[i] or not s or s.startswith("#"):
            yield from flush()
        elif s.startswith("|"):
            yield from flush()
            if not SEP_ROW.match(s):
                yield i + 1, s
        else:
            if LIST_ITEM.match(s):
                yield from flush()
            para.append((i + 1, s))
    yield from flush()


def _claims(text: str, dotted_ok: bool = False, ignore: frozenset = frozenset()) -> bool:
    pats = [p for p in CLAIM_PATTERNS if not (dotted_ok and p is DOTTED)]
    return any(p.match(tok.strip()) for tok in BACKTICK.findall(text) if tok.strip() not in ignore for p in pats)


def _expected_sections(doc_type: str) -> list[str]:
    tpl = qpaths.REPO / "templates" / f"{doc_type}.md"
    if not tpl.exists():
        return []
    return [s["name"] for s in sections(tpl.read_text(encoding="utf-8"))]


def _field_rows(lines: list[str]) -> dict[int, tuple[list[str], int]]:
    """Line number -> (cells, index of the Source column) for each data row of a `| Field | ...` table."""
    out, source_ix = {}, None
    for n, ln in enumerate(lines, 1):
        s = ln.strip()
        if not s.startswith("|"):
            source_ix = None
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if cells and cells[0].lower() == "field":
            low = [c.lower() for c in cells]
            source_ix = low.index("source") if "source" in low else len(cells) - 1
        elif source_ix is not None and not SEP_ROW.match(s):
            if cells and cells[0].strip("`").strip().lower() in EMPTY_FIELD:
                continue
            out[n] = (cells, source_ix)
    return out


def _field_leaf(name: str) -> str:
    return re.split(r"\.", name.strip("`").strip())[-1].replace("[]", "")


def _run_field(run_json: dict, field: str) -> bool:
    cur = run_json
    for part in field.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return False
    return True


def _norm(s: str) -> str:
    return s.replace("_", "").lower()


def check(path: Path, runs_root: Path | None = None) -> list[Problem]:
    text = Path(path).read_text(encoding="utf-8")
    raw_lines = text.split("\n")
    lines = _strip_comments(text)
    fence = _fence_mask(lines)
    probs: list[Problem] = []

    def add(line: int, reason: str):
        probs.append({"line": line, "reason": reason})

    # metadata
    meta = parse_meta(text)
    if not meta:
        add(1, "missing quorra-doc metadata line")
    else:
        if meta["type"] not in TYPES:
            add(1, f"unknown document type {meta['type']!r}")
        body = next(META_RE.match(ln.strip()) for ln in raw_lines[:5] if META_RE.match(ln.strip())).group("body")
        if not meta["doc_id"] or not meta["run"] or re.search(r"<[^>]*>", body):
            add(1, "metadata not filled")

    secs = sections(text)
    # rule 1
    for s in secs:
        if s["name"] not in HEADER_SECTIONS and _is_empty(s):
            add(s["start"], "empty section")
    # structure
    for s in secs:
        if not s["marked"]:
            add(s["start"], "heading without section marker")
    if meta.get("type") in TYPES:
        counts: dict[str, int] = {}
        for s in secs:
            if s["marked"]:
                counts[s["name"]] = counts.get(s["name"], 0) + 1
        for name in dict.fromkeys(_expected_sections(meta["type"])):
            n_found = counts.get(name, 0)
            if n_found == 0:
                add(1, f"missing section {name}")
            elif n_found > 1 and name != "endpoint":
                for s in [x for x in secs if x["marked"] and x["name"] == name][1:]:
                    add(s["start"], f"duplicate section {name}")
    # placeholders left outside comments, code and backticks
    for n, ln in enumerate(lines, 1):
        if not fence[n - 1] and PLACEHOLDER.search(BACKTICK.sub("", ln)):
            add(n, "placeholder left")
    frows = _field_rows(lines) if meta.get("type") == "contract" else {}
    proposed = {n for n, (cells, six) in frows.items() if six < len(cells) and cells[six].strip() == PROPOSED}
    proposed_names = frozenset(frows[n][0][0].strip("`").strip() for n in proposed)
    # rule 2
    for s in secs:
        if s["cite"] != "required":
            continue
        for n, unit in _units(lines, fence, s["start"], s["end"]):
            if n in proposed:
                continue
            if _claims(unit, dotted_ok=n in frows, ignore=proposed_names) and not RUN_CITE.search(unit) and not any(
                    c["form"] == "repo_path" for c in kb_cite.parse_text(unit, "", set(qpaths.load_repos()) | _generic_repos(unit))):
                first = next(n + k for k, t in enumerate(unit.split("\n")) if _claims(t, n in frows, proposed_names)) if "\n" in unit else n
                add(first, "uncited technical claim")

    # rules 3-5, 7
    repos = qpaths.load_repos()
    runs_root = Path(runs_root) if runs_root else qpaths.runs_dir()
    run_json = None
    if meta.get("run"):
        try:
            run_json = json.loads((runs_root / meta["run"] / "run.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            run_json = None
    blobs: dict[tuple, str | None] = {}
    sha_cache: dict[str, str | None] = {}

    def sha_for(repo: str):
        if repo not in sha_cache:
            sha_cache[repo] = (meta.get("shas") or {}).get(repo) or kb_cite.indexed_sha(repo)
        return sha_cache[repo]

    clones: dict[str, Path | None] = {}

    def clone(repo: str):
        if repo not in clones:
            clones[repo] = clone_for(repo, sha_for(repo))
        return clones[repo]

    def blob(repo: str, path_: str):
        if (repo, path_) not in blobs:
            try:
                cl = clone(repo)
                blobs[(repo, path_)] = gitsrc.blob_text(cl, sha_for(repo), path_) if cl else None
            except gitsrc.GitError:
                blobs[(repo, path_)] = None
        return blobs[(repo, path_)]

    # claim units, so a backticked name next to a real citation is not a citation-form error
    unit_of: dict[int, int] = {}
    unit_cited: list[bool] = []
    for first, unit in _units(lines, fence, 0, len(lines)):
        for k in range(unit.count("\n") + 1):
            unit_of[first + k] = len(unit_cited)
        unit_cited.append(any(c["form"] == "repo_path" for c in kb_cite.parse_text(unit, "", set(repos))))

    row_cites: dict[int, dict] = {}
    for n, ln in enumerate(lines, 1):
        generic = _generic_repos(ln)
        if not fence[n - 1] and PERSIAN_CITE.search(ln):
            add(n, "use ASCII digits in citations")
        for c in kb_cite.parse_text(ln, "", set(repos) | generic):
            repo = c["repo"]
            if c["form"] != "repo_path":
                if fence[n - 1]:
                    continue
                if unit_cited[unit_of[n]] if n in unit_of else False:
                    if any(c["raw"] in span for span in BACKTICK.findall(ln)):
                        continue
                add(n, "cite as repo:path:line")
            elif repo not in repos:
                add(n, f"unknown repo {repo}")
            elif not sha_for(repo):
                add(n, f"no commit recorded for {repo}")
            elif c["line"] < 1:
                add(n, f"invalid line number {c['line']}")
            elif clone(repo) is None:
                add(n, f"commit {sha_for(repo)[:7]} not found in {repo}")
            else:
                body = blob(repo, c["path"])
                if body is None:
                    add(n, f"file not found at {repo}@{sha_for(repo)[:7]}")
                else:
                    total = len(body.splitlines())
                    last = max(c["line"], c["end"] or 0)
                    if last > total:
                        add(n, f"line {last} beyond end of file ({total} lines)")
                    else:
                        row_cites.setdefault(n, c)
        for m in RUN_CITE.finditer(ln):
            if run_json is None or not _run_field(run_json, m.group("field")):
                add(n, f"unknown run field run:{m.group('field')}")

    # rule 7 and field sources
    header = next((m.group("v") for ln in lines[:next((i for i, x in enumerate(lines) if HEADING_RE.match(x)), len(lines))]
                   if (m := STATUS_RE.match(ln.strip()))), "")
    for n, (cells, six) in frows.items():
        name = cells[0].strip("`").strip()
        if not name:
            continue
        if n in proposed:
            sec = next((x for x in secs if x["start"] <= n <= x["end"]), None)
            if header.strip().lower() != "draft":
                add(n, f"proposed field {name} in a non-draft contract")
            elif not sec or f"`{name}`" not in _changes_text(lines, sec):
                add(n, f"proposed field {name} not listed under Changes for callers")
            continue
        src = cells[six] if six < len(cells) else ""
        if not any(c["form"] == "repo_path" for c in kb_cite.parse_text(src, "", set(repos) | _generic_repos(src))):
            add(n, f"field {name} has no source")
        c = row_cites.get(n)
        if c:
            body = blob(c["repo"], c["path"])
            if body is not None and _norm(_field_leaf(name)) not in _norm(body):
                add(n, f"field {name} not declared in {c['repo']}:{c['path']}")

    # rule 6 (raw text: secrets inside comments count too)
    for n, ln in enumerate(raw_lines, 1):
        if _safe(ln) != ln:
            add(n, "possible secret")
        if jira_ops.ATTRIBUTION.search(ln):
            add(n, "tool attribution line")

    seen, out = set(), []
    for p in sorted(probs, key=lambda p: (p["line"], p["reason"])):
        key = (p["line"], p["reason"])
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def _changes_text(lines: list[str], sec: dict) -> str:
    """The text of the endpoint's `### Changes for callers` subsection."""
    out, inside = [], False
    for ln in lines[sec["start"]:sec["end"]]:
        if ln.startswith("###"):
            inside = bool(re.match(r"###\s+Changes for callers\b", ln, re.I))
        elif inside:
            out.append(ln)
    return "\n".join(out)


def _generic_repos(text: str) -> set[str]:
    """Repo-looking prefixes of repo:path:line citations, known or not, so an unknown repo is reported as one."""
    return {m.group("repo") for m in GENERIC_REPO_CITE.finditer(text)}


def main(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("draft")
    ap.add_argument("--runs-root")
    a = ap.parse_args(argv)
    probs = check(Path(a.draft), Path(a.runs_root) if a.runs_root else None)
    for p in probs:
        print(f"{a.draft}:{p['line']}: {p['reason']}")
    return 1 if probs else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
