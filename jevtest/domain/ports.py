"""The interfaces jevtest's core needs from the outside world.

The application layer depends only on these. Adapters implement them; the command line wires implementations to
them in one place (`jevtest.cli.main`), which is also what lets tests pass fakes instead.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol

from .kinds import AppState, Direction, Orientation
from .model import Answer, ModelCall, Question, State
from .results import CheckResult, StepResult, TestResult
from .screen import Element, Point, Screen
from .steps import Test


class Device(Protocol):
    """A phone, emulator or simulator with the app under test installed on it.

    Every method raises `DeviceError` when the device can't do it.
    """

    def install(self, app: Path) -> str:
        """Install the build and return its package or bundle id."""
        ...

    def check_ready(self) -> None:
        """Raise `DeviceError` if the device can't be tested right now (e.g. it's locked)."""
        ...

    def launch(self) -> None:
        """Launch the app and wait until it is in the foreground."""
        ...

    def resume(self) -> None:
        """Bring the app back to the foreground without restarting it."""
        ...

    def stop(self) -> None:
        """Stop the app."""
        ...

    def clear_data(self) -> None:
        """Clear the app's data, as if newly installed."""
        ...

    def reinstall(self) -> None:
        """Uninstall and install the build again."""
        ...

    def app_state(self) -> AppState:
        """Where the app is. Cheap: it doesn't read the screen."""
        ...

    def screen(self) -> Screen:
        """What's on the screen now."""
        ...

    def screenshot(self, path: Path) -> None:
        """Save a PNG of the screen."""
        ...

    def wait_idle(self, timeout: float, quiet: float | None = None) -> None:
        """Return once the screen has stopped changing (for `quiet` seconds), or after `timeout` seconds."""
        ...

    def wait_change(self, timeout: float) -> None:
        """Return as soon as the screen may have changed, or after `timeout` seconds."""
        ...

    def tap(self, x: int, y: int) -> None:
        """Tap a point."""
        ...

    def double_tap(self, x: int, y: int) -> None:
        """Double-tap a point."""
        ...

    def long_press(self, x: int, y: int) -> None:
        """Press and hold a point."""
        ...

    def swipe(self, direction: Direction, element: Element | None = None, screen: Screen | None = None) -> None:
        """Swipe on an element, or across the part of the screen the keyboard doesn't cover."""
        ...

    def scroll(self, direction: Direction, screen: Screen | None = None) -> None:
        """Scroll the content so more of it in `direction` comes into view."""
        ...

    def type_text(self, text: str, at: Point | None = None) -> None:
        """Type into the focused field, or first focus the field at `at`."""
        ...

    def clear_text(self, element: Element) -> None:
        """Erase a text field."""
        ...

    def key(self, name: str) -> None:
        """Press a named key."""
        ...

    def back(self) -> None:
        """Go back."""
        ...

    def home(self) -> None:
        """Go to the home screen."""
        ...

    def hide_keyboard(self) -> None:
        """Close the on-screen keyboard."""
        ...

    def rotate(self, orientation: Orientation) -> None:
        """Rotate the device. Put back when the device is closed."""
        ...

    def set_location(self, latitude: float, longitude: float) -> None:
        """Set the GPS location."""
        ...

    def open_url(self, url: str) -> None:
        """Open a deep link or URL."""
        ...

    def dark_mode(self, on: bool) -> None:
        """Switch dark appearance on or off. Put back when the device is closed."""
        ...

    def grant(self, permission: str) -> None:
        """Grant the app a runtime permission."""
        ...

    def network(self, on: bool) -> None:
        """Switch Wi-Fi and mobile data on or off. Put back when the device is closed."""
        ...

    def restore(self) -> None:
        """Put back what steps changed (orientation, appearance, network, location).

        Called before each fresh test, so one test's changes never leak into the next.
        """
        ...

    def close(self) -> None:
        """Put back what steps changed and release everything the device started."""
        ...


class DecisionModel(Protocol):
    """The model that makes jevtest's judgement calls (Jev), behind its lockfile."""

    @property
    def calls(self) -> Sequence[ModelCall]:
        """Every request made so far, in order."""
        ...

    def ask(self, state: State, questions: Mapping[str, Question]) -> Mapping[str, Answer]:
        """Ask several questions about one state; get one answer per question.

        Raises:
            ModelError: The model couldn't be asked, or answered outside the options.
        """
        ...


class Clock(Protocol):
    """Time, so tests can use a fake one."""

    def now(self) -> float:
        """Seconds on a monotonic clock."""
        ...

    def sleep(self, seconds: float) -> None:
        """Wait."""
        ...


class RunListener(Protocol):
    """Told what happens as tests run, so it can be shown as it happens."""

    def test_started(self, test: Test) -> None:
        """A test is starting."""
        ...

    def start_failed(self, test: Test, reason: str) -> None:
        """The app could not be started for a test."""
        ...

    def use_started(self, name: str, depth: int) -> None:
        """A `use:` step is running another test's steps."""
        ...

    def step_done(self, result: StepResult, depth: int) -> None:
        """A step's action finished (its checks follow)."""
        ...

    def check_done(self, result: CheckResult, depth: int) -> None:
        """A check finished."""
        ...

    def test_done(self, result: TestResult) -> None:
        """A test finished."""
        ...
