"""The screen of the app under test, as a flat list of elements."""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Sequence
from dataclasses import dataclass

from .words import nfc

Bounds = tuple[int, int, int, int]
"""An element's rectangle: left, top, right, bottom, in the device's own units."""

Point = tuple[int, int]
"""A point on the screen, in the device's own units."""


BETWEEN_WORDS = re.compile(r"(?<=\S)\s+(?=\S)")


STRAIGHT_QUOTES = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"'})
"""Curly quotes as the straight ones a keyboard types. The permission prompts write them curly (measured: Android's
\u201cDon\u2019t allow\u201d, iOS's \u201cDon\u2019t Allow\u201d); a test file says `tap: Don't allow`."""


def folded(text: str) -> str:
    """`text` as matching compares it: case ignored, each letter in one encoding (NFC), and quotes straight.

    So an é typed as an e and an accent matches an é stored as one character, and Don't matches Don\u2019t.
    """
    return nfc(text).casefold().translate(STRAIGHT_QUOTES)


def one_space(text: str) -> str:
    """`text` with the spaces and line breaks between its words made one space; its ends as they are."""
    return BETWEEN_WORDS.sub(" ", text)


@dataclass(frozen=True)
class Element:
    """One thing on the screen: a button, a text field, a label, a switch, ...

    Attributes:
        kind: What it is: button, text_field, password_field, text, switch, checkbox, image, cell, ...
        text: What it shows, as one line: its label, content description or value, or several of them together.
        parts: The separate texts `text` is made of, when it's made of more than one: an iOS label and value
            (``Email: a@b.c``), an Android text and content description (``Go (Go now)``). Each can be matched
            on its own.
        hint: Placeholder or hint text.
        resource_id: The Android resource id or iOS accessibility identifier.
        bounds: Where it is on the screen.
        enabled: Whether it responds to input.
        editable: Whether it takes typed text.
        clickable: Whether it responds to a tap.
        scrollable: Whether its content scrolls.
        focused: Whether it has input focus.
        checked: On or off for a switch or checkbox; None for anything that can't be checked.
        selected: Whether it is selected.
        value: A text field's current contents (what clearing it must delete).
        id: Its id on this screen (e1, e2, ...), assigned by `Screen`.
    """

    kind: str
    text: str = ""
    parts: tuple[str, ...] = ()
    hint: str = ""
    resource_id: str = ""
    bounds: Bounds = (0, 0, 0, 0)
    enabled: bool = True
    editable: bool = False
    clickable: bool = False
    scrollable: bool = False
    focused: bool = False
    checked: bool | None = None
    selected: bool = False
    value: str = ""
    id: str = ""

    @property
    def center(self) -> Point:
        """The middle of the element: where a tap lands."""
        x1, y1, x2, y2 = self.bounds
        return (x1 + x2) // 2, (y1 + y2) // 2

    @property
    def end(self) -> Point:
        """A point just inside the right edge: tapping there puts the text cursor after the text."""
        x1, y1, x2, y2 = self.bounds
        return x2 - max(1, min(8, (x2 - x1) // 4)), (y1 + y2) // 2

    def says(self, target: str) -> bool:
        """Whether the element's text, one of its parts, its hint or its id is exactly `target` (`folded`).

        Case is ignored because platforms render the same text in different case (Android shows many buttons in
        capitals; the permission prompt says "Don't allow" on Android and "Don't Allow" on iOS), and curly quotes
        match straight ones, which is all a keyboard types. Nothing else is ignored: the whole text must be the
        target, never a part of a longer text. Spaces and line breaks between the target's
        words count as one space, as they do in the screen's text when it's read.
        """
        wanted = folded(one_space(target))
        return any(folded(name) == wanted for name in self.names())

    def names(self) -> tuple[str, ...]:
        """Every text a test can name the element by, in order: text, parts, hint, id."""
        return tuple(dict.fromkeys(t for t in (self.text, *self.parts, self.hint, self.resource_id) if t))

    def label(self) -> str:
        """A short description for logs and for Jev, e.g. ``button 'Sign in'``."""
        name = self.text or self.hint or self.resource_id or "(no label)"
        return f"{self.kind} '{name}'"


EDGE = 0.08
"""The share of the screen's height, at its top and at its bottom, that phones keep for their own gestures."""


@dataclass(frozen=True)
class Screen:
    """Everything on the screen at one moment.

    Attributes:
        width: Screen width, in the device's own units.
        height: Screen height, in the device's own units.
        elements: The elements, in reading order. Each gets an id (e1, e2, ...) here.
        keyboard_visible: Whether the on-screen keyboard is up.
        keyboard_top: Where the keyboard starts (0 when there is none).
    """

    width: int
    height: int
    elements: tuple[Element, ...] = ()
    keyboard_visible: bool = False
    keyboard_top: int = 0

    def __post_init__(self) -> None:
        """Number the elements. The caller's elements are left as they were: these are copies."""
        numbered = tuple(dataclasses.replace(el, id=f"e{i}") for i, el in enumerate(self.elements, 1))
        object.__setattr__(self, "elements", numbered)

    @property
    def content_height(self) -> int:
        """The height not covered by the keyboard: where page gestures belong."""
        covered = self.keyboard_visible and 0 < self.keyboard_top < self.height
        return self.keyboard_top if covered else self.height

    def under_keyboard(self, el: Element) -> bool:
        """Whether the keyboard is over the point where a tap on the element lands: touching it would hit a key."""
        return el.center[1] >= self.content_height

    def clear_of_edges(self, el: Element) -> bool:
        """Whether the element's middle is clear of the top and bottom `EDGE` of the screen.

        Of the part the keyboard leaves, when it's up. Phones keep those edges for their own gestures, like the home
        swipe.
        """
        margin = self.height * EDGE
        return margin <= el.center[1] <= self.content_height - margin

    def takes_keys(self, el: Element) -> bool:
        """Whether typing now goes into the element without a tap: it has focus and the keyboard is up.

        Tapping it anyway would move the text cursor, and with the keyboard over it, hit a key.
        """
        return el.focused and self.keyboard_visible

    @property
    def editable(self) -> tuple[Element, ...]:
        """The elements that take typed text."""
        return tuple(el for el in self.elements if el.editable)

    def by_id(self, element_id: str) -> Element:
        """The element with this id.

        Raises:
            KeyError: No element has that id.
        """
        for el in self.elements:
            if el.id == element_id:
                return el
        raise KeyError(element_id)

    def shows(self, text: str) -> bool:
        """Whether an element says exactly `text` (see `Element.says`). Never a part of a longer text."""
        return any(el.says(text) for el in self.elements)

    def near(self, text: str) -> tuple[str, ...]:
        """What the screen says that `text` may have meant, for the error when it isn't there. Never matched."""
        return near_names(text, self.elements)

    def region(self, el: Element) -> str:
        """Where an element is, in words (``top-left`` ... ``bottom-right``): Jev reads words better than numbers."""
        cx, cy = el.center
        v = "top" if cy < self.height / 3 else "bottom" if cy > self.height * 2 / 3 else "middle"
        h = "left" if cx < self.width / 3 else "right" if cx > self.width * 2 / 3 else "center"
        return f"{v}-{h}"


NEAR_LIMIT = 5
"""How many near matches an error lists."""


def near_names(target: str, elements: Sequence[Element]) -> tuple[str, ...]:
    """Longer names on these elements that contain `target`, ignoring case.

    Only for error messages, so the user can fix the test file: a near match is never matched.
    """
    wanted = folded(target)
    found = (name for el in elements for name in el.names() if _contains_longer(folded(name), wanted))
    return tuple(dict.fromkeys(found))[:NEAR_LIMIT]


def _contains_longer(name: str, wanted: str) -> bool:
    return wanted in name and name != wanted
