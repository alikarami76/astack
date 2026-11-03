import importlib.util
import json
import sys
import threading
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "adapters" / "studio"))
spec = importlib.util.spec_from_file_location("studio", ROOT / "adapters" / "studio" / "studio.py")
studio = importlib.util.module_from_spec(spec)
spec.loader.exec_module(studio)
import html_to_tsx  # noqa: E402

PAGE = """<!doctype html><html lang="fa" dir="rtl"><head><meta charset="utf-8"><title>t</title><link rel="stylesheet" href="style.css"></head>
<body><main><section class="col hero" data-shot="hero"><h1>تخفیف ویژه</h1><p class="lead">متن <b>مهم</b> اینجا.</p>
<table><tr><th>شاخص</th></tr><tr><td>۱۲٪</td></tr></table><img src="img/a.png" alt="تصویر">
<svg viewBox="0 0 10 10"><text x="1" y="2" text-anchor="start" font-size="3">۱۰</text></svg></section></main></body></html>"""
CSS = ":root { --ink: #0f1020; }\nbody { font-family: 'Vazirmatn'; color: var(--ink); }\n.hero { padding: 4px; }\n.body { margin: 0; }\n"


@pytest.fixture
def runs(tmp_path, monkeypatch):
    root = tmp_path / "runs"
    run = root / "2026-09-30-demo"
    (run / "page" / "img").mkdir(parents=True)
    (run / "page" / "index.html").write_text(PAGE, encoding="utf-8")
    (run / "page" / "style.css").write_text(CSS)
    (run / "page" / "img" / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (run / "options" / "b" / "page").mkdir(parents=True)
    (run / "options" / "b" / "page" / "index.html").write_text(PAGE.replace("داینامیک", "کوتاه"), encoding="utf-8")
    (run / "run.json").write_text(json.dumps({"run_id": run.name, "type": "chart", "request": "Demo run. More text", "date": "2026-09-30"}))
    monkeypatch.setenv("FLYNN_RUNS_DIR", str(root))
    return run


def test_run_model_options_versions_pick_and_requests(runs):
    s = studio.run_summary(runs)
    assert [o["name"] for o in s["options"]] == ["main", "b"]
    vid = studio.snapshot(runs, label="round 1")
    assert (runs / "versions" / vid / "main" / "page" / "index.html").exists() and studio.versions(runs)[0]["label"] == "round 1"
    assert studio.pick(runs, "b")["option"] == "b"
    with pytest.raises(KeyError):
        studio.pick(runs, "nope")
    req = studio.export(runs, "figma", "b")
    assert req["status"] == "waiting" and req["page"] == "options/b/page/index.html"
    assert [r["id"] for r in studio.pending_requests()] == [req["id"]]
    studio.close_request(runs, req["id"], "done", link="https://figma.com/design/x")
    assert studio.pending_requests() == [] and studio.state(runs)["requests"][0]["link"].startswith("https://figma.com")
    assert studio.list_runs()[0]["decision"]["option"] == "b"


def test_html_export_zips_the_page(runs):
    e = studio.export(runs, "html")
    names = zipfile.ZipFile(runs / e["path"]).namelist()
    assert "index.html" in names and "style.css" in names


def test_tsx_follows_web_app_conventions(runs, tmp_path):
    folder = html_to_tsx.convert(runs / "page" / "index.html", tmp_path / "tsx", name="DemoPage")
    tsx = (folder / "index.tsx").read_text(encoding="utf-8")
    consts = (folder / "constants.ts").read_text(encoding="utf-8")
    scss = (folder / f"{folder.name}.module.scss").read_text(encoding="utf-8")
    assert "export const DemoPage: React.FC<DemoPageProps>" in tsx and "export interface DemoPageProps" in tsx
    assert "تخفیف" not in tsx and "تخفیف ویژه" in consts            # Persian lives in TRANSLATIONS
    assert "className={styles.hero}" in tsx or "styles.hero" in tsx
    assert "<tbody>" in tsx and "textAnchor=" in tsx and "fontSize=" in tsx and "import imgA from './assets/a.png';" in tsx
    assert "../" not in tsx and '"' not in tsx.split("return (")[0].replace('"', "")
    assert "font-family" not in scss and ".root {" in scss and ".body {" in scss and "& {" in scss


def test_server_api_and_path_safety(runs):
    httpd = studio.serve(port=0, open_browser=False)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        runs_list = json.load(urllib.request.urlopen(base + "/api/runs"))
        assert runs_list[0]["id"] == runs.name
        html = urllib.request.urlopen(f"{base}/r/{runs.name}/page/index.html").read().decode()
        assert f"/r/{runs.name}/__review/overlay.js" in html
        thumb = urllib.request.urlopen(f"{base}/r/{runs.name}/page/index.html?thumb=1").read().decode()
        assert "overlay.js" not in thumb
        req = urllib.request.Request(f"{base}/api/run/{runs.name}/pick", data=b'{"option":"main"}', headers={"Content-Type": "application/json"})
        assert json.load(urllib.request.urlopen(req))["option"] == "main"
        req = urllib.request.Request(f"{base}/r/{runs.name}/__review/api/comments", data=json.dumps({"text": "بزرگ‌تر", "anchor": {"selector": "h1"}}).encode(), headers={"Content-Type": "application/json"})
        assert json.load(urllib.request.urlopen(req))["n"] == 1
        for bad in (f"/r/{runs.name}/../../etc/passwd", "/r/..%2f..%2f/x", "/static/..%2fstudio.py"):
            with pytest.raises(urllib.error.HTTPError):
                urllib.request.urlopen(base + bad)
        assert urllib.request.urlopen(base + f"/run/{runs.name}").status == 200
    finally:
        httpd.shutdown()
