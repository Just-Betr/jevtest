"""What jevtest prints: each step and check as it finishes, and a summary per device.

This is the only place that decides wording and layout. With several devices at once, each test's lines are
printed together, tagged with the device, so output never interleaves.
"""

from __future__ import annotations

import sys
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from jevtest.domain.kinds import Status
from jevtest.domain.model import ModelCall, Picked
from jevtest.domain.results import CheckResult, RunResult, StepResult, TestResult
from jevtest.domain.steps import (
    Action,
    Background,
    Clear,
    DarkMode,
    Do,
    Grant,
    Key,
    Location,
    Network,
    OpenUrl,
    Rotate,
    Screenshot,
    Scroll,
    ScrollTo,
    Step,
    Swipe,
    Test,
    Touch,
    TypeText,
    Use,
    Wait,
)

BARE_NAMES = {"Launch": "launch", "Stop": "stop", "Restart": "restart", "ClearData": "clear_data",
              "Reinstall": "reinstall", "Back": "back", "Home": "home", "HideKeyboard": "hide_keyboard"}


def describe_action(action: Action) -> str:  # noqa: C901, PLR0911, PLR0912 - one case per action type
    """An action as the user wrote it, e.g. ``tap: Save`` or ``type: Bret into='Nickname'``."""
    match action:
        case Do(goal):
            return f"do: {goal}"
        case Use(test):
            return f"use: {test}"
        case Touch(gesture, target):
            return f"{gesture}: {target}"
        case Clear(target):
            return f"clear: {target}"
        case TypeText(text, into):
            return f"type: {text}" + (f" into={into!r}" if into is not None else "")
        case Scroll(direction):
            return f"scroll: {direction}"
        case Swipe(direction, target):
            return f"swipe: {direction}" + (f" target={target!r}" if target is not None else "")
        case ScrollTo(text, direction):
            return f"scroll_to: {text} direction={direction.value!r}"
        case Key(name):
            return f"key: {name}"
        case Wait(seconds):
            return f"wait: {seconds}"
        case Background(seconds):
            return f"background: {seconds}"
        case Rotate(orientation):
            return f"rotate: {orientation}"
        case Location(latitude, longitude):
            return f"location: {latitude},{longitude}"
        case OpenUrl(url):
            return f"open_url: {url}"
        case DarkMode(on) | Network(on):
            return f"{'dark_mode' if isinstance(action, DarkMode) else 'network'}: {'on' if on else 'off'}"
        case Grant(permission):
            return f"grant: {permission}"
        case Screenshot(name):
            return f"screenshot: {name}"
        case _:
            return BARE_NAMES[type(action).__name__]


def describe_step(step: Step) -> str:
    """A step's action as the user wrote it; empty for a step that only checks."""
    return "" if step.action is None else describe_action(step.action)


class Printer:
    """Prints whole blocks of lines, so devices running at once never interleave.

    Args:
        parallel: Several devices are running: tag each line with its device.
        out: Where to print.
    """

    def __init__(self, *, parallel: bool, out: TextIO | None = None) -> None:
        self.parallel = parallel
        self.out = out or sys.stdout
        self._lock = threading.Lock()

    def block(self, tag: str, lines: Sequence[str]) -> None:
        """Print lines together, tagged with `tag` when devices run in parallel."""
        if self.parallel:
            lines = [f"[{tag}] {line}" if line else line for line in "\n".join(lines).split("\n")]
        with self._lock:
            print("\n".join(lines), file=self.out, flush=True)


class ConsoleListener:
    """Renders a device's run as it happens (the `RunListener` the command line uses).

    A single device streams each line; with several devices, each test is printed whole when it ends. Every
    test's lines are also kept, for the JUnit and JSON reports.

    Args:
        printer: Where lines go.
        tag: The device's tag, e.g. ``android · Pixel 8``.
        verbose: Also print every model question and answer.
    """

    def __init__(self, printer: Printer, tag: str, *, verbose: bool) -> None:
        self.printer = printer
        self.tag = tag
        self.verbose = verbose
        self.logs: dict[str, list[str]] = {}
        self._current: list[str] = []

    def _emit(self, line: str) -> None:
        self._current.append(line)
        if not self.printer.parallel:
            self.printer.block(self.tag, [line])

    def _calls(self, pad: str, calls: Sequence[ModelCall]) -> None:
        if not self.verbose:
            return
        for call in calls:
            for qid, answer in call.answers.items():
                if isinstance(answer, Picked):
                    top = sorted(answer.probabilities.items(), key=lambda kv: (-kv[1], kv[0]))[:3]
                    got = f"{answer.choice}  [" + ", ".join(f"{k} {v:.2f}" for k, v in top) + "]"
                else:
                    got = f"yes={answer.yes:.2f}"
                self._emit(f"{pad}    jev {qid}: {got}")
            source = "from lockfile" if call.recorded else f"{call.ms} ms"
            self._emit(f"{pad}    jev call {source}, {len(call.questions)} question(s)")

    def test_started(self, test: Test) -> None:
        """Begin a test's block."""
        self._current = []
        self._emit(f"\n▶ {test.name}")

    def start_failed(self, test: Test, reason: str) -> None:
        """The app couldn't be started."""
        self._emit(f"  ✗ could not start app: {reason}")

    def use_started(self, name: str, depth: int) -> None:
        """A `use:` step begins."""
        self._emit(f"{'  ' * (depth + 1)}▸ use: {name}")

    def step_done(self, result: StepResult, depth: int) -> None:
        """An action finished: the action, what it did, Jev's moves."""
        pad = "  " * (depth + 1)
        mark = "✓" if result.status is Status.PASS else "✗"
        extra = f" — {result.detail}" if result.detail else ""
        self._emit(f"{pad}{mark} {describe_step(result.step)} ({result.seconds}s){extra}")
        for d in result.decisions:
            self._emit(f"{pad}    → {d.move.describe()}  (confidence {d.confidence:.2f})")
        self._calls(pad, result.model_calls)

    def check_done(self, result: CheckResult, depth: int) -> None:
        """A check finished."""
        pad = "  " * (depth + 1)
        mark = "✓" if result.status is Status.PASS else "✗"
        extra = f" — {result.detail}" if result.detail else ""
        self._emit(f"{pad}{mark} {result.check.name}: {result.check.text}{extra}")
        self._calls(pad, result.model_calls)

    def test_done(self, result: TestResult) -> None:
        """End a test's block: print it (in parallel mode) and keep it for the reports."""
        self._emit(f"  {'PASS' if result.status is Status.PASS else 'FAIL'} {result.name} ({result.seconds}s)")
        self.logs[result.name] = list(self._current)
        if self.printer.parallel:
            self.printer.block(self.tag, self._current)


def summary(result: RunResult, calls: Sequence[ModelCall], out: Path) -> list[str]:
    """A device's summary: the tally, each failure's reason, and Jev's share of the run."""
    live = [c for c in calls if not c.recorded]
    jev_s = sum(c.ms for c in live) / 1000
    cost = sum(c.cost for c in live)
    share = f" ({jev_s / result.seconds:.0%} of run time)" if result.seconds else ""
    lines = [f"\n{result.passed}/{len(result.tests)} passed in {result.seconds:.0f}s"]
    lines += [f"  FAILED {t.name}: {t.failure}" for t in result.tests if t.status is Status.FAIL]
    lines.append(f"Jev: {len(calls)} decision{'' if len(calls) == 1 else 's'}, {len(calls) - len(live)} from "
                 f"lockfile, {len(live)} asked live in {jev_s:.1f}s{share}, ${cost:.4f}")
    lines.append(f"Results: {out}")
    return lines
