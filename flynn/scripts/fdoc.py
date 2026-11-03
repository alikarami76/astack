#!/usr/bin/env python3
"""flynn document validator (docs/flynn-doc-format.md).
  fdoc.py validate <doc.json> [--json]
Exit 0 when there are no errors (warnings are allowed), 1 otherwise."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "kb")]
import store  # noqa: E402

KINDS = ("screen", "slide", "graphic", "chart")
DIRS = ("rtl", "ltr")
LANGS = ("fa", "en")
PRIMITIVES = {"Frame": (), "Text": ("text",), "Image": ("src", "alt"), "Icon": ("name",)}
LEAVES = {"Text", "Image", "Icon"}
STYLE_KEYS = ("fill", "color", "stroke", "radius", "elevation", "typography")
LAYOUT_TOKEN_KEYS = ("gap", "padding")
LAYOUT_ENUMS = {"direction": ("row", "column", "stack"), "align": ("start", "center", "end", "stretch"),
                "justify": ("start", "center", "end", "space-between"), "width": ("fill", "hug"), "height": ("fill", "hug")}
TIER_WARN = {"drift", "inferred", "proposed", "figma-only", "code-only"}


class _V:
    def __init__(self, conn, system: str):
        self.issues: list[dict] = []
        self.tokens = store.token_names(conn, system)
        self.comps = store.components(conn, system)
        self.system = system
        self.seen: set[str] = set()

    def add(self, sev: str, code: str, path: str, msg: str) -> None:
        self.issues.append({"severity": sev, "code": code, "path": path, "message": msg})

    def token(self, value, key: str, path: str, overrides: set) -> None:
        for v in (value if isinstance(value, list) else [value]):
            if isinstance(v, str) and v in self.tokens:
                continue
            if key in overrides:
                self.add("warning", "off-system-override", path, f"{key}={v!r} is not a {self.system} token (override)")
            else:
                self.add("error", "off-system-value", path,
                         f"{key}={v!r} is not a {self.system} token; use a token, or list {key!r} in overrides")

    def node(self, n, path: str) -> None:
        if not isinstance(n, dict):
            self.add("error", "bad-node", path, "node must be an object")
            return
        nid = n.get("id")
        if not isinstance(nid, str) or not nid:
            self.add("error", "bad-id", path, "node needs a non-empty string id")
        elif nid in self.seen:
            self.add("error", "duplicate-id", path, f"id {nid!r} is used twice")
        else:
            self.seen.add(nid)
        comp = n.get("component")
        if not isinstance(comp, str) or not comp:
            self.add("error", "no-component", path, "node needs a component")
            return
        if comp in PRIMITIVES:
            for f in PRIMITIVES[comp]:
                if not n.get(f):
                    self.add("error", "missing-field", path, f"{comp} needs {f!r}")
            if n.get("variant") or n.get("props"):
                self.add("error", "primitive-has-props", path, f"{comp} is a primitive; it takes no variant or props")
        else:
            self._component(n, comp, path)
        overrides = set(n.get("overrides") or [])
        style = n.get("style") or {}
        for k, v in style.items():
            if k not in STYLE_KEYS:
                self.add("error", "unknown-style-key", path, f"style key {k!r} is not one of {STYLE_KEYS}")
            else:
                self.token(v, k, path, overrides)
        layout = n.get("layout") or {}
        for k, v in layout.items():
            if k in LAYOUT_TOKEN_KEYS:
                if isinstance(v, list) and not 1 <= len(v) <= 4:
                    self.add("error", "bad-layout", path, f"{k} takes one to four tokens")
                self.token(v, k, path, overrides)
            elif k in LAYOUT_ENUMS:
                if v not in LAYOUT_ENUMS[k]:
                    self.add("error", "bad-layout", path, f"layout.{k}={v!r} is not one of {LAYOUT_ENUMS[k]}")
            else:
                self.add("error", "bad-layout", path, f"unknown layout key {k!r}")
        children = n.get("children") or []
        if children and comp in LEAVES:
            self.add("error", "leaf-children", path, f"{comp} cannot have children")
        for i, ch in enumerate(children):
            cid = ch.get("id") if isinstance(ch, dict) and isinstance(ch.get("id"), str) and ch.get("id") else f"[{i}]"
            self.node(ch, f"{path}/{cid}")

    def _component(self, n: dict, comp: str, path: str) -> None:
        spec = self.comps.get(comp)
        if spec is None:
            self.add("error", "unknown-component", path,
                     f"{comp!r} is not in the {self.system} library; draft a component spec and get the user's approval first")
            return
        if spec.get("tier") in TIER_WARN:
            self.add("warning", "tier", path, f"{comp} is {spec['tier']}; say so when presenting")
        axes = spec.get("variants") or {}
        for axis, val in (n.get("variant") or {}).items():
            if axis not in axes:
                self.add("error", "unknown-variant-axis", path, f"{comp} has no variant axis {axis!r} (has {sorted(axes)})")
            elif val not in axes[axis]:
                self.add("error", "unknown-variant-value", path, f"{comp}.{axis}={val!r} is not one of {axes[axis]}")
        pspec = spec.get("props") or {}
        props = n.get("props") or {}
        for name, val in props.items():
            if name not in pspec:
                self.add("error", "unknown-prop", path, f"{comp} has no prop {name!r} (has {sorted(pspec)})")
            elif pspec[name].get("values") and val not in pspec[name]["values"]:
                self.add("error", "bad-prop-value", path, f"{comp}.{name}={val!r} is not one of {pspec[name]['values']}")
        for name, ps in pspec.items():
            if ps.get("required") and name not in props:
                self.add("error", "missing-prop", path, f"{comp} requires prop {name!r}")


def validate(doc, conn) -> list[dict]:
    if not isinstance(doc, dict):
        return [{"severity": "error", "code": "bad-field", "path": "", "message": "document must be a JSON object"}]
    system = doc.get("system") if isinstance(doc.get("system"), str) else ""
    v = _V(conn, system)
    if doc.get("flynn_doc") != 1:
        v.add("error", "version", "", f"flynn_doc must be 1, got {doc.get('flynn_doc')!r}")
    if not system:
        v.add("error", "bad-field", "system", "system is required")
    for f, allowed in (("kind", KINDS), ("dir", DIRS), ("lang", LANGS)):
        if doc.get(f) not in allowed:
            v.add("error", "bad-field", f, f"{f} must be one of {allowed}, got {doc.get(f)!r}")
    vp = doc.get("viewport")
    if not (isinstance(vp, dict) and all(isinstance(vp.get(k), int) and vp[k] > 0 for k in ("w", "h"))):
        v.add("error", "bad-field", "viewport", "viewport needs positive integer w and h")
    if system and not v.tokens and not v.comps:
        v.add("warning", "empty-system", "system", f"system {system!r} has no tokens or components in the store")
    root = doc.get("root")
    if not isinstance(root, dict):
        v.add("error", "no-root", "root", "document needs a root node")
    else:
        v.node(root, root.get("id") if isinstance(root.get("id"), str) and root.get("id") else "root")
    return v.issues


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="fdoc.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    va = sub.add_parser("validate")
    va.add_argument("file")
    va.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        doc = json.loads(Path(args.file).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"fdoc.py: cannot read {args.file}: {e}", file=sys.stderr)
        return 1
    issues = validate(doc, store.connect())
    errors = sum(i["severity"] == "error" for i in issues)
    if args.json:
        print(json.dumps(issues, ensure_ascii=False))
    else:
        for i in issues:
            print(f"{i['severity']} {i['code']} {i['path']}: {i['message']}")
        print("OK" if not errors else f"{errors} error(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
