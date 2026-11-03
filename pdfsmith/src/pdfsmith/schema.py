"""Load, validate and complete doc.yaml."""

from __future__ import annotations

import copy
import datetime
import os
import re
from pathlib import Path

import jsonschema
import yaml

from .errors import InputError
from .paths import ROOT

_STR_OR_NULL = {"type": ["string", "null"]}
_ENV_REF = {"type": "string", "pattern": "^env:[A-Za-z_][A-Za-z0-9_]*$"}
_FILES = {"type": "array", "items": {"type": "string", "minLength": 1}, "uniqueItems": True}
PERMISSIONS = ("print", "copy", "modify", "annotate", "forms", "assemble")
FEATURES_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "attachments": _FILES,
        "forms": {"enum": ["auto", "off"]},
        "encrypt": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "required": ["owner"],
            "properties": {
                "owner": _ENV_REF,
                "user": {"anyOf": [_ENV_REF, {"type": "null"}]},
                "allow": {"type": "array", "items": {"enum": list(PERMISSIONS)}, "uniqueItems": True},
            },
        },
        "merge": {
            "type": "object",
            "additionalProperties": False,
            "properties": {"before": _FILES, "after": _FILES},
        },
        "pdfa": {"type": "boolean"},
    },
}

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "template": {"type": "string"},
        "theme": _STR_OR_NULL,
        "name": {"type": ["string", "null"], "pattern": "^[A-Za-z0-9._-]+$"},
        "lang": {"enum": ["fa", "en", "mixed"]},
        "dir": {"enum": ["rtl", "ltr", "auto", None]},
        "page": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "size": {"type": "string"},
                "orientation": {"enum": ["portrait", "landscape"]},
                "margins": {"type": "string"},
                "mirror": {"type": "boolean"},
            },
        },
        "digits": {"enum": ["fa", "latin", "fa-body-latin-data", None]},
        "calendar": {"enum": ["jalali", "gregorian", None]},
        "halfspace": {"enum": ["auto", "off"]},
        "meta": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "title": _STR_OR_NULL,
                "subtitle": _STR_OR_NULL,
                "kicker": _STR_OR_NULL,
                "author": _STR_OR_NULL,
                "subject": _STR_OR_NULL,
                "keywords": {"type": "array", "items": {"type": "string"}},
                "date": {},
            },
        },
        "cover": {"type": "boolean"},
        "toc": {"type": "boolean"},
        "header": {"type": "string"},
        "footer": {"type": "string"},
        "watermark": _STR_OR_NULL,
        "confidential": {"type": "boolean"},
        "content": {"type": ["string", "array", "null"], "items": {"type": "string"}},
        "html": _STR_OR_NULL,
        "data": _STR_OR_NULL,
        "tex": _STR_OR_NULL,
        "network": {"type": "boolean"},
        "timeout": {"type": "number", "minimum": 5},
        "fonts": {"type": "object", "additionalProperties": {"type": ["string", "array"], "items": {"type": "string"}}},
        "features": FEATURES_SCHEMA,
        "checks": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "pages": {"type": ["array", "null"], "items": {"type": "integer", "minimum": 1}, "minItems": 2, "maxItems": 2},
                "allow_overflow": {"type": "array", "items": {"type": "string"}},
                "overflow_pt": {"type": "number", "minimum": 0},
            },
        },
        "tier": {"enum": ["quick", "standard", "full", None]},
        "audience": {"enum": ["internal", "executive", "external"]},
    },
}

DEFAULTS = {
    "template": "report",
    "theme": None,
    "name": None,
    "lang": "en",
    "dir": None,
    "page": {"size": "A4", "orientation": "portrait", "margins": "20mm", "mirror": False},
    "digits": None,
    "calendar": None,
    "halfspace": "off",
    "meta": {"title": None, "subtitle": None, "kicker": None, "author": None, "subject": None, "keywords": [], "date": None},
    "cover": False,
    "toc": False,
    "header": "",
    "footer": "{page} / {pages}",
    "watermark": None,
    "confidential": False,
    "content": None,
    "html": None,
    "data": None,
    "tex": None,
    "network": False,
    "timeout": 120,
    "fonts": {},
    "features": {"attachments": [], "forms": "auto", "encrypt": None, "merge": {"before": [], "after": []}, "pdfa": False},
    "checks": {"pages": None, "allow_overflow": [], "overflow_pt": 2},
    "tier": None,
    "audience": "internal",
}

LATEX = "latex"
ROADMAP = str(ROOT / "docs/superpowers/plans/2026-09-30-pdfsmith-00-roadmap.md")


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def _not_yet(key: str):
    raise InputError(f"doc.yaml: {key} is not implemented yet (see {ROADMAP})")


def resolve_env(ref: str, where: str) -> str:
    """The value of an env:VAR reference. Passwords never live in doc.yaml or in the build folder."""
    name = ref.removeprefix("env:")
    value = os.environ.get(name)
    if not value:
        raise InputError(f"doc.yaml: {where}: environment variable {name} is not set or is empty")
    return value


def _check_features(features: dict, build_dir: Path) -> None:
    for key, files in (("attachments", features["attachments"]), ("merge.before", features["merge"]["before"]), ("merge.after", features["merge"]["after"])):
        for name in files:
            if not (build_dir / name).is_file():
                raise InputError(f"doc.yaml: features.{key}: file '{name}' not found in {build_dir}")
    enc = features["encrypt"]
    if enc is not None:
        enc.setdefault("user", None)
        enc.setdefault("allow", ["print", "copy"])
        resolve_env(enc["owner"], "features.encrypt.owner")
        if enc["user"]:
            resolve_env(enc["user"], "features.encrypt.user")
    if features["pdfa"]:
        if enc is not None:
            raise InputError("doc.yaml: features.pdfa: PDF/A does not allow encryption; remove features.encrypt or set pdfa: false")
        if features["merge"]["before"] or features["merge"]["after"]:
            raise InputError("doc.yaml: features.pdfa: merged PDFs are not known to be PDF/A, so the result would not conform; remove features.merge or set pdfa: false")


def _check_latex(doc: dict, build_dir: Path) -> None:
    """The latex route compiles a .tex file; the Markdown and HTML sources and the Persian setup do not apply."""
    given = [k for k in ("content", "html", "data") if doc[k]]
    if given:
        raise InputError(f"doc.yaml: {', '.join(given)} cannot be used with template: latex; the source is the file named by tex (default main.tex)")
    if doc["lang"] != "en":
        _not_yet(f"lang: {doc['lang']} with template: latex")
    doc["tex"] = doc["tex"] or "main.tex"
    tex = Path(doc["tex"])
    if tex.is_absolute() or ".." in tex.parts:
        raise InputError(f"doc.yaml: tex: '{doc['tex']}' must be a path inside {build_dir}")
    if not (build_dir / tex).is_file():
        raise InputError(f"doc.yaml: tex: file '{doc['tex']}' not found in {build_dir}")


def load_doc(build_dir: Path) -> dict:
    build_dir = Path(build_dir)
    path = build_dir / "doc.yaml"
    if not path.is_file():
        raise InputError(f"{path}: doc.yaml not found")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise InputError(f"{path}: invalid YAML: {e}") from e
    if not isinstance(raw, dict):
        raise InputError(f"{path}: doc.yaml must be a mapping of keys to values")
    errors = sorted(jsonschema.Draft202012Validator(SCHEMA).iter_errors(raw), key=lambda e: list(e.path))
    if errors:
        err = errors[0]
        where = ".".join(str(p) for p in err.path) or "(top level)"
        message = err.message
        parts = tuple(err.path)
        if parts[:2] == ("features", "encrypt"):
            # Never echo the offending value: a mistyped setting here may be a plaintext password.
            if parts[-1] in ("owner", "user"):
                message = "must be an env:VAR reference (passwords never go in doc.yaml)"
            else:
                message = "invalid encrypt setting (values are not shown, because they may be passwords)"
        raise InputError(f"doc.yaml: {where}: {message}")

    doc = _merge(DEFAULTS, raw)
    lang = doc["lang"]
    doc["dir"] = doc["dir"] or {"fa": "rtl", "mixed": "auto"}.get(lang, "ltr")
    doc["theme"] = doc["theme"] or ("neutral" if lang == "en" else "neutral-fa")
    doc["digits"] = doc["digits"] or ("fa" if lang == "fa" else "latin")
    doc["calendar"] = doc["calendar"] or ("jalali" if lang == "fa" else "gregorian")
    doc["name"] = doc["name"] or re.sub(r"[^A-Za-z0-9._-]+", "-", build_dir.resolve().name).strip("-") or "document"
    doc["pdf_lang"] = "fa-IR" if lang == "fa" or (lang == "mixed" and doc["dir"] == "rtl") else "en-US"

    date = doc["meta"]["date"]
    if date is None:
        date = datetime.date.today()
    elif isinstance(date, str):
        try:
            date = datetime.date.fromisoformat(date)
        except ValueError as e:
            raise InputError(f"doc.yaml: meta.date: '{date}' is not a YYYY-MM-DD date") from e
    elif isinstance(date, datetime.datetime):
        date = date.date()
    elif not isinstance(date, datetime.date):
        raise InputError(f"doc.yaml: meta.date: {date!r} is not a YYYY-MM-DD date")
    doc["meta"]["date"] = date

    pages = doc["checks"]["pages"]
    if pages and pages[0] > pages[1]:
        raise InputError(f"doc.yaml: checks.pages: {pages} starts after it ends; write the smaller number first")

    latex = doc["template"] == LATEX
    if doc["tex"] and not latex:
        raise InputError("doc.yaml: tex is only used with template: latex")
    if latex:
        _check_latex(doc, build_dir)
    sources = [] if latex else [k for k in ("content", "html", "data") if doc[k]]
    if not sources and not latex:
        raise InputError("doc.yaml: set one of content (Markdown files), html (a raw HTML file) or data (a JSON file)")
    if len(sources) > 1:
        raise InputError(f"doc.yaml: set only one of content, html or data (found {', '.join(sources)})")
    if doc["data"]:
        _not_yet("data (JSON input)")
    for src in sources:
        files = doc[src] if isinstance(doc[src], list) else [doc[src]]
        for name in files:
            if not (build_dir / name).is_file():
                raise InputError(f"doc.yaml: {src}: file '{name}' not found in {build_dir}")

    if doc["watermark"]:
        _not_yet("watermark")
    if doc["confidential"]:
        _not_yet("confidential")
    if doc["page"]["mirror"]:
        _not_yet("page.mirror")
    features = doc["features"]
    if features["encrypt"] is not None:
        _not_yet("features.encrypt")
    if features["merge"]["before"] or features["merge"]["after"]:
        _not_yet("features.merge")
    if features["pdfa"]:
        _not_yet("features.pdfa")
    _check_features(features, build_dir)
    return doc
