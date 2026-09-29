"""Single values from a test file, checked exactly: no conversions, and an error that says what to write instead.

Each function takes the value as YAML read it and `what` (how the error names it), and either returns it as its
exact type or raises `TestFileError`.
"""

from __future__ import annotations

import math
from enum import StrEnum
from typing import TypeVar

from jevtest.domain.failures import TestFileError
from jevtest.domain.settings import JEV_VERSION, LIMITS, WHOLE, Settings
from jevtest.domain.words import number_text

E = TypeVar("E", bound=StrEnum)

KINDS: dict[type, str] = {
    bool: "true/false",
    int: "a number",
    float: "a number",
    str: "text",
    list: "a list",
    dict: "a mapping",
}


def kind(value: object) -> str:
    """What a YAML value is, in words: ``text``, ``a number``, ``nothing``, ..."""
    if value is None:
        return "nothing"
    return KINDS.get(type(value), type(value).__name__)


def _looks_numeric(text: str) -> bool:
    try:
        float(text)
    except ValueError:
        return False
    return True


def number(value: object, what: str, minimum: float = 0, maximum: float = math.inf, unit: str = "") -> float:
    """A finite number from `minimum` to `maximum` (in `unit`, for the message). ``"2"`` is text, not a number.

    Raises:
        TestFileError: It isn't a number, isn't finite, or is outside those limits.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        hint = " (remove the quotes)" if isinstance(value, str) and _looks_numeric(value) else ""
        raise TestFileError(f"{what} must be a number, got {value!r}{hint}")
    if not math.isfinite(value):
        raise TestFileError(f"{what} must be a finite number, got {value}")
    if not minimum <= value <= maximum:
        low, high = number_text(minimum), number_text(maximum) if maximum != math.inf else ""
        limits = f"from {low} to {high}" if high else f"at least {low}"
        raise TestFileError(f"{what} must be {limits}{f' {unit}' if unit else ''}, got {number_text(value)}")
    return float(value)


def text(value: object, what: str) -> str:
    """Non-empty text without leading or trailing spaces. ``42`` is a number, not text.

    Raises:
        TestFileError: It isn't text, is empty, or has spaces at either end.
    """
    if not isinstance(value, str):
        hint = f" (to use {value!r} as text, put it in quotes)" if isinstance(value, int | float | bool) else ""
        raise TestFileError(f"{what} needs text, got {kind(value)}{hint}")
    if value.strip() == "" or value != value.strip():
        raise TestFileError(f"{what} needs text without leading or trailing spaces, got {value!r}")
    try:
        value.encode()
    except UnicodeEncodeError:
        raise TestFileError(f"{what} has characters that aren't valid Unicode: {value!r}") from None
    if "\x00" in value:
        raise TestFileError(f"{what} has a null character in it: {value!r}")
    return value


def choice(value: object, enum: type[E], what: str) -> E:
    """One of an enum's values, exactly as written (``down``, not ``DOWN``).

    Raises:
        TestFileError: It isn't one of them.
    """
    allowed = [e.value for e in enum]
    if value not in allowed:
        raise TestFileError(f"{what} must be one of {', '.join(allowed)}; got {value!r}")
    return enum(value)


def on_off(value: object, what: str) -> bool:
    """On or off: YAML's ``on``/``off`` or ``true``/``false``.

    Raises:
        TestFileError: It's anything else.
    """
    if not isinstance(value, bool):
        raise TestFileError(f"{what} must be on or off (true or false), got {value!r}")
    return value


def setting(name: str, value: object) -> float:
    """A numeric setting within its limits; a count must be a whole number.

    Raises:
        TestFileError: It isn't a number (a whole number for counts) or is outside its limits.
    """
    low, high = LIMITS[name]
    if name in WHOLE:
        if isinstance(value, bool) or not isinstance(value, int):
            raise TestFileError(
                f"`{name}` must be a whole number from {number_text(low)} to {number_text(high)}, got {value!r}"
            )
        if not low <= value <= high:
            raise TestFileError(f"`{name}` must be from {number_text(low)} to {number_text(high)}, got {value}")
        return value
    return number(value, f"`{name}`", minimum=low, maximum=high)


def coherent(settings: Settings) -> Settings:
    """The settings, if they make sense together.

    Raises:
        TestFileError: `interval` is longer than `timeout`: a wait would check once, yet say it waited.
    """
    if settings.interval > settings.timeout:
        raise TestFileError(
            f"`interval` ({number_text(settings.interval)}s) is longer than `timeout` "
            f"({number_text(settings.timeout)}s): a step would check "
            "only once"
        )
    return settings


def model(value: object) -> str:
    """A pinned Jev version, like ``jev-1.13.0``.

    Raises:
        TestFileError: It isn't text, or isn't a pinned Jev version.
    """
    name = text(value, "`model`")
    if not JEV_VERSION.fullmatch(name):
        raise TestFileError(
            f"`model` must be a pinned Jev version like jev-1.13.0, got {name!r}: jevtest uses Jev only, and an "
            "alias such as jev-latest moves when a new Jev ships"
        )
    return name
