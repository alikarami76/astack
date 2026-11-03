#!/usr/bin/env python3
"""Prepare one repo for a research run: fetch, index, copy tooling configs and AI guides, write scout area lists.
  prep_repo.py api-app --run runs/2026-09-30-research-api-app
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gitsrc  # noqa: E402
import qpaths  # noqa: E402
from qpaths import normalize_prefix  # noqa: E402,F401 - re-exported for callers and tests
from index_repo import refresh  # noqa: E402
from qindex import areas  # noqa: E402

TOOLING_PATTERNS = [r"^\.gitlab-ci\.yml$", r"^\.danger$", r"^composer\.json$", r"^package\.json$", r"^phpunit[\w.\-]*\.xml(\.dist)?$",
                    r"^phpcs[\w.]*$", r"^phpstan[\w.]*$", r"^\.eslintrc[\w.]*$", r"^\.prettierrc[\w.]*$", r"^tsconfig[\w.]*\.json$",
                    r"^\.editorconfig$", r"^\.husky/", r"^eslint-rules/", r"^pre-commit[\w.\-]*$", r"^\.gitlab/"]
GUIDE_PATTERNS = [r"^\.cursor/", r"(^|/)ai_guide[\w.\-]*\.md$", r"^AGENTS\.md$", r"^CLAUDE\.md$", r"^\.github/copilot-instructions\.md$",
                  r"^CONTRIBUTING[\w.]*$", r"^README\.md$", r"^docs/.*\.md$"]


def _match(files: list[str], patterns: list[str]) -> list[str]:
    pats = [re.compile(p) for p in patterns]
    return sorted(f for f in files if any(p.search(f) for p in pats))


def _copy(repo: Path, sha: str, paths: list[str], dest: Path) -> list[str]:
    skipped: list[str] = []
    root = dest.resolve()
    for path, data in gitsrc.read_files(repo, sha, paths):
        if data is None:
            continue
        out = dest / path
        if not (out.resolve() == root or root in out.resolve().parents):
            skipped.append(path)
            continue
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
    return skipped


def prep(name: str, run_dir: Path, fetch: bool = True, path_prefix: str | None = None) -> dict:
    cfg = qpaths.repo_cfg(name)
    idx = refresh(name, fetch=fetch)
    sha = idx["sha"]
    path_prefix = normalize_prefix(path_prefix)
    out = Path(run_dir) / (name if not path_prefix else f"{name}--{'--'.join(path_prefix.strip('/').split('/'))}")
    out.mkdir(parents=True, exist_ok=True)
    files = gitsrc.list_files(cfg["path"], sha)
    tooling, guides = _match(files, TOOLING_PATTERNS), _match(files, GUIDE_PATTERNS)  # repo-wide: tooling conventions are repo-level
    rank = gitsrc.recent_order(cfg["path"], sha)
    skipped = _copy(cfg["path"], sha, tooling, out / "tooling") + _copy(cfg["path"], sha, guides, out / "guides")
    lists = areas.area_files(cfg["path"], sha, cfg["lang"], qpaths.index_path(name), path_prefix=path_prefix, rank=rank)
    (out / "areas").mkdir(exist_ok=True)
    for area, fl in lists.items():
        (out / "areas" / f"{area}.txt").write_text("\n".join(fl) + ("\n" if fl else ""))
    live = set(files)
    recent = sorted((p for p in rank if p in live and (not path_prefix or p.startswith(path_prefix))), key=rank.get)
    (out / "recent.txt").write_text("\n".join(recent) + ("\n" if recent else ""))
    manifest = {"repo": name, "scope": path_prefix or "", "ref": cfg["ref"], "sha": sha,
                "sha_date": gitsrc.commit_date(cfg["path"], sha),  # held-out MR cutoff: the research commit's committer date
                "lang": cfg["lang"], "index": idx, "tooling": tooling,
                "guides": guides, "areas": {a: len(f) for a, f in lists.items()},
                "recent_files": len(recent), "skipped_paths": skipped}
    (out / "prep.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False))
    return manifest


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="prep_repo.py")
    ap.add_argument("name")
    ap.add_argument("--run", required=True)
    ap.add_argument("--no-fetch", action="store_true")
    ap.add_argument("--path-prefix", default=None)
    a = ap.parse_args(argv)
    print(json.dumps(prep(a.name, Path(a.run), fetch=not a.no_fetch, path_prefix=a.path_prefix), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
