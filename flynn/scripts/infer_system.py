#!/usr/bin/env python3
"""Infer a design system's tokens and section outline from Canva design data (spec section 5.3).
  infer_system.py <system> <canva-json>... --out <dir>
Each input is a Canva read-design result (open_transaction, design_content) or its design_content. Writes
<dir>/system.json (the full inference) and <dir>/kb-rows.json (token rows for `kb.py add-many`). Everything is a
guess for the user to review: rows are tier "inferred". Stdlib only."""
from __future__ import annotations

import argparse
import colorsys
import json
from collections import Counter, defaultdict
from pathlib import Path

BOLD = {"semibold", "bold", "ultrabold", "heavy", "black", "extrabold"}
SPACE_NAMES = ("space.xs", "space.sm", "space.md", "space.lg", "space.xl", "space.2xl")


def load_obj(obj: dict) -> dict:
    return obj["design_content"] if "design_content" in obj else obj


def load(path) -> dict:
    return load_obj(json.loads(Path(path).read_text(encoding="utf-8")))


def walk(obj):
    """Every nested element dict: text, rect, shape, and cells inside tables."""
    if isinstance(obj, dict):
        if "type" in obj or "textRegions" in obj:
            yield obj
        for v in obj.values():
            if isinstance(v, (dict, list)):
                yield from walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk(v)


def _hex(c) -> str | None:
    if isinstance(c, dict):
        c = (c.get("color") or {}).get("color") if "color" in c and isinstance(c.get("color"), dict) else c.get("color")
    return c.lower() if isinstance(c, str) and c.startswith("#") and len(c) == 7 else None


def _hls(h: str) -> tuple[float, float, float]:
    return colorsys.rgb_to_hls(*(int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)))


def _fills(e: dict) -> list[str]:
    out = []
    if e.get("type") == "rect":
        out.append(_hex(e.get("fill")))
    for p in e.get("paths") or []:
        out.append(_hex(p.get("fill")))
    return [c for c in out if c]


def _regions(e: dict):
    for r in e.get("textRegions") or []:
        f = r.get("formatting") or {}
        yield r.get("characters", ""), f


def _style(f: dict) -> tuple[float, str]:
    return round(float(f.get("fontSize") or 0) * 2) / 2, str(f.get("fontWeight") or "normal")


def _area(e: dict) -> float:
    return float(e.get("width") or 0) * float(e.get("height") or 0)


def _top_by(counter: Counter, area: dict, exclude=()) -> list[str]:
    return [k for k, _ in sorted(((k, v) for k, v in counter.items() if k not in exclude),
                                 key=lambda kv: (-kv[1], -area.get(kv[0], 0), str(kv[0])))]


def _collect(designs: list[tuple[str, dict]]) -> dict:
    """Usage counters over a set of designs that share one layout."""
    k = {"text": Counter(), "fill": Counter(), "thin": Counter(), "subtle": Counter(), "bg": Counter(),
         "text_area": defaultdict(float), "fill_area": defaultdict(float), "styles": Counter(),
         "style_area": defaultdict(float), "style_lh": defaultdict(Counter), "style_color": defaultdict(Counter),
         "per_design": {}, "texts": {}}
    for did, d in designs:
        per, texts = Counter(), []
        for page in d["pages"]:
            w, h = page["dimensions"]["width"], page["dimensions"]["height"]
            bg = _hex(page.get("background") or {})
            if bg:
                k["bg"][bg] += 1
            for e in walk(page.get("elements") or []):
                for c in _fills(e):
                    if _area(e) >= 0.9 * w * h:
                        continue  # a full-page background rect
                    k["fill"][c] += 1
                    k["fill_area"][c] += _area(e)
                    if min(float(e.get("width") or 99), float(e.get("height") or 99)) <= 2:
                        k["thin"][c] += 1
                    elif float(e.get("width") or 0) >= 0.5 * w and float(e.get("height") or 0) >= 40:
                        k["subtle"][c] += 1
                for chars, f in _regions(e):
                    c, st = _hex(f.get("color")), _style(f)
                    if c:
                        k["text"][c] += 1
                        k["text_area"][c] += _area(e)
                        k["style_color"][st][c] += 1
                    k["styles"][st] += 1
                    per[st] += 1
                    k["style_area"][st] += _area(e)
                    k["style_lh"][st][round(float(f.get("lineHeight") or 0), 2)] += 1
                    if "top" in e:
                        texts.append((float(e["top"]), st, chars.strip()))
        k["per_design"][did] = per
        k["texts"][did] = sorted(texts)
    return k


def _is_label(chars: str) -> bool:
    """A section title has words; '01' or '۰۲' is a list number that happens to share the heading style."""
    return any(ch.isalpha() for ch in chars)


def _heading(k: dict):
    cands = {st for per in k["per_design"].values() for st, n in per.items() if n >= 3 and st[1] in BOLD}
    return max(cands, key=lambda s: s[0]) if cands else None


def infer(system: str, designs: list[tuple[str, dict]]) -> dict:
    """Roles, type and spacing come from the dominant layout (the most common page width, ties going to the first
    design); designs with another width are summarised as variants and never shape the roles."""
    widths = {did: d["pages"][0]["dimensions"]["width"] for did, d in designs}
    dominant = Counter(widths.values()).most_common(1)[0][0]
    main = [(did, d) for did, d in designs if widths[did] == dominant]
    k = _collect(main)
    text_c, fill_c, styles = k["text"], k["fill"], k["styles"]
    usage = text_c + fill_c
    saturated = Counter({c: n for c, n in usage.items() if _hls(c)[2] > 0.5 and 0.25 <= _hls(c)[1] <= 0.75})
    accent = saturated.most_common(1)[0][0] if saturated else None
    text_rank = _top_by(text_c, k["text_area"], exclude={accent})
    background = k["bg"].most_common(1)[0][0] if k["bg"] else "#ffffff"
    border = next(iter(_top_by(k["thin"], k["fill_area"], exclude={accent})), None)
    subtle = next((c for c in _top_by(k["subtle"], k["fill_area"], exclude={accent, background}) if _hls(c)[1] > 0.9), None)

    def best(st):
        return {"size": st[0], "weight": st[1], "line_height": k["style_lh"][st].most_common(1)[0][0],
                "color": k["style_color"][st].most_common(1)[0][0] if k["style_color"][st] else None}

    heading = _heading(k)
    bigger = [st for st in styles if st[1] in BOLD and heading and st[0] > heading[0]]
    display = max(bigger, key=lambda s: s[0]) if bigger else None
    body = next(iter(_top_by(Counter({s: n for s, n in styles.items() if s[1] not in BOLD}), k["style_area"])), None)
    caption = min((s for s, n in styles.items() if n >= 3), key=lambda s: s[0], default=None)
    sections = {did: [ch for _, st, ch in ts if heading and st == heading and _is_label(ch)] for did, ts in k["texts"].items()}

    variants = []
    for did, d in designs:
        if widths[did] == dominant:
            continue
        v = _collect([(did, d)])
        vh = _heading(v)
        sections[did] = [ch for _, st, ch in v["texts"][did] if vh and st == vh and _is_label(ch)]
        variants.append({"design": did, "width": widths[did],
                         "top_text_colors": [c for c, _ in v["text"].most_common(5)],
                         "top_fill_colors": [c for c, _ in v["fill"].most_common(5)],
                         "heading": {"size": vh[0], "weight": vh[1]} if vh else None})

    lefts, rights, gaps = Counter(), Counter(), Counter()
    for did, d in main:
        for page in d["pages"]:
            w, h = page["dimensions"]["width"], page["dimensions"]["height"]
            els = [e for e in page.get("elements") or [] if "top" in e and _area(e) < 0.9 * w * h]
            for e in els:
                if e.get("type") == "text" and float(e.get("width") or 0) >= 0.5 * w:
                    lefts[round(float(e["left"]))] += 1
                    rights[round(w - float(e["left"]) - float(e["width"]))] += 1
            els.sort(key=lambda e: float(e["top"]))
            for a, b in zip(els, els[1:]):
                g = round(float(b["top"]) - float(a["top"]) - float(a["height"]))
                if 1 <= g <= 120:
                    gaps[g] += 1
    scale = sorted(g for g, _ in gaps.most_common(len(SPACE_NAMES)))

    tokens = []
    for c, n in usage.most_common():
        tokens.append({"name": f"color.raw.{c[1:]}", "kind": "color", "level": "raw", "value": c,
                       "notes": f"text {text_c[c]}x, fill {fill_c[c]}x"})
    for v in variants:
        for c in dict.fromkeys(v["top_text_colors"] + v["top_fill_colors"]):
            if c not in usage:
                tokens.append({"name": f"color.raw.{c[1:]}", "kind": "color", "level": "raw", "value": c,
                               "notes": f"variant only ({v['design']}, width {v['width']})"})
    for st, n in styles.most_common():
        if n >= 2:
            tokens.append({"name": f"type.raw.{st[0]:g}-{st[1]}", "kind": "type", "level": "raw",
                           "value": json.dumps(best(st), ensure_ascii=False), "notes": f"used {n}x"})
    roles = {"color.accent": accent, "color.text.primary": text_rank[0] if text_rank else None,
             "color.text.secondary": text_rank[1] if len(text_rank) > 1 else None,
             "color.text.tertiary": text_rank[2] if len(text_rank) > 2 else None,
             "color.border": border, "color.surface": background, "color.surface.subtle": subtle}
    for name, c in roles.items():
        if c:
            tokens.append({"name": name, "kind": "color", "level": "semantic", "value": c, "refers_to": f"color.raw.{c[1:]}",
                           "notes": "role guessed from usage counts"})
    for name, st in (("type.display", display), ("type.heading", heading), ("type.body", body), ("type.caption", caption)):
        if st:
            tokens.append({"name": name, "kind": "type", "level": "semantic", "value": json.dumps(best(st), ensure_ascii=False),
                           "refers_to": f"type.raw.{st[0]:g}-{st[1]}", "notes": "role guessed from sizes and counts"})
    margin_left = lefts.most_common(1)[0][0] if lefts else None
    if margin_left is not None:
        tokens.append({"name": "space.page-margin", "kind": "space", "level": "semantic", "value": f"{margin_left}px",
                       "notes": f"most common left edge of wide text at width {dominant}"})
    for name, g in zip(SPACE_NAMES, scale):
        tokens.append({"name": name, "kind": "space", "level": "raw", "value": f"{g}px", "notes": f"vertical gap seen {gaps[g]}x"})

    return {"system": system, "sources": [did for did, _ in designs], "dominant_width": dominant, "variants": variants,
            "colors": {c: {"text": text_c[c], "fill": fill_c[c]} for c in usage},
            "type_styles": [{**best(st), "count": n} for st, n in styles.most_common()],
            "spacing": {"margin_left": margin_left, "margin_right": rights.most_common(1)[0][0] if rights else None,
                        "gaps": scale, "gap_counts": dict(gaps.most_common(12))},
            "sections": sections, "tokens": tokens}


def kb_rows(result: dict, design_ids: list[str]) -> list[dict]:
    src = "canva:" + ",".join(design_ids)
    return [{"type": "token", "row": {"system": result["system"], "tier": "inferred", "source": src, **t}}
            for t in result["tokens"]]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="infer_system.py")
    ap.add_argument("system")
    ap.add_argument("files", nargs="+")
    ap.add_argument("--out", required=True)
    ap.add_argument("--ids", help="comma-separated Canva design ids, in file order (default: the design titles)")
    args = ap.parse_args(argv)
    designs = [(load(f)["title"], load(f)) for f in args.files]
    ids = args.ids.split(",") if args.ids else [did for did, _ in designs]
    if args.ids:
        designs = [(i, d) for i, (_, d) in zip(ids, designs)]
    result = infer(args.system, designs)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "system.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "kb-rows.json").write_text(json.dumps(kb_rows(result, ids), ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"dominant width {result['dominant_width']}; variants {result['variants']}")
    for t in result["tokens"]:
        if t["level"] == "semantic":
            print(f"  {t['name']} = {t['value']}")
    print(f"sections: {json.dumps(result['sections'], ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
