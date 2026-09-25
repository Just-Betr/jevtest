"""The screen as Jev sees it: a flat list of on-screen elements, as text."""

from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class Element:
    kind: str                 # button, text_field, text, switch, checkbox, image, cell, ...
    text: str = ""            # visible label / content description / value
    hint: str = ""            # placeholder or hint text
    resource_id: str = ""     # android resource-id / iOS accessibilityIdentifier
    bounds: tuple[int, int, int, int] = (0, 0, 0, 0)  # x1, y1, x2, y2 in driver units
    enabled: bool = True
    editable: bool = False
    clickable: bool = False
    scrollable: bool = False
    focused: bool = False
    checked: bool | None = None   # None = not a checkable element
    selected: bool = False
    id: str = ""              # e1, e2, ... assigned by Screen

    @property
    def center(self) -> tuple[int, int]:
        x1, y1, x2, y2 = self.bounds
        return (x1 + x2) // 2, (y1 + y2) // 2

    def label(self) -> str:
        """Short human description, used in option criteria and logs."""
        name = self.text or self.hint or self.resource_id or "(no label)"
        return f"{self.kind} '{name}'"


@dataclass
class Screen:
    width: int
    height: int
    elements: list[Element] = field(default_factory=list)
    keyboard_visible: bool = False

    def __post_init__(self):
        for i, el in enumerate(self.elements, 1):
            el.id = f"e{i}"

    def by_id(self, element_id: str) -> Element:
        return next(el for el in self.elements if el.id == element_id)

    @property
    def editable(self) -> list[Element]:
        return [el for el in self.elements if el.editable]

    def texts(self) -> list[str]:
        return [t for el in self.elements for t in (el.text, el.hint) if t]

    def signature(self) -> str:
        """Identical for identical screens: lets callers skip re-asking Jev about an unchanged screen."""
        return json.dumps([self.to_state(), self.keyboard_visible])

    def region(self, el: Element) -> str:
        # Jev is weak with raw numbers, so give it words.
        cx, cy = el.center
        v = "top" if cy < self.height / 3 else "bottom" if cy > self.height * 2 / 3 else "middle"
        h = "left" if cx < self.width / 3 else "right" if cx > self.width * 2 / 3 else "center"
        return f"{v}-{h}"

    def to_state(self) -> list[dict]:
        out = []
        for el in self.elements:
            d = {"id": el.id, "type": el.kind}
            if el.text:
                d["text"] = el.text
            if el.hint:
                d["hint"] = el.hint
            if el.resource_id:
                d["resource_id"] = el.resource_id
            d["position"] = self.region(el)
            if not el.enabled:
                d["enabled"] = False
            if el.checked is not None:
                d["checked"] = el.checked
            if el.focused:
                d["focused"] = True
            if el.selected:
                d["selected"] = True
            if el.scrollable:
                d["scrollable"] = True
            out.append(d)
        return out
