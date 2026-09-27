"""Reading what device tools print (JSON from devicectl, simctl and the agents; plists from Xcode).

Tools change between versions. A shape jevtest doesn't expect is a `DeviceError` that says what was expected,
never a `KeyError` from deep inside a driver.
"""

from __future__ import annotations

import json
import plistlib
from collections.abc import Mapping

from jevtest.adapters.shapes import is_json_object, is_list
from jevtest.domain.failures import DeviceError

Object = Mapping[str, object]
"""A JSON object or plist dictionary, not yet checked."""


def unexpected(what: str, value: object) -> DeviceError:
    """The error for a tool output that isn't the shape jevtest expects."""
    shown = repr(value)
    return DeviceError(f"{what} is not what jevtest expects: {shown[:200]}")


def as_object(value: object, what: str) -> Object:
    """A JSON object or plist dictionary.

    Raises:
        DeviceError: It's anything else.
    """
    if not is_json_object(value):
        raise unexpected(what, value)
    return value


def as_list(value: object, what: str) -> list[object]:
    """A JSON array or plist array.

    Raises:
        DeviceError: It's anything else.
    """
    if not is_list(value):
        raise unexpected(what, value)
    return value


def as_text(value: object, what: str) -> str:
    """A string.

    Raises:
        DeviceError: It's anything else.
    """
    if not isinstance(value, str):
        raise unexpected(what, value)
    return value


def texts(value: object, what: str) -> list[str]:
    """A list of strings.

    Raises:
        DeviceError: It isn't a list, or an item isn't a string.
    """
    return [as_text(item, what) for item in as_list(value, what)]


def dig(value: object, *path: str) -> object:
    """The value at `path` through nested objects, or None where the path stops (an optional field)."""
    for key in path:
        if not is_json_object(value):
            return None
        value = value.get(key)
    return value


def text_at(value: object, *path: str) -> str:
    """The string at `path`, or "" where there is none."""
    found = dig(value, *path)
    return found if isinstance(found, str) else ""


def parse_json(raw: str | bytes, what: str) -> object:
    """Parsed JSON.

    Raises:
        DeviceError: It isn't JSON.
    """
    try:
        parsed: object = json.loads(raw)
    except json.JSONDecodeError:
        raise unexpected(what, raw) from None
    return parsed


def parse_plist(raw: bytes, what: str) -> Object:
    """A parsed plist dictionary.

    Raises:
        DeviceError: It isn't a plist dictionary.
    """
    try:
        parsed: object = plistlib.loads(raw)
    except plistlib.InvalidFileException:
        raise DeviceError(f"{what} can't be read") from None
    return as_object(parsed, what)
