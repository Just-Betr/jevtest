"""The Android agent's screen dump (uiautomator-style XML) as a `Screen`. Pure: no device, no I/O.

The agent sends the view tree with each node's class, text, bounds and state. jevtest keeps the elements a tester
could care about, drops system UI and anything off screen or invisible, and names each kind in its own words.
"""

from __future__ import annotations

import dataclasses
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping

from jevtest.domain.screen import Bounds, Element, Screen
from jevtest.domain.words import lines, one_line

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
        system_bars=tuple(_bars(root.get("bars", ""))),
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
    labels = _field_labels(root)
    label_nodes = set(labels.values())
    bars = _bars(root.get("bars", ""))
    found = (
        _element(node.attrib, width, height, field_label=labels[node].get("text", "") if node in labels else "")
        for node in root.iter("node")
        if node not in label_nodes
    )
    return [dataclasses.replace(el, bounds=_outside(el.bounds, bars)) for el in found if el is not None]


def _bars(written: str) -> list[Bounds]:
    """The system's windows over the app (``l,t,r,b;...``), like the status bar: a touch there is the system's."""
    return [(x1, y1, x2, y2) for x1, y1, x2, y2 in (map(int, bar.split(",")) for bar in written.split(";") if bar)]


def _outside(bounds: Bounds, bars: list[Bounds]) -> Bounds:
    """The part of `bounds` a finger reaches: less any edge a bar lies across.

    An app drawn edge to edge puts elements under the status bar, and a tap on an element's middle there reaches the
    status bar, not the app (measured: a switch from y 105 to 176, under a status bar to y 142, didn't switch). An
    element wholly under a bar keeps its bounds: it can still be read, and a touch on it fails as a person's would.
    """
    x1, y1, x2, y2 = bounds
    for bx1, by1, bx2, by2 in bars:
        if bx1 <= x1 and x2 <= bx2:  # across its width: trim its top or its bottom
            if by1 <= y1 < by2 < y2:
                y1 = by2
            elif y1 < by1 < y2 <= by2:
                y2 = by1
        elif by1 <= y1 and y2 <= by2:  # down its height (a side bar in landscape): trim its left or its right
            if bx1 <= x1 < bx2 < x2:
                x1 = bx2
            elif x1 < bx1 < x2 <= bx2:
                x2 = bx1
    return x1, y1, x2, y2


def _field_labels(root: ET.Element) -> dict[ET.Element, ET.Element]:
    """Each Compose text field's label: the first text inside a field with no hint of its own.

    Compose reports a TextField as an EditText with no hint, holding its label as a TextView, whether the field is
    empty or filled (measured with Material 3 on Android 17). The label is the field's hint, as a View's is.
    """
    return {
        field: label
        for field in root.iter("node")
        if field.get("class", "").split(".")[-1] in EDITABLE and not field.get("hint")
        if (label := next((n for n in field.iter("node") if n is not field and n.get("text")), None)) is not None
    }


def _element(a: Attributes, width: int, height: int, field_label: str = "") -> Element | None:
    """One node of the tree, or None if it's system UI, off screen, or carries nothing a test could use.

    `field_label` is a text field's label from inside it (`_field_labels`): its hint.
    """
    bounds = None if a.get("package") == SYSTEM_UI else _bounds(a.get("bounds", ""), width, height)
    if bounds is None:
        return None
    cls = a.get("class", "").split(".")[-1]
    text = a.get("text", "")
    editable = cls in EDITABLE
    clickable = any(_true(a, name) for name in ("clickable", "long-clickable"))
    checkable = _true(a, "checkable") or cls in TOGGLES
    label, rid = _label(a), _resource_id(a)
    slider = KINDS.get(cls) == "slider"
    if not any((label, editable, clickable, checkable, slider, _true(a, "scrollable"), rid)):
        return None  # nothing a test could find it by or do with it (a slider can be swiped, labelled or not)
    return Element(
        kind=_kind(cls, clickable=clickable, password=editable and _true(a, "password")),
        text=one_line(label),
        parts=_parts(a),
        hint=a.get("hint", "") or field_label,
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
        position=float(a["position"]) if slider and "position" in a else None,
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
    """The texts an element is made of, each on its own.

    Its text and description when it shows both (``Go (Go now)``), and each line of either when it has several.
    """
    text, desc = a.get("text", ""), a.get("content-desc", "")
    both = (one_line(text), one_line(desc)) if text and desc and text != desc else ()
    return tuple(dict.fromkeys((*both, *lines(text), *lines(desc))))


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
