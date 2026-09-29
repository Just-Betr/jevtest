"""Words as jevtest writes and compares them: numbers in words, and text in one Unicode encoding."""

from __future__ import annotations

import unicodedata


def nfc(text: str) -> str:
    """`text` with each letter in one encoding (NFC): an é written as an e and an accent becomes the one-character é.

    Apps and keyboards write both; compared this way, they are the same text.
    """
    return unicodedata.normalize("NFC", text)


def plural(n: int, noun: str, many: str | None = None) -> str:
    """``1 file``, ``2 files``; `many` for a noun that doesn't just add an s (``entry``, ``entries``)."""
    return f"{n} {noun if n == 1 else many or noun + 's'}"


def ordinal(n: int) -> str:
    """``1st``, ``2nd``, ``3rd``, ``4th``, ... ``11th``, ``21st``."""
    teens = range(10, 21)  # 11th, 12th, 13th: never 11st
    suffix = "th" if n % 100 in teens else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"
