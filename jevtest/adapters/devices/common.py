"""What the Android and iOS devices share: running tools and helper processes, the agent cache, gestures."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from jevtest.domain.failures import DeviceError
from jevtest.domain.kinds import Direction
from jevtest.domain.screen import Element, Screen

FOLLOW_UP = 3.0
"""Seconds a device waits for its own follow-ups to an action: a tapped field taking keyboard focus, a web
view's content arriving after the web view, the keyboard closing, the screen turning."""

CHECK_INTERVAL = 0.25
"""Seconds between two checks of such a follow-up (the same as a step's default `interval`)."""


def no_app_opens(url: str) -> DeviceError:
    """The error for a link no app on the device handles."""
    return DeviceError(f"No app on the device opens {url}: check the link, and that the app registers its scheme")


def wait_until(condition: Callable[[], bool], what: str) -> None:
    """Check `condition`; if it's false, wait `CHECK_INTERVAL` seconds and check again, for up to `FOLLOW_UP`.

    Raises:
        DeviceError: It's still false: the message is "`what` within N seconds".
    """
    deadline = time.monotonic() + FOLLOW_UP
    while not condition():
        if time.monotonic() + CHECK_INTERVAL > deadline:
            raise DeviceError(f"{what} within {FOLLOW_UP:g} seconds")
        time.sleep(CHECK_INTERVAL)


def run_bytes(cmd: list[str], *, timeout: float = 120, check: bool = True) -> bytes:
    """Run a command and return its output.

    Raises:
        DeviceError: The command isn't installed, timed out, or (with `check`) exited with an error.
    """
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
    except FileNotFoundError:
        raise DeviceError(f"Command not found: {cmd[0]}") from None
    except subprocess.TimeoutExpired:
        raise DeviceError(f"Timed out after {timeout:g}s: {' '.join(map(str, cmd))}") from None
    if check and p.returncode != 0:
        detail = (p.stderr or p.stdout).decode(errors="replace").strip()[:800]
        raise DeviceError(f"{' '.join(map(str, cmd))} failed ({p.returncode}): {detail}")
    return p.stdout


def run(cmd: list[str], *, timeout: float = 120, check: bool = True) -> str:
    """Run a command and return its output as text.

    Raises:
        DeviceError: The command isn't installed, timed out, or (with `check`) exited with an error.
    """
    return run_bytes(cmd, timeout=timeout, check=check).decode(errors="replace")


def cache_dir() -> Path:
    """Where built agents and their logs are kept: ``$JEVTEST_CACHE``, or ``~/.cache/jevtest``."""
    return Path(os.environ.get("JEVTEST_CACHE", Path.home() / ".cache" / "jevtest"))


def drop_older(kind: str, keep: str) -> None:
    """Remove the cached builds that `kind` (a regex whose first group is a version) matches, but for `keep`.

    Every jevtest version whose agent differs builds its own; without this the cache only grows (measured: 6.5 GB of
    iOS agent builds at about 150 MB each).
    """
    pattern = re.compile(kind)
    for entry in cache_dir().iterdir():
        found = pattern.fullmatch(entry.name)
        if found and found[1] != keep:
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)


def digest(src: Path) -> str:
    """Hash of a source tree, so a changed on-device agent gets rebuilt."""
    h = hashlib.sha256()
    for f in sorted(src.rglob("*")):
        if f.is_file() and "xcuserdata" not in f.parts:
            h.update(str(f.relative_to(src)).encode())
            h.update(f.read_bytes())
    return h.hexdigest()[:12]


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
"""Told about slow one-time work (building an agent), so the user knows why a run is waiting."""


class BaseDevice(ABC):
    """What the Android and iOS devices share: swipe and scroll geometry from the screen and a drag.

    Subclasses implement the rest of the `Device` port; the abstract methods here are the ones this class uses
    or that every device must think about (putting the device back, releasing what it started).
    """

    @abstractmethod
    def screen(self) -> Screen:
        """What's on the screen now."""

    @abstractmethod
    def drag(self, x1: int, y1: int, x2: int, y2: int, *, scroll: bool = False) -> None:
        """Press at (x1, y1), move to (x2, y2), lift. A `scroll` drag moves the content as far as the finger."""

    @abstractmethod
    def restore(self) -> None:
        """Put back what steps changed on the device."""

    @abstractmethod
    def check_ready(self) -> None:
        """Raise `DeviceError` if the device can't be tested right now (asleep, locked)."""

    @abstractmethod
    def close(self) -> None:
        """Release what the device started."""

    def wait_until(self, done: Callable[[Screen], bool], what: str) -> None:
        """`wait_until` the screen shows an action's effect.

        The effect comes a moment after the action returns: the keyboard sliding away, the screen turning.

        Raises:
            DeviceError: The effect didn't show: the message is "`what` within N seconds".
        """
        wait_until(lambda: done(self.screen()), what)

    def swipe(
        self,
        direction: Direction,
        element: Element | None = None,
        screen: Screen | None = None,
        *,
        scroll: bool = False,
    ) -> None:
        """Finger swipe in `direction`, across an element or across the page (a `scroll` one moves no further).

        The page is the part of the screen the keyboard doesn't cover: a drag that starts on the keyboard moves
        nothing.
        """
        if element is not None:
            x1, y1, x2, y2 = element.bounds
        else:
            s = screen or self.screen()
            x1, y1, x2, y2 = 0, 0, s.width, s.content_height
        cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
        dx, dy = int((x2 - x1) * 0.35), int((y2 - y1) * 0.3)
        moves = {
            Direction.UP: (cx, cy + dy, cx, cy - dy),
            Direction.DOWN: (cx, cy - dy, cx, cy + dy),
            Direction.LEFT: (cx + dx, cy, cx - dx, cy),
            Direction.RIGHT: (cx - dx, cy, cx + dx, cy),
        }
        self.drag(*moves[Direction(direction)], scroll=scroll)

    def scroll(self, direction: Direction, screen: Screen | None = None) -> None:
        """Scroll so more of the content in `direction` comes into view: the finger moves the other way."""
        finger = {
            Direction.DOWN: Direction.UP,
            Direction.UP: Direction.DOWN,
            Direction.LEFT: Direction.RIGHT,
            Direction.RIGHT: Direction.LEFT,
        }
        self.swipe(finger[Direction(direction)], screen=screen, scroll=True)
