import functools
import os
import tempfile

os.environ.setdefault("PDFSMITH_CACHE", tempfile.mkdtemp(prefix="pdfsmith-test-cache-"))

import pytest  # noqa: E402
import yaml  # noqa: E402


@functools.cache
def _browser_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            for kwargs in ({"channel": "chrome"}, {}):
                try:
                    p.chromium.launch(**kwargs).close()
                    return True
                except Exception:
                    continue
    except Exception:
        pass
    return False


def pytest_collection_modifyitems(config, items):
    _skip_without_tectonic(items)
    needs = [item for item in items if "chrome" in item.keywords]
    if not needs or _browser_available():
        return
    skip = pytest.mark.skip(reason="no Chrome or Playwright Chromium available")
    for item in needs:
        item.add_marker(skip)


def _skip_without_tectonic(items):
    import shutil

    if shutil.which("tectonic"):
        return
    skip = pytest.mark.skip(reason="tectonic is not installed")
    for item in items:
        if "tectonic" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def make_doc(tmp_path):
    def _make(doc: dict, content: str | None = None, html: str | None = None, name: str = "d"):
        d = tmp_path / name
        d.mkdir()
        if content is not None:
            (d / "content.md").write_text(content, encoding="utf-8")
            doc.setdefault("content", "content.md")
        if html is not None:
            (d / "page.html").write_text(html, encoding="utf-8")
            doc.setdefault("html", "page.html")
        (d / "doc.yaml").write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")
        return d

    return _make
