"""The iOS agent's screen dump (XCUITest's element tree, as JSON) as a `Screen`. Pure: no device, no I/O.

The agent reports every element with its XCUITest type (in snake_case), label, value and frame, in points.
jevtest drops the app itself, scroll indicators, empty containers and anything off screen, and keeps one of each
wrapper-and-child pair XCUITest reports twice.
"""

from __future__ import annotations

import dataclasses
import re
from typing import NotRequired, TypedDict

from jevtest.domain.screen import Element, Screen
from jevtest.domain.words import one_line

CONTAINERS = frozenset({"other", "navigation_bar", "tab_bar", "list", "scroll_view", "webview"})
"""Container types that only matter when they carry a label or identifier."""

SCROLLERS = frozenset({"list", "scroll_view"})
"""Container types whose content scrolls: a swipe or scroll along one crosses it (`Screen.scrollers`)."""

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
    position: NotRequired[float]


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
    scrollers = (_bounds(raw, width, height) for raw in tree["elements"] if raw["type"] in SCROLLERS)
    return Screen(
        width=width,
        height=height,
        elements=_one_switch_per_toggle(tuple(elements.values())),
        scrollers=tuple(dict.fromkeys(b for b in scrollers if b is not None)),
        keyboard_visible=tree.get("keyboard", False),
        keyboard_top=int(tree.get("keyboard_top", 0)),
    )


def _one_switch_per_toggle(elements: tuple[Element, ...]) -> tuple[Element, ...]:
    """A SwiftUI Toggle reported once, as the labelled switch where its knob is.

    XCUITest reports a SwiftUI Toggle as a labelled switch across its whole row, and inside it the unlabelled
    switch a finger turns: a tap on the row's middle does nothing (measured on iOS 26.5). UIKit reports a switch
    beside its label as just the knob, which a tap on the label's switch already reaches.
    """
    knob_of = {
        i: knob
        for i, row in enumerate(elements)
        if row.kind == "switch"
        and row.text
        and (knob := next((k for k in elements if _is_knob_in(k, row)), None)) is not None
    }
    knobs = set(knob_of.values())
    return tuple(
        dataclasses.replace(el, bounds=knob_of[i].bounds) if i in knob_of else el
        for i, el in enumerate(elements)
        if el not in knobs
    )


def _is_knob_in(knob: Element, row: Element) -> bool:
    """Whether `knob` is an unlabelled switch within `row`'s bounds."""
    (x1, y1, x2, y2), (rx1, ry1, rx2, ry2) = knob.bounds, row.bounds
    return knob.kind == "switch" and not knob.text and rx1 <= x1 and ry1 <= y1 and x2 <= rx2 and y2 <= ry2


def _element(raw: AgentElement, width: int, height: int) -> Element | None:
    """One element, or None if it's the app itself, a scroll bar, off screen, or an empty container."""
    kind, label = raw["type"], raw.get("label", "")
    bounds = _bounds(raw, width, height)
    if bounds is None or kind == "application" or SCROLL_INDICATOR.match(label):
        return None
    value = _value(kind, raw)
    text, parts = _text(kind, label, value)
    identifier = raw.get("identifier", "")
    if kind in CONTAINERS and not (text or identifier):
        return None
    editable = kind in EDITABLE
    return Element(
        kind="text" if kind == "other" else kind,
        text=text,
        parts=parts,
        hint=raw.get("placeholder", ""),
        value=value if editable else "",
        resource_id=identifier,
        bounds=bounds,
        enabled=raw.get("enabled", True),
        editable=editable,
        clickable=kind in TOUCHABLE,
        focused=editable and raw.get("focused", False),  # web views mark everything focused
        selected=raw.get("selected", False),
        checked=_checked(kind, value),
        position=raw.get("position"),
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


def _text(kind: str, label: str, value: str) -> tuple[str, tuple[str, ...]]:
    """The label, plus any value that says something the label doesn't (a web <select>'s choice, a field's text).

    Returns the text as shown (``Email: a@b.c``) and, when it joins a label and a value, each on its own.
    """
    shown = value.strip() and kind not in HIDDEN_VALUE and value.strip() != label.strip()
    if not shown:
        return one_line(label), ()
    if not label:
        return one_line(value), ()
    return one_line(f"{label}: {value}"), (one_line(label), one_line(value))
