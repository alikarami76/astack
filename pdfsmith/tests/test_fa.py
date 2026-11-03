import datetime

from pdfsmith import fa


def test_digits():
    assert fa.to_fa_digits("2026 and ٣") == "۲۰۲۶ and ۳"
    assert fa.to_latin_digits("۱۴۰۵٣") == "14053"


def test_fa_numbers_separators():
    assert fa.fa_numbers("12.5% و 1,250") == "۱۲٫۵٪ و ۱٬۲۵۰"


def test_normalize_arabic_letters():
    assert fa.normalize_text("علي كتاب") == "علی کتاب"


def test_normalize_zwnj():
    assert fa.normalize_text("می‌خواهم") == "می‌خواهم"
    assert fa.normalize_text("کتاب‌‌ها") == "کتاب‌ها"
    assert fa.normalize_text("کتاب‌ ها") == "کتاب ها"


def test_normalize_punctuation():
    assert fa.normalize_text("سلام ، خوبی ؟") == "سلام، خوبی؟"
    assert fa.normalize_text("« متن »") == "«متن»"


def test_add_halfspaces():
    assert fa.add_halfspaces("می خواهم کتاب ها را") == "می‌خواهم کتاب‌ها را"


def test_split_latin_strips_trailing_punctuation():
    assert fa.split_latin("نرخ BuyBox Price. است") == [(False, "نرخ "), (True, "BuyBox Price"), (False, ". است")]


def test_split_latin_does_not_absorb_numbers():
    assert fa.split_latin("نرخ BuyBox 12.5%") == [(False, "نرخ "), (True, "BuyBox"), (False, " 12.5%")]


def test_split_latin_leading_digit():
    assert fa.split_latin("حالت 3D فعال") == [(False, "حالت "), (True, "3D"), (False, " فعال")]


def test_split_latin_typographic_apostrophe():
    assert fa.split_latin("قیمت the user’s Price") == [(False, "قیمت "), (True, "the user’s Price")]


def test_split_latin_straight_apostrophe():
    assert fa.split_latin("قیمت the user's Price") == [(False, "قیمت "), (True, "the user's Price")]


def test_convert_plain():
    assert fa.convert_plain("گزارش 1405 BuyBox 2", "fa") == "گزارش ۱۴۰۵ BuyBox ۲"
    assert fa.convert_plain("گزارش ۱۴۰۵", "latin") == "گزارش 1405"


def test_persian_words():
    assert fa.persian_words("می‌خواهیم، BuyBox ۱۲ کتاب‌") == {"می‌خواهیم", "کتاب"}


def test_persian_words_normalises_forms():
    assert fa.persian_words("ﻻ") == {"لا"}
    assert fa.persian_words("خانۀ") == fa.persian_words("خانهٔ")


def test_format_date():
    d = datetime.date(2026, 9, 30)
    assert fa.format_date(d, "jalali", "fa", "fa") == "۸ مهر ۱۴۰۵"
    assert fa.format_date(d, "gregorian", "latin", "en") == "30 September 2026"
    assert fa.format_date(d, "jalali", "latin", "en") == "8 Mehr 1405"


def _t(html, **kw):
    opts = dict(digits="fa", isolate=True, auto_dir=False, normalize=True, halfspace=False)
    opts.update(kw)
    return fa.transform_html(html, fa.TransformOptions(**opts))


def test_transform_isolates_and_converts():
    out, st = _t("<body><p>نرخ BuyBox برابر 12.5% است</p></body>")
    assert out == '<body><p>نرخ <bdi dir="ltr">BuyBox</bdi> برابر ۱۲٫۵٪ است</p></body>'
    assert "B" in st.chars["body"] and "نرخ" in st.fa_words


def test_transform_skips_code_and_records_latin_numbers():
    out, st = _t("<body><p>کد <code>x = 42</code></p></body>")
    assert "<code>x = 42</code>" in out
    assert "42" in st.latin_numbers and "4" in st.chars["mono"]


def test_transform_data_context_keeps_latin_digits():
    out, st = _t("<body><p>عدد 7</p><table><tr><td>1250</td></tr></table></body>", digits="fa-body-latin-data")
    assert "عدد ۷" in out and "<td>1250</td>" in out and "1250" in st.latin_numbers


def test_transform_auto_dir():
    out, _ = _t("<body><p>x</p><h2 id='a'>y</h2></body>", auto_dir=True, isolate=False)
    assert '<p dir="auto">' in out and "<h2 dir=\"auto\" id='a'>" in out


def test_transform_passes_head_entities_and_scripts():
    out, st = _t("<head><title>عنوان 1</title><style>p{x:1}</style></head><body><p>a &amp; b</p><script>var a=1<2;</script></body>")
    assert "<title>عنوان 1</title>" in out and "&amp;" in out and "var a=1<2;" in out and "p{x:1}" in out
    assert "عنوان" not in st.fa_words


def test_transform_words_exclude_pre():
    _, st = _t("<body><p>کتاب‌ها</p><pre>متن‌کد</pre></body>")
    assert "کتاب‌ها" in st.fa_words and "متن‌کد" not in st.fa_words


def test_transform_raw_block_untouched():
    out, _ = _t("<body><p data-raw>علي 12</p></body>")
    assert "علي 12" in out


def test_transform_heading_role():
    _, st = _t("<body><h1>گ</h1><p>ب</p></body>")
    assert "گ" in st.chars["heading"] and "ب" in st.chars["body"]


def test_transform_svg_text_converted_without_bdi():
    out, _ = _t("<body><svg><text>قیمت Price 5</text></svg></body>")
    assert "<bdi" not in out and "۵" in out


def _p(inner, **kw):
    out, st = _t(f"<body><p>{inner}</p></body>", **kw)
    return out[len("<body><p>"):-len("</p></body>")], st


def _bdi(s):
    return f'<bdi dir="ltr">{s}</bdi>'


def test_isolate_keeps_entity_inside_one_run():
    assert _p("تیم R&amp;D و Q&amp;A")[0] == f"تیم {_bdi('R&amp;D')} و {_bdi('Q&amp;A')}"


def test_isolate_absorbs_punctuation_between_latin_words():
    assert _p("پیام Hello, World است")[0] == f"پیام {_bdi('Hello, World')} است"
    assert _p("نسخهٔ Windows 11 Pro و (the user) است")[0] == f"نسخهٔ {_bdi('Windows 11 Pro')} و ({_bdi('the user')}) است"


def test_isolate_spans_inline_tag_at_element_level():
    assert _p("پنل <strong>Admin</strong> Panel است")[0] == f"پنل {_bdi('<strong>Admin</strong> Panel')} است"
    assert _p("پنل <em>Admin Panel</em> است")[0] == f"پنل <em>{_bdi('Admin Panel')}</em> است"


def test_isolate_leaves_unbalanced_run_to_the_browser():
    # The run starts inside <strong> after Persian text, so no single isolate can hold it without splitting it.
    assert _p("پنل <strong>فروش Admin</strong> Panel است")[0] == "پنل <strong>فروش Admin</strong> Panel است"


def test_isolate_url_is_one_run_without_digit_conversion():
    out, st = _p("نشانی https://example.com/p?id=42&amp;x=7 است.")
    assert out == f"نشانی {_bdi('https://example.com/p?id=42&amp;x=7')} است."
    assert {"42", "7"} <= st.latin_numbers


def test_isolate_email_and_paths():
    assert _p("ایمیل jane.k+1@example.com است")[0] == f"ایمیل {_bdi('jane.k+1@example.com')} است"
    assert _p("پوشهٔ /usr/local/bin و فایل C:\\data\\q3.sql را ببینید")[0] == (
        f"پوشهٔ {_bdi('/usr/local/bin')} و فایل {_bdi('C:\\data\\q3.sql')} را ببینید"
    )
    assert _p("مسیر ./build/out.pdf.")[0] == f"مسیر {_bdi('./build/out.pdf')}."


def test_isolate_wraps_inline_code_whole():
    out, _ = _p("دستور run <code>make build</code> now را بزنید")
    assert out == f"دستور {_bdi('run <code>make build</code> now')} را بزنید"


def test_escapes_decoded_text():
    out, _ = _t("<head><title>A &amp; B</title></head><body><p>a &lt;b&gt; &amp; c</p></body>", isolate=False)
    assert "<title>A &amp; B</title>" in out and "<p>a &lt;b&gt; &amp; c</p>" in out


def test_words_join_across_inline_tags():
    _, st = _t("<body><p><strong>کتاب</strong>ها</p></body>")
    assert "کتابها" in st.fa_words and "کتاب" not in st.fa_words and "ها" not in st.fa_words
    _, st = _t('<body><p><a href="u">قیمت</a>\u200cها</p></body>')
    assert "قیمت\u200cها" in st.fa_words and "قیمت" not in st.fa_words


def test_words_do_not_join_across_blocks_or_breaks():
    _, st = _t("<body><p>کتاب</p><p>ها</p><p>دو<br>سه</p></body>")
    assert {"کتاب", "ها", "دو", "سه"} <= st.fa_words and "دوسه" not in st.fa_words


def test_head_script_and_style_pass_through_undecoded():
    out, _ = _t("<head><script>if (a < b && c) {}</script><style>a > b {}</style></head><body></body>")
    assert "if (a < b && c) {}" in out and "a > b {}" in out


def test_split_latin_keeps_leading_symbol():
    for token in ["#BlackFriday", "@acme", "--verbose", "-v", "$HOME", ".env", ".NET"]:
        assert fa.split_latin(f"گزینهٔ {token} را ببین") == [(False, "گزینهٔ "), (True, token), (False, " را ببین")], token


def test_split_latin_leading_symbol_needs_a_boundary():
    assert fa.split_latin("نسخهٔ v1.2 و a-b") == [(False, "نسخهٔ "), (True, "v1.2"), (False, " و "), (True, "a-b")]
    assert fa.split_latin("عدد -5 و #12 است") == [(False, "عدد -5 و #12 است")]


def test_split_latin_keeps_mark_when_persian_follows():
    assert fa.split_latin("پیام Hello, World! را دید") == [(False, "پیام "), (True, "Hello, World!"), (False, " را دید")]
    assert fa.split_latin("آیا Hello World?! را دید") == [(False, "آیا "), (True, "Hello World?!"), (False, " را دید")]


def test_split_latin_leaves_final_mark_outside():
    assert fa.split_latin("پایان Hello, World!") == [(False, "پایان "), (True, "Hello, World"), (False, "!")]
    assert fa.split_latin("نرخ BuyBox Price. است") == [(False, "نرخ "), (True, "BuyBox Price"), (False, ". است")]


def test_transform_isolates_leading_symbol():
    out, _ = _t("<body><p>تگ #BlackFriday را ببین</p></body>")
    assert out == '<body><p>تگ <bdi dir="ltr">#BlackFriday</bdi> را ببین</p></body>'
