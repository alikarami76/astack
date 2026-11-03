"""Markdown to HTML with stable heading ids and empty-section detection."""

from __future__ import annotations

import re
from dataclasses import dataclass

from markdown_it import MarkdownIt
from mdit_py_plugins.deflist import deflist_plugin
from mdit_py_plugins.footnote import footnote_plugin

_CUSTOM_ID = re.compile(r"\s*\{#([A-Za-z][\w-]*)\}\s*$")


@dataclass
class Heading:
    level: int
    text: str
    id: str


@dataclass
class MdResult:
    html: str
    headings: list
    empty_sections: list
    title: str | None


def _parser() -> MarkdownIt:
    md = MarkdownIt("commonmark", {"html": True}).enable(["table", "strikethrough"])
    md.use(footnote_plugin).use(deflist_plugin)
    md.add_render_rule("footnote_anchor", lambda *args, **kwargs: "")
    return md


def render_markdown(text: str) -> MdResult:
    md = _parser()
    env: dict = {}
    tokens = md.parse(text, env)
    headings: list[Heading] = []
    for i, tok in enumerate(tokens):
        if tok.type != "heading_open":
            continue
        kids = tokens[i + 1].children or []
        hid = f"h{len(headings) + 1}"
        if kids and kids[-1].type == "text":
            m = _CUSTOM_ID.search(kids[-1].content)
            if m:
                hid = m.group(1)
                kids[-1].content = kids[-1].content[: m.start()]
        tok.attrSet("id", hid)
        label = "".join(c.content for c in kids if c.type in ("text", "code_inline")).strip()
        headings.append(Heading(int(tok.tag[1]), label, hid))

    by_id = {h.id: h.text for h in headings}
    tops = [t for t in tokens if t.level == 0 and t.nesting != -1]
    empty = []
    for j, tok in enumerate(tops):
        if tok.type != "heading_open":
            continue
        nxt = tops[j + 1] if j + 1 < len(tops) else None
        if nxt is None or (nxt.type == "heading_open" and int(nxt.tag[1]) <= int(tok.tag[1])):
            empty.append(by_id[tok.attrGet("id")])

    html = md.renderer.render(tokens, md.options, env)
    title = next((h.text for h in headings if h.level == 1), None)
    return MdResult(html, headings, empty, title)
