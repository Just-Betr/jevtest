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
from .words import ordinal


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


ElementMove = TouchElement | SwipeElement | TypeInto | ClearField
"""A move on one element of the screen."""

PageMove = WaitForScreen | GoBack | PressEnter | CloseKeyboard | ScrollPage
"""A move on the screen as a whole."""

Move = Finished | Impossible | ElementMove | PageMove
"""Every move Jev can pick: `Finished` and `Impossible` end the goal, the rest are made on the device."""


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
    probabilities: Mapping[str, float] = field(default_factory=dict[str, float])


# --- what a `do:` goal saves, so later runs repeat it without Jev ----------------------------------------------


@dataclass(frozen=True)
class Target:
    """Which element a saved step acts on: its kind and name, and which of the elements with both it is.

    Attributes:
        kind: The element's kind (``button``, ``text_field``, ...).
        name: What it says: its text, else its hint, else its id; empty for an element with none of them (a web
            field on some Android versions). A ``${NAME}`` value in it is written as its name.
        nth: Which of the elements with this kind and name, counting from the top of the screen (1 = first).
        count: How many elements had this kind and name when the step was saved.
    """

    kind: str
    name: str
    nth: int = 1
    count: int = 1

    def describe(self) -> str:
        """E.g. ``button 'Sign in'``, ``the 2nd of 3 button 'Delete'``, ``the 1st of 2 text_field (no name)``."""
        what = f"{self.kind} '{self.name}'" if self.name else f"{self.kind} (no name)"
        return what if self.count == 1 else f"the {ordinal(self.nth)} of {self.count} {what}"


STEP_ACTIONS = frozenset(
    {
        "tap",
        "double_tap",
        "long_press",
        "swipe_left",
        "swipe_right",
        "type",
        "clear",
        "scroll_up",
        "scroll_down",
        "scroll_left",
        "scroll_right",
        "back",
        "press_enter",
        "hide_keyboard",
    }
)
"""The actions a saved step can be. `type`, `clear`, taps and swipes have a target; `type` also has text."""


@dataclass(frozen=True)
class SavedStep:
    """One step a `do:` goal took, saved in the lockfile so later runs repeat it without Jev.

    Attributes:
        action: One of `STEP_ACTIONS`.
        target: The element it acts on, for taps, swipes, `type` and `clear`; None for the others.
        text: For `type`: the value as the goal writes it, a ``${NAME}`` as its name.
    """

    action: str
    target: Target | None = None
    text: str | None = None

    def describe(self) -> str:
        """E.g. ``tap button 'Sign in'``, ``type "${EMAIL}" into text_field 'Email'``, ``scroll down``."""
        words = self.action.replace("_", " ")
        if self.action == "type" and self.target is not None:
            return f'type "{self.text}" into {self.target.describe()}'
        if self.target is not None:
            return f"{words} {self.target.describe()}"
        return words
