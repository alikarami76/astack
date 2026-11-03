#!/usr/bin/env python3
"""flynn HTML adapter: pack a rendered newsletter into an Outlook-ready email.

  email_pack.py <run> --subject "..." [--to a@x,b@y] [--link-base URL] [--out <file.eml>]

Reads <run>/page/index.html for the order of the [data-shot] blocks, each block's text (used as the image's
alt text) and its optional data-link. Takes the matching <run>/shots/sec-<name>.png files from render.py (rendered with --width 720 --pad 0, so the
blocks stack edge to edge) and
writes <run>/newsletter.eml, a multipart/related message:
  - an HTML body that is a plain 640px table of the section images: Outlook strips flex, grid and web fonts,
    so the email is images, with alt text for clients that block images, and one link per block;
  - a plain-text alternative made from the same block texts;
  - the PNGs embedded as cid: parts.
The message carries X-Unsent: 1, so Outlook opens it as a draft for the user to review and send. This script never
sends mail. Relative data-link values are joined to --link-base; placeholders like [لینک] are dropped."""
from __future__ import annotations

import argparse
import html
import re
import sys
from email.message import EmailMessage
from email.utils import make_msgid
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin

VOID = {"br", "img", "hr", "meta", "link", "input", "source", "wbr"}


class _Blocks(HTMLParser):
    """Collect each [data-shot] block in document order: name, data-link and its visible text."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks: list[dict] = []
        self.stack: list[dict | None] = []  # per open element: the block it opened, or None
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("script", "style"):
            self.skip += 1
        if tag in VOID:
            if tag == "img" and self._current() is not None and a.get("alt"):
                self._current()["text"].append(a["alt"])
            return
        blk = None
        if "data-shot" in a and self._current() is None:
            blk = {"name": a["data-shot"], "link": a.get("data-link") or "", "text": []}
            self.blocks.append(blk)
        self.stack.append(blk)

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)
        if tag in VOID:
            return
        if self.stack:
            self.stack.pop()

    def handle_data(self, data):
        cur = self._current()
        if cur is not None and not self.skip and data.strip():
            cur["text"].append(data.strip())

    def _current(self):
        for b in reversed(self.stack):
            if b is not None:
                return b
        return None


def blocks(page_html: str) -> list[dict]:
    p = _Blocks()
    p.feed(page_html)
    for b in p.blocks:
        b["text"] = re.sub(r"\s+", " ", " ".join(b["text"])).strip()
    return p.blocks


def resolve_link(link: str, base: str | None) -> str:
    link = (link or "").strip()
    if not link or link.startswith("[") or link == "#":
        return ""
    if re.match(r"^[a-z][a-z0-9+.-]*:", link, re.I):
        return link
    return urljoin(base, link) if base else ""


def png_width(path: Path) -> int:
    with path.open("rb") as f:
        head = f.read(24)
    return int.from_bytes(head[16:20], "big")


def pack(run: Path, subject: str, to: str = "", link_base: str | None = None, out: Path | None = None) -> Path:
    page = run / "page" / "index.html"
    shots = run / "shots"
    blks = blocks(page.read_text(encoding="utf-8"))
    if not blks:
        raise SystemExit(f"no [data-shot] blocks in {page}")
    missing = [b["name"] for b in blks if not (shots / f"sec-{b['name']}.png").exists()]
    if missing:
        raise SystemExit(f"missing section PNGs (run render.py first): {', '.join(missing)}")

    msg = EmailMessage()
    msg["Subject"] = subject
    if to:
        msg["To"] = to
    msg["X-Unsent"] = "1"  # Outlook opens the .eml as an editable draft
    text = "\n\n".join(b["text"] + (f"\n{resolve_link(b['link'], link_base)}" if resolve_link(b["link"], link_base) else "") for b in blks)
    msg.set_content(text)

    cids, rows = [], []
    for b in blks:
        png = shots / f"sec-{b['name']}.png"
        cid = make_msgid(domain="flynn.local")
        cids.append((cid, png))
        w = min(png_width(png) // 2, 640)  # PNGs are 2x; show at CSS size
        img = (f'<img src="cid:{cid[1:-1]}" width="{w}" alt="{html.escape(b["text"][:900], quote=True)}" '
               f'style="display:block;width:{w}px;max-width:100%;height:auto;border:0;outline:none;text-decoration:none;'
               f'font-family:Tahoma,Arial,sans-serif;font-size:14px;color:#2e3040;">')
        link = resolve_link(b["link"], link_base)
        cell = f'<a href="{html.escape(link, quote=True)}" target="_blank" style="text-decoration:none;">{img}</a>' if link else img
        rows.append(f'<tr><td align="center" style="padding:0;">{cell}</td></tr>')
    body = ('<!doctype html><html lang="fa" dir="rtl"><head><meta charset="utf-8">'
            f'<title>{html.escape(subject)}</title></head>'
            '<body style="margin:0;padding:0;background:#f6f6f9;">'
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#f6f6f9;">'
            '<tr><td align="center" style="padding:16px 0;">'
            '<table role="presentation" width="640" cellpadding="0" cellspacing="0" border="0" style="width:640px;max-width:100%;">'
            + "".join(rows) +
            '</table></td></tr></table></body></html>')
    msg.add_alternative(body, subtype="html")
    html_part = msg.get_payload()[1]
    for cid, png in cids:
        html_part.add_related(png.read_bytes(), maintype="image", subtype="png", cid=cid, filename=png.name)

    out = out or run / "newsletter.eml"
    out.write_bytes(bytes(msg))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run")
    ap.add_argument("--subject", required=True)
    ap.add_argument("--to", default="")
    ap.add_argument("--link-base")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    print(pack(Path(a.run), a.subject, a.to, a.link_base, Path(a.out) if a.out else None))
    return 0


if __name__ == "__main__":
    sys.exit(main())
