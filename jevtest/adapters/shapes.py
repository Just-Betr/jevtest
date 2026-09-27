"""The shapes parsed YAML and JSON come in, as type guards.

``isinstance(value, dict)`` says nothing about what's inside; these say what a parser can actually produce, so
the checker follows every value from the parser to where it becomes a domain type.
"""

from __future__ import annotations

from typing import TypeGuard


def is_mapping(value: object) -> TypeGuard[dict[object, object]]:
    """A YAML mapping: its keys can be any scalar YAML reads (text, numbers, true/false, dates)."""
    return isinstance(value, dict)


def is_json_object(value: object) -> TypeGuard[dict[str, object]]:
    """A JSON object (or plist dictionary): its keys are always text."""
    return isinstance(value, dict)


def is_list(value: object) -> TypeGuard[list[object]]:
    """A YAML sequence, JSON array or plist array."""
    return isinstance(value, list)


def objects_by_key(value: object) -> dict[str, dict[str, object]] | None:
    """A JSON object whose every value is an object (e.g. answers by question id), or None if it isn't one."""
    if not is_json_object(value):
        return None
    found: dict[str, dict[str, object]] = {}
    for key, item in value.items():
        if not is_json_object(item):
            return None
        found[key] = item
    return found
