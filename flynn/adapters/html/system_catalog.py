#!/usr/bin/env python3
"""Build a review catalogue page for a design system folder.

  system_catalog.py systems/admin-panel --run runs/<run> [--crops <dir>]

Writes <run>/page/index.html (editorial theme): tokens, then every component grouped by
category with its tier, import, props, variants, states, usage rules (inferred ones flagged
for review), TSX snippet and live crops, then the patterns. Open it in Studio or the review
UI so the user can pin comments on any component.
"""
from __future__ import annotations

import argparse
import html
import json
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
THEME = HERE / "themes" / "editorial"
ORDER = ["navigation", "action", "input", "overlay", "feedback", "data-display", "layout", "typography", "media"]
FA_CAT = {"navigation": "ناوبری", "action": "اکشن", "input": "ورودی", "overlay": "لایه و پنجره", "feedback": "بازخورد و وضعیت",
          "data-display": "نمایش داده", "layout": "چیدمان", "typography": "متن", "media": "تصویر و آیکون"}

CSS = """
main > section.col { max-width: var(--wide); }
.cmp { direction: ltr; text-align: left; }
.cmp h3, .cmp .chips { justify-content: flex-start; }
.cmp p, .cmp .rules div, .cmp li { unicode-bidi: plaintext; }
.cmp .review { display: inline; margin-left: 6px; }
.cat-nav { display:flex; flex-wrap:wrap; gap:8px; margin: 24px 0 8px; }
.cat-nav a { font-size:14px; padding:6px 12px; border-radius:999px; background:var(--soft); color:var(--ink); text-decoration:none; }
.cmp { border-top:1px solid var(--line); padding: 36px 0; }
.cmp h3 { display:flex; align-items:center; gap:10px; direction:ltr; justify-content:flex-end; font-family: inherit; }
.tier { font-size:12px; font-weight:700; padding:3px 10px; border-radius:999px; background:var(--soft); color:var(--muted); direction:ltr; }
.tier.code-only { background:#eef4ff; color:#1f4fb8; } .tier.verified { background:#e8f6ee; color:#0b7a43; } .tier.drift { background:var(--accent-soft); color:var(--accent-text); }
.meta { font-size:14px; color:var(--muted); direction:ltr; text-align:left; font-family: ui-monospace, Menlo, monospace; }
pre { direction:ltr; text-align:left; background:#0f1020; color:#e8e8f0; padding:14px 16px; border-radius:12px; font-size:12.5px; line-height:1.6; overflow:auto; white-space:pre-wrap; }
table.props { width:100%; border-collapse:collapse; font-size:13.5px; direction:ltr; text-align:left; margin: 12px 0; }
table.props th, table.props td { border-bottom:1px solid var(--line); padding:6px 8px; vertical-align:top; text-align:left; }
table.props th { color:var(--muted); font-weight:700; }
table.props code { font-size:12.5px; }
.rules { display:grid; grid-template-columns: 1fr 1fr; gap:12px; margin: 14px 0; }
.rules div { background:var(--soft); border-radius:12px; padding:12px 14px; font-size:15px; }
.rules b { display:block; font-size:13px; color:var(--muted); margin-bottom:4px; }
.review { font-size:12px; color:var(--accent-text); font-weight:700; }
.crops { display:flex; flex-wrap:wrap; gap:12px; margin-top:14px; }
.crops img { max-width:100%; max-height:320px; border:1px solid var(--line); border-radius:10px; background:#fff; }
.chips { display:flex; flex-wrap:wrap; gap:6px; direction:ltr; justify-content:flex-end; }
.chips span { font-size:12.5px; background:var(--soft); border-radius:6px; padding:2px 8px; font-family: ui-monospace, Menlo, monospace; }
.swatches { display:grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap:10px; }
.sw { border:1px solid var(--line); border-radius:10px; overflow:hidden; font-size:12px; direction:ltr; }
.sw i { display:block; height:44px; } .sw div { padding:6px 8px; } .sw small { color:var(--muted); display:block; }
.oq { font-size:14px; color:var(--muted); }
"""


def esc(s) -> str:
    return html.escape("" if s is None else str(s))


def load(folder: Path):
    comps = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((folder / "components").glob("*.json"))]
    pats = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((folder / "patterns").glob("*.json"))]
    tp = folder / "tokens.json"
    toks = json.loads(tp.read_text(encoding="utf-8")) if tp.exists() else []
    return comps, pats, toks


def props_table(props) -> str:
    if not isinstance(props, dict) or not props:
        return ""
    rows = []
    for k, v in props.items():
        v = v if isinstance(v, dict) else {"type": v}
        rows.append(f"<tr><td><code>{esc(k)}</code>{' *' if v.get('required') else ''}</td><td><code>{esc(v.get('type'))}</code></td>"
                    f"<td><code>{esc(v.get('default', ''))}</code></td><td>{esc(v.get('notes', ''))}</td></tr>")
    return f"<table class='props'><tr><th>prop</th><th>type</th><th>default</th><th>notes</th></tr>{''.join(rows)}</table>"


def component_html(c: dict, crops: dict[str, list[str]]) -> str:
    u = c.get("usage") or {}
    review = "<span class='review'>inferred by flynn, needs review</span>" if isinstance(u, dict) and u.get("source") == "inferred" else ""
    variants = c.get("variants") or {}
    vhtml = "".join(f"<div class='meta'>{esc(k)}:</div><div class='chips'>{''.join(f'<span>{esc(x)}</span>' for x in (v if isinstance(v, list) else [v]))}</div>"
                    for k, v in variants.items()) if isinstance(variants, dict) else ""
    states = c.get("states") or []
    shtml = f"<div class='chips'>{''.join(f'<span>{esc(s)}</span>' for s in states)}</div>" if states else ""
    imgs = "".join(f"<img src='img/{esc(f)}' alt='{esc(c['name'])}' loading='lazy'>" for f in crops.get(c["name"], []))
    oq = c.get("code_issues") or c.get("open_questions") or []
    dec = c.get("decisions") or []
    return f"""<section class="cmp" dir="ltr" id="c-{esc(c['name'])}" data-shot="c-{esc(c['name']).replace('.', '-')}">
<h3>{''.join(f'<span class="tier drift">{esc(t)}</span>' for t in (c.get('tags') or []) if t in ('deprecated', 'internal'))}<span class="tier {esc(c.get('tier'))}">{esc(c.get('tier'))}</span>{esc(c['name'])}</h3>
<p>{esc(c.get('description'))}</p>
<div class="meta">{esc(c.get('import') or c.get('code_path'))}{f" · used in {c['usage_count']} files" if c.get('usage_count') is not None else ''}</div>
{f"<div class='crops'>{imgs}</div>" if imgs else ''}
{f"<div class='rules'><div><b>Use when {review}</b>{esc(u.get('use_when'))}</div><div><b>Never</b>{esc(u.get('never'))}</div></div>" if isinstance(u, dict) and (u.get('use_when') or u.get('never')) else ''}
{vhtml}{f"<div class='meta'>states:</div>{shtml}" if shtml else ''}
{props_table(c.get('props'))}
{f"<p class='oq'>RTL: {esc(c.get('rtl_notes'))}</p>" if c.get('rtl_notes') else ''}
{f"<pre>{esc(c.get('tsx_snippet'))}</pre>" if c.get('tsx_snippet') else ''}
{f"<p class='oq'><b>Decisions:</b> {' '.join(esc(q) for q in dec)}</p>" if dec else ''}
{f"<p class='oq'>Code issues for developers: {' · '.join(esc(q) for q in oq)}</p>" if oq else ''}
</section>"""


def tokens_html(toks: list[dict]) -> str:
    colors = [t for t in toks if t.get("kind") == "color" and isinstance(t.get("value"), str) and t["value"].startswith(("#", "rgb"))]
    sw = "".join(f"<div class='sw'><i style='background:{esc(t['value'])}'></i><div>{esc(t['name'])}<small>{esc(t['value'])}{' · ' + esc(t.get('css_var') or t.get('scss_var') or '') if (t.get('css_var') or t.get('scss_var')) else ''}</small></div></div>"
                 for t in colors[:160])
    kinds: dict[str, int] = {}
    for t in toks:
        kinds[t.get("kind") or "other"] = kinds.get(t.get("kind") or "other", 0) + 1
    other = "".join(f"<span>{esc(k)}: {v}</span>" for k, v in sorted(kinds.items()))
    return f"""<section class="col" data-shot="tokens"><h2>توکن‌ها</h2><div class="chips">{other}</div>
<h4>رنگ‌ها</h4><div class="swatches">{sw}</div></section>"""


def pattern_html(p: dict) -> str:
    comps = "".join(f"<span>{esc(x)}</span>" for x in (p.get("components") or []))
    anat = "".join(f"<li>{esc(a)}</li>" for a in (p.get("anatomy") or []))
    rules = p.get("rules")
    rules = "".join(f"<li>{esc(r)}</li>" for r in rules) if isinstance(rules, list) else esc(rules)
    return f"""<section class="cmp" dir="ltr" id="p-{esc(p['name'])}" data-shot="p-{esc(p['name'])}"><h3><span class="tier {esc(p.get('tier'))}">{esc(p.get('tier'))}</span>{esc(p['name'])}</h3>
<p>{esc(p.get('description'))}</p><div class="chips">{comps}</div>{f'<ol>{anat}</ol>' if anat else ''}<div class="oq">{rules}</div></section>"""


def build(folder: Path, run: Path, crops_dir: Path | None) -> Path:
    comps, pats, toks = load(folder)
    page = run / "page"
    (page / "img").mkdir(parents=True, exist_ok=True)
    for f in THEME.glob("*.css"):
        shutil.copy(f, page / f.name)
    shutil.copytree(THEME / "fonts", page / "fonts", dirs_exist_ok=True)
    crops: dict[str, list[str]] = {}
    if crops_dir and (crops_dir / "crops.json").exists():
        crops = json.loads((crops_dir / "crops.json").read_text())
        for files in crops.values():
            for f in files:
                shutil.copy(crops_dir / f, page / "img" / f)
    by_cat: dict[str, list[dict]] = {}
    for c in comps:
        by_cat.setdefault(c.get("category") or "other", []).append(c)
    cats = sorted(by_cat, key=lambda k: ORDER.index(k) if k in ORDER else 99)
    nav = "".join(f"<a href='#cat-{esc(k)}'>{esc(FA_CAT.get(k, k))} ({len(by_cat[k])})</a>" for k in cats) + "<a href='#patterns'>الگوها</a>"
    body = "".join(f"<section class='col' id='cat-{esc(k)}'><h2>{esc(FA_CAT.get(k, k))}</h2>{''.join(component_html(c, crops) for c in sorted(by_cat[k], key=lambda c: c['name']))}</section>" for k in cats)
    tiers: dict[str, int] = {}
    for c in comps:
        tiers[c.get("tier")] = tiers.get(c.get("tier"), 0) + 1
    inferred = sum(1 for c in comps if isinstance(c.get("usage"), dict) and c["usage"].get("source") == "inferred")
    name = folder.name
    doc = f"""<!doctype html><html lang="fa" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(name)} library</title><link rel="stylesheet" href="style.css"><style>{CSS}</style></head><body><main>
<section class="col hero" data-shot="hero"><div class="eyebrow">کتابخانهٔ طراحی flynn</div><h1>{esc(name)}</h1>
<p class="lead">{len(comps)} کامپوننت، {len(pats)} الگو و {len(toks)} توکن، استخراج‌شده از کد web-app و صفحه‌های زنده. همه فعلاً <b>code-only</b> هستند تا با کتابخانهٔ فیگما مقایسه شوند. قاعده‌های «کی استفاده شود» که برداشت flynn هستند ({inferred} مورد) علامت بازبینی دارند.</p>
<div class="chips">{''.join(f'<span>{esc(k)}: {v}</span>' for k, v in tiers.items())}</div><nav class="cat-nav">{nav}</nav></section>
{tokens_html(toks) if toks else ''}
{body}
<section class="col" id="patterns"><h2>الگوها</h2>{''.join(pattern_html(p) for p in pats)}</section>
</main></body></html>"""
    out = page / "index.html"
    out.write_text(doc, encoding="utf-8")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("system")
    ap.add_argument("--run", required=True)
    ap.add_argument("--crops")
    a = ap.parse_args(argv)
    print(build(Path(a.system).resolve(), Path(a.run).resolve(), Path(a.crops).resolve() if a.crops else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
