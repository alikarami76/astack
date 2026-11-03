"""Render stage: Chrome + Paged.js -> raw.pdf and geometry.json."""

from __future__ import annotations

import json
from pathlib import Path

from .errors import EnvError, RenderError

GEOMETRY_JS = r"""
(opts) => {
  const out = { pages: 0, overflow: [], headings: [] };
  const pages = Array.from(document.querySelectorAll('.pagedjs_page'));
  out.pages = pages.length;
  const allowed = (el) => opts.allow.some((sel) => { try { return !!el.closest(sel); } catch (e) { return false; } });
  const BLOCKS = 'p,h1,h2,h3,h4,h5,h6,li,table,pre,figure,img,svg,blockquote,dl,div,section,nav,main';
  const clean = (s) => (s || '').trim().replace(/\s+/g, ' ');
  pages.forEach((page, i) => {
    const content = page.querySelector('.pagedjs_page_content');
    if (!content) return;
    const cr = content.getBoundingClientRect();
    const pr = page.getBoundingClientRect();
    const reported = [];
    Array.from(content.querySelectorAll(BLOCKS)).reverse().forEach((el) => {
      if (el.parentElement === content) return; // Paged.js column wrapper, not document content
      if (allowed(el) || reported.some((r) => el.contains(r))) return;
      if (el.tagName.toLowerCase() !== 'svg' && el.closest('svg')) return;
      const r = el.getBoundingClientRect();
      if (r.width === 0 && r.height === 0) return;
      let over = Math.max(cr.left - r.left, r.right - cr.right, r.bottom - cr.bottom);
      if (el.clientWidth > 0 && getComputedStyle(el).overflowX === 'visible') {
        over = Math.max(over, el.scrollWidth - el.clientWidth);
      }
      if (over > 1) {
        reported.push(el);
        out.overflow.push({ page: i + 1, tag: el.tagName.toLowerCase(), text: clean(el.textContent).slice(0, 60), px: Math.round(over) });
      }
    });
    content.querySelectorAll('h1,h2,h3,h4,h5,h6').forEach((h) => {
      const r = h.getBoundingClientRect();
      out.headings.push({ page: i + 1, level: Number(h.tagName[1]), text: clean(h.textContent), id: h.id || '', y_pt: Math.round((r.top - pr.top) * 7.5) / 10 });
    });
  });
  out.overflow.sort((a, b) => a.page - b.page);
  return out;
}
"""


def _launch(p):
    errors = []
    for kwargs in ({"channel": "chrome"}, {}):
        try:
            return p.chromium.launch(**kwargs)
        except Exception as e:
            errors.append(str(e).strip().splitlines()[0])
    raise EnvError("no browser found. Install Google Chrome, or run `uv run playwright install chromium`. Details: " + " | ".join(errors))


def chrome_version() -> str:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = _launch(p)
        version = browser.version
        browser.close()
        return version


def render(html_path: Path, pdf_path: Path, geometry_path: Path, *, timeout_s: float = 120, network: bool = False, allow_overflow=()) -> dict:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import TimeoutError as PlaywrightTimeout
    from playwright.sync_api import sync_playwright

    html_path, pdf_path, geometry_path = Path(html_path), Path(pdf_path), Path(geometry_path)
    blocked: list[str] = []
    errors: list[str] = []
    failed: list[dict] = []

    def request_failed(request):
        # Paged.js re-requests an image when it lays the content out, so each URL is kept once.
        if request.url not in blocked and all(f["url"] != request.url for f in failed):
            failed.append({"url": request.url, "error": request.failure or "unknown error"})

    def route(r):
        url = r.request.url
        if network or url.startswith(("file:", "data:", "blob:", "about:")):
            r.continue_()
        else:
            blocked.append(url)
            r.abort()

    with sync_playwright() as p:
        browser = _launch(p)
        try:
            page = browser.new_page()
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("requestfailed", request_failed)
            page.route("**/*", route)
            page.goto(html_path.resolve().as_uri(), wait_until="load")
            try:
                page.wait_for_function("window.__pagedDone === true || !!window.__pagedError", timeout=timeout_s * 1000)
            except PlaywrightTimeout as e:
                raise RenderError(f"pagination did not finish within {timeout_s}s. For very long documents raise 'timeout' in doc.yaml. Page errors: {errors[:3]}") from e
            failure = page.evaluate("window.__pagedError || null")
            if failure:
                raise RenderError(f"Paged.js failed: {failure.splitlines()[0]}")
            geometry = page.evaluate(GEOMETRY_JS, {"allow": list(allow_overflow)})
            geometry.update(chrome=browser.version, blocked=sorted(set(blocked)), failed=failed[:20], page_errors=errors[:20])
            pdf_path.parent.mkdir(parents=True, exist_ok=True)
            page.pdf(path=str(pdf_path), prefer_css_page_size=True, print_background=True, tagged=True, outline=True)
        except PlaywrightError as e:
            raise RenderError(f"Chrome failed while rendering {html_path.name}: {str(e).strip().splitlines()[0]}") from e
        finally:
            browser.close()
    geometry_path.write_text(json.dumps(geometry, ensure_ascii=False, indent=1), encoding="utf-8")
    return geometry
