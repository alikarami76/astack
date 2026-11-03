# scripts/qindex/areas.py
"""Per-area file lists for the research scouts, taken from the index plus path/content patterns."""
from __future__ import annotations

import re
import sys
from pathlib import Path

import gitsrc
from qindex import db

AREAS = {
    "php": {
        "layout": {"path": r"^src/"},
        "naming": {"kinds": ["service", "repository", "entity", "usecase", "dto", "enum", "view", "form", "controller"]},
        "data-storage": {"kinds": ["entity", "table", "repository"], "path": r"(^|/)(migrations?|Migration)/"},
        "apis": {"kinds": ["route", "view", "form", "controller", "dto"]},
        "async": {"kinds": ["console_command", "consumer", "cron", "listener"]},
        "testing": {"path": r"(^|/)(tests?|tests-legacy)/.*\.php$"},
        "errors-logging": {"path": r"\.php$", "content": r"throw new|catch\s*\(|->(error|warning|critical|log)\(|Logger"},
        "tooling": {"path": r"(^|/)(\.gitlab-ci\.yml|\.danger|composer\.json|phpunit[\w.\-]*\.xml(\.dist)?|phpcs[\w.]*|phpstan[\w.]*|"
                            r"\.editorconfig|pre-commit[\w.\-]*)$"},
        "reviews": {"path": r"(^|/)\.gitlab/"},
    },
    "ts": {
        "components": {"kinds": ["component"]},
        "state": {"path": r"\.(ts|tsx)$", "content": r"useQuery|useMutation|createSlice|useSelector|useDispatch|from ['\"]zustand|create\(\s*\(set"},
        "api-clients": {"kinds": ["api_client", "endpoint"]},
        "styling-rtl": {"path": r"\.(scss|css|module\.scss)$|(^|/)styles/"},
        "forms": {"path": r"\.(ts|tsx)$", "content": r"\buseForm\b|Formik|react-hook-form|\byup\.|\bz\.object\b"},
        "i18n": {"kinds": ["i18n_key"], "path": r"(^|/)translations/"},
        "testing": {"path": r"\.(test|spec)\.tsx?$|\.stories\.tsx$|(^|/)tests?/"},
        "tooling": {"path": r"(^|/)(\.gitlab-ci\.yml|\.eslintrc[\w.]*|\.prettierrc[\w.]*|package\.json|tsconfig[\w.]*\.json|"
                            r"\.husky/.*|eslint-rules/.*|\.babelrc)$"},
        "reviews": {"path": r"(^|/)\.gitlab/"},
    },
    "java": {
        "layout": {"path": r"^src/main/java/"},
        "naming": {"kinds": ["controller", "service", "repository", "entity", "dto", "config"]},
        "data-storage": {"kinds": ["entity", "table", "repository", "migration"]},
        "apis": {"kinds": ["route", "controller", "dto"]},
        "async": {"kinds": ["consumer", "producer", "cron", "listener"]},
        "testing": {"path": r"^src/test/"},
        "errors-logging": {"content": r"throw new|@ExceptionHandler|@ControllerAdvice|log\.(error|warn)", "path": r"\.java$"},
        "tooling": {"path": r"(^|/)(build\.gradle\.kts|settings\.gradle\.kts|gradle\.properties|\.gitlab-ci\.yml|Dockerfile|docker-compose\.yml|Makefile)$"},
        "reviews": {"path": r"(^|/)\.gitlab/"},
    },
}

PREFIX_EXEMPT = {"tooling", "reviews"}


def area_files(repo: Path, sha: str, lang: str, db_path, cap: int = 60, path_prefix: str | None = None,
               rank: dict[str, int] | None = None) -> dict[str, list[str]]:
    conn = db.connect(db_path)
    repo_files = gitsrc.list_files(repo, sha)
    scoped_files = [f for f in repo_files if f.startswith(path_prefix)] if path_prefix else repo_files
    if rank is None:
        rank = gitsrc.recent_order(repo, sha)
    content_cache: dict[str, str] = {}
    out = {}
    for area, spec in AREAS[lang].items():
        chosen: set[str] = set()
        all_files = repo_files if area in PREFIX_EXEMPT else scoped_files  # tooling/reviews conventions are repo-level
        file_set = set(all_files)
        if spec.get("kinds"):
            q = f"SELECT DISTINCT file FROM symbols WHERE kind IN ({','.join('?' * len(spec['kinds']))})"
            chosen |= {r[0] for r in conn.execute(q, spec["kinds"])} & file_set
        if spec.get("path"):
            pat = re.compile(spec["path"])
            matched = [f for f in all_files if pat.search(f)]
            if spec.get("content"):
                cpat = re.compile(spec["content"])
                todo = sorted((f for f in matched if f not in content_cache), key=lambda f: (rank.get(f, 10**9), f))
                need = todo[:3000]
                if len(todo) > 3000:
                    print(f"areas: {area}: content scan cap hit, skipped {len(todo) - 3000} files", file=sys.stderr)
                for path, data in gitsrc.read_files(repo, sha, need):
                    content_cache[path] = (data or b"").decode("utf-8", "replace")
                matched = [f for f in matched if cpat.search(content_cache.get(f, ""))]
            chosen |= set(matched)
        out[area] = sorted(chosen, key=lambda f: (rank.get(f, 10**9), f))[:cap]
    return out
