"""Words as jevtest writes and compares them: numbers, and text in one Unicode encoding."""

from __future__ import annotations

import unicodedata


def nfc(text: str) -> str:
    """`text` with each letter in one encoding (NFC): an é written as an e and an accent becomes the one-character é.

    Apps and keyboards write both; compared this way, they are the same text.
    """
    return unicodedata.normalize("NFC", text)


def number_text(value: float) -> str:
    """A number as a person wrote it, never rounded: ``2`` for 2.0, ``151.2093`` for 151.2093."""
    return str(int(value)) if float(value).is_integer() else repr(float(value))


def one_line(text: str) -> str:
    """`text` as jevtest reads it from a screen.

    Each run of spaces, tabs and line breaks becomes one space, with none at the ends.
    """
    return " ".join(text.split())


def lines(text: str) -> tuple[str, ...]:
    """Each line of `text` that says something, as `one_line` reads it; none when it has only one.

    An app that joins several texts into one element separates them with line breaks: Flutter reports a tab as
    ``Form`` and ``Tab 2 of 3``, one line each.
    """
    found = tuple(one_line(line) for line in text.splitlines() if line.strip())
    return found if len(found) > 1 else ()


def plural(n: int, noun: str, many: str | None = None) -> str:
    """``1 file``, ``2 files``; `many` for a noun that doesn't just add an s (``entry``, ``entries``)."""
    return f"{n} {noun if n == 1 else many or noun + 's'}"


def ordinal(n: int) -> str:
    """``1st``, ``2nd``, ``3rd``, ``4th``, ... ``11th``, ``21st``."""
    teens = range(10, 21)  # 11th, 12th, 13th: never 11st
    suffix = "th" if n % 100 in teens else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"
