from vecstore.chunk import MAX_CHARS, chunk_file, chunk_text, slug

MD = """# Buybox
intro line

## Weights
price 0.94, lead time 0.05

```sql
# not a heading
SELECT 1
```

## Weights
second section with the same heading
"""


def test_sections_carry_heading_path_and_skip_code_fence_hashes():
    parts = chunk_text(MD, "notes/buybox")
    heads = [h for h, _ in parts]
    assert heads == ["Buybox", "Buybox › Weights", "Buybox › Weights"]
    assert parts[1][1].startswith("notes/buybox › Buybox › Weights\n")
    assert "# not a heading" in parts[1][1]


def test_long_sections_split_at_paragraphs():
    body = "\n\n".join(("word " * 100).strip() for _ in range(6))  # six ~500-char paragraphs
    parts = chunk_text("## Big\n" + body, "t")
    assert len(parts) >= 3 and all(len(t) <= MAX_CHARS + 80 for _, t in parts)


def test_file_ids_are_stable_and_unique(tmp_path):
    (tmp_path / "notes").mkdir()
    f = tmp_path / "notes" / "buybox.md"
    f.write_text(MD)
    ids = [e for e, _, _ in chunk_file(f, tmp_path)]
    assert ids == ["notes/buybox.md#buybox", "notes/buybox.md#weights", "notes/buybox.md#weights-2"]
    assert ids == [e for e, _, _ in chunk_file(f, tmp_path)]


def test_non_markdown_is_one_headless_section(tmp_path):
    f = tmp_path / "q.sql"
    f.write_text("-- nmv by day\nSELECT 1")
    [(eid, head, text)] = chunk_file(f, tmp_path)
    assert eid == "q.sql#top" and head == "" and "SELECT 1" in text


def test_slug_keeps_farsi():
    assert slug("بای‌باکس Winner!") == "بای-باکس-winner"


def test_ids_never_collide(tmp_path):
    (tmp_path / "notes").mkdir()
    f = tmp_path / "notes" / "test.md"
    f.write_text("## Weights\nsection 1\n\n## Weights\nsection 2\n\n## Weights 2\nsection 3\n")
    ids = [e for e, _, _ in chunk_file(f, tmp_path)]
    assert len(set(ids)) == len(ids), f"IDs must be unique, but got: {ids}"
    assert ids == ["notes/test.md#weights", "notes/test.md#weights-2", "notes/test.md#weights-2-2"]
