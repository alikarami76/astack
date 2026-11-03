"""Farsi/Arabic text normalisation for indexing and queries. Pure Python, no dependencies.

Apply the same function to the text that is indexed (full-text body, vector text) and to every query, so that
Arabic and Persian spellings of one word meet. normalize() keeps the zero-width non-joiner (ZWNJ) because it is part
of correct Persian spelling; fts_text() is the variant for keyword tokens, where ZWNJ is a word break.
"""
from __future__ import annotations

import re

ZWNJ = "‌"
_MAP = str.maketrans({
    "ي": "ی", "ى": "ی", "ۍ": "ی", "ې": "ی",  # Arabic yeh and alef maksura, other yeh forms -> Persian yeh
    "ك": "ک", "ڪ": "ک",  # Arabic kaf -> Persian keheh
    "ە": "ه", "ۀ": "ه",  # heh variants -> heh
    "ـ": None,  # tatweel
    "‍": ZWNJ,  # zero-width joiner folds to ZWNJ
    "‎": None, "‏": None, "‪": None, "‫": None, "‬": None, "‭": None,
    "‮": None, "⁦": None, "⁧": None, "⁨": None, "⁩": None, "﻿": None,  # direction marks
    " ": " ", " ": " ", " ": " ",  # no-break and thin spaces
    **{chr(0x06F0 + i): str(i) for i in range(10)},  # Persian digits
    **{chr(0x0660 + i): str(i) for i in range(10)},  # Arabic-Indic digits
})
_HARAKAT = re.compile("[ؐ-ًؚ-ٰٟۖ-ۜ۟-۪ۤۧۨ-ۭ]")
_ZWNJ_RUN = re.compile(r"\s*" + ZWNJ + r"[\s" + ZWNJ + r"]*")
_WS = re.compile(r"\s+")


def normalize(text) -> str:
    if not text:
        return ""
    s = _HARAKAT.sub("", str(text).translate(_MAP))
    s = _ZWNJ_RUN.sub(ZWNJ, s)
    s = re.sub(r"(?:^|(?<=\s))" + ZWNJ + r"+|" + ZWNJ + r"+(?=\s|$)", "", s)  # ZWNJ at a word edge means nothing
    return _WS.sub(" ", s).strip()


def fts_text(text) -> str:
    """normalize(), then ZWNJ becomes a space: a keyword token never spans a ZWNJ."""
    return normalize(text).replace(ZWNJ, " ")
