"""Tests and their steps: one action, then the checks that must hold after it.

Each kind of action is its own type, so a step can only carry the values that kind needs.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from .kinds import Direction, Gesture, Orientation, Platform
from .settings import DEFAULTS, Settings

# --- actions ---------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Do:
    """Reach a plain-English goal: Jev picks the actions. Quoted values are what it may type."""

    goal: str


@dataclass(frozen=True)
class Use:
    """Run another test's steps here."""

    test: str


@dataclass(frozen=True)
class Touch:
    """Tap, double-tap or long-press the element a target names."""

    gesture: Gesture
    target: str


@dataclass(frozen=True)
class Clear:
    """Erase a text field."""

    target: str


@dataclass(frozen=True)
class TypeText:
    """Type text, into the named field or (without `into`) the focused one. Typed exactly as written."""

    text: str
    into: str | None = None


@dataclass(frozen=True)
class Scroll:
    """Scroll the content."""

    direction: Direction


@dataclass(frozen=True)
class Swipe:
    """Swipe across the screen, or on the element a target names."""

    direction: Direction
    target: str | None = None


@dataclass(frozen=True)
class ScrollTo:
    """Scroll until the text is on screen."""

    text: str
    direction: Direction


@dataclass(frozen=True)
class Key:
    """Press a named key (enter, delete, ...)."""

    name: str


@dataclass(frozen=True)
class Wait:
    """Wait a fixed time."""

    seconds: float


@dataclass(frozen=True)
class Background:
    """Send the app to the background for a while, then bring it back."""

    seconds: float


@dataclass(frozen=True)
class Rotate:
    """Rotate the device."""

    orientation: Orientation


@dataclass(frozen=True)
class Location:
    """Set the GPS location."""

    latitude: float
    longitude: float


@dataclass(frozen=True)
class OpenUrl:
    """Open a deep link or URL."""

    url: str


@dataclass(frozen=True)
class DarkMode:
    """Switch dark appearance on or off."""

    on: bool


@dataclass(frozen=True)
class Grant:
    """Grant the app a runtime permission."""

    permission: str


@dataclass(frozen=True)
class Network:
    """Switch Wi-Fi and mobile data on or off."""

    on: bool


@dataclass(frozen=True)
class Screenshot:
    """Save a screenshot."""

    name: str


@dataclass(frozen=True)
class Launch:
    """Launch the app."""


@dataclass(frozen=True)
class Stop:
    """Stop the app."""


@dataclass(frozen=True)
class Restart:
    """Stop and launch the app."""


@dataclass(frozen=True)
class ClearData:
    """Stop the app and clear its data."""


@dataclass(frozen=True)
class Reinstall:
    """Uninstall and install the build again."""


@dataclass(frozen=True)
class Back:
    """Go back."""


@dataclass(frozen=True)
class Home:
    """Go to the home screen."""


@dataclass(frozen=True)
class HideKeyboard:
    """Close the on-screen keyboard."""


Action = (
    Do | Use | Touch | Clear | TypeText | Scroll | Swipe | ScrollTo | Key | Wait | Background | Rotate | Location
    | OpenUrl | DarkMode | Grant | Network | Screenshot | Launch | Stop | Restart | ClearData | Reinstall | Back
    | Home | HideKeyboard
)
"""Everything a step can do."""

# --- checks ----------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Expect:
    """Jev judges the statement true of the screen."""

    name: ClassVar[str] = "expect"
    text: str


@dataclass(frozen=True)
class See:
    """The text is on screen."""

    name: ClassVar[str] = "see"
    text: str


@dataclass(frozen=True)
class NotSee:
    """The text is not on screen."""

    name: ClassVar[str] = "not_see"
    text: str


Check = Expect | See | NotSee
"""Something that must be true after a step's action."""

# --- tests -----------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Step:
    """One action (or none), then the checks that must hold after it.

    Attributes:
        action: What to do, or None for a step that only checks.
        checks: What must be true afterwards, in order.
        settings: The settings this step runs with: the file's, with any the step sets for itself.
        source: The step as written in the test file, for reports.
    """

    action: Action | None
    checks: tuple[Check, ...] = ()
    settings: Settings = DEFAULTS
    source: object = None


@dataclass(frozen=True)
class Test:
    """A named list of steps.

    Attributes:
        name: Unique across a test file and everything it includes.
        fresh: Start from a clean install (True) or carry on from where the previous test left the app (False).
        steps: The steps, in order.
    """

    __test__ = False  # not a pytest test class

    name: str
    fresh: bool
    steps: tuple[Step, ...]


@dataclass(frozen=True)
class Suite:
    """One test file, ready to run.

    Attributes:
        path: The test file.
        apps: The build for each platform, in the order the file lists them.
        devices: The devices for each platform; tests are split across several.
        tests: The tests to run, in order.
        library: Every test `use:` can name: this file's and those of the files it includes.
        variables: The ``${NAME}`` values the file uses.
        includes: The library files it includes, directly or not.
        settings: The file's settings. Each step carries its own copy, with the step's own changes.
    """

    path: Path
    apps: Mapping[Platform, Path]
    devices: Mapping[Platform, tuple[str, ...]]
    tests: tuple[Test, ...]
    library: Mapping[str, Test]
    variables: Mapping[str, str]
    includes: tuple[Path, ...] = ()
    settings: Settings = DEFAULTS
