from vecstore.normalize import fts_text, normalize


def test_arabic_yeh_and_kaf_become_persian():
    assert normalize("كتاب") == "کتاب"
    assert normalize("علي") == "علی"
    assert normalize("مصطفى") == "مصطفی"
    assert normalize("ي ى ك") == "ی ی ک"


def test_tatweel_and_harakat_removed():
    assert normalize("سـلام") == "سلام"
    assert normalize("مُحَمَّد") == "محمد"
    assert normalize("إِنَّ") == "إن"  # hamza forms are kept, only the marks go


def test_digits_to_ascii():
    assert normalize("۱۲۳۴۵۶۷۸۹۰") == "1234567890"
    assert normalize("٠١٢٣٤٥٦٧٨٩") == "0123456789"
    assert normalize("رول ۲ و Rule 2") == "رول 2 و Rule 2"


def test_zwnj_kept_but_cleaned():
    assert normalize("می‌خواهم") == "می‌خواهم"
    assert normalize("می ‌ خواهم") == "می‌خواهم"
    assert normalize("می‌‌خواهم") == "می‌خواهم"
    assert normalize("می‍خواهم") == "می‌خواهم"  # joiner variant folds to ZWNJ
    assert normalize("‌سلام‌") == "سلام"  # stray at the edges
    assert normalize("سلام ‌") == "سلام"


def test_directional_marks_removed():
    assert normalize("a‏b‎") == "ab"


def test_whitespace_collapsed():
    assert normalize("  a \t b\n\nc  ") == "a b c"
    assert normalize("a b") == "a b"


def test_idempotent_and_safe_on_empty_and_non_text():
    s = "كتابِ ۱۲ می ‌خواهم"
    assert normalize(normalize(s)) == normalize(s)
    assert normalize("") == "" and normalize(None) == ""
    assert normalize("Plain ASCII 100%") == "Plain ASCII 100%"


def test_fts_text_turns_zwnj_into_space():
    assert fts_text("می‌خواهم ك") == "می خواهم ک"


def test_variants_meet_after_normalising():
    assert normalize("بای‌باکس ۲") == normalize("بای ‌ باکس 2")
    assert normalize("كيفيت") == normalize("کیفیت")
