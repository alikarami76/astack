"""Compose stage: template + theme + fonts + content -> self-contained HTML and compose.json."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

import jinja2
import yaml
from markupsafe import Markup

from . import fa, fonts
from .errors import EnvError, InputError
from .md import render_markdown
from .paths import COMPONENTS, TEMPLATES, VENDOR
from .schema import load_doc
from .themes import load_theme, theme_css

PAGED_PLACEHOLDER = "/*__PAGED__*/"
SIZES = {"A3": "A3", "A4": "A4", "A5": "A5", "LETTER": "letter", "LEGAL": "legal", "16:9": "254mm 142.875mm", "4:3": "254mm 190.5mm"}
LABELS = {"toc": {"fa": "فهرست مطالب", "en": "Contents"}}
_ALIGN = {'style="text-align:right"': 'style="text-align:end"', 'style="text-align:left"': 'style="text-align:start"'}
_ASSET_TAG = re.compile(r"<(img|source|link)\b[^>]*>", re.I)
_ASSET_ATTR = re.compile(r"""(\s(src|srcset|href)\s*=\s*)("[^"]*"|'[^']*'|[^\s"'>]+)""", re.I)
_ASSET_ATTRS = {"img": {"src", "srcset"}, "source": {"src", "srcset"}, "link": {"href"}}
_STYLE_ATTR = re.compile(r"""(\sstyle\s*=\s*)("[^"]*"|'[^']*')""", re.I)
_STYLE_BLOCK = re.compile(r"(<style\b[^>]*>)(.*?)(</style>)", re.I | re.S)
_CSS_URL = re.compile(r"""(url\(\s*)(["']?)([^"')\s]+)(\2\s*\))""", re.I)
_RELATIVE = re.compile(r"(?![a-zA-Z][a-zA-Z0-9+.-]*:|/|#)\S")
_PLACEHOLDER = re.compile(r"(\{title\}|\{section\}|\{page\}|\{pages\})")


@dataclass
class Composed:
    html_path: Path
    info: dict


def list_templates() -> list[str]:
    return sorted(p.parent.name for p in TEMPLATES.glob("*/template.html.j2"))


def _css_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ") + '"'


def _counter(name: str, digits: str) -> str:
    return f"counter({name}, persian)" if digits in fa.FA_DIGIT_STYLES else f"counter({name})"


def _margin_literals(template: str, digits: str, normalize: bool) -> list[str]:
    return [fa.convert_plain(tok, digits, normalize) for tok in _PLACEHOLDER.split(template) if tok and not _PLACEHOLDER.fullmatch(tok)]


def _margin_content(template: str, title: str, digits: str, normalize: bool = False) -> str:
    parts = []
    for tok in _PLACEHOLDER.split(template):
        if not tok:
            continue
        special = {"{page}": _counter("page", digits), "{pages}": _counter("pages", digits), "{section}": "string(section)", "{title}": _css_str(title)}
        parts.append(special.get(tok) or _css_str(fa.convert_plain(tok, digits, normalize)))
    return " ".join(parts) if parts else "none"


def margin_latin_numbers(doc: dict, title: str) -> set[str]:
    """Latin numbers the page margins print, such as the 2 in 'v2', which the digits check must allow."""
    texts = []
    for template in (doc["header"], doc["footer"]):
        texts += _margin_literals(template, doc["digits"], doc.get("lang") in ("fa", "mixed"))
        if "{title}" in template:
            texts.append(title)
    return {n for text in texts for n in re.findall(r"[0-9]+", text)}


def page_css(doc: dict, title: str) -> str:
    page = doc["page"]
    size = SIZES.get(page["size"].upper(), page["size"])
    if " " not in size:
        size = f"{size} {page['orientation']}"
    box = "font-family: var(--font-body); font-size: 8pt; color: var(--color-muted);"
    lines = ["@page {", f"  size: {size};", f"  margin: {page['margins']};"]
    normalize = doc.get("lang") in ("fa", "mixed")
    if doc["header"]:
        lines.append(f"  @top-center {{ content: {_margin_content(doc['header'], title, doc['digits'], normalize)}; {box} }}")
    if doc["footer"]:
        lines.append(f"  @bottom-center {{ content: {_margin_content(doc['footer'], title, doc['digits'], normalize)}; {box} }}")
    lines.append("}")
    if doc["cover"]:
        lines.append("@page cover { @top-center { content: none; } @bottom-center { content: none; } }")
    lines.append("h1 { string-set: section content(text); }")
    return "\n".join(lines) + "\n"


class _Headings(HTMLParser):
    def __init__(self):
        super().__init__()
        self.items: list[dict] = []
        self.current: dict | None = None

    def handle_starttag(self, tag, attrs):
        if re.fullmatch(r"h[1-6]", tag):
            self.current = {"level": int(tag[1]), "text": "", "id": dict(attrs).get("id") or ""}

    def handle_endtag(self, tag):
        if self.current and tag == f"h{self.current['level']}":
            self.current["text"] = " ".join(self.current["text"].split())
            self.items.append(self.current)
            self.current = None

    def handle_data(self, data):
        if self.current:
            self.current["text"] += data


def _html_headings(html: str) -> list[dict]:
    parser = _Headings()
    parser.feed(html)
    parser.close()
    return parser.items


def _abs_url(url: str, base: Path) -> str:
    if not _RELATIVE.match(url):
        return url
    path, suffix = re.match(r"([^?#]*)(.*)", url, re.S).groups()
    return (base / path).resolve().as_uri() + suffix


def _abs_srcset(value: str, base: Path) -> str:
    entries = []
    for entry in value.split(","):
        if not entry.strip():
            entries.append(entry)
            continue
        lead = entry[: len(entry) - len(entry.lstrip())]
        url, *descriptor = entry.strip().split(None, 1)
        entries.append(lead + " ".join([_abs_url(url, base)] + descriptor))
    return ",".join(entries)


def _abs_css(css: str, base: Path) -> str:
    return _CSS_URL.sub(lambda m: m.group(1) + m.group(2) + _abs_url(m.group(3), base) + m.group(4), css)


def _abs_attr_value(raw: str, fix) -> str:
    quote = raw[0] if raw[:1] in "\"'" else ""
    inner = raw[1:-1] if quote else raw
    return quote + fix(inner) + quote


def _abs_tag(m: re.Match, base: Path) -> str:
    wanted = _ASSET_ATTRS[m.group(1).lower()]

    def attr(a: re.Match) -> str:
        name = a.group(2).lower()
        if name not in wanted:
            return a.group()
        fix = (lambda v: _abs_srcset(v, base)) if name == "srcset" else (lambda v: _abs_url(v.strip(), base))
        return a.group(1) + _abs_attr_value(a.group(3), fix)

    return _ASSET_ATTR.sub(attr, m.group())


def _absolutize(html: str, base: Path) -> str:
    """Rewrite relative asset URLs against the build folder, because composed.html sits in build/. A <base href> would
    do the same but would also break #anchor links."""
    html = _ASSET_TAG.sub(lambda m: _abs_tag(m, base), html)
    html = _STYLE_ATTR.sub(lambda m: m.group(1) + _abs_attr_value(m.group(2), lambda v: _abs_css(v, base)), html)
    return _STYLE_BLOCK.sub(lambda m: m.group(1) + _abs_css(m.group(2), base) + m.group(3), html)


def _template_pages(tpl_dir: Path):
    path = tpl_dir / "expect.yaml"
    return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("pages") if path.is_file() else None


def _features_info(features: dict, build_dir: Path) -> dict:
    """The features the post and check stages act on, with file paths made absolute. Passwords stay as env:VAR."""
    def paths(names):
        return [str((build_dir / name).resolve()) for name in names]

    return {
        "attachments": paths(features["attachments"]),
        "forms": features["forms"],
        "encrypt": features["encrypt"],
        "merge": {"before": paths(features["merge"]["before"]), "after": paths(features["merge"]["after"])},
        "pdfa": features["pdfa"],
    }


def _mono_font_keys(roles) -> set[str]:
    """Font keys used only by the mono role, so spans in them are code (a chain that shares a font with body text gets no exemption for it)."""
    if "mono" not in roles:
        return set()
    others = fonts.allowed_font_keys({r: v for r, v in roles.items() if r != "mono"})
    return fonts.allowed_font_keys({"mono": roles["mono"]}) - others


def compose(build_dir: Path) -> Composed:
    build_dir = Path(build_dir)
    doc = load_doc(build_dir)
    if doc["template"] == "latex":
        raise InputError("template 'latex' has no HTML to compose or preview; run `pdfsmith build` to compile main.tex")
    tpl_dir = TEMPLATES / doc["template"]
    if not (tpl_dir / "template.html.j2").is_file():
        raise InputError(f"doc.yaml: template '{doc['template']}' not found. Available: {', '.join(list_templates())}")
    theme = load_theme(doc["theme"])
    roles = fonts.resolve_roles(theme["fonts"], doc["fonts"], fonts.scan(build_dir))
    generic = theme.get("generic", {})
    stacks = {r: fonts.font_stack(role, generic.get(r, "monospace" if r == "mono" else "sans-serif")) for r, role in roles.items()}

    key = "content" if doc["content"] else "html"
    files = doc[key] if isinstance(doc[key], list) else [doc[key]]
    source = "\n\n".join((build_dir / f).read_text(encoding="utf-8") for f in files)
    if key == "content":
        result = render_markdown(source)
        body, headings, empty, src_title = result.html, [vars(h) for h in result.headings], result.empty_sections, result.title
    else:
        body, headings, empty = source, _html_headings(source), []
        src_title = next((h["text"] for h in headings if h["level"] == 1), None)
    body = _absolutize(body, build_dir)
    for physical, logical in _ALIGN.items():
        body = body.replace(physical, logical)

    is_fa = doc["lang"] in ("fa", "mixed")
    lang_key = "en" if doc["lang"] == "en" else "fa"
    title = fa.convert_plain(doc["meta"]["title"] or src_title or doc["name"], doc["digits"], normalize=is_fa)
    meta = dict(doc["meta"], title=title, date_display=fa.format_date(doc["meta"]["date"], doc["calendar"], doc["digits"], lang_key))
    html_dir = "rtl" if doc["dir"] == "rtl" or (doc["dir"] == "auto" and doc["lang"] == "fa") else "ltr"
    template_css = tpl_dir / "template.css"
    css = "\n".join([
        fonts.font_face_css(roles),
        theme_css(theme, stacks),
        (COMPONENTS / "base.css").read_text(encoding="utf-8"),
        page_css(doc, title),
        template_css.read_text(encoding="utf-8") if template_css.is_file() else "",
    ])

    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader([str(tpl_dir), str(COMPONENTS)]),
        autoescape=jinja2.select_autoescape(["html", "j2"]),
        keep_trailing_newline=True,
    )
    html = env.get_template("template.html.j2").render(
        doc=doc,
        meta=meta,
        body=Markup(body),
        headings=headings,
        toc=[h for h in headings if h["level"] <= 2 and h["id"]],
        labels={k: v[lang_key] for k, v in LABELS.items()},
        css=Markup(css),
        html_lang=doc["pdf_lang"],
        html_dir=html_dir,
        html_classes=f"digits-{doc['digits']} lang-{doc['lang']}",
        paged_js=Markup(PAGED_PLACEHOLDER),
    )
    options = fa.TransformOptions(
        digits=doc["digits"],
        isolate=doc["dir"] in ("rtl", "auto"),
        auto_dir=doc["dir"] == "auto",
        normalize=is_fa,
        halfspace=doc["halfspace"] == "auto",
    )
    html, stats = fa.transform_html(html, options)
    paged = VENDOR / "paged.polyfill.js"
    if not paged.is_file():
        raise EnvError(f"{paged} is missing. Run `pdfsmith doctor` to check the installation.")
    html = html.replace(PAGED_PLACEHOLDER, paged.read_text(encoding="utf-8"), 1)

    missing = {}
    for context, chars in stats.chars.items():
        gaps = fonts.missing_glyphs(chars, roles.get(context) or roles["body"])
        if gaps:
            missing[context] = sorted(gaps)

    info = {
        "title": title,
        "author": doc["meta"]["author"],
        "subject": doc["meta"]["subject"],
        "keywords": doc["meta"]["keywords"] or [],
        "lang": doc["lang"],
        "pdf_lang": doc["pdf_lang"],
        "rtl": html_dir == "rtl",
        "digits": doc["digits"],
        "template": doc["template"],
        "theme": doc["theme"],
        "headings": headings,
        "empty_sections": empty,
        "missing_glyphs": missing,
        "missing_fonts": {r: role.missing for r, role in roles.items() if role.missing},
        "allowed_font_keys": sorted(fonts.allowed_font_keys(roles)),
        "mono_font_keys": sorted(_mono_font_keys(roles)),
        "fa_words": sorted(stats.fa_words),
        "latin_numbers": sorted(stats.latin_numbers | margin_latin_numbers(doc, title)),
        "pages_expect": doc["checks"]["pages"] or _template_pages(tpl_dir),
        "engine": "weasyprint" if doc["features"]["pdfa"] else "chrome",
        "features": _features_info(doc["features"], build_dir),
    }
    out = build_dir / "build"
    out.mkdir(exist_ok=True)
    (out / "composed.html").write_text(html, encoding="utf-8")
    (out / "compose.json").write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    return Composed(out / "composed.html", info)
