"""What Jev decided to do next while reaching a `do:` goal.

Each move is its own type, so a move that acts on an element always has one. `describe()` is also what Jev is
shown as the actions already taken, so its wording is part of every recorded decision: change it and recorded
lockfiles no longer match.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from .kinds import Direction, Gesture
from .screen import Element


@dataclass(frozen=True)
class Finished:
    """The goal is reached."""

    def describe(self) -> str:
        """``done``."""
        return "done"


@dataclass(frozen=True)
class Impossible:
    """The goal can't be reached from this screen."""

    def describe(self) -> str:
        """``impossible``."""
        return "impossible"


@dataclass(frozen=True)
class WaitForScreen:
    """The screen is still loading: wait for it to change."""

    def describe(self) -> str:
        """``wait``."""
        return "wait"


@dataclass(frozen=True)
class GoBack:
    """Go back, or dismiss a dialog or menu."""

    def describe(self) -> str:
        """``back``."""
        return "back"


@dataclass(frozen=True)
class PressEnter:
    """Press Enter to submit what was typed."""

    def describe(self) -> str:
        """``press enter``."""
        return "press enter"


@dataclass(frozen=True)
class CloseKeyboard:
    """Close the on-screen keyboard."""

    def describe(self) -> str:
        """``hide keyboard``."""
        return "hide keyboard"


@dataclass(frozen=True)
class ScrollPage:
    """Scroll the page to reveal more content."""

    direction: Direction

    def describe(self) -> str:
        """E.g. ``scroll down``."""
        return f"scroll {self.direction}"


@dataclass(frozen=True)
class TouchElement:
    """Tap, double-tap or long-press an element."""

    gesture: Gesture
    element: Element

    def describe(self) -> str:
        """E.g. ``tap button 'Sign in'``."""
        return f"{self.gesture} {self.element.label()}"


@dataclass(frozen=True)
class SwipeElement:
    """Swipe on one element, e.g. to delete a list row."""

    direction: Direction
    element: Element

    def describe(self) -> str:
        """E.g. ``swipe_left cell 'Item 3'``."""
        return f"swipe_{self.direction} {self.element.label()}"


@dataclass(frozen=True)
class TypeInto:
    """Type one of the goal's quoted values into a field."""

    element: Element
    text: str

    def describe(self) -> str:
        """E.g. ``type "${EMAIL}" into text_field 'Email'``."""
        return f'type "{self.text}" into {self.element.label()}'


@dataclass(frozen=True)
class ClearField:
    """Erase a text field."""

    element: Element

    def describe(self) -> str:
        """E.g. ``clear text_field 'Email'``."""
        return f"clear {self.element.label()}"


Move = (
    Finished | Impossible | WaitForScreen | GoBack | PressEnter | CloseKeyboard | ScrollPage | TouchElement
    | SwipeElement | TypeInto | ClearField
)
"""Every move Jev can pick."""


@dataclass(frozen=True)
class Decision:
    """A move, with how sure Jev was of it.

    Attributes:
        move: What to do.
        confidence: Jev's confidence in the chosen action (0 to 1).
        probabilities: Jev's probability for each action it was offered.
    """

    move: Move
    confidence: float
    probabilities: Mapping[str, float] = field(default_factory=dict)
