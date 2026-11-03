"""Persian text layer: digits, normalisation, bidi isolation, dates, word sets and the HTML transform."""

from __future__ import annotations

import datetime
import re
import unicodedata
from dataclasses import dataclass, field
from html.parser import HTMLParser

import jdatetime

ZWNJ = "‌"
FA_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
AR_DIGITS = "٠١٢٣٤٥٦٧٨٩"
FA_DIGIT_STYLES = ("fa", "fa-body-latin-data")
_TO_FA = str.maketrans("0123456789" + AR_DIGITS, FA_DIGITS + FA_DIGITS)
_TO_LATIN = str.maketrans(FA_DIGITS + AR_DIGITS, "0123456789" * 2)
_ARABIC_LETTERS = str.maketrans({"ي": "ی", "ى": "ی", "ك": "ک"})

JALALI_FA = ["فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور", "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند"]
JALALI_EN = ["Farvardin", "Ordibehesht", "Khordad", "Tir", "Mordad", "Shahrivar", "Mehr", "Aban", "Azar", "Dey", "Bahman", "Esfand"]
GREGORIAN_FA = ["ژانویه", "فوریه", "مارس", "آوریل", "مه", "ژوئن", "ژوئیه", "اوت", "سپتامبر", "اکتبر", "نوامبر", "دسامبر"]
GREGORIAN_EN = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]

_LETTERS = "ؠ-ٟٮ-ۓەۺ-ۿ"
FA_WORD = re.compile(f"[{_LETTERS}][{_LETTERS}{ZWNJ}]*")
_FA_BLOCK = "؀-ۿ"
# A Latin word may start with one symbol that belongs to it (#tag, @user, $VAR, .env) or with - or -- (a command
# flag), but only where no Latin letter or digit comes right before, so "a-b" and "v1.2" are not split.
_LEAD = r"(?:(?<![A-Za-z0-9_])(?:--?|[#@$.]))?"
_WORD = _LEAD + r"(?:[0-9]+(?=[A-Za-z]))?[A-Za-z][A-Za-z0-9_\-./:@#+&'" + "\u2019" + r"]*"
_URL = r"(?:https?|ftp)://[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+"
_EMAIL = r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+"
_PATH = r"(?<![A-Za-z0-9_./\\:])(?:[A-Za-z]:\\|~/|\.{1,2}/|/)(?=[A-Za-z0-9_.~\-])[A-Za-z0-9._~\-/\\]+"
# URLs, email addresses and paths come first so they stay whole; plain Latin words come last.
LATIN_TOKEN = re.compile(f"(?P<url>{_URL})|(?P<email>{_EMAIL})|(?P<path>{_PATH})|(?P<word>{_WORD})")
# What may sit between two Latin tokens of one phrase: spaces, ASCII punctuation and Latin digits, never Persian.
_GAP = re.compile(r"[ \t\r\n\u00a0,;:&()\[\]\"'\u2018\u2019\u201c\u201d\-\u2013\u2014./!?%+*=#@0-9]*")
_TRAILING = ".:,;!?'\u2019-"
_PAIRS = (("(", ")"), ("[", "]"))
_LATIN_NUM = re.compile(r"[0-9]+")
# ! and ? right after a Latin phrase belong to it when Persian text follows in the same sentence.
_KEEP_MARKS = re.compile(r"[!?]+(?=[ \t\u00a0]*[" + _LETTERS + "])")


def to_fa_digits(s: str) -> str:
    return s.translate(_TO_FA)


def to_latin_digits(s: str) -> str:
    return s.translate(_TO_LATIN)


def fa_numbers(s: str) -> str:
    s = re.sub(r"(?<=[0-9])\.(?=[0-9])", "٫", s)
    s = re.sub(r"(?<=[0-9]),(?=[0-9]{3}(?![0-9]))", "٬", s)
    s = re.sub(r"(?<=[0-9])%", "٪", s)
    return to_fa_digits(s)


def normalize_text(s: str) -> str:
    s = s.translate(_ARABIC_LETTERS)
    s = re.sub(ZWNJ + "{2,}", ZWNJ, s)
    s = re.sub(ZWNJ + r"(?=\s)|(?<=\s)" + ZWNJ, "", s)
    s = re.sub(r"[ \t]+([،؛؟])", r"\1", s)
    s = re.sub(r"([،؛؟])(?=[^\s" + ZWNJ + r"»)\]])", r"\1 ", s)
    s = re.sub(r"«[ \t]+", "«", s)
    s = re.sub(r"[ \t]+»", "»", s)
    return s


def add_halfspaces(s: str) -> str:
    s = re.sub(rf"(?<![{_FA_BLOCK}])(ن?می) (?=[{_FA_BLOCK}])", rf"\1{ZWNJ}", s)
    s = re.sub(rf"(?<=[{_FA_BLOCK}]) (ها|های|هایی|هایم|هایت|هایش)(?![{_FA_BLOCK}])", rf"{ZWNJ}\1", s)
    return s


def _strip_token(kind: str, tok: str) -> str:
    tok = tok.rstrip(_TRAILING + ("/" if kind == "word" else ""))
    for left, right in _PAIRS:
        while tok.endswith(right) and tok.count(right) > tok.count(left):
            tok = tok[:-1].rstrip(_TRAILING)
    return tok


def latin_spans(s: str) -> list[tuple[int, int]]:
    """Spans of s that read left to right: Latin phrases, URLs, emails and paths, with the spaces, punctuation and
    numbers between their words, so one isolate holds the whole phrase."""
    tokens = []
    for m in LATIN_TOKEN.finditer(s):
        tok = _strip_token(m.lastgroup, m.group())
        if tok:
            tokens.append([m.start(), m.start() + len(tok)])
    spans: list[list[int]] = []
    for start, end in tokens:
        if spans and start >= spans[-1][1] and _GAP.fullmatch(s, spans[-1][1], start):
            spans[-1][1] = end
        elif not spans or start >= spans[-1][1]:
            spans.append([start, end])
    for span in spans:
        marks = _KEEP_MARKS.match(s, span[1])
        if marks:
            span[1] = marks.end()
        for left, right in _PAIRS:
            text = s[span[0]:span[1]]
            while text.count(left) > text.count(right) and s[span[1]:span[1] + 1] == right:
                span[1] += 1
                text += right
            while text.count(right) > text.count(left) and span[0] > 0 and s[span[0] - 1] == left:
                span[0] -= 1
                text = left + text
    return [(a, b) for a, b in spans]


def split_latin(s: str) -> list[tuple[bool, str]]:
    out: list[tuple[bool, str]] = []
    pos = 0
    for start, end in latin_spans(s):
        if start > pos:
            out.append((False, s[pos:start]))
        out.append((True, s[start:end]))
        pos = end
    if pos < len(s):
        out.append((False, s[pos:]))
    return out


def _digits_for(chunk: str, digits: str) -> str:
    if digits in FA_DIGIT_STYLES:
        return fa_numbers(chunk)
    if digits == "latin":
        return to_latin_digits(chunk)
    return chunk


def convert_plain(s: str, digits: str, normalize: bool = False) -> str:
    if normalize:
        s = normalize_text(s)
    return "".join(chunk if is_latin else _digits_for(chunk, digits) for is_latin, chunk in split_latin(s))


def persian_words(text: str) -> set[str]:
    t = unicodedata.normalize("NFKC", text)
    t = t.replace("ۀ", "هٔ").replace("ۀ", "هٔ")
    return {w.rstrip(ZWNJ) for w in FA_WORD.findall(t)}


def format_date(d: datetime.date, calendar: str = "gregorian", digits: str = "latin", lang: str = "en") -> str:
    if calendar == "jalali":
        j = jdatetime.date.fromgregorian(date=d)
        month = (JALALI_EN if lang == "en" else JALALI_FA)[j.month - 1]
        s = f"{j.day} {month} {j.year}"
    else:
        month = (GREGORIAN_EN if lang == "en" else GREGORIAN_FA)[d.month - 1]
        s = f"{d.day} {month} {d.year}"
    return to_fa_digits(s) if digits in FA_DIGIT_STYLES else s


@dataclass
class TransformOptions:
    digits: str = "latin"
    isolate: bool = False
    auto_dir: bool = False
    normalize: bool = False
    halfspace: bool = False


@dataclass
class TextStats:
    chars: dict = field(default_factory=lambda: {"body": set(), "heading": set(), "mono": set()})
    fa_words: set = field(default_factory=set)
    latin_numbers: set = field(default_factory=set)


MONO = {"code", "pre", "kbd", "samp"}
PASS_THROUGH = {"script", "style"}
NO_BDI = {"svg", "math", "bdi"}
DATA = {"table", "figure"}
HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
AUTO_DIR = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "td", "th", "blockquote", "figcaption", "dt", "dd"}
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
# Inline elements do not end a run of text: Latin phrases are found, and Persian words are collected, across them.
INLINE = {
    "a", "abbr", "b", "bdi", "bdo", "br", "cite", "code", "data", "del", "dfn", "em", "font", "i", "img", "ins",
    "kbd", "label", "mark", "q", "s", "samp", "small", "span", "strong", "sub", "sup", "time", "u", "var", "wbr",
}
BREAKS = {"br", "img"}
_BREAK = " "
_BDI_OPEN, _BDI_CLOSE = '<bdi dir="ltr">', "</bdi>"


def _escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


@dataclass
class _Text:
    text: str
    role: str
    fixed: bool  # code or data-raw: never normalised or digit-converted
    no_bdi: bool
    want_fa: bool
    stack: tuple


@dataclass
class _Tag:
    html: str
    kind: str  # "open", "close", "void" or "other"
    tag: str
    stack: tuple

    @property
    def width(self) -> int:
        return 1 if self.kind == "void" and self.tag in BREAKS else 0


class _Transformer(HTMLParser):
    def __init__(self, options: TransformOptions):
        super().__init__(convert_charrefs=True)
        self.o = options
        self.out: list[str] = []
        self.stack: list[tuple[str, dict]] = []
        self.in_body = False
        self.stats = TextStats()
        self.run: list = []

    def _has(self, tags) -> bool:
        return any(tag in tags for tag, _ in self.stack)

    def _raw(self) -> bool:
        return any("data-raw" in attrs for _, attrs in self.stack)

    def _data_context(self) -> bool:
        return any(
            tag in DATA or attrs.get("data-digits") == "latin" or "chart" in attrs.get("class", "").split()
            for tag, attrs in self.stack
        )

    def _role(self) -> str:
        if self._has(MONO):
            return "mono"
        if self._has(HEADINGS):
            return "heading"
        return "body"

    def _tags(self) -> tuple:
        return tuple(tag for tag, _ in self.stack)

    def _buffering(self) -> bool:
        return self.in_body and not self._has(PASS_THROUGH)

    def _emit_tag(self, html: str, kind: str, tag: str):
        if tag in INLINE and self._buffering():
            self.run.append(_Tag(html, kind, tag, self._tags()))
        else:
            self.flush()
            self.out.append(html)

    def handle_starttag(self, tag, attrs):
        text = self.get_starttag_text()
        if tag == "body":
            self.in_body = True
        if self.o.auto_dir and tag in AUTO_DIR and not any(k == "dir" for k, _ in attrs):
            cut = 1 + len(tag)
            text = f'{text[:cut]} dir="auto"{text[cut:]}'
        self._emit_tag(text, "void" if tag in VOID else "open", tag)
        if tag not in VOID:
            self.stack.append((tag, {k: (v or "") for k, v in attrs}))

    def handle_startendtag(self, tag, attrs):
        self._emit_tag(self.get_starttag_text(), "void", tag)

    def handle_endtag(self, tag):
        self._emit_tag(f"</{tag}>", "close", tag)
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break

    def handle_comment(self, data):
        if self._buffering():
            self.run.append(_Tag(f"<!--{data}-->", "other", "", self._tags()))
        else:
            self.out.append(f"<!--{data}-->")

    def handle_decl(self, decl):
        self.flush()
        self.out.append(f"<!{decl}>")

    def handle_pi(self, data):
        self.flush()
        self.out.append(f"<?{data}>")

    def unknown_decl(self, data):
        self.flush()
        self.out.append(f"<![{data}]>")

    def handle_data(self, data):
        if self._has(PASS_THROUGH):
            self.out.append(data)  # script and style text arrives undecoded
            return
        if not self.in_body:
            self.out.append(_escape(data))
            return
        role = self._role()
        fixed = role == "mono" or self._raw()
        if not fixed:
            data = normalize_text(data) if self.o.normalize else data
            if self.o.halfspace:
                data = add_halfspaces(data)
        want_fa = self.o.digits == "fa" or (self.o.digits == "fa-body-latin-data" and not self._data_context())
        no_bdi = not self.o.isolate or self._has(NO_BDI) or self._raw()
        self.run.append(_Text(data, role, fixed, no_bdi, want_fa, self._tags()))

    def close(self):
        super().close()
        self.flush()

    # A run is the text of one block, with its inline tags. It is processed as a whole so that a Latin phrase split by
    # an inline tag, an entity or punctuation still gets a single isolate, and a Persian word split by a tag is one word.
    def flush(self):
        run, self.run = self.run, []
        if not run:
            return
        joined = "".join(it.text if isinstance(it, _Text) else _BREAK * it.width for it in run)
        spans = latin_spans(joined)
        spans = self._snap_to_code(run, spans)
        pieces = self._split(run, {b for span in spans for b in span})
        opens, closes = self._isolates(pieces, spans)
        words: list[str] = []
        pos = 0
        in_span = False
        span_ends = {e for _, e in spans}
        span_starts = {s for s, _ in spans}
        for k, it in enumerate(pieces):
            if k in closes:
                self.out.append(_BDI_CLOSE)
            if k in opens:
                self.out.append(_BDI_OPEN)
            if isinstance(it, _Tag):
                self.out.append(it.html)
                words.append(" " * it.width)
                pos += it.width
                continue
            if pos in span_starts:
                in_span = True
            if pos in span_ends:
                in_span = pos in span_starts
            text = self._convert(it, in_span)
            self.out.append(_escape(text))
            self.stats.chars[it.role].update(text)
            words.append(text if it.role != "mono" else " ")
            pos += len(it.text)
        if len(pieces) in closes:
            self.out.append(_BDI_CLOSE)
        self.stats.fa_words |= persian_words("".join(words))

    def _convert(self, it: _Text, latin: bool) -> str:
        text = it.text
        if it.fixed or latin:
            self.stats.latin_numbers.update(_LATIN_NUM.findall(text))
            return text
        if it.want_fa:
            return fa_numbers(text)
        if self.o.digits == "latin":
            return to_latin_digits(text)
        self.stats.latin_numbers.update(_LATIN_NUM.findall(text))
        return text

    @staticmethod
    def _snap_to_code(run, spans):
        # An isolate never starts or ends inside code: a span that touches code text grows to hold all of it.
        ranges, pos = [], 0
        for it in run:
            width = len(it.text) if isinstance(it, _Text) else it.width
            if isinstance(it, _Text) and it.role == "mono" and width:
                ranges.append((pos, pos + width))
            pos += width
        out: list[list[int]] = []
        for start, end in spans:
            for a, b in ranges:
                if a < end and start < b:
                    start, end = min(start, a), max(end, b)
            if out and start <= out[-1][1]:
                out[-1][1] = max(out[-1][1], end)
            else:
                out.append([start, end])
        return [(a, b) for a, b in out]

    @staticmethod
    def _split(run, cuts):
        pieces, pos = [], 0
        for it in run:
            if isinstance(it, _Tag):
                pieces.append(it)
                pos += it.width
                continue
            end = pos + len(it.text)
            marks = sorted(c for c in cuts if pos < c < end)
            last = pos
            for c in marks + [end]:
                if c > last:
                    pieces.append(_Text(it.text[last - pos:c - pos], it.role, it.fixed, it.no_bdi, it.want_fa, it.stack))
                last = c
            pos = end
        return pieces

    def _isolates(self, pieces, spans):
        """Pick, for each span, where its <bdi> opens and closes: the innermost pair of gaps between pieces that keeps
        the tags inside it balanced and sits outside code. A span with no such pair is left to the browser."""
        offsets, pos = [], 0
        for it in pieces:
            offsets.append(pos)
            pos += len(it.text) if isinstance(it, _Text) else it.width
        offsets.append(pos)
        end_stack = self._tags()
        opens, closes = set(), set()
        for start, end in spans:
            inside = [it for it, off in zip(pieces, offsets) if isinstance(it, _Text) and start <= off < end]
            if not inside or any(it.no_bdi for it in inside):
                continue
            open_gaps = [k for k in range(len(pieces), -1, -1) if offsets[k] == start and self._gap_ok(pieces, k, end_stack)]
            close_gaps = [k for k in range(len(pieces) + 1) if offsets[k] == end and self._gap_ok(pieces, k, end_stack)]
            pair = next(((o, c) for o in open_gaps for c in close_gaps if o < c and self._balanced(pieces[o:c])), None)
            if pair:
                opens.add(pair[0])
                closes.add(pair[1])
        return opens, closes

    @staticmethod
    def _gap_ok(pieces, k, end_stack) -> bool:
        stack = pieces[k].stack if k < len(pieces) else end_stack
        return not any(tag in MONO for tag in stack)

    @staticmethod
    def _balanced(items) -> bool:
        depth: list[str] = []
        for it in items:
            if isinstance(it, _Tag) and it.kind == "open":
                depth.append(it.tag)
            elif isinstance(it, _Tag) and it.kind == "close":
                if not depth or depth.pop() != it.tag:
                    return False
        return not depth


def transform_html(html: str, options: TransformOptions) -> tuple[str, TextStats]:
    t = _Transformer(options)
    t.feed(html)
    t.close()
    return "".join(t.out), t.stats
