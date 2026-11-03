from pdfsmith.md import render_markdown


def test_headings_and_ids():
    r = render_markdown("# Title\n\ntext\n\n## A\n\nx\n\n### B\n\ny\n")
    assert [(h.level, h.text, h.id) for h in r.headings] == [(1, "Title", "h1"), (2, "A", "h2"), (3, "B", "h3")]
    assert '<h2 id="h2">A</h2>' in r.html and r.title == "Title"


def test_custom_id():
    r = render_markdown("## Intro {#intro}\n\nx\n")
    assert r.headings[0].id == "intro" and r.headings[0].text == "Intro" and '<h2 id="intro">Intro</h2>' in r.html


def test_tables_footnotes_deflists():
    r = render_markdown("| a | b |\n|---|---|\n| 1 | 2 |\n\nText[^1]\n\n[^1]: Note\n\nTerm\n: Def\n")
    assert "<table>" in r.html and "footnote" in r.html and "<dl>" in r.html
    assert "↩" not in r.html


def test_raw_html_allowed():
    assert '<div class="x">hi</div>' in render_markdown('<div class="x">hi</div>\n').html


def test_empty_sections():
    assert render_markdown("## A\n\n## B\n\ntext\n\n### C\n").empty_sections == ["A", "C"]


def test_nested_section_not_empty():
    assert render_markdown("## A\n\n### B\n\nx\n").empty_sections == []


def test_zwnj_preserved():
    assert "می‌خواهیم" in render_markdown("می‌خواهیم").html
