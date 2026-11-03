"""Mechanical checks on a built PDF."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pikepdf
import pymupdf

from . import attach, fa
from .fonts import pdf_font_key

LEFTOVER = re.compile(r"\{\{|\}\}|\{%|%\}|\bTODO\b|\bTBD\b|lorem ipsum|\[object Object\]", re.I)
URI_OK = re.compile(r"^(https?://\S+|mailto:\S+)$")


@dataclass
class Check:
    name: str
    status: str
    detail: str = ""
    items: list = field(default_factory=list)


def _result(name: str, items: list, ok_detail: str) -> Check:
    if items:
        return Check(name, "fail", f"{len(items)} problem(s)", items[:50])
    return Check(name, "pass", ok_detail)


def _report(pdf_path: Path, pages: int, checks: list[Check]) -> dict:
    return {"pdf": str(pdf_path), "pages": pages, "passed": all(c.status != "fail" for c in checks), "checks": [asdict(c) for c in checks]}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", s or "")).strip()


def _pages(doc, info) -> Check:
    expected = info.get("pages_expect")
    if not expected:
        return Check("pages", "skip", "no expected range")
    lo, hi = expected
    n = doc.page_count
    return _result("pages", [] if lo <= n <= hi else [f"{n} pages, expected {lo}-{hi}"], f"{n} pages within {lo}-{hi}")


def _fonts(doc, info) -> Check:
    allowed = set(info.get("allowed_font_keys") or [])
    items, seen = [], set()
    for number, page in enumerate(doc, 1):
        for font in page.get_fonts():
            ext, kind, base = font[1], font[2], font[3]
            if base in seen:
                continue
            seen.add(base)
            if ext == "n/a":
                items.append(f"{base}: not embedded (first seen on page {number})")
            if kind == "Type3":
                items.append(f"{base}: Type 3 font (page {number})")
            if allowed and pdf_font_key(base) not in allowed:
                items.append(f"{base}: not in the theme's font chains, so Chrome fell back to a system font (page {number}). Add a font that covers these characters to the role's chain.")
    for role, chars in (info.get("missing_glyphs") or {}).items():
        items.append(f"{role} text has characters no font in its chain covers: " + " ".join(f"U+{ord(c):04X} {c}" for c in chars[:12]))
    return _result("fonts", items, f"{len(seen)} fonts, all embedded")


def _overflow(geometry) -> Check:
    if geometry is None:
        return Check("overflow", "skip", "no geometry (standalone check)")
    items = [f"page {o['page']}: <{o['tag']}> '{o['text']}' spills {o['px']}px past the content box" for o in geometry.get("overflow", [])]
    return _result("overflow", items, "nothing spills past the margins")


def _prose_texts(doc, mono_keys) -> list[str]:
    """Page text without the spans set in the theme's mono font (code blocks and inline code)."""
    if not mono_keys:
        return [page.get_text() for page in doc]
    keys = set(mono_keys)
    texts = []
    for page in doc:
        lines = []
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                lines.append("".join(s["text"] for s in line["spans"] if pdf_font_key(s["font"]) not in keys))
        texts.append("\n".join(lines))
    return texts


def _leftovers(texts) -> Check:
    items = [
        f"page {number}: '{m.group()}' near '{text[max(0, m.start() - 20):m.end() + 20].strip()}'"
        for number, text in enumerate(texts, 1)
        for m in LEFTOVER.finditer(text)
    ]
    return _result("leftovers", items, "no template leftovers")


def _links(doc) -> Check:
    items = []
    for number, page in enumerate(doc, 1):
        for link in page.get_links():
            kind = link["kind"]
            if kind in (pymupdf.LINK_GOTO, pymupdf.LINK_NAMED) and not (0 <= link.get("page", -1) < doc.page_count):
                items.append(f"page {number}: internal link '{link.get('nameddest') or ''}' points nowhere")
            elif kind == pymupdf.LINK_URI and not URI_OK.match(link.get("uri") or ""):
                items.append(f"page {number}: link '{link.get('uri')}' is not an http(s) or mailto URL")
    return _result("links", items, "links resolve")


NA_LATEX = "skipped: n/a for latex"


def _overflow_latex(info) -> Check:
    """Overfull boxes from the TeX log: an hbox wider than checks.overflow_pt fails, smaller ones and vboxes warn."""
    limit = info.get("overflow_pt", 2)
    boxes = info.get("latex_overfull") or []

    def line(b, what):
        return f"{b['file']}:{b['lines'] or '?'}: {b['pt']:g}pt {what}"

    bad = sorted((b for b in boxes if b["kind"] == "h" and b["pt"] > limit), key=lambda b: -b["pt"])
    if bad:
        items = [line(b, "too wide") for b in bad[:5]]
        if len(bad) > 5:
            items.append(f"(+{len(bad) - 5} more overfull boxes; see the .log in build/)")
        return Check("overflow", "fail", f"{len(bad)} overfull box(es) wider than {limit:g}pt", items)
    minor = [line(b, "too wide" if b["kind"] == "h" else "too high") for b in boxes]
    if minor:
        return Check("overflow", "warn", f"{len(minor)} small overfull box(es), none wider than {limit:g}pt", minor[:50])
    return Check("overflow", "pass", "no overfull boxes in the TeX log")


def _outline_latex(doc, info) -> Check:
    """hyperref writes the bookmarks from \\section and friends, so the PDF's own outline is the evidence."""
    toc = doc.get_toc()
    if toc:
        return Check("outline", "pass", f"{len(toc)} bookmarks")
    if info.get("latex_sections"):
        return Check("outline", "fail", "1 problem(s)", ["the source has sectioning commands but the PDF has no bookmarks; load the hyperref package"])
    return Check("outline", "skip", "no headings")


def _resources_latex(info) -> Check:
    warnings = info.get("latex_warnings") or []
    if warnings:
        return Check("resources", "warn", f"{len(warnings)} warning(s)", [f"LaTeX: {w}" for w in warnings[:50]])
    return Check("resources", "pass", "no undefined references or citations")


def _outline(doc, geometry) -> Check:
    heads = [h for h in (geometry or {}).get("headings", []) if h["level"] <= 3 and h["text"].strip()]
    if not heads:
        return Check("outline", "skip", "no headings")
    titles = {_norm(entry[1]) for entry in doc.get_toc()}
    items = [f"heading '{h['text']}' (page {h['page']}) has no bookmark" for h in heads if _norm(h["text"]) not in titles]
    return _result("outline", items, f"{len(heads)} headings bookmarked")


def _metadata(pdf_path, info) -> Check:
    try:
        with pikepdf.open(pdf_path) as pdf:
            title = str(pdf.docinfo.get("/Title", ""))
            lang = str(pdf.Root.get("/Lang", ""))
    except (pikepdf.PdfError, OSError) as e:
        return Check("metadata", "fail", f"cannot read the metadata: {e}")
    items = []
    if not title.strip():
        items.append("no /Title")
    elif info.get("title") and title != info["title"]:
        items.append(f"/Title is '{title}', expected '{info['title']}'")
    if not lang:
        items.append("no /Lang")
    elif info.get("pdf_lang") and lang != info["pdf_lang"]:
        items.append(f"/Lang is '{lang}', expected '{info['pdf_lang']}'")
    return _result("metadata", items, f"title and language set ({lang})")


# Chrome writes one /ActualText per glyph. Read that way, ZWNJ survives, but a lam-alef ligature comes out reversed
# and hamza or kasra land beside the wrong letter. Read from ToUnicode instead, ligatures and marks are right and
# ZWNJ is lost. A word is intact when either reading has it; a word broken in the PDF itself fails both.
GLYPH_FLAGS = pymupdf.TEXTFLAGS_TEXT | pymupdf.TEXT_PRESERVE_LIGATURES | pymupdf.TEXT_IGNORE_ACTUALTEXT


def _persian(texts, info, glyph_texts=()) -> Check:
    words = info.get("fa_words") or []
    if not words:
        return Check("persian", "skip", "no Persian text")
    found: set[str] = set()
    for text in list(texts) + list(glyph_texts):
        found |= fa.persian_words(text)
    items = [f"'{w}' is missing or broken in the PDF text (reversed letters or a dropped half-space)" for w in words if w not in found]
    return _result("persian", items, f"{len(words)} Persian words extract intact")


def _digits(texts, info) -> Check:
    if info.get("digits") not in fa.FA_DIGIT_STYLES:
        return Check("digits", "skip", "Latin digits allowed")
    allowed = set(info.get("latin_numbers") or [])
    items = sorted({f"page {number}: Latin number '{n}'" for number, text in enumerate(texts, 1) for n in re.findall(r"[0-9]+", text) if n not in allowed})
    return _result("digits", items, "digit style matches")


def _empty(info) -> Check:
    if "empty_sections" not in info:
        return Check("empty-sections", "skip", "no source info")
    return _result("empty-sections", [f"section '{s}' has no content" for s in info["empty_sections"]], "no empty sections")


def _resources(info, geometry) -> Check:
    items = [
        f"font role '{role}': '{family}' is not installed; the next font in its chain is used"
        for role, families in (info.get("missing_fonts") or {}).items()
        for family in families
    ]
    geometry = geometry or {}
    items += [f"blocked (network is off): {url}" for url in geometry.get("blocked", [])]
    items += [f"failed to load: {f['url']} ({f['error']})" for f in geometry.get("failed", [])]
    items += [f"JavaScript error: {e}" for e in geometry.get("page_errors", [])]
    if items:
        return Check("resources", "warn", f"{len(items)} warning(s)", items[:50])
    return Check("resources", "pass", "all fonts and resources loaded")


# Each verifier returns None when its feature was not requested, or the list of problems it found.
FEATURE_CHECKS = [attach.verify_attachments]


def _features(pdf_path, info, geometry) -> Check:
    if not info.get("features"):
        return Check("features", "skip", "no feature info (standalone check)")
    try:
        with pikepdf.open(pdf_path) as pdf:
            results = [verify(pdf, info, geometry) for verify in FEATURE_CHECKS]
    except (pikepdf.PdfError, OSError) as e:
        return Check("features", "fail", f"cannot read the PDF: {e}")
    active = [r for r in results if r is not None]
    if not active:
        return Check("features", "skip", "no features requested")
    return _result("features", [item for r in active for item in r], f"{len(active)} requested feature(s) present")


def run_checks(pdf_path: Path, info: dict | None = None, geometry: dict | None = None) -> dict:
    info = info or {}
    pdf_path = Path(pdf_path)
    try:
        doc = pymupdf.open(pdf_path)
    except Exception as e:
        return _report(pdf_path, 0, [Check("opens", "fail", str(e))])
    problem = "the PDF is encrypted and needs a password to open" if doc.needs_pass else "the PDF has no pages" if doc.page_count == 0 else None
    if problem:
        doc.close()
        return _report(pdf_path, 0, [Check("opens", "fail", problem)])
    texts = [page.get_text() for page in doc]
    glyph_texts = [page.get_text(flags=GLYPH_FLAGS) for page in doc] if info.get("fa_words") else []
    checks = [
        Check("opens", "pass", f"{doc.page_count} pages"),
        _pages(doc, info),
        _fonts(doc, info),
        _overflow(geometry),
        _leftovers(_prose_texts(doc, info.get("mono_font_keys"))),
        _links(doc),
        _outline(doc, geometry),
        _metadata(pdf_path, info),
        _persian(texts, info, glyph_texts),
        _digits(texts, info),
        _empty(info),
        _features(pdf_path, info, geometry),
        _resources(info, geometry),
    ]
    if info.get("template") == "latex":
        # Checks that only make sense for HTML printed by Chrome are skipped explicitly, never passed silently.
        for i, check in enumerate(checks):
            if check.name in ("persian", "digits", "empty-sections"):
                checks[i] = Check(check.name, "skip", NA_LATEX)
            elif check.name == "overflow":
                checks[i] = _overflow_latex(info)
            elif check.name == "outline":
                checks[i] = _outline_latex(doc, info)
            elif check.name == "resources":
                checks[i] = _resources_latex(info)
    pages = doc.page_count
    doc.close()
    return _report(pdf_path, pages, checks)
