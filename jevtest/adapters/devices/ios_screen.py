"""The iOS agent's screen dump (XCUITest's element tree, as JSON) as a `Screen`. Pure: no device, no I/O.

The agent reports every element with its XCUITest type (in snake_case), label, value and frame, in points.
jevtest drops the app itself, scroll indicators, empty containers and anything off screen, and keeps one of each
wrapper-and-child pair XCUITest reports twice.
"""

from __future__ import annotations

import re
from typing import NotRequired, TypedDict

from jevtest.domain.screen import Element, Screen

CONTAINERS = frozenset({"other", "navigation_bar", "tab_bar", "list", "scroll_view", "webview"})
"""Container types that only matter when they carry a label or identifier."""

HIDDEN_VALUE = frozenset({"switch", "password_field"})
"""Types whose value is shown some other way: a switch's as on/off, a secure field's is bullets."""

TOUCHABLE = frozenset({"button", "cell", "link", "switch", "tab", "menu_item", "segmented_control", "dropdown"})
EDITABLE = frozenset({"text_field", "password_field", "text_area"})
SWITCH_ON = frozenset({"1", "true"})
SCROLL_INDICATOR = re.compile(r"^(Vertical|Horizontal) scroll bar\b")
MIN_SIZE = 2
"""Points: anything thinner is off screen or invisible."""


class AgentElement(TypedDict):
    """One element as the agent sends it."""

    type: str
    x: float
    y: float
    w: float
    h: float
    label: NotRequired[str]
    value: NotRequired[str | None]
    placeholder: NotRequired[str]
    identifier: NotRequired[str]
    enabled: NotRequired[bool]
    focused: NotRequired[bool]
    selected: NotRequired[bool]


class AgentTree(TypedDict):
    """The agent's ``/tree`` reply."""

    width: float
    height: float
    elements: list[AgentElement]
    keyboard: NotRequired[bool]
    keyboard_top: NotRequired[float]


def parse_tree(tree: AgentTree) -> Screen:
    """The elements a tester cares about, duplicates removed, in the agent's order."""
    width, height = int(tree["width"]), int(tree["height"])
    elements: dict[tuple[str, str, tuple[int, int, int, int]], Element] = {}
    for raw in tree["elements"]:
        el = _element(raw, width, height)
        if el is not None:  # XCUITest often reports a wrapper and its child: keep the first
            elements.setdefault((el.kind, el.text, el.bounds), el)
    return Screen(width=width, height=height, elements=tuple(elements.values()),
                  keyboard_visible=tree.get("keyboard", False), keyboard_top=int(tree.get("keyboard_top", 0)))


def _element(raw: AgentElement, width: int, height: int) -> Element | None:
    """One element, or None if it's the app itself, a scroll bar, off screen, or an empty container."""
    kind, label = raw["type"], raw.get("label", "")
    bounds = _bounds(raw, width, height)
    if bounds is None or kind == "application" or SCROLL_INDICATOR.match(label):
        return None
    value = _value(kind, raw)
    text = _text(kind, label, value)
    identifier = raw.get("identifier", "")
    if kind in CONTAINERS and not (text or identifier):
        return None
    editable = kind in EDITABLE
    return Element(
        kind="text" if kind == "other" else kind, text=text, hint=raw.get("placeholder", ""),
        value=value if editable else "", resource_id=identifier, bounds=bounds,
        enabled=raw.get("enabled", True), editable=editable, clickable=kind in TOUCHABLE,
        focused=editable and raw.get("focused", False),  # web views mark everything focused
        selected=raw.get("selected", False), checked=_checked(kind, value),
    )


def _value(kind: str, raw: AgentElement) -> str:
    """The element's value; an empty field reports its placeholder as its value, which isn't one."""
    value = raw.get("value") or ""
    return "" if kind in EDITABLE and value == raw.get("placeholder", "") else value


def _checked(kind: str, value: str) -> bool | None:
    """A switch's state; None for anything else."""
    return value in SWITCH_ON if kind == "switch" else None


def _bounds(raw: AgentElement, width: int, height: int) -> tuple[int, int, int, int] | None:
    """The frame clipped to the screen, or None when too little of it is on screen."""
    x1, y1 = max(int(raw["x"]), 0), max(int(raw["y"]), 0)
    x2, y2 = min(int(raw["x"] + raw["w"]), width), min(int(raw["y"] + raw["h"]), height)
    if x2 - x1 < MIN_SIZE or y2 - y1 < MIN_SIZE:
        return None
    return x1, y1, x2, y2


def _text(kind: str, label: str, value: str) -> str:
    """The label, plus any value that says something the label doesn't (a web <select>'s choice, a field's text)."""
    shown = value.strip() and kind not in HIDDEN_VALUE and value.strip() != label.strip()
    text = (f"{label}: {value}" if label else value) if shown else label
    return " ".join(text.split())
