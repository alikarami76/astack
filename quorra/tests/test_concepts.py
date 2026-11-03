from pathlib import Path

import concepts
from gitfix import make_repo
from qindex import build

KB = Path(__file__).parent / "fixtures" / "kb"


def test_candidates_extracts_identifiers_tables_persian_and_headings():
    [c] = concepts.candidates(KB)
    assert c["feature"] == "featuredoffer" and len(c["docs"]) == 2
    names = {i["name"]: i["count"] for i in c["identifiers"]}
    assert names["FeaturedOfferService"] == 2 and "EditionEntity" in names
    assert c["tables"][0] == {"name": "featured_offers", "count": 3}
    persian = {p["text"] for p in c["persian_terms"]}
    assert "پیشنهاد ویژه برنده" in persian and "پیشنهاد ویژه" in persian
    assert "How an offer is chosen" in c["headings"]


def test_verify_ok_moved_missing(tmp_path, monkeypatch):
    repo, sha = make_repo(tmp_path, {"src/Service/FeaturedOfferService.php": "<?php\nclass FeaturedOfferService {}\n",
                                     "src/Command/RecalculateFeaturedCommand.php": "<?php\nclass RecalculateFeaturedCommand {}\n"})
    build.build_index("pricing", repo, "php", sha, tmp_path / "idx" / "pricing.sqlite")
    monkeypatch.setenv("QUORRA_INDEX_DIR", str(tmp_path / "idx"))
    res = concepts.verify([{"name_en": "featuredoffer winner", "implementations": [
        {"repo": "pricing", "file": "src/Service/FeaturedOfferService.php", "symbol": "FeaturedOfferService"},
        {"repo": "pricing", "file": "src/Old/Place.php", "symbol": "RecalculateFeaturedCommand"},
        {"repo": "pricing", "file": "src/X.php", "symbol": "NoSuchThing"}]}])
    assert [r["status"] for r in res] == ["ok", "moved", "missing"]
    assert res[1]["found"][0]["file"] == "src/Command/RecalculateFeaturedCommand.php"


def _kb(tmp_path, text):
    d = tmp_path / "kb" / "features" / "f"
    d.mkdir(parents=True)
    (d / "a.md").write_text(text, encoding="utf-8")
    return tmp_path / "kb"


def test_identifiers_keyword_prefixed_and_qualified(tmp_path):
    kb = _kb(tmp_path, "`PriceHistory` `PromotionType` `OrderWinner` `FooService::run` `BarCommand()` "
                       "`BazEntity.php` `App\\Entity\\PriceEntity` `lowercase` `Random`\n")
    [c] = concepts.candidates(kb)
    names = {i["name"] for i in c["identifiers"]}
    assert {"PriceHistory", "PromotionType", "OrderWinner", "FooService", "BarCommand", "BazEntity", "PriceEntity"} <= names
    assert "Random" not in names and "lowercase" not in names


def test_persian_excludes_punctuation_digits_and_subterms(tmp_path):
    kb = _kb(tmp_path, "سلام، دنیا؛ خوب؟ سلام، دنیا؛ خوب؟\n۱۲۳ قیمت ۱۲۳ قیمت\nپیشنهاد ویژه پیشنهاد ویژه\n")
    [c] = concepts.candidates(kb)
    terms = {p["text"] for p in c["persian_terms"]}
    assert {"سلام", "دنیا", "خوب", "قیمت", "پیشنهاد ویژه"} <= terms
    assert not any(ch in t for t in terms for ch in "،؛؟۱۲۳")
    assert "بای" not in terms and "باکس" not in terms


def test_headings_dedupe_and_skip_fences(tmp_path):
    kb = _kb(tmp_path, "# A\n## B\n# A\n```\n# not a heading\n```\n## B\n")
    [c] = concepts.candidates(kb)
    assert c["headings"] == ["A", "B"]


def test_verify_no_index_invalid_and_cli_exit(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("QUORRA_INDEX_DIR", str(tmp_path / "idx"))
    (tmp_path / "idx").mkdir()
    (tmp_path / "idx" / "broken.sqlite").write_text("not a db")
    concepts_in = [{"name_en": "x", "implementations": [
        {"repo": "absent", "file": "a.php", "symbol": "A"},
        {"repo": "broken", "file": "a.php", "symbol": "A"},
        {"repo": "absent", "file": "a.php"}]}]
    res = concepts.verify(concepts_in)
    assert [r["status"] for r in res] == ["no_index", "no_index", "invalid"]
    assert "symbol" in res[2]["reason"] and res[0]["found"] == []
    f = tmp_path / "c.json"
    f.write_text(__import__("json").dumps(concepts_in))
    assert concepts.main(["verify", str(f)]) == 2


def test_minor_verify_treats_non_dict_concepts_as_invalid(tmp_path):
    import concepts as cm
    res = cm.verify(["junk", 5, {"name_en": "x", "implementations": "notalist"}, {"name_en": "y", "implementations": ["bad"]}],
                    index_dir=tmp_path)
    assert [r["status"] for r in res] == ["invalid", "invalid", "invalid", "invalid"]
