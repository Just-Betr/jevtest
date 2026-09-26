"""Running tests: each step's action on the device, then its checks, until the first failure.

The runner reaches the device, the model and time only through the domain's ports, reports progress to a
`RunListener` as it goes, and returns plain result records. It prints nothing and writes no files except the
screenshots it is asked to take.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path
from typing import TypeVar, assert_never

from jevtest.domain.decisions import (
    ClearField,
    CloseKeyboard,
    Decision,
    Finished,
    GoBack,
    Impossible,
    Move,
    PressEnter,
    ScrollPage,
    SwipeElement,
    TouchElement,
    TypeInto,
    WaitForScreen,
)
from jevtest.domain.failures import DeviceError, ModelError, StepFailed
from jevtest.domain.kinds import AppState, Direction, Gesture, Status
from jevtest.domain.model import ModelCall
from jevtest.domain.ports import Clock, Device, RunListener
from jevtest.domain.results import CheckResult, RunResult, StepResult, TestResult
from jevtest.domain.rules import LAUNCH_QUIET, MAX_ACTIONS, MAX_SCROLLS, SETTLE, THRESHOLD, TIMEOUT
from jevtest.domain.screen import Element, Screen
from jevtest.domain.steps import (
    Action,
    Back,
    Background,
    Check,
    Clear,
    ClearData,
    DarkMode,
    Do,
    Expect,
    Grant,
    HideKeyboard,
    Home,
    Key,
    Launch,
    Location,
    Network,
    OpenUrl,
    Reinstall,
    Restart,
    Rotate,
    Screenshot,
    Scroll,
    ScrollTo,
    See,
    Step,
    Stop,
    Suite,
    Swipe,
    Test,
    Touch,
    TypeText,
    Use,
    Wait,
)
from jevtest.domain.variables import fill

from .brain import Brain

APP_MAY_LEAVE = (Stop, ClearData, Reinstall, Home, OpenUrl)
"""Actions after which the app may be closed or in the background."""

NO_SETTLE = (Stop, ClearData, Reinstall, Home, Wait, Screenshot, ScrollTo, Do, Use)
"""Actions that do their own waiting, or change nothing on screen."""

T = TypeVar("T")


class TestRunner:
    """Runs a suite's tests on one device.

    Args:
        suite: The test file: its tests, the tests `use:` can name, and its ``${NAME}`` values.
        device: The device, with the app installed.
        brain: jevtest's judgement, backed by the decision model.
        screenshots: Where failure screenshots (and `screenshot:` steps) are saved.
        clock: Time.
        listener: Told about each test, step and check as it finishes.
    """

    __test__ = False  # not a pytest test class

    def __init__(self, suite: Suite, device: Device, brain: Brain, screenshots: Path, *, clock: Clock,
                 listener: RunListener) -> None:
        self.suite = suite
        self.device = device
        self.brain = brain
        self.screenshots = screenshots
        self.clock = clock
        self.listener = listener
        self._app_should_run = False
        self._shots = 0

    # --- tests -------------------------------------------------------------------------------------------------
    def run(self) -> RunResult:
        """Run every test in the suite, in order."""
        return RunResult(tuple(self.run_test(test) for test in self.suite.tests))

    def run_test(self, test: Test) -> TestResult:
        """Start the app, then run the test's steps until one fails."""
        self.listener.test_started(test)
        started = self.clock.now()
        try:
            self._start_app(test.fresh)
        except DeviceError as e:
            self.listener.start_failed(test, str(e))
            result = TestResult(test.name, Status.FAIL, self._since(started), start_failure=str(e),
                                screenshot=self._screenshot(f"FAIL_{test.name}"))
            self.listener.test_done(result)
            return result
        steps, status = self._run_steps(test.steps, 0)
        if status is Status.FAIL:
            steps = (*steps[:-1], replace(steps[-1], screenshot=self._screenshot(f"FAIL_{test.name}")))
        result = TestResult(test.name, status, self._since(started), steps)
        self.listener.test_done(result)
        return result

    def _start_app(self, fresh: bool) -> None:
        self.device.check_ready()
        if fresh:
            self.device.restore()  # an earlier test's rotate: or dark_mode: must not leak into this one
            self.device.stop()
            self.device.clear_data()
        if fresh or self.device.app_state() is not AppState.FOREGROUND:
            self.device.launch()
            self.device.wait_idle(SETTLE, quiet=LAUNCH_QUIET)
        self._app_should_run = True

    def _run_steps(self, steps: Sequence[Step], depth: int) -> tuple[tuple[StepResult, ...], Status]:
        done: list[StepResult] = []
        for step in steps:
            done.append(self._run_step(step, depth))
            if done[-1].status is Status.FAIL:
                return tuple(done), Status.FAIL
        return tuple(done), Status.PASS

    def _run_step(self, step: Step, depth: int) -> StepResult:
        started = self.clock.now()
        if isinstance(step.action, Use):
            self.listener.use_started(step.action.test, depth)
            inner, status = self._run_steps(self.suite.library[step.action.test].steps, depth + 1)
            result = StepResult(step, status, self._since(started), steps=inner)
        elif step.action is not None:
            result = self._run_action(step, step.action, started)
            self.listener.step_done(result, depth)
        else:
            result = StepResult(step, Status.PASS, 0.0)
        if result.status is Status.PASS and step.checks:
            checks = self._run_checks(step, depth + 2 if step.action is not None else depth)
            status = Status.FAIL if checks[-1].status is Status.FAIL else Status.PASS
            result = replace(result, status=status, checks=checks)
        return replace(result, seconds=self._since(started))

    def _run_action(self, step: Step, action: Action, started: float) -> StepResult:
        mark = len(self.brain.model.calls)
        decisions: list[Decision] = []
        status, detail = Status.PASS, None
        try:
            detail = self._act(step, action, decisions)
            if not isinstance(action, NO_SETTLE):
                self._settle()
            self._check_app(action)
        except (StepFailed, DeviceError, ModelError) as e:
            status, detail = Status.FAIL, str(e)
        return StepResult(step, status, self._since(started), detail, tuple(decisions), self._calls_since(mark))

    def _run_checks(self, step: Step, depth: int) -> tuple[CheckResult, ...]:
        results: list[CheckResult] = []
        for check in step.checks:
            mark = len(self.brain.model.calls)
            try:
                result = CheckResult(check, Status.PASS, self._check(check, self._timeout(step)))
            except (StepFailed, DeviceError, ModelError) as e:
                result = CheckResult(check, Status.FAIL, str(e))
            result = replace(result, model_calls=self._calls_since(mark))
            self.listener.check_done(result, depth)
            results.append(result)
            if result.status is Status.FAIL:
                break
        return tuple(results)

    # --- helpers -----------------------------------------------------------------------------------------------
    def _since(self, started: float) -> float:
        return round(self.clock.now() - started, 1)

    def _calls_since(self, mark: int) -> tuple[ModelCall, ...]:
        return tuple(self.brain.model.calls[mark:])

    def _settle(self) -> None:
        """Wait until the screen stops changing: the device reacts to itself, there's no fixed sleep."""
        self.device.wait_idle(SETTLE)

    def _value(self, text: str) -> str:
        """`text` with its ``${NAME}`` values filled in, for what the app sees only."""
        return fill(text, self.suite.variables)

    @staticmethod
    def _timeout(step: Step) -> float:
        """How long a step waits for what it looks for: its own `timeout:`, or the fixed default."""
        return TIMEOUT if step.timeout is None else step.timeout

    def _screenshot(self, name: str) -> str:
        self._shots += 1
        safe = re.sub(r"[^A-Za-z0-9_-]+", "_", name)[:60].strip("_") or "screen"
        path = self.screenshots / f"{self._shots:03d}_{safe}.png"
        try:
            self.device.screenshot(path)
        except DeviceError as e:
            return f"(screenshot failed: {e})"
        return path.name

    def _poll(self, attempt: Callable[[Screen], T | None], timeout: float, failure: str) -> T:
        """Call `attempt` until it returns something, or time runs out.

        `attempt` runs again only when the screen has changed, so an unchanged screen is never judged
        twice. Between reads it waits for the next change.
        """
        deadline = self.clock.now() + timeout
        last: Screen | None = None
        while True:
            screen = self.device.screen()
            if screen != last:
                last = screen
                result = attempt(screen)
                if result is not None:
                    return result
            left = deadline - self.clock.now()
            if left <= 0:
                raise StepFailed(failure)
            self.device.wait_change(left)

    def _locate(self, target: str, timeout: float, *, editable: bool = False) -> Element:
        wanted = self._value(target)

        def attempt(screen: Screen) -> Element | None:
            return self.brain.locate(wanted, screen, screen.editable if editable else None)

        what = "text field" if editable else "element"
        return self._poll(attempt, timeout, f"Could not find {what} '{target}' on screen")

    def _check_app(self, action: Action) -> None:
        """Fail if the app crashed or left the foreground during the action."""
        if not self._app_should_run or isinstance(action, APP_MAY_LEAVE):
            return
        state = self.device.app_state()
        if state is AppState.NOT_RUNNING:
            raise StepFailed("The app is no longer running (crashed or closed)")
        if state is AppState.BACKGROUND:
            raise StepFailed("The app left the foreground")

    def _check(self, check: Check, timeout: float) -> str | None:
        """`expect:` asks the model; `see:` and `not_see:` compare text. Retries until `timeout`."""
        wanted = self._value(check.text)
        if isinstance(check, Expect):
            last: list[float] = []

            def judged(screen: Screen) -> str | None:
                last.append(self.brain.check(wanted, screen))
                return f"Jev {last[-1]:.2f}" if last[-1] > THRESHOLD else None

            try:
                return self._poll(judged, timeout, "")
            except StepFailed:
                raise StepFailed(f"Jev says false ({last[-1]:.2f})") from None
        present = isinstance(check, See)

        def seen(screen: Screen) -> str | None:
            return "" if screen.shows(wanted) == present else None

        failure = "not on screen" if present else "still on screen"
        return self._poll(seen, timeout, failure) or None

    # --- actions -----------------------------------------------------------------------------------------------
    def _act(  # noqa: C901, PLR0911, PLR0912, PLR0915 - one flat case per action type is the clearest form
        self, step: Step, action: Action, decisions: list[Decision],
    ) -> str | None:
        """Do one action; return what it acted on, for the log."""
        d = self.device
        match action:
            case Launch():
                d.launch()
                self._app_should_run = True
            case Stop():
                d.stop()
                self._app_should_run = False
            case Restart():
                d.stop()
                d.launch()
                self._app_should_run = True
            case ClearData():
                d.stop()
                d.clear_data()
                self._app_should_run = False
            case Reinstall():
                d.reinstall()
                self._app_should_run = False
            case Back():
                d.back()
            case Home():
                d.home()
            case HideKeyboard():
                d.hide_keyboard()
            case Wait(seconds):
                self.clock.sleep(seconds)
            case Background(seconds):
                d.home()
                self.clock.sleep(seconds)
                d.resume()
            case Key(name):
                d.key(name)
            case Scroll(direction):
                d.scroll(direction)
            case Swipe(direction, target):
                el = self._locate(target, self._timeout(step)) if target is not None else None
                d.swipe(direction, element=el)
                return f"on {el.label()}" if el else None
            case ScrollTo(text, direction):
                return self._scroll_to(text, direction)
            case Touch(gesture, target):
                el = self._locate(target, self._timeout(step))
                self._touch(gesture, el)
                return f"on {el.label()}"
            case Clear(target):
                el = self._locate(target, self._timeout(step), editable=True)
                d.clear_text(el)
                return f"on {el.label()}"
            case TypeText(text, into):
                if into is None:
                    d.type_text(self._value(text))
                    return None
                el = self._locate(into, self._timeout(step), editable=True)
                d.type_text(self._value(text), at=el.center)  # the device focuses the field
                return f"into {el.label()}"
            case Rotate(orientation):
                d.rotate(orientation)
            case Location(latitude, longitude):
                d.set_location(latitude, longitude)
            case OpenUrl(url):
                d.open_url(self._value(url))
            case Screenshot(name):
                return f"saved {self._screenshot(name)}"
            case DarkMode(on):
                d.dark_mode(on)
            case Grant(permission):
                d.grant(permission)
            case Network(on):
                d.network(on)
            case Do(goal):
                return self._achieve(goal, decisions)
            case Use():  # pragma: no cover - `use:` steps are run by _run_step
                raise AssertionError("use: steps are not actions")
            case _:  # pragma: no cover
                assert_never(action)
        return None

    def _touch(self, gesture: Gesture, el: Element) -> None:
        x, y = el.center
        match gesture:
            case Gesture.TAP:
                self.device.tap(x, y)
            case Gesture.DOUBLE_TAP:
                self.device.double_tap(x, y)
            case Gesture.LONG_PRESS:
                self.device.long_press(x, y)
            case _:  # pragma: no cover - every gesture is handled above
                assert_never(gesture)

    def _scroll_to(self, text: str, direction: Direction) -> str | None:
        """Scroll until the text is on screen, or the content stops moving, or `MAX_SCROLLS` scrolls.

        The text is matched in code, like `see:`: a model asked whether absent text is there tends to pick
        something similar.
        """
        wanted = self._value(text)
        screen = self.device.screen()
        for scrolls in range(MAX_SCROLLS + 1):
            if screen.shows(wanted):
                return f"{scrolls} scroll(s)" if scrolls else None
            if scrolls == MAX_SCROLLS:
                raise StepFailed(f"Scrolled {direction} {MAX_SCROLLS} times but never found '{text}'")
            self.device.scroll(direction, screen=screen)
            self._settle()
            before, screen = screen, self.device.screen()
            if screen == before:
                raise StepFailed(f"Scrolled {direction} to the end but never found '{text}'")
        raise AssertionError("unreachable")  # pragma: no cover

    # --- the goal loop -----------------------------------------------------------------------------------------
    def _achieve(self, goal: str, decisions: list[Decision]) -> str:
        """Look at the screen, let the model pick the next move, make it; repeat until the goal is done."""
        taken: list[str] = []
        while True:
            screen = self.device.screen()
            decision = self.brain.next_action(goal, screen, taken)
            decisions.append(decision)
            move = decision.move
            if isinstance(move, Finished):
                return f"{len(taken)} action(s)"
            if isinstance(move, Impossible):
                raise StepFailed("Jev says the goal is impossible from this screen")
            if len(taken) == MAX_ACTIONS:
                raise StepFailed(f"Goal not reached after {MAX_ACTIONS} actions")
            if taken[-2:] == [move.describe()] * 2:
                raise StepFailed(f"Stuck repeating: {move.describe()}")
            self._make(move, screen)
            taken.append(move.describe())
            self._settle()

    def _make(self, move: Move, screen: Screen) -> None:
        """Make one of the model's moves on the device."""
        d = self.device
        match move:
            case TouchElement(gesture, element):
                self._touch(gesture, element)
            case SwipeElement(direction, element):
                d.swipe(direction, element=element)
            case TypeInto(element, text):
                # a focused field with the keyboard up is ready; tapping it would move the caret
                ready = element.focused and screen.keyboard_visible
                d.type_text(self._value(text), at=None if ready else element.center)
            case ClearField(element):
                d.clear_text(element)
            case ScrollPage(direction):
                d.scroll(direction, screen=screen)
            case GoBack():
                d.back()
            case PressEnter():
                d.key("enter")
            case CloseKeyboard():
                d.hide_keyboard()
            case WaitForScreen():
                d.wait_change(SETTLE)
            case Finished() | Impossible():  # pragma: no cover - _achieve ends before making these
                raise AssertionError(f"{move} is not made on the device")
            case _:  # pragma: no cover - every move is handled above
                assert_never(move)
