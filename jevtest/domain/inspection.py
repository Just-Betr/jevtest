"""What was on a screen, as steps name it: for people and agents writing tests, beside each screenshot."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass

from .screen import Bounds, Element, Screen

HEADER = "# What was on the screen, as steps name it: any one name finds the element (tap:, see:, into:)."
SHARED = "a name more than one element has: a step with it may find either; a name of its own finds only this one"


@dataclass(frozen=True)
class ElementNotes:
    """One element as a test names it.

    Attributes:
        kind: What it is: button, text_field, text, switch, ...
        names: Every text a step can find it by, in order (`Element.names`), as output shows them.
        shared: How many elements on the screen have each of its names that more than one has.
        bounds: Where it is, in the device's own units (`ScreenNotes.width` by `ScreenNotes.height`).
        state: What it is now, where that's anything but the usual: ``checked``, ``focused``, ``selected``,
            ``editable``, ``scrollable``, ``disabled``.
    """

    kind: str
    names: tuple[str, ...]
    shared: dict[str, int]
    bounds: Bounds
    state: tuple[str, ...]

    @property
    def find_by(self) -> str:
        """The name a step should use: the first only this element has, else its first; "" when it has none."""
        own = [n for n in self.names if n not in self.shared]
        return own[0] if own else (self.names[0] if self.names else "")


@dataclass(frozen=True)
class ScreenNotes:
    """A screen as tests name it: its size, whether the keyboard is up, and its elements in reading order."""

    width: int
    height: int
    keyboard_visible: bool
    elements: tuple[ElementNotes, ...]

    def text(self) -> str:
        """One line per element: its kind, then each name a step can find it by, a shared one with how many share it."""
        lines = [HEADER]
        for el in self.elements:
            names = " | ".join(f"'{n}'" + (f" ({el.shared[n]} on screen)" if n in el.shared else "") for n in el.names)
            lines.append(f"{el.kind:<14} {names or '(no name)'}")
        return "\n".join(lines) + "\n"


def notes(screen: Screen, mask: Callable[[str], str]) -> ScreenNotes:
    """`screen` as tests name it, each text passed through `mask` (a ``${NAME}`` value written as its name)."""
    named = [tuple(mask(n) for n in el.names()) for el in screen.elements]
    counts = Counter(n for names in named for n in set(names))
    return ScreenNotes(
        screen.width,
        screen.height,
        screen.keyboard_visible,
        tuple(
            ElementNotes(el.kind, names, {n: counts[n] for n in names if counts[n] > 1}, el.bounds, _state(el))
            for el, names in zip(screen.elements, named, strict=True)
        ),
    )


def _state(el: Element) -> tuple[str, ...]:
    flags = {
        "checked": el.checked is True,
        "focused": el.focused,
        "selected": el.selected,
        "editable": el.editable,
        "scrollable": el.scrollable,
        "disabled": not el.enabled,
    }
    return tuple(name for name, on in flags.items() if on)
