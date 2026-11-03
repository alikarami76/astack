"""Example oracles for a made-up ticket-booking site's seat-hold form: what the requirement says the answer is,
written from the requirement text and cited, never copied from the code under test. Each function names its source.
Add a rule only with a source (a product map entry, a PRD line or a knowledge-base page); a rule guessed from memory
is a wrong oracle.

The rules below are invented to show the pattern. Replace them with your own. Money is handled as exact integers or
fractions, so the oracle never shares the code's rounding errors.
"""
from __future__ import annotations

import math
from fractions import Fraction

MAX_SEATS = 8  # invented PRD line: "at most 8 seats per hold"


def percent_from_price(base: int, price: int):
    """Percent off shown after typing a price: the whole-number part of (base - price) / base x 100, computed
    exactly. None outside 5..90. Requirement R3: "percent off is between 5 and 90"."""
    pct = math.floor(100 - Fraction(price, base) * 100)
    return None if pct > 90 or pct < 5 else pct


def price_from_percent(base: int, percent: int) -> int:
    """Price after a typed percent off, rounded down to a multiple of 50, exactly.
    Requirement R3: "the price ends in 0 or 5 tens"."""
    return (base * (100 - percent) // 100) // 50 * 50


def seats_valid(seats, free: int) -> bool:
    """R3: "Seats per hold: whole number, at least 1, up to the free seats and at most 8"."""
    return isinstance(seats, int) and not isinstance(seats, bool) and 1 <= seats <= min(free, MAX_SEATS)


def price_valid(price: int, max_price: int) -> bool:
    """R3: the price ends in a multiple of 50 and is at most the row's maximum."""
    return price % 50 == 0 and 0 < price <= max_price


def shows_valid(show_days: list[str], shows_already_held: list[int]) -> bool:
    """R4: "A hold covers one day; a show the user already holds cannot be added"; at least one show is required."""
    return bool(show_days) and len(set(show_days)) == 1 and not shows_already_held


def deposit_range(seats: int) -> tuple[int, int]:
    """R2: the optional deposit is 20 to 50 percent of the seat total (whole currency units, rounded up at the
    lower end so 20% is reached)."""
    return math.ceil(seats * Fraction(20, 100)), math.ceil(seats * Fraction(50, 100))


# ---- Ports of the code under test (for comparison only; never use them as the oracle) ----
def js_percent_from_price(base: int, price: int):
    """Port of a typical front-end percent helper. Python floats are IEEE 754 doubles, like JavaScript numbers, so
    the results are identical; datasim's node adapter runs the original JavaScript to prove it."""
    result = math.floor(100 - (price / base) * 100)
    return None if result > 90 or result < 5 else result


def js_price_from_percent(base: int, percent: int) -> int:
    """Port of the matching price helper."""
    price = math.floor(base * ((100 - percent) / 100))
    return math.floor(price / 50) * 50
