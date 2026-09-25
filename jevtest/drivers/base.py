"""The device interface every platform driver implements."""

from __future__ import annotations

import hashlib
import os
import subprocess
import threading
from abc import ABC, abstractmethod
from pathlib import Path

from ..screen import Element, Screen


class DriverError(RuntimeError):
    pass


def run(cmd: list[str], timeout: float = 120, check: bool = True, binary: bool = False):
    """Run a command; return stdout (text, or bytes if `binary`). Raise DriverError on failure."""
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except FileNotFoundError:
        raise DriverError(f"Command not found: {cmd[0]}") from None
    except subprocess.TimeoutExpired:
        raise DriverError(f"Timed out after {timeout:g}s: {' '.join(map(str, cmd))}") from None
    if check and p.returncode != 0:
        detail = (p.stderr or p.stdout).decode(errors="replace").strip()[:800]
        raise DriverError(f"{' '.join(map(str, cmd))} failed ({p.returncode}): {detail}")
    return p.stdout if binary else p.stdout.decode(errors="replace")


def cache_dir() -> Path:
    return Path(os.environ.get("JEVTEST_CACHE", Path.home() / ".cache" / "jevtest"))


def digest(src: Path) -> str:
    """Hash of a source tree, so a changed on-device agent gets rebuilt."""
    h = hashlib.sha256()
    for f in sorted(src.rglob("*")):
        if f.is_file() and "xcuserdata" not in f.parts:
            h.update(str(f.relative_to(src)).encode())
            h.update(f.read_bytes())
    return h.hexdigest()[:12]


def start_process(cmd: list[str], ready: str, log: Path, timeout: float, env: dict | None = None):
    """Start a long-running helper; return as soon as it prints `ready`. Its output goes to `log`."""
    log.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env,
                            start_new_session=True)
    seen = threading.Event()

    def pump():
        with open(log, "w") as f:
            for line in proc.stdout:
                f.write(line)
                f.flush()
                if ready in line:
                    seen.set()
        seen.set()  # the process ended

    threading.Thread(target=pump, daemon=True).start()
    if not seen.wait(timeout):
        proc.kill()
        raise DriverError(f"{cmd[0]} did not report ready within {timeout:g}s (log: {log})")
    if proc.poll() is not None:
        raise DriverError(f"{cmd[0]} exited: {log.read_text()[-1500:]}")
    return proc


def stop_process(proc: subprocess.Popen | None):
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()


class Driver(ABC):
    platform: str
    app_id: str = ""

    # --- app lifecycle -------------------------------------------------
    @abstractmethod
    def install(self, app_path: Path) -> str:
        """Install the app build, return its package / bundle id."""

    @abstractmethod
    def launch(self): ...

    @abstractmethod
    def stop(self): ...

    @abstractmethod
    def clear_data(self): ...

    @abstractmethod
    def reinstall(self): ...

    # --- observe -------------------------------------------------------
    @abstractmethod
    def screen(self) -> Screen: ...

    @abstractmethod
    def screenshot(self, path: Path): ...

    # --- touch & keys --------------------------------------------------
    @abstractmethod
    def tap(self, x: int, y: int): ...

    @abstractmethod
    def double_tap(self, x: int, y: int): ...

    @abstractmethod
    def long_press(self, x: int, y: int, seconds: float = 1.2): ...

    @abstractmethod
    def drag(self, x1: int, y1: int, x2: int, y2: int, seconds: float = 0.3): ...

    @abstractmethod
    def type_text(self, text: str, at: tuple[int, int] | None = None):
        """Type into the focused field (`at`: the field's center, if known)."""

    @abstractmethod
    def clear_text(self, el: Element): ...

    @abstractmethod
    def key(self, name: str):
        """enter, delete, tab, escape, ..."""

    @abstractmethod
    def back(self): ...

    @abstractmethod
    def home(self): ...

    @abstractmethod
    def hide_keyboard(self): ...

    # --- device --------------------------------------------------------
    @abstractmethod
    def rotate(self, orientation: str): ...

    @abstractmethod
    def set_location(self, lat: float, lon: float): ...

    @abstractmethod
    def open_url(self, url: str): ...

    @abstractmethod
    def dark_mode(self, on: bool): ...

    @abstractmethod
    def grant(self, permission: str): ...

    @abstractmethod
    def network(self, on: bool): ...

    @abstractmethod
    def app_state(self) -> str:
        """'foreground', 'background' or 'not_running' (cheap: no screen dump)."""

    @abstractmethod
    def resume(self):
        """Bring the app back to the foreground without restarting it."""

    def close(self):  # noqa: B027 - optional hook
        """Release anything the driver started (its on-device agent)."""

    def wait_idle(self, timeout: float):  # noqa: B027 - optional hook
        """Return once the UI has stopped changing (at most `timeout` s).

        The default does nothing: iOS XCUITest already waits for the app to be idle
        inside every action and screen read.
        """

    def wait_change(self, timeout: float):  # noqa: B027 - optional hook
        """Return as soon as the screen may have changed (at most `timeout` s).

        The default returns at once, so callers simply read the screen again.
        """

    # --- shared helpers --------------------------------------------------
    def swipe(self, direction: str, el: Element | None = None, screen: Screen | None = None):
        """Finger swipe in `direction` (up/down/left/right), across an element or the screen."""
        if el is not None:
            x1, y1, x2, y2 = el.bounds
        else:
            s = screen or self.screen()
            x1, y1, x2, y2 = 0, 0, s.width, s.height
        cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
        w, h = x2 - x1, y2 - y1
        dx, dy = int(w * 0.35), int(h * 0.3)
        moves = {
            "up": (cx, cy + dy, cx, cy - dy),
            "down": (cx, cy - dy, cx, cy + dy),
            "left": (cx + dx, cy, cx - dx, cy),
            "right": (cx - dx, cy, cx + dx, cy),
        }
        if direction not in moves:
            raise DriverError(f"Unknown swipe direction '{direction}' (use up/down/left/right)")
        self.drag(*moves[direction])

    def scroll(self, direction: str, screen: Screen | None = None):
        """Scroll the content so more of it in `direction` becomes visible."""
        finger = {"down": "up", "up": "down", "left": "right", "right": "left"}
        if direction not in finger:
            raise DriverError(f"Unknown scroll direction '{direction}' (use up/down/left/right)")
        self.swipe(finger[direction], screen=screen)
