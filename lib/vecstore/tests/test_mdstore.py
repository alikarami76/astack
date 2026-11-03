import os
import time

import pytest

from vecstore import Unavailable
from vecstore.mdstore import MdStore
from vecstore.vecfile import VecFile


def _write(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


@pytest.fixture
def md(tmp_path):
    root = tmp_path / "memory"
    _write(root / "tables" / "buybox.md", "# Buybox scores\nwinner flags per variant\n")
    _write(root / "lessons.md", "## ClickHouse\nkeep max_threads at one\n")
    _write(root / "tables" / "stub_orders.md", "# orders\nAUTO-GENERATED stub\n")
    _write(root / "provenance" / "registry.md", "# signed\nnever index me\n")
    return MdStore("toy", root, tmp_path / "toy.vectors.sqlite", "Instruct: toy\nQuery: ",
                   exclude=("provenance/*",), generated=lambda rel, text: "AUTO-GENERATED" in text,
                   eval_dir=tmp_path / "eval")


def test_lexical_finds_sections_and_excludes_globs(md):
    md.refresh_index()
    res = md.lexical("winner", None, 10)
    assert res[0]["id"] == "tables/buybox.md#buybox-scores" and res[0]["type"] == "tables"
    assert res[0]["path"] == "tables/buybox.md" and res[0]["heading"] == "Buybox scores"
    assert md.lexical("signed", None, 10) == []


def test_generated_chunks_are_searchable_but_not_embedded(md):
    md.refresh_index()
    assert md.lexical("orders", None, 10)
    assert {e for e, _, _ in md.entries()} == {"tables/buybox.md#buybox-scores", "lessons.md#clickhouse"}


def test_refresh_picks_up_edits_and_deletions(md):
    assert md.refresh_index() == 3
    assert md.refresh_index() == 0
    f = md.root / "lessons.md"
    f.write_text("## ClickHouse\nkeep max_threads at one\n\n## Farsi\nشگفت‌انگیز\n")
    os.utime(f, ns=(time.time_ns(), time.time_ns() + 1_000_000))
    (md.root / "tables" / "buybox.md").unlink()
    assert md.refresh_index() == 2
    assert md.lexical("winner", None, 10) == []
    assert md.lexical("شگفت‌انگیز", None, 10)


def test_search_embeds_small_changes_inline_and_hints_on_large(md, fake_embed, capsys):
    md.refresh_index()
    md.sync_vectors(fake_embed)
    md.max_inline = 0
    _write(md.root / "new.md", "## New\nfresh note\n")
    md.search("fresh", mode="hybrid", embed_fn=fake_embed)
    assert "1 chunks stale; run: vecstore index toy" in capsys.readouterr().err


def test_search_embeds_small_changes_inline(md, fake_embed, capsys):
    md.refresh_index()
    md.sync_vectors(fake_embed)
    initial_count = VecFile(md.vec_path).count()
    _write(md.root / "new.md", "## New\nfresh note\n")
    md.search("fresh", mode="hybrid", embed_fn=fake_embed)
    after_count = VecFile(md.vec_path).count()
    assert after_count == initial_count + 1
    assert "stale" not in capsys.readouterr().err


def test_search_falls_back_once_when_embedding_fails(md, fake_embed, capsys):
    md.refresh_index()
    md.sync_vectors(fake_embed)
    _write(md.root / "new.md", "## New\nfresh note\n")

    def boom(texts):
        raise Unavailable("no daemon")
    res = md.search("fresh", mode="hybrid", embed_fn=boom)
    assert res[0]["id"] == "new.md#new"
    assert capsys.readouterr().err.count("keyword results only") == 1


def test_auto_stays_lexical_without_baseline(md, fake_embed):
    md.refresh_index()
    md.sync_vectors(fake_embed)

    def never(texts):
        raise AssertionError("auto without a passing baseline must not embed")
    assert md.search("winner", embed_fn=never)[0]["id"] == "tables/buybox.md#buybox-scores"


def test_rebuild_clears_everything(md, fake_embed):
    md.refresh_index()
    md.sync_vectors(fake_embed)
    md.rebuild()
    assert VecFile(md.vec_path).count() == 0
    assert md.refresh_index() == 3


def test_etype_filter(md):
    md.refresh_index()
    tables = md.lexical("buybox", "tables", 10)
    assert all(r["type"] == "tables" for r in tables)
    assert len(tables) > 0
    root = md.lexical("clickhouse", "root", 10)
    assert all(r["type"] == "root" for r in root)
    assert len(root) > 0


def test_query_syntax_is_neutralised(md):
    md.refresh_index()
    # These queries contain FTS5 syntax that should be neutralised and not raise
    res1 = md.lexical('winner" OR *', None, 10)
    assert any(r["id"] == "tables/buybox.md#buybox-scores" for r in res1)
    res2 = md.lexical('AND', None, 10)
    # AND alone may not match anything, but should not raise
    res3 = md.lexical('NEAR(', None, 10)
    # NEAR syntax should be neutralised, not raise
    assert isinstance(res3, list)
