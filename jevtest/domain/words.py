"""Numbers in words, as jevtest's messages write them."""

from __future__ import annotations


def plural(n: int, noun: str, many: str | None = None) -> str:
    """``1 file``, ``2 files``; `many` for a noun that doesn't just add an s (``entry``, ``entries``)."""
    return f"{n} {noun if n == 1 else many or noun + 's'}"


def ordinal(n: int) -> str:
    """``1st``, ``2nd``, ``3rd``, ``4th``, ... ``11th``, ``21st``."""
    teens = range(10, 21)  # 11th, 12th, 13th: never 11st
    suffix = "th" if n % 100 in teens else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"
