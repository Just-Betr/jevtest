"""The Android agent's screen dump (uiautomator-style XML) as a `Screen`. Pure: no device, no I/O.

The agent sends the view tree with each node's class, text, bounds and state. jevtest keeps the elements a tester
could care about, drops system UI and anything off screen or invisible, and names each kind in its own words.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping

from jevtest.domain.screen import Element, Screen
from jevtest.domain.words import one_line

KINDS: Mapping[str, str] = {
    "EditText": "text_field",
    "AutoCompleteTextView": "text_field",
    "Button": "button",
    "ImageButton": "button",
    "CheckBox": "checkbox",
    "Switch": "switch",
    "ToggleButton": "switch",
    "RadioButton": "radio",
    "ImageView": "image",
    "TextView": "text",
    "SeekBar": "slider",
    "ProgressBar": "progress",
    "Spinner": "dropdown",
    "WebView": "webview",
    "RecyclerView": "list",
    "ListView": "list",
    "ScrollView": "scroll_view",
}
"""Android view classes, in jevtest's words."""

EDITABLE = frozenset({"EditText", "AutoCompleteTextView"})
TOGGLES = frozenset({"CheckBox", "Switch", "RadioButton", "ToggleButton", "SwitchCompat", "SwitchMaterial"})
"""Always report on/off for these: WebView checkboxes come through with checkable="false"."""

PLAIN_VIEWS = frozenset({"View", ""})
"""Flutter and Compose render most widgets as plain Views."""

SYSTEM_UI = "com.android.systemui"
FRAMEWORK_ID = "android:id/"
MIN_SIZE = 2
"""Pixels: anything thinner is off screen or invisible."""

BOUNDS = re.compile(r"-?\d+")
Attributes = Mapping[str, str]
Size = Callable[[int], tuple[int, int]]
"""The screen's size in pixels for a rotation (0-3, as the agent reports it)."""


def parse_screen(xml: str, size: Size) -> Screen:
    """The screen the XML describes, at the size its rotation gives."""
    root = ET.fromstring(xml)
    width, height = size(int(root.get("rotation", "0")))
    return Screen(
        width=width,
        height=height,
        elements=tuple(parse_hierarchy(root, width, height)),
        keyboard_visible=keyboard_up(root),
        keyboard_top=int(root.get("ime-top", "0")),
    )


def keyboard_up(root: ET.Element) -> bool:
    """Whether the on-screen keyboard is showing."""
    return root.get("ime") == "true"


def typing_ready(xml: str) -> bool:
    """Whether typed keys will land: the keyboard is up and a text field has focus.

    Read from the whole tree, not the screen's elements: with the keyboard up in landscape, a web page can scroll
    the focused field until nothing of it is left on screen, and it still takes the keys.
    """
    root = ET.fromstring(xml)
    return keyboard_up(root) and any(
        node.get("focused") == "true" and node.get("class", "").split(".")[-1] in EDITABLE for node in root.iter("node")
    )


def has_empty_webview(xml: str) -> bool:
    """Whether a WebView is on screen with no content yet (its page reaches the tree a moment later)."""
    return any(
        node.get("class") == "android.webkit.WebView" and node.find(".//node") is None
        for node in ET.fromstring(xml).iter("node")
    )


def parse_hierarchy(root: ET.Element, width: int, height: int) -> list[Element]:
    """The elements a tester cares about, in document order."""
    found = (_element(node.attrib, width, height) for node in root.iter("node"))
    return [el for el in found if el is not None]


def _element(a: Attributes, width: int, height: int) -> Element | None:
    """One node of the tree, or None if it's system UI, off screen, or carries nothing a test could use."""
    bounds = None if a.get("package") == SYSTEM_UI else _bounds(a.get("bounds", ""), width, height)
    if bounds is None:
        return None
    cls = a.get("class", "").split(".")[-1]
    text = a.get("text", "")
    editable = cls in EDITABLE
    clickable = any(_true(a, name) for name in ("clickable", "long-clickable"))
    checkable = _true(a, "checkable") or cls in TOGGLES
    label, rid = _label(a), _resource_id(a)
    if not any((label, editable, clickable, checkable, _true(a, "scrollable"), rid)):
        return None  # nothing a test could find it by or do with it
    return Element(
        kind=_kind(cls, clickable=clickable, password=editable and _true(a, "password")),
        text=one_line(label),
        parts=_parts(a),
        hint=a.get("hint", ""),
        resource_id=rid,
        bounds=bounds,
        enabled=a.get("enabled", "true") == "true",
        editable=editable,
        clickable=clickable,
        scrollable=_true(a, "scrollable"),
        focused=_true(a, "focused"),
        checked=_true(a, "checked") if checkable else None,
        selected=_true(a, "selected"),
        value=text if editable else "",
    )


def _true(a: Attributes, name: str) -> bool:
    return a.get(name) == "true"


def _bounds(written: str, width: int, height: int) -> tuple[int, int, int, int] | None:
    """``[x1,y1][x2,y2]`` clipped to the screen, or None when too little of it is on screen."""
    numbers = BOUNDS.findall(written)
    if len(numbers) != len("xyxy"):
        return None
    x1, y1, x2, y2 = map(int, numbers)
    x1, y1, x2, y2 = max(x1, 0), max(y1, 0), min(x2, width), min(y2, height)
    if x2 - x1 < MIN_SIZE or y2 - y1 < MIN_SIZE:
        return None
    return x1, y1, x2, y2


def _label(a: Attributes) -> str:
    """What the element says, as written: its text, its description, or both when they differ."""
    text, desc = a.get("text", ""), a.get("content-desc", "")
    return f"{text} ({desc})" if text and desc and text != desc else (text or desc)


def _parts(a: Attributes) -> tuple[str, ...]:
    """The text and the description on their own, when the element shows both (``Go (Go now)``)."""
    text, desc = a.get("text", ""), a.get("content-desc", "")
    return (one_line(text), one_line(desc)) if text and desc and text != desc else ()


def _resource_id(a: Attributes) -> str:
    """The app's own id for the element; framework ids (``android:id/...``) are layout, not meaning."""
    full = a.get("resource-id", "")
    return "" if full.startswith(FRAMEWORK_ID) else full.split("/")[-1]


def _kind(cls: str, *, clickable: bool, password: bool) -> str:
    """What an Android view class is, in jevtest's words."""
    if password:
        return "password_field"
    if cls in KINDS:
        return KINDS[cls]
    if cls in PLAIN_VIEWS:
        return "button" if clickable else "text"
    return cls.lower()
