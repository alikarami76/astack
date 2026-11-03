"""render.py --pages: one PDF page and one PNG per <section class="page">, overflow and page-count checks,
and Studio's choice of render mode."""
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


render = _load(ROOT / "adapters" / "html" / "render.py", "html_render_pages")

DOC = """<!doctype html><html lang="en"><head><meta charset="utf-8"><style>
@page { size: A4; margin: 0 }
html, body { margin: 0; font-family: sans-serif; }
.page { width: 210mm; height: 297mm; overflow: hidden; position: relative; padding: 20mm; box-sizing: border-box;
        page-break-after: always; break-after: page; }
.page:last-child { page-break-after: auto; break-after: auto; }
.tall { height: 400mm; }
</style></head><body><main>%s</main></body></html>"""


def _doc(tmp_path: Path, pages: list[str]) -> Path:
    d = tmp_path / "run" / "page"
    d.mkdir(parents=True)
    (d / "index.html").write_text(DOC % "".join(f'<section class="page">{p}</section>' for p in pages), encoding="utf-8")
    return d / "index.html"


def _run(fn, *a, **k):
    pytest.importorskip("playwright.sync_api")
    try:
        return fn(*a, **k)
    except Exception as e:  # chromium not installed here
        pytest.skip(f"chromium unavailable: {e}")


def test_one_pdf_page_and_png_per_page(tmp_path):
    page = _doc(tmp_path, ["<h1>Cover</h1>", "<p>Findings</p>", "<p>Method</p>"])
    out = tmp_path / "run" / "shots"
    r = _run(render.render_pages, page, out, pdf="doc.pdf")
    assert r["pages"] == 3 and r["pdfPages"] == 3 and r["pageCount"] is True
    assert len(list((out / "pages").glob("*.png"))) == 3
    assert r["pageOverflow"] == [] and r["ok"]


def test_content_spilling_out_of_a_page_fails(tmp_path):
    page = _doc(tmp_path, ["<p>ok</p>", '<div class="tall">too tall for A4</div>'])
    r = _run(render.render_pages, page, tmp_path / "run" / "shots", pdf="doc.pdf")
    assert [o["slide"] for o in r["pageOverflow"]] == [2]
    assert r["ok"] is False


def test_studio_picks_the_render_mode():
    sys.path.insert(0, str(ROOT / "adapters" / "studio"))
    studio = _load(ROOT / "adapters" / "studio" / "studio.py", "studio_mode")
    assert studio._layout_mode('<section class="slide title">') == "slides"
    assert studio._layout_mode('<section class="page cover">') == "pages"
    assert studio._layout_mode('<div class="pagehead"><main class="col">') == ""
