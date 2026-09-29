"""Tests and their steps: one action, then the checks that must hold after it.

Each kind of action is its own type, so a step can only carry the values that kind needs.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from .kinds import Direction, Gesture, Orientation, Platform
from .settings import DEFAULTS, Settings

# --- actions ---------------------------------------------------------------------------------------------------


class _Action:
    """What every action says about itself. The runner reads this, never a list of types.

    Attributes:
        app_may_leave: The app may be closed or in the background afterwards, and that isn't a failure.
    """

    __slots__ = ()
    app_may_leave: ClassVar[bool] = False


@dataclass(frozen=True)
class Do(_Action):
    """Reach a plain-English goal: Jev picks the actions. Quoted values are what it may type."""

    goal: str


@dataclass(frozen=True)
class Use(_Action):
    """Run another test's steps here."""

    test: str


@dataclass(frozen=True)
class Touch(_Action):
    """Tap, double-tap or long-press the element a target names."""

    gesture: Gesture
    target: str


@dataclass(frozen=True)
class Clear(_Action):
    """Erase a text field."""

    target: str


@dataclass(frozen=True)
class TypeText(_Action):
    """Type text, into the named field or (without `into`) the focused one. Typed exactly as written."""

    text: str
    into: str | None = None


@dataclass(frozen=True)
class Scroll(_Action):
    """Scroll the content."""

    direction: Direction


@dataclass(frozen=True)
class Swipe(_Action):
    """Swipe across the screen, or on the element a target names."""

    direction: Direction
    target: str | None = None


@dataclass(frozen=True)
class ScrollTo(_Action):
    """Scroll until the text is on screen."""

    text: str
    direction: Direction


KEYS = ("backspace", "delete", "enter", "escape", "return", "space", "tab")
"""The keys `key:` presses on every platform."""
ANDROID_KEYS = (
    "app_switch", "back", "dpad_down", "dpad_left", "dpad_right", "dpad_up", "home", "menu", "move_end",
    "move_home", "power", "search", "volume_down", "volume_up",
)  # fmt: skip
"""The keys `key:` presses on Android only (as does a key code number)."""


@dataclass(frozen=True)
class Key(_Action):
    """Press a named key (enter, delete, ...), or on Android a key code given as a number."""

    name: str


@dataclass(frozen=True)
class Wait(_Action):
    """Wait a fixed time."""

    seconds: float


@dataclass(frozen=True)
class Background(_Action):
    """Send the app to the background for a while, then bring it back."""

    seconds: float


@dataclass(frozen=True)
class Rotate(_Action):
    """Rotate the device."""

    orientation: Orientation


@dataclass(frozen=True)
class Location(_Action):
    """Set the GPS location."""

    latitude: float
    longitude: float


@dataclass(frozen=True)
class OpenUrl(_Action):
    """Open a deep link or URL. The link may open in another app."""

    app_may_leave: ClassVar[bool] = True
    url: str


@dataclass(frozen=True)
class DarkMode(_Action):
    """Switch dark appearance on or off."""

    on: bool


@dataclass(frozen=True)
class Grant(_Action):
    """Grant the app runtime permissions: the same names on every platform, or names per platform.

    Each platform names permissions its own way (``android.permission.CAMERA``, ``camera``), so a file that runs
    on both gives both: `per_platform` pairs each platform with its names.
    """

    permissions: tuple[str, ...] = ()
    per_platform: tuple[tuple[str, tuple[str, ...]], ...] = ()

    def names_on(self, platform: str) -> tuple[str, ...] | None:
        """The permissions' names on `platform`; None when it has none there."""
        if not self.per_platform:
            return self.permissions
        return dict(self.per_platform).get(platform)


@dataclass(frozen=True)
class Network(_Action):
    """Switch Wi-Fi and mobile data on or off."""

    on: bool


@dataclass(frozen=True)
class Screenshot(_Action):
    """Save a screenshot."""

    name: str


@dataclass(frozen=True)
class Launch(_Action):
    """Launch the app."""


@dataclass(frozen=True)
class Stop(_Action):
    """Stop the app."""

    app_may_leave: ClassVar[bool] = True


@dataclass(frozen=True)
class Restart(_Action):
    """Stop and launch the app."""


@dataclass(frozen=True)
class ClearData(_Action):
    """Stop the app and clear its data."""

    app_may_leave: ClassVar[bool] = True


@dataclass(frozen=True)
class Reinstall(_Action):
    """Uninstall and install the build again."""

    app_may_leave: ClassVar[bool] = True


@dataclass(frozen=True)
class Back(_Action):
    """Go back."""


@dataclass(frozen=True)
class Home(_Action):
    """Go to the home screen."""

    app_may_leave: ClassVar[bool] = True


@dataclass(frozen=True)
class HideKeyboard(_Action):
    """Close the on-screen keyboard."""


Action = (
    Do
    | Use
    | Touch
    | Clear
    | TypeText
    | Scroll
    | Swipe
    | ScrollTo
    | Key
    | Wait
    | Background
    | Rotate
    | Location
    | OpenUrl
    | DarkMode
    | Grant
    | Network
    | Screenshot
    | Launch
    | Stop
    | Restart
    | ClearData
    | Reinstall
    | Back
    | Home
    | HideKeyboard
)
"""Everything a step can do."""


def waits(action: Action) -> bool:
    """Whether the action waits until something is true.

    That's its element being on screen (`tap:`, `type: into`, ...), or its own condition (`type:` the keyboard up,
    `do:` before each move, `scroll_to:` after each scroll, `screenshot:` the screen stopped moving).
    """
    match action:
        case Touch() | Clear() | TypeText() | Do() | ScrollTo() | Screenshot():
            return True
        case Swipe(_, target):
            return target is not None
        case _:
            return False


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
        label: The action as written in the test file, on one line, e.g. ``tap: Save``; empty for a step that
            only checks.
    """

    action: Action | None
    checks: tuple[Check, ...] = ()
    settings: Settings = DEFAULTS
    label: str = ""


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


# --- where settings apply -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Scope:
    """Which steps a setting means something for.

    Attributes:
        where: The steps, in words, for the error when a setting is on any other step.
        applies: Whether the setting means something for a step with this action and these checks.
    """

    where: str
    applies: Callable[[Action | None, Sequence[Check]], bool]


SETTING_SCOPES: Mapping[str, Scope] = {
    "timeout": Scope(
        "a step that waits (for its element, its checks, or a do:/scroll_to: condition)",
        lambda action, checks: bool(checks) or (action is not None and waits(action)),
    ),
    "interval": Scope(
        "a step that waits (for its element, its checks, or a do:/scroll_to: condition)",
        lambda action, checks: bool(checks) or (action is not None and waits(action)),
    ),
    "max_actions": Scope("a do: step", lambda action, _: isinstance(action, Do)),
    "max_scrolls": Scope("a scroll_to: step", lambda action, _: isinstance(action, ScrollTo)),
    "confidence": Scope(
        "a step with an expect: check", lambda _, checks: any(isinstance(check, Expect) for check in checks)
    ),
}
"""Each setting a step can change for itself, and the steps it means something for."""
