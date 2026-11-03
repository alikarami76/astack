#!/usr/bin/env python3
"""flynn Canva adapter: turn an HTML page built with the editorial templates into a plan that the flynn agent
carries out through the Canva MCP tools. This script never calls Canva itself.

  canva_plan.py <page.html> --design <design id> --elements <read-design dump.json> [--out <folder>]
      Map the page's text onto an existing Canva design's text elements. Writes <out>/canva-ops.json (the
      `page_index` and `operations` for the edit-design tool: replace_text only) and <out>/canva-plan.md
      (what maps where, what does not fit, what the user must do by hand). <out> defaults to the run folder
      (the page's parent's parent).

  canva_plan.py outline <page.html> [--out <folder>] [--topic "..."] [--audience "..."]
      Write <out>/canva-outline.json, the arguments for generate-design-structured (topic, audience, style,
      length, design_type, presentation_outlines), for when there is no Canva template to fill. Canva requires
      the outline to go through request-outline-review and the user's approval before generate-design-structured.

Why only a plan: Canva's limits for flynn are verified (2026-09-30). The Free plan cannot use custom fonts,
so the theme's font is not available. The API cannot set fonts or page height. Exports were blocked for these
designs. Uploads and imports need public URLs, so local screenshots cannot be sent. Text is the one thing flynn
can move reliably, so text is what this adapter maps; everything else is listed for the user.

Matching: page blocks and Canva text elements each get a role (title, heading, label, body). Page roles come
from the markup (h1 title, h2 section heading, h3-h6 item titles counted as body, small labels like .k/.kicker/.sec-label/.tag/.num, the rest body). Canva roles
come from font size and weight, ranked across the design. Within each role, page blocks in document order are
paired with Canva elements in reading order (top to bottom, then right to left). A replacement that is much
longer than the element was designed for is flagged as not fitting, since Canva cannot grow the page."""
from __future__ import annotations

import argparse
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

BLOCK_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "figcaption", "td", "th", "dt", "dd", "blockquote", "div", "span", "a", "b", "small"}
LABEL_CLASSES = {"k", "kicker", "sec-label", "tag", "num", "n", "eyebrow", "label", "who", "pill", "kind", "i"}
SKIP_TAGS = {"script", "style", "svg", "title", "head"}
BRAND_CLASSES = {"wordmark", "logo"}
BRAND_TEXT = {"فروشگاه نمونه", "sample shop"}


class _Blocks(HTMLParser):
    """Collect text blocks: each block element owns the text directly inside it (not its block children)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.blocks, self.shot, self.skip, self.n = [], [], [], 0, 0
        self._shot_depth = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in SKIP_TAGS:
            self.skip += 1
            return
        if tag in ("br",):
            if self.stack:
                self.stack[-1]["text"] += "\n"
            return
        if tag in ("img", "meta", "link", "input", "hr"):
            return
        if "data-shot" in a:
            self.shot.append(a["data-shot"])
            self._shot_depth.append(len(self.stack))
        cls = set((a.get("class") or "").split())
        inline = tag in ("span", "a", "b", "small") and not (cls & LABEL_CLASSES)
        self.n += 1  # document order = order of start tags
        self.stack.append({"tag": tag, "cls": cls, "text": "", "inline": inline, "shot": self.shot[-1] if self.shot else None, "start": self.n})

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS:
            self.skip = max(0, self.skip - 1)
            return
        if not self.stack or tag in ("br", "img", "meta", "link", "input", "hr"):
            return
        # pop to the matching tag (tolerate unclosed tags)
        while self.stack:
            el = self.stack.pop()
            if el["inline"] and self.stack:
                self.stack[-1]["text"] += el["text"]  # inline text belongs to the enclosing block
            else:
                t = re.sub(r"[ \t\r\f\v]+", " ", el["text"]).strip()
                t = re.sub(r" *\n *", "\n", t)
                if t:
                    self.blocks.append({"tag": el["tag"], "cls": sorted(el["cls"]), "text": t, "shot": el["shot"], "order": el["start"]})
            if self._shot_depth and len(self.stack) == self._shot_depth[-1]:
                self._shot_depth.pop(); self.shot.pop()
            if el["tag"] == tag:
                break

    def handle_data(self, data):
        if self.skip or not self.stack:
            return
        self.stack[-1]["text"] += data


def page_blocks(html: str) -> list[dict]:
    p = _Blocks()
    p.feed(html)
    # blocks close child-first; restore document order by the position of their first text
    # the brand wordmark is part of the template, never copy to move; blocks close child-first, so restore
    # document order from the start-tag index
    blocks = [b for b in p.blocks if not set(b["cls"]) & BRAND_CLASSES]
    for b in blocks:
        b["role"] = page_role(b)
    blocks.sort(key=lambda b: b["order"])
    return blocks


def page_role(b: dict) -> str:
    if b["tag"] == "h1":
        return "title"
    if b["tag"] == "h2":
        return "heading"  # section titles; h3-h6 are item titles and travel with the body copy
    if set(b["cls"]) & LABEL_CLASSES or b["tag"] in ("th",):
        return "label"
    return "body"


# ---------- Canva side ----------

def canva_page(dump: dict, page_index: int = 1) -> dict:
    """Accept the shapes flynn has saved: a read-design response ({"design_content": {"pages": [...]}}), a single
    page wrapper ({"page": {...}}), or a bare page ({"elements": [...]})."""
    if "design_content" in dump:
        return dump["design_content"]["pages"][page_index - 1]
    if "pages" in dump:
        return dump["pages"][page_index - 1]
    if "page" in dump:
        return dump["page"]
    return dump


def text_elements(page: dict) -> list[dict]:
    out = []
    for e in page.get("elements", []):
        if e.get("type") != "text":
            continue
        regions = e.get("textRegions") or []
        text = "".join(r.get("characters", "") for r in regions)
        fmt = (regions[0].get("formatting") if regions else {}) or {}
        out.append({"locator_id": e.get("locator_id") or e["id"], "top": e["top"], "left": e["left"], "width": e["width"],
                    "height": e["height"], "text": text, "size": fmt.get("fontSize", 14), "weight": fmt.get("fontWeight", "normal"),
                    "locked": e.get("isLocked", False)})
    return out


def canva_roles(els: list[dict]) -> None:
    sizes = sorted({e["size"] for e in els}, reverse=True)
    top = sizes[0] if sizes else 0
    body = sorted((e["size"] for e in els), key=lambda s: sum(1 for x in els if x["size"] == s))[-1] if els else 0  # most common size
    for e in els:
        if e["text"].strip().lower() in BRAND_TEXT:
            e["role"] = "brand"  # the wordmark stays as it is
            continue
        if re.fullmatch(r"[\d۰-۹]{1,3}", e["text"].strip()):
            e["role"] = "label"  # section and step numerals, whatever their size
            continue
        heavy = str(e["weight"]) in ("bold", "semibold", "heavy", "ultrabold", "black") or str(e["weight"]).isdigit() and int(e["weight"]) >= 600
        words = len(e["text"].split())
        if e["size"] == top and top > body:
            e["role"] = "title"
        elif e["size"] > body and heavy and words <= 12:
            e["role"] = "heading"  # headings are larger, bold and short; a larger paragraph is still body
        elif (e["size"] < body and heavy and words <= 4) or re.fullmatch(r"[\d۰-۹]{1,3}", e["text"].strip()):
            e["role"] = "label"
        else:
            e["role"] = "body"


def capacity(e: dict) -> float:
    """Rough number of characters the element can hold at its font size (Persian glyphs ~0.5em wide)."""
    lines = max(1.0, round(e["height"] / (e["size"] * 1.6)))
    per_line = e["width"] / (e["size"] * 0.45)
    return lines * per_line


def plan(html: str, dump: dict, design_id: str, page_index: int = 1) -> dict:
    blocks = page_blocks(html)
    page = canva_page(dump, page_index)
    els = [e for e in text_elements(page) if not e["locked"]]
    canva_roles(els)
    els.sort(key=lambda e: (round(e["top"] / 4), -(e["left"] + e["width"])))  # reading order: top, then right to left
    ops, mapped, overflow, unplaced = [], [], [], []
    used = set()
    for role in ("title", "heading", "label", "body"):
        slots = [e for e in els if e["role"] == role]
        texts = [b for b in blocks if b["role"] == role]
        for b, e in zip(texts, slots):
            used.add(e["locator_id"])
            ops.append({"type": "replace_text", "locator_id": e["locator_id"], "text": b["text"]})
            fits = len(b["text"]) <= max(capacity(e) * 1.15, len(e["text"]) * 1.1)
            mapped.append({"role": role, "shot": b["shot"], "from": e["text"], "to": b["text"], "locator_id": e["locator_id"], "fits": fits})
            if not fits:
                overflow.append(mapped[-1])
        unplaced += [{"role": role, "shot": b["shot"], "text": b["text"]} for b in texts[len(slots):]]
    untouched = [{"role": e["role"], "text": e["text"], "locator_id": e["locator_id"]} for e in els if e["locator_id"] not in used and e["role"] != "brand"]
    return {"design_id": design_id, "page_index": page_index,
            "edit_design": {"page_index": page_index, "operations": ops,
                            "user_intent": "Replace the template's text with the page's copy (flynn Canva handoff)"},
            "mapped": mapped, "overflow": overflow, "unplaced": unplaced, "untouched": untouched}


def plan_md(p: dict, page_path: str) -> str:
    L = [f"# Canva plan for design {p['design_id']}", "",
         f"Source page: `{page_path}`. Operations: `canva-ops.json` ({len(p['edit_design']['operations'])} text replacements, page {p['page_index']}).", "",
         "How flynn runs it: `read-design` the design to open a transaction, then `edit-design` with `finalize: keep_open` and the operations,",
         "show the user the returned preview, and only after they approve call `edit-design` with `finalize: commit`. Never commit without their approval.", "",
         "## Does not fit", ""]
    L += [f"- **{m['role']}** «{m['to'][:80]}» into a slot made for «{m['from'][:40]}» ({m['locator_id']}). Shorten the copy, or resize the element by hand; Canva will not grow the page." for m in p["overflow"]] or ["- Nothing: every replacement is within the element's rough capacity."]
    L += ["", "## Page text with no slot in the design", ""]
    L += [f"- {u['role']} ({u['shot'] or 'no section'}): «{u['text'][:100]}»" for u in p["unplaced"]] or ["- None."]
    L += ["", "## Template text left unchanged", "", "Delete or keep each of these by hand (the plan never deletes):", ""]
    L += [f"- {u['role']}: «{u['text'][:80]}» ({u['locator_id']})" for u in p["untouched"]] or ["- None."]
    L += ["", "## What the user must do by hand in Canva", "",
          "- Fonts: the theme's font is not available on the Canva Free plan, and the API cannot set fonts. The design keeps the template's font.",
          "- Images and screenshots: uploads need a public URL, so local screenshots cannot be sent. Drag them in from the run's `page/img/` folder.",
          "- Page height: the API cannot change it. If the new copy is longer, extend the page in Canva, then move the lower sections down.",
          "- Export: exports were blocked for these designs; download from the Canva editor.", "",
          "## Mapping", "", "| Role | Section | Replaces | With | Fits |", "|---|---|---|---|---|"]
    L += [f"| {m['role']} | {m['shot'] or ''} | {m['from'][:30].replace('|', '/')} | {m['to'][:50].replace('|', '/')} | {'yes' if m['fits'] else '**no**'} |" for m in p["mapped"]]
    return "\n".join(L) + "\n"


def outline(html: str, topic: str | None = None, audience: str = "product, business and finance teams") -> dict:
    """Arguments for generate-design-structured: one outline entry per data-shot section."""
    blocks = page_blocks(html)
    sections, order = {}, []
    for b in blocks:
        key = b["shot"] or "page"
        if key not in sections:
            sections[key] = []
            order.append(key)
        sections[key].append(b)
    outlines = []
    for key in order:
        bs = sections[key]
        head = next((b["text"] for b in bs if b["role"] in ("title", "heading")), None) or bs[0]["text"]
        rest = [b["text"].replace("\n", " ") for b in bs if b["text"] != head]
        desc = " ".join(rest)
        outlines.append({"title": head.replace("\n", " ")[:120], "description": (desc[:600] + ("…" if len(desc) > 600 else "")) or head})
    title = next((b["text"] for b in blocks if b["role"] == "title"), outlines[0]["title"] if outlines else "")
    return {"topic": (topic or title.replace("\n", " "))[:150], "audience": audience,
            "style": "Clean editorial, right-to-left Persian, white background, the teal accent (#0f766e) as a sparing accent, large bold headings",
            "length": f"{len(outlines)} pages", "design_type": "presentation", "presentation_outlines": outlines,
            "_note": "Send this through request-outline-review and get the user's approval before generate-design-structured."}


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "outline":
        ap = argparse.ArgumentParser(prog="canva_plan.py outline")
        ap.add_argument("page"); ap.add_argument("--out"); ap.add_argument("--topic"); ap.add_argument("--audience", default="product, business and finance teams")
        a = ap.parse_args(argv[1:])
        page = Path(a.page)
        out = Path(a.out) if a.out else page.resolve().parent.parent
        o = outline(page.read_text(encoding="utf-8"), a.topic, a.audience)
        out.mkdir(parents=True, exist_ok=True)
        (out / "canva-outline.json").write_text(json.dumps(o, ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps({"out": str(out / "canva-outline.json"), "pages": len(o["presentation_outlines"])}, ensure_ascii=False))
        return 0
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("page"); ap.add_argument("--design", required=True); ap.add_argument("--elements", required=True)
    ap.add_argument("--page-index", type=int, default=1); ap.add_argument("--out")
    a = ap.parse_args(argv)
    page = Path(a.page)
    out = Path(a.out) if a.out else page.resolve().parent.parent
    p = plan(page.read_text(encoding="utf-8"), json.loads(Path(a.elements).read_text(encoding="utf-8")), a.design, a.page_index)
    out.mkdir(parents=True, exist_ok=True)
    (out / "canva-ops.json").write_text(json.dumps({"design_id": p["design_id"], **p["edit_design"]}, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "canva-plan.md").write_text(plan_md(p, str(page)), encoding="utf-8")
    print(json.dumps({"ops": len(p["edit_design"]["operations"]), "overflow": len(p["overflow"]), "unplaced": len(p["unplaced"]),
                      "untouched": len(p["untouched"]), "out": str(out)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
