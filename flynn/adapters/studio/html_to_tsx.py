#!/usr/bin/env python3
"""flynn Studio: convert a flynn HTML page into a React component written the way web-app writes
components (conventions from quorra, run 2026-09-30-scf-tsx-export-conventions):

  <camelName>/index.tsx            named arrow-function export, `interface <Name>Props`, CSS module classes
  <camelName>/<camelName>.module.scss   the page's CSS scoped under .root; no font-family, no @font-face,
                                   no direction (web-app sets RTL and Vazirmatn globally)
  <camelName>/constants.ts         every Persian string in a TRANSLATIONS object, not inline in the TSX
  <camelName>/assets/              the images, imported (Rsbuild bundles them)

Formatting follows the repo's Prettier settings (4 spaces, single quotes, trailing commas, semicolons).
Imports stay inside the folder (no ../). Tables get a <tbody>. SVG attributes are camelCased.
Honest limits: raw HTML elements, not UIKit components (quorra has not mapped UIKit props to flynn components
yet); a developer swaps in UIKit Button/Tab/TableView where the page has those patterns.

  html_to_tsx.py <page/index.html> <out dir> [--name ReleasePage]"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from html.parser import HTMLParser
from pathlib import Path

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
SKIP = {"script", "style", "head", "title", "link", "meta"}
ATTR_MAP = {"class": "className", "for": "htmlFor", "tabindex": "tabIndex", "colspan": "colSpan", "rowspan": "rowSpan",
            "viewbox": "viewBox", "readonly": "readOnly", "maxlength": "maxLength", "srcset": "srcSet", "crossorigin": "crossOrigin"}
SVG_KEEP = {"viewBox", "preserveAspectRatio", "xmlns"}
ONLY_SYMBOLS = re.compile(r"^[\s\W_]*$")


class Node:
    def __init__(self, tag, attrs=None, parent=None):
        self.tag, self.attrs, self.children, self.parent = tag, dict(attrs or []), [], parent


class P(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root")
        self.cur = self.root
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in SKIP:
            if tag not in VOID:
                self.skip += 1
            return
        if self.skip:
            return
        n = Node(tag, attrs, self.cur)
        self.cur.children.append(n)
        if tag not in VOID:
            self.cur = n

    def handle_startendtag(self, tag, attrs):
        if tag in SKIP or self.skip:
            return
        self.cur.children.append(Node(tag, attrs, self.cur))

    def handle_endtag(self, tag):
        if tag in SKIP and tag not in VOID:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip or tag in VOID:
            return
        n = self.cur
        while n is not self.root and n.tag != tag:
            n = n.parent
        if n is not self.root:
            self.cur = n.parent

    def handle_data(self, data):
        if self.skip:
            return
        self.cur.children.append(data)


def camel(s: str) -> str:
    return re.sub(r"-([a-z])", lambda m: m.group(1).upper(), s)


def pascal(s: str) -> str:
    parts = re.split(r"[^A-Za-z0-9]+", s)
    return "".join(p[:1].upper() + p[1:] for p in parts if p) or "Page"


def _q(s: str) -> str:
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _cls(c: str) -> str:
    """styles.foo when foo is a valid identifier, styles['sec-label'] otherwise."""
    return f"styles.{c}" if re.match(r"^[A-Za-z_$][\w$]*$", c) else f"styles[{_q(c)}]"


def style_obj(css: str) -> str:
    items = []
    for decl in css.split(";"):
        if ":" not in decl:
            continue
        k, v = decl.split(":", 1)
        k, v = k.strip(), v.strip()
        if not k:
            continue
        key = k if k.startswith("--") else camel(k)
        items.append(f"{_q(key) if key.startswith('--') else key}: {_q(v)}")
    return "{{ " + ", ".join(items) + " }}"


MODULE_CLASS = re.compile(r"[A-Z]")  # hashed CSS-module classes (Button__root-x1y2z3) exist only in the site build


class Emitter:
    def __init__(self, in_svg_tags=("svg",), uikit: dict[str, str] | None = None, sc_mode: bool = False):
        self.uikit = uikit or {}          # component name -> import line, from systems/admin-panel specs
        self.sc_mode = sc_mode            # page drawn with the Admin Panel kit: global utility classes, no module
        self.used: dict[str, str] = {}
        self.variants: dict[str, dict] = {}
        self.unknown: set[str] = set()
        self.strings: dict[str, str] = {}
        self.by_text: dict[str, str] = {}
        self.images: dict[str, str] = {}
        self.classes: set[str] = set()
        self.shot = "page"
        self.counter: dict[str, int] = {}

    def key_for(self, text: str) -> str:
        if text in self.by_text:
            return self.by_text[text]
        base = camel(re.sub(r"[^a-z0-9]+", "-", self.shot.lower()).strip("-")) or "page"
        self.counter[base] = self.counter.get(base, 0) + 1
        k = f"{base}{self.counter[base]}"
        self.strings[k], self.by_text[text] = text, k
        return k

    def attrs(self, n: Node, in_svg: bool) -> str:
        out = []
        for k, v in n.attrs.items():
            v = v if v is not None else ""
            lk = k.lower()
            if lk == "class":
                cls = [c for c in v.split() if c]
                if self.sc_mode:
                    keep = [c for c in cls if not MODULE_CLASS.search(c)]
                    if keep:
                        out.append(f"className={_q(' '.join(keep))}")
                    continue
                self.classes.update(cls)
                if len(cls) == 1:
                    out.append(f"className={{{_cls(cls[0])}}}")
                elif cls:
                    out.append("className={[" + ", ".join(_cls(c) for c in cls) + "].join(' ')}")
                continue
            if lk == "style":
                out.append(f"style={style_obj(v)}")
                continue
            if lk == "dir" and n.tag in ("html", "body"):
                continue
            if lk == "src" and n.tag == "img" and not re.match(r"^(https?:|data:|/)", v):
                ident = "img" + pascal(Path(v).stem)
                self.images[ident] = v
                out.append(f"src={{{ident}}}")
                continue
            if lk in ("alt", "title", "aria-label", "placeholder") and re.search(r"[؀-ۿ]", v):
                out.append(f"{ATTR_MAP.get(lk, k)}={{TRANSLATIONS.{self.key_for(v)}}}")
                continue
            if lk.startswith(("data-", "aria-")):
                name = k
            elif in_svg and ("-" in k or ":" in k) and k not in SVG_KEEP:
                name = camel(k.replace(":", "-"))
            else:
                name = ATTR_MAP.get(lk, k)
            out.append(f"{name}={_q(v) if not v.isdigit() or name.startswith(('data', 'aria')) else _q(v)}")
        return (" " + " ".join(out)) if out else ""

    def text(self, t: str, ind: str) -> list[str]:
        t = re.sub(r"\s+", " ", t)
        if not t.strip():
            return [" "] if t == " " else []
        core = t.strip()
        if ONLY_SYMBOLS.match(core) and not re.search(r"[؀-ۿ0-9]", core):
            return [ind + "{" + _q(t) + "}"]
        lead, trail = (" " if t[:1] == " " else ""), (" " if t[-1:] == " " else "")
        return [ind + ("{' '}" if lead else "") + f"{{TRANSLATIONS.{self.key_for(core)}}}" + ("{' '}" if trail else "")]

    def node(self, n, depth: int, in_svg=False) -> list[str]:
        ind = "    " * depth
        if isinstance(n, str):
            return self.text(n, ind)
        if n.tag in ("html", "body", "#root"):
            return [l for c in n.children for l in self.node(c, depth, in_svg)]
        in_svg = in_svg or n.tag == "svg"
        if n.attrs.get("data-shot"):
            self.shot = n.attrs["data-shot"]
        if n.attrs.get("data-uikit"):
            return self.uikit_node(n, ind)
        tag = n.tag
        a = self.attrs(n, in_svg)
        kids = n.children
        if tag == "table" and not any(isinstance(c, Node) and c.tag in ("tbody", "thead") for c in kids):
            body = Node("tbody", [], n)
            body.children = kids
            kids = [body]
        if tag in VOID or not [c for c in kids if not (isinstance(c, str) and not c.strip())]:
            return [f"{ind}<{tag}{a} />"]
        inner = [l for c in kids for l in self.node(c, depth + 1, in_svg)]
        inner = [l for l in inner if l.strip()]
        return [f"{ind}<{tag}{a}>", *inner, f"{ind}</{tag}>"]


    def uikit_node(self, n, ind: str) -> list[str]:
        name = n.attrs["data-uikit"]
        comp = name.split(".")[-1]
        if name in self.uikit:
            self.used[comp] = self.uikit[name]
        else:
            self.unknown.add(name)
        try:
            props = json.loads(n.attrs.get("data-props") or "{}")
        except ValueError:
            props = {}
        if not n.attrs.get("data-props"):
            props = infer_props(n.attrs.get("class") or "", self.variants.get(name) or {})
        parts = []
        for k, v in props.items():
            if isinstance(v, str) and re.search(r"[؀-ۿ]", v):
                parts.append(f"{k}={{TRANSLATIONS.{self.key_for(v)}}}")
            elif isinstance(v, str):
                parts.append(f"{k}={_q(v)}")
            else:
                parts.append(f"{k}={{{json.dumps(v, ensure_ascii=False)}}}")
        if any(isinstance(d, Node) and d is not n and d.attrs.get("data-uikit") for d in _walk(n)):
            # a container around other library components: keep the structure inside it
            inner = [l for c in n.children for l in self.node(c, len(ind) // 4 + 1)]
            inner = [l for l in inner if l.strip()]
            p = (" " + " ".join(parts)) if parts else ""
            return [f"{ind}<{comp}{p}>", *inner, f"{ind}</{comp}>"]
        text = re.sub(r"\s+", " ", "".join(_texts(n))).strip()
        label = n.attrs.get("data-children", "text" if text and comp in CHILD_TEXT else "")
        p = (" " + " ".join(parts)) if parts else ""
        if label == "text" and text:
            return [f"{ind}<{comp}{p}>{{TRANSLATIONS.{self.key_for(text)}}}</{comp}>"]
        if text and "label" not in props and "title" not in props:
            p += f" /* text: {text[:40]} */"
        return [f"{ind}<{comp}{p} />"]


CHILD_TEXT = {"Button", "Badge", "Chip", "Link", "Text", "Hint", "Title"}


def _texts(n):
    for c in n.children:
        if isinstance(c, str):
            yield c
        elif c.tag not in ("svg", "style", "script"):
            yield from _texts(c)


def infer_props(classes: str, variants: dict) -> dict:
    """Read variant props off CSS-module modifier classes: btn--outlined-x1 -> variant 'outlined', Button__medium-x -> size."""
    parts = set()
    for c in classes.split():
        c = re.sub(r"-[A-Za-z0-9_]{6}$", "", c)            # drop the module hash
        for piece in re.split(r"__|--", c)[1:]:
            parts.add(piece.lower())
    out = {}
    for key, values in variants.items():
        if not isinstance(values, list):
            continue
        for v in values:
            sv = str(v).strip("'\" ")
            if sv and sv.lower() in parts:
                out[key] = sv
                break
    return out


def uikit_variants(system: Path) -> dict[str, dict]:
    m = {}
    for f in sorted((system / "components").glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        if isinstance(d.get("variants"), dict):
            props = d.get("props") if isinstance(d.get("props"), dict) else {}
            # only variant keys that are real props; iconSize and friends are too ambiguous to read off classes
            m[d["name"]] = {k: v for k, v in d["variants"].items() if k in props and not k.lower().startswith("icon")}
    return m


def uikit_map(system: Path) -> dict[str, str]:
    m = {}
    for f in sorted((system / "components").glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        imp = d.get("import") or (f"import {{ {d['name'].split('.')[-1]} }} from '@/{d['code_path'].removeprefix('src/').rsplit('/index', 1)[0].rsplit('.', 1)[0]}';" if d.get("code_path") else None)
        if imp:
            m[d["name"]] = imp
    return m


def scss_from(css_files: list[Path]) -> str:
    raw = "\n".join(f.read_text(encoding="utf-8") for f in css_files)
    raw = re.sub(r"/\*.*?\*/", "", raw, flags=re.S)
    raw = re.sub(r"@font-face\s*\{[^}]*\}", "", raw)
    raw = re.sub(r"@page\s*\{[^}]*\}", "", raw)
    raw = re.sub(r"font-family\s*:[^;}]+;?", "", raw)
    raw = re.sub(r"(?m)^\s*html\s*\{[^}]*\}", "", raw)
    raw = re.sub(r"(?<![\w.#-])(:root|body)(?=[\s,{:.])", "&", raw)  # elements only, not .body classes
    raw = re.sub(r"\n\s*\n+", "\n", raw).strip()
    body = "\n".join("    " + l if l.strip() else l for l in raw.splitlines())
    return ("// Generated by flynn Studio from a editorial page. Scoped under .root because web-app\n"
            "// sets RTL and the Vazirmatn font globally; the theme tokens stay local custom properties.\n"
            "@import '@/styles/mixins';\n\n.root {\n" + body + "\n}\n")


SC_SYSTEM = Path(__file__).resolve().parents[2] / "systems" / "admin-panel"


def convert(page: Path, out: Path, name: str | None = None, system: Path | None = None) -> Path:
    page = Path(page)
    comp = name or pascal(page.parent.parent.name.split("-", 3)[-1] if page.parent.name == "page" else page.stem)
    comp = comp if comp[:1].isalpha() else "Page" + comp
    folder = out / (comp[:1].lower() + comp[1:])
    if folder.exists():
        shutil.rmtree(folder)
    (folder / "assets").mkdir(parents=True)
    html = page.read_text(encoding="utf-8")
    p = P()
    p.feed(html)
    main = next((c for c in _walk(p.root) if isinstance(c, Node) and c.tag == "body"), p.root)
    page_title = None
    if 'admin-panel.css' in html:
        # kit page: the app shell (top bar, footer, icon sprite) and the title row come from the repo's layout
        main = next((c for c in _walk(p.root) if isinstance(c, Node) and c.tag == "main"), main)
        for c in list(main.children):
            if isinstance(c, Node) and c.attrs.get("data-uikit") == "PageTitle":
                try:
                    page_title = json.loads(c.attrs.get("data-props") or "{}")
                except ValueError:
                    page_title = {}
                main.children.remove(c)
    links = re.findall(r'<link[^>]+href="([^"]+\.css)"', html)
    sc_mode = any(h.endswith("admin-panel.css") for h in links)
    system = system or (SC_SYSTEM if sc_mode else None)
    e = Emitter(uikit=uikit_map(system) if system and (system / "components").exists() else {}, sc_mode=sc_mode)
    if system and (system / "components").exists():
        e.variants = uikit_variants(system)
    jsx = [l for c in main.children for l in e.node(c, 3) if l.strip()]
    css_files = [page.parent / h for h in links if (page.parent / h).exists() and not h.endswith("admin-panel.css")]
    scss = scss_from(css_files) if not sc_mode else (
        "// Generated by flynn Studio from a Admin Panel kit page. Layout uses the repo's global utility\n"
        "// classes and UIKit components, so no local styles are needed yet.\n@import '@/styles/mixins';\n\n.root {\n}\n")
    module = f"{folder.name}.module.scss"
    (folder / module).write_text(scss, encoding="utf-8")
    for ident, src in e.images.items():
        s = page.parent / src
        if s.exists():
            shutil.copy(s, folder / "assets" / Path(src).name)
    img_imports = "".join(f"import {ident} from './assets/{Path(src).name}';\n" for ident, src in sorted(e.images.items()))
    uikit_imports = "".join(f"{imp}\n" for _, imp in sorted(e.used.items()))
    tsx = (f"import React from 'react';\n\n{uikit_imports}{img_imports}"
           f"import {{ TRANSLATIONS }} from './constants';\nimport styles from './{module}';\n\n"
           f"export interface {comp}Props {{\n    className?: string;\n}}\n\n"
           f"export const {comp}: React.FC<{comp}Props> = ({{ className }}) => {{\n"
           f"    return (\n        <div className={{[styles.root, className].filter(Boolean).join(' ')}}>\n"
           + "\n".join(jsx) + "\n        </div>\n    );\n};\n")
    (folder / "index.tsx").write_text(tsx, encoding="utf-8")
    if page_title:
        e.strings["pageTitle"] = page_title.get("title", "")
        e.strings["pageDescription"] = page_title.get("description", "")
    consts = "export const TRANSLATIONS = {\n" + "".join(
        f"    {k}: {_q(v)},\n" for k, v in e.strings.items()) + "} as const;\n"
    (folder / "constants.ts").write_text(consts, encoding="utf-8")
    (folder / "README.md").write_text(
        f"# {comp}\n\nGenerated by flynn Studio from `{page}`.\n\n"
        "- Written to web-app conventions (quorra): named arrow export, `interface <Name>Props`, CSS module,\n"
        "  Persian strings in `constants.ts` → `TRANSLATIONS`, images imported from `./assets`.\n"
        + (f"- UIKit components used: {', '.join(sorted(e.used)) or 'none'}. Elements marked `data-uikit` in the page became\n"
           "  those components; check each one's props against its spec in flynn's `systems/admin-panel/components/`.\n"
           + (f"- Not in the library (left as a self-closing tag, fix by hand): {', '.join(sorted(e.unknown))}.\n" if e.unknown else "")
           if sc_mode or e.used else
           "- Raw HTML elements, not UIKit components: swap in `@/components/UIKit/*` (Button, Tab, TableView, Tooltip)\n"
           "  where the page uses those patterns.\n")
        + (f"- The page title comes from the layout: call `useLayout({{ pageTitle: {{ title: TRANSLATIONS.pageTitle, description: TRANSLATIONS.pageDescription }} }})`"
           f" (title «{page_title.get('title', '')}», description «{page_title.get('description', '')}»).\n" if page_title else "")
        + "- Run the repo's Prettier and oxlint after moving it in.\n"
        f"- {len(e.strings)} strings, {len(e.images)} images, {len(e.classes)} CSS classes.\n", encoding="utf-8")
    return folder


def _walk(n):
    yield n
    if isinstance(n, Node):
        for c in n.children:
            yield from _walk(c)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("page"); ap.add_argument("out"); ap.add_argument("--name")
    ap.add_argument("--system", help="design system folder for UIKit mapping (default: admin-panel for kit pages)")
    a = ap.parse_args(argv)
    print(convert(Path(a.page), Path(a.out), a.name, Path(a.system) if a.system else None))
    return 0


if __name__ == "__main__":
    sys.exit(main())
