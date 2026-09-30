"""What the Android and iOS devices share: running tools and helper processes, the agent cache, gestures."""

from __future__ import annotations

import json
import re
import subprocess
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Protocol

from jevtest.adapters.shapes import is_json_object
from jevtest.domain.failures import DeviceError
from jevtest.domain.kinds import AppState, Direction
from jevtest.domain.screen import EDGE, SIDE_EDGE, Bounds, Element, Screen
from jevtest.domain.steps import SWIPE_DISTANCE

from .cache import in_use_dir, unwritable

FOLLOW_UP = 3.0
"""Seconds a device waits for its own follow-ups to an action: a tapped field taking keyboard focus, a web
view's content arriving after the web view, the keyboard closing, the screen turning."""

CHECK_INTERVAL = 0.25
"""Seconds between two checks of such a follow-up (the same as a step's default `interval`)."""


class ToolFailed(DeviceError):
    """A tool exited with an error. `output` is everything it printed, both streams, to recognise why by."""

    def __init__(self, cmd: list[str], returncode: int, output: str) -> None:
        super().__init__(f"{' '.join(map(str, cmd))} failed ({returncode}): {output.strip()[:800]}")
        self.returncode = returncode
        self.output = output


class TimedOut(DeviceError):
    """A follow-up to an action didn't show within `FOLLOW_UP` seconds (see `wait_until`)."""


class AgentRefused(DeviceError):
    """The on-device agent answered, with why it couldn't do what was asked. `said` is its own words."""

    def __init__(self, agent: str, path: str, said: str) -> None:
        super().__init__(f"{agent} {path}: {said}")
        self.said = said


def no_app_opens(url: str) -> DeviceError:
    """The error for a link no app on the device handles."""
    return DeviceError(f"No app on the device opens {url}: check the link, and that the app registers its scheme")


def wait_until(condition: Callable[[], bool], what: str) -> None:
    """Check `condition`; if it's false, wait `CHECK_INTERVAL` seconds and check again, for up to `FOLLOW_UP`.

    Raises:
        TimedOut: It's still false: the message is "`what` within N seconds".
    """
    deadline = time.monotonic() + FOLLOW_UP
    while not condition():
        if time.monotonic() + CHECK_INTERVAL > deadline:
            raise TimedOut(f"{what} within {FOLLOW_UP:g} seconds")
        time.sleep(CHECK_INTERVAL)


def run_bytes(cmd: list[str], *, timeout: float = 120, check: bool = True) -> bytes:
    """Run a command and return its output.

    Raises:
        DeviceError: The command isn't installed or timed out.
        ToolFailed: With `check`, it exited with an error.
    """
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
    except FileNotFoundError:
        raise DeviceError(f"Command not found: {cmd[0]}") from None
    except subprocess.TimeoutExpired:
        raise DeviceError(f"Timed out after {timeout:g}s: {' '.join(map(str, cmd))}") from None
    if check and p.returncode != 0:
        raise ToolFailed(cmd, p.returncode, (p.stderr + p.stdout).decode(errors="replace"))
    return p.stdout


def run(cmd: list[str], *, timeout: float = 120, check: bool = True) -> str:
    """Run a command and return its output as text.

    Raises:
        DeviceError: The command isn't installed or timed out.
        ToolFailed: With `check`, it exited with an error.
    """
    return run_bytes(cmd, timeout=timeout, check=check).decode(errors="replace")


class Undo:
    """What puts back each thing steps changed on a device, by what it is in words, kept on disk as it changes.

    A run that ends normally puts it all back and forgets it. One that is killed outright (``kill -9``, a CI job
    past its grace period) can't: the next run on the device finds what it left, and puts that back first. So
    each entry is JSON, and says how to put it back in terms the device reads the same way either time.
    """

    def __init__(self, device_id: str) -> None:
        self._entries: dict[str, object] = {}
        self._device_id = device_id

    @property
    def path(self) -> Path:
        """The file it's kept in."""
        return in_use_dir() / f"{self._device_id}.undo.json"

    def __contains__(self, what: str) -> bool:
        return what in self._entries

    def remember(self, what: str, how: Callable[[], object]) -> None:
        """Before the first change to `what` ("dark mode"), note how to put it back: `how` is called only then.

        Raises:
            DeviceError: It can't be noted on disk, so the change mustn't be made: a run killed then couldn't be
                put back by the next one.
        """
        if what in self._entries:
            return
        back = how()
        try:
            self.path.write_text(json.dumps({**self._entries, what: back}))
        except OSError as e:
            raise unwritable(e, f"note how to put back {what}", ", so it wasn't changed") from None
        self._entries[what] = back

    def entries(self) -> dict[str, object]:
        """What steps changed, and how to put each back, in the order they first changed it."""
        return dict(self._entries)

    def forget_all(self) -> None:
        """Everything is put back: forget it, on disk too.

        Raises:
            DeviceError: The file can't be removed, so the next run would put it all back again.
        """
        self._entries.clear()
        try:
            self.path.unlink(missing_ok=True)
        except OSError as e:
            raise unwritable(e, "forget what was put back") from None

    def left_by_a_stopped_run(self) -> dict[str, object]:
        """What a run that couldn't finish left to put back on this device; {} if nothing."""
        try:
            data: object = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError):
            return {}
        return dict(data) if is_json_object(data) else {}


def start_process(
    cmd: list[str], ready: str, log: Path, timeout: float, env: dict[str, str] | None = None
) -> subprocess.Popen[str]:
    """Start a long-running helper and return as soon as it prints `ready`. Its output goes to `log`.

    Raises:
        DeviceError: It didn't get ready within `timeout` seconds, or exited first. The message quotes its log.
    """
    log.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env, start_new_session=True
    )
    output = proc.stdout
    if output is None:  # pragma: no cover - stdout=PIPE always gives a pipe
        raise DeviceError(f"{cmd[0]} started without an output pipe")
    done = threading.Event()
    state = {"ready": False}

    def pump() -> None:
        with output, log.open("w") as f:  # closes the pipe when the helper's output ends
            for line in output:
                f.write(line)
                f.flush()
                if ready in line and not state["ready"]:
                    state["ready"] = True
                    done.set()
        done.set()  # the output ended: the process is exiting

    threading.Thread(target=pump, daemon=True).start()
    if not done.wait(timeout):
        proc.kill()
        proc.wait()  # reap it, so no zombie is left behind
        raise DeviceError(f"{cmd[0]} did not report ready within {timeout:g}s. {log_errors(log)}")
    if not state["ready"]:
        proc.wait()
        raise DeviceError(f"{cmd[0]} exited before it was ready. {log_errors(log)}")
    return proc


ERROR_LINE = re.compile(r"(?i)\b(error|failed|failure|exception)\b")


def log_errors(log: Path, keep: int = 6) -> str:
    """The error lines of a helper's log (or its last lines, if none look like errors), and where it is."""
    lines = [line.strip() for line in log.read_text(errors="replace").splitlines() if line.strip()]
    errors = [line for line in lines if ERROR_LINE.search(line) and not line.startswith("t =")]
    shown = list(dict.fromkeys(errors))[-keep:] or lines[-keep:]
    return "\n".join(["Its log says:", *(f"  {line}" for line in shown), f"Full log: {log}"])


class Process(Protocol):
    """What `stop_process` needs of a helper process (a `subprocess.Popen`)."""

    def poll(self) -> int | None:
        """The exit code, or None while it runs."""
        ...

    def terminate(self) -> None:
        """Ask it to stop."""
        ...

    def kill(self) -> None:
        """Stop it now."""
        ...

    def wait(self, timeout: float | None = None) -> int:
        """Wait for it to exit."""
        ...


def stop_process(proc: Process | None) -> None:
    """Stop a helper process: politely, then by force after 10 seconds."""
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()


Progress = Callable[[str], None]
"""Told what a device is doing that the user should know: slow one-time work (building an agent), so they know why
a run is waiting, and putting back what a stopped run left changed."""


class BaseDevice(ABC):
    """What the Android and iOS devices share: swipe and scroll geometry from the screen and a drag.

    Subclasses implement the rest of the `Device` port; the abstract methods here are the ones this class uses
    or that every device must think about (putting the device back, releasing what it started).
    """

    @abstractmethod
    def screen(self) -> Screen:
        """What's on the screen now."""

    @abstractmethod
    def drag(self, x1: int, y1: int, x2: int, y2: int) -> None:
        """Press at (x1, y1), move to (x2, y2), lift: a finger's swipe."""

    def _scroll_drag(self, x1: int, y1: int, x2: int, y2: int) -> None:
        """A drag that moves the content as far as the finger, and no further.

        A device whose `drag` lets the content fling on overrides this.
        """
        self.drag(x1, y1, x2, y2)

    @abstractmethod
    def app_state(self) -> AppState:
        """Where the app is: in the foreground, the background, or not running."""

    @abstractmethod
    def _press_home(self) -> None:
        """Press the Home button (or gesture); it returns before the app has left."""

    def home(self) -> None:
        """Press Home, and wait until the app has left the foreground (the press returns before it has)."""
        self._press_home()
        wait_until(
            lambda: self.app_state() is not AppState.FOREGROUND, "The app was still in the foreground after Home"
        )

    _undo: Undo
    _progress: Progress

    @abstractmethod
    def _put_back(self, entry: object) -> bool:
        """Put back one thing an `Undo` holds (this run's, or one a stopped run left).

        Returns False if `entry` isn't one this version reads: another jevtest version may write it differently.

        Raises:
            DeviceError: Putting it back failed.
        """

    def restore(self) -> None:
        """Put back what steps changed on the device, as it was before them."""
        self._put_back_all(self._undo.entries())

    def _put_back_left_by_a_stopped_run(self) -> None:
        """Put back what a run on this device that was killed left changed, before this run changes anything."""
        left = self._undo.left_by_a_stopped_run()
        if left:
            self._progress(f"putting back what a run that was stopped left changed: {', '.join(left)}")
            self._put_back_all(left)

    def _put_back_all(self, entries: Mapping[str, object]) -> None:
        """Put back every entry, saying which couldn't be: one that fails doesn't stop the rest."""
        for what, entry in entries.items():
            try:
                if not self._put_back(entry):
                    self._progress(f"can't put back {what} (another jevtest version changed it): set it by hand")
            except DeviceError as e:
                why = str(e).splitlines()[0]  # not an agent log tail an error may end with
                self._progress(f"couldn't put back {what} ({why}): set it by hand")
        self._undo.forget_all()

    @abstractmethod
    def prepare_for_test(self) -> None:
        """Before each test: check the device can be tested, and start again what a failed test lost.

        Raises `DeviceError` if it's asleep or locked. Android starts its agent again if something stopped it.
        """

    @abstractmethod
    def close(self) -> None:
        """Release what the device started."""

    def wait_until(self, done: Callable[[Screen], bool], what: str) -> None:
        """`wait_until` the screen shows an action's effect.

        The effect comes a moment after the action returns: the keyboard sliding away, the screen turning.

        Raises:
            TimedOut: The effect didn't show: the message is "`what` within N seconds".
        """
        wait_until(lambda: done(self.screen()), what)

    def swipe(
        self,
        direction: Direction,
        element: Element | None = None,
        screen: Screen | None = None,
        distance: float | None = None,
    ) -> None:
        """Finger swipe in `direction`: on an element, along what it's in (`Screen.swiped`); else across the page.

        A slider's thumb goes to that end.
        """
        s = screen or self.screen()
        if element is not None and element.position is not None and direction in {Direction.LEFT, Direction.RIGHT}:
            # A flick moves a slider an amount that varies from one run to the next (measured on iOS 26.5); a drag
            # slow enough for the thumb to keep up with the finger takes it to the end every time.
            self._scroll_drag(*self._thumb_to_end(direction, element, s.width))
        elif element is not None:
            lane, on = s.swiped(element, direction)
            self.drag(*_across(direction, lane, s, distance, on=on))
        else:
            self.drag(*_across(direction, _page(s), s, distance))

    @staticmethod
    def _thumb_to_end(direction: Direction, slider: Element, width: int) -> tuple[int, int, int, int]:
        """A drag from the middle of a horizontal slider's thumb to the screen's edge in `direction`.

        The thumb moves only when the finger starts on it (an iOS slider ignores a drag beside it, and one on
        the thumb's very edge). Its middle runs from half its width in from one end to half its width in from
        the other. It is about as wide as the slider is tall (measured: 37 points wide on a 31-point SwiftUI
        slider), and the finger lands on it whenever it is more than half that wide. The thumb trails the
        finger by the distance a touch moves before it counts as a drag (measured: 9 points), so the finger goes
        on past the slider's end, to the screen's edge.
        """
        x1, y1, x2, y2 = slider.bounds
        inset = min(y2 - y1, x2 - x1) / 2
        thumb = round(x1 + inset + (slider.position or 0) * (x2 - x1 - 2 * inset))
        return thumb, (y1 + y2) // 2, width - 1 if direction == Direction.RIGHT else 0, (y1 + y2) // 2

    def scroll(self, direction: Direction, screen: Screen | None = None, lane: Bounds | None = None) -> None:
        """Scroll so more of the content in `direction` comes into view: the finger moves the other way.

        Across the page, or along `lane`: a carousel scrolls when a finger drags along it, not across the page.
        """
        finger = {
            Direction.DOWN: Direction.UP,
            Direction.UP: Direction.DOWN,
            Direction.LEFT: Direction.RIGHT,
            Direction.RIGHT: Direction.LEFT,
        }
        s = screen or self.screen()
        self._scroll_drag(*_across(finger[Direction(direction)], lane or _page(s), s))


def _page(screen: Screen) -> Bounds:
    """Where a drag across the page goes: `Screen.page`, clear of the keyboard and the app's bars."""
    return screen.page


def _across(
    direction: Direction, over: Bounds, screen: Screen, distance: float | None = None, on: Bounds | None = None
) -> tuple[int, int, int, int]:
    """Where a swipe in `direction` starts and ends: `distance` percent across `over` (None: `SWIPE_DISTANCE`'s).

    The finger goes down on `on`, what the swipe is on, when that's narrower than `over` (a row's text, a list
    row that doesn't fill the list): the swipe keeps its length. It never starts where the phone would take it for
    its own gesture (`_clear_of_edge_gestures`), and it ends on the screen.
    """
    x1, y1, x2, y2 = over
    cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
    across, down = (distance, distance) if distance is not None else SWIPE_DISTANCE
    dx, dy = int((x2 - x1) * across / 200), int((y2 - y1) * down / 200)
    moves = {
        Direction.UP: (cx, cy + dy, cx, cy - dy),
        Direction.DOWN: (cx, cy - dy, cx, cy + dy),
        Direction.LEFT: (cx + dx, cy, cx - dx, cy),
        Direction.RIGHT: (cx - dx, cy, cx + dx, cy),
    }
    sx, sy, ex, ey = moves[Direction(direction)]
    if on is not None:
        ox1, oy1, ox2, oy2 = on
        nx, ny = min(max(sx, ox1), ox2 - 1), min(max(sy, oy1), oy2 - 1)
        sx, sy, ex, ey = nx, ny, ex + nx - sx, ey + ny - sy
    mx, my = _clear_of_edge_gestures(direction, sx, sy, screen)
    sx, sy, ex, ey = sx + mx, sy + my, ex + mx, ey + my
    return sx, sy, min(max(ex, 0), screen.width - 1), min(max(ey, 0), screen.height - 1)


def _clear_of_edge_gestures(direction: Direction, x: int, y: int, screen: Screen) -> tuple[int, int]:
    """How far a swipe from (`x`, `y`) in `direction` must move so the phone doesn't take it as its own gesture.

    A phone takes a swipe inward from an edge: Android's back from either side, iOS's back from the left, home from
    the bottom and the notifications from the top. A swipe outward is the app's, however near the edge it starts.
    """
    w, h = screen.width, screen.height
    return {
        Direction.RIGHT: (max(0, round(w * SIDE_EDGE) - x), 0),
        Direction.LEFT: (min(0, round(w * (1 - SIDE_EDGE)) - x), 0),
        Direction.UP: (0, min(0, round(h * (1 - EDGE)) - y)),
        Direction.DOWN: (0, max(0, round(h * EDGE) - y)),
    }[Direction(direction)]
