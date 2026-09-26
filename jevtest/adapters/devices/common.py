"""What the Android and iOS devices share: running tools and helper processes, the agent cache, gestures."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path

from jevtest.domain.failures import DeviceError
from jevtest.domain.kinds import Direction
from jevtest.domain.screen import Element, Screen


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


def digest(src: Path) -> str:
    """Hash of a source tree, so a changed on-device agent gets rebuilt."""
    h = hashlib.sha256()
    for f in sorted(src.rglob("*")):
        if f.is_file() and "xcuserdata" not in f.parts:
            h.update(str(f.relative_to(src)).encode())
            h.update(f.read_bytes())
    return h.hexdigest()[:12]


def start_process(cmd: list[str], ready: str, log: Path, timeout: float,
                  env: dict[str, str] | None = None) -> subprocess.Popen[str]:
    """Start a long-running helper and return as soon as it prints `ready`. Its output goes to `log`.

    Raises:
        DeviceError: It didn't get ready within `timeout` seconds, or exited first. The message quotes its log.
    """
    log.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env,
                            start_new_session=True)
    done = threading.Event()
    state = {"ready": False}

    def pump() -> None:
        assert proc.stdout is not None  # stdout=PIPE
        with log.open("w") as f:
            for line in proc.stdout:
                f.write(line)
                f.flush()
                if ready in line and not state["ready"]:
                    state["ready"] = True
                    done.set()
        done.set()  # the output ended: the process is exiting

    threading.Thread(target=pump, daemon=True).start()
    if not done.wait(timeout):
        proc.kill()
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


def stop_process(proc: subprocess.Popen[str] | None) -> None:
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
    """What the Android and iOS devices share: swipe and scroll geometry, and hooks that default to nothing.

    Subclasses implement the rest of the `Device` port.
    """

    @abstractmethod
    def screen(self) -> Screen:
        """What's on the screen now."""

    @abstractmethod
    def drag(self, x1: int, y1: int, x2: int, y2: int) -> None:
        """Press at (x1, y1), move to (x2, y2), lift."""

    def restore(self) -> None:  # noqa: B027 - optional hook
        """Put back what steps changed. Default: nothing was changed."""

    def check_ready(self) -> None:  # noqa: B027 - optional hook
        """Raise `DeviceError` if the device can't be tested right now. Default: always ready."""

    def close(self) -> None:  # noqa: B027 - optional hook
        """Release what the device started. Default: nothing to release."""

    def wait_idle(self, timeout: float, quiet: float | None = None) -> None:  # noqa: B027 - optional hook
        """Return once the screen has stopped changing. Default: return at once."""

    def wait_change(self, timeout: float) -> None:  # noqa: B027 - optional hook
        """Return as soon as the screen may have changed. Default: return at once; callers read the screen again."""

    def swipe(self, direction: Direction, element: Element | None = None, screen: Screen | None = None) -> None:
        """Finger swipe in `direction`, across an element or across the page.

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
        self.drag(*moves[Direction(direction)])

    def scroll(self, direction: Direction, screen: Screen | None = None) -> None:
        """Scroll so more of the content in `direction` comes into view: the finger moves the other way."""
        finger = {Direction.DOWN: Direction.UP, Direction.UP: Direction.DOWN,
                  Direction.LEFT: Direction.RIGHT, Direction.RIGHT: Direction.LEFT}
        self.swipe(finger[Direction(direction)], screen=screen)
