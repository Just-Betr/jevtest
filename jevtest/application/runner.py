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
    ElementMove,
    Finished,
    GoBack,
    Impossible,
    PageMove,
    PressEnter,
    ScrollPage,
    SwipeElement,
    TouchElement,
    TypeInto,
    WaitForScreen,
)
from jevtest.domain.failures import DeviceError, ModelError, NotRecorded, StepFailed
from jevtest.domain.kinds import AppState, Direction, Gesture, Status
from jevtest.domain.model import ModelCall
from jevtest.domain.ports import Clock, Device, RunListener
from jevtest.domain.results import CheckResult, RunResult, StepResult, TestResult
from jevtest.domain.screen import Element, Screen, near_names
from jevtest.domain.settings import Settings
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
from jevtest.domain.variables import fill, hide

from .brain import Brain, Located

LAUNCH_QUIET = 0.5
"""Seconds without a change that count as "the app has finished starting" (apps pause longer while starting)."""

END_OF_CONTENT = 2
"""Scrolls in a row that must move nothing before `scroll_to:` calls it the end. One isn't enough: a real
phone's web view sometimes ignores a single scroll."""

T = TypeVar("T")


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


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

    def __init__(
        self, suite: Suite, device: Device, brain: Brain, screenshots: Path, *, clock: Clock, listener: RunListener
    ) -> None:
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
            self._start_app(fresh=test.fresh)
        except DeviceError as e:
            failure = self._masked(str(e))
            self.listener.start_failed(failure)
            result = TestResult(
                test.name,
                Status.FAIL,
                self._since(started),
                start_failure=failure,
                screenshot=self._screenshot(f"FAIL_{test.name}"),
            )
            self.listener.test_done(result)
            return result
        steps, status = self._run_steps(test.steps, 0)
        if status is Status.FAIL:
            steps = (*steps[:-1], replace(steps[-1], screenshot=self._screenshot(f"FAIL_{test.name}")))
        result = TestResult(test.name, status, self._since(started), steps)
        self.listener.test_done(result)
        return result

    def _start_app(self, *, fresh: bool) -> None:
        self.device.check_ready()
        if fresh:
            self.device.restore()  # an earlier test's rotate: or dark_mode: must not leak into this one
            self.device.stop()
            self.device.clear_data()
        if fresh or self.device.app_state() is not AppState.FOREGROUND:
            self.device.launch()
            self.device.wait_idle(self.suite.settings.settle, quiet=LAUNCH_QUIET)
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
            if action.settles:
                self._settle(step.settings)
            self._check_app(action)
        except (StepFailed, DeviceError, ModelError) as e:
            status, detail = Status.FAIL, str(e)
        return StepResult(
            step,
            status,
            self._since(started),
            detail and self._masked(detail),
            tuple(self._masked_decision(d) for d in decisions),
            self._calls_since(mark),
        )

    def _run_checks(self, step: Step, depth: int) -> tuple[CheckResult, ...]:
        results: list[CheckResult] = []
        for check in step.checks:
            mark = len(self.brain.model.calls)
            try:
                detail = self._check(check, step.settings)
                result = CheckResult(check, Status.PASS, detail and self._masked(detail))
            except (StepFailed, DeviceError, ModelError) as e:
                result = CheckResult(check, Status.FAIL, self._masked(str(e)))
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

    def _settle(self, settings: Settings) -> None:
        """Wait until the screen stops changing: the device reacts to itself, there's no fixed sleep."""
        self.device.wait_idle(settings.settle)

    def _value(self, text: str) -> str:
        """`text` with its ``${NAME}`` values filled in, for what the app sees only."""
        return fill(text, self.suite.variables)

    def _screenshot(self, name: str) -> str:
        self._shots += 1
        safe = re.sub(r"[^A-Za-z0-9_-]+", "_", name)[:60].strip("_") or "screen"
        path = self.screenshots / f"{self._shots:03d}_{safe}.png"
        try:
            self.device.screenshot(path)
        except DeviceError as e:
            return f"(screenshot failed: {e})"
        return path.name

    def _poll(
        self,
        attempt: Callable[[Screen], T | None],
        timeout: float,
        failure: Callable[[Screen], str],
        *,
        steady: bool = False,
    ) -> T:
        """Call `attempt` until it returns something, or time runs out.

        `attempt` runs again only when the screen has changed, so an unchanged screen is never judged
        twice. Between reads it waits for the next change. When time runs out, `failure` says why, from the
        last screen. With `steady`, each screen is one that has stopped changing (see `_steady_screen`): for
        attempts that ask Jev, whose answer is recorded for that exact screen.

        A screen that isn't in the lockfile (``--lock frozen``) may be one still changing: the next one is
        looked up too, and if the last one wasn't recorded either, that is the error.

        Raises:
            StepFailed: Time ran out: `failure` says why. With `steady`, also when the screen never stopped
                changing at all.
        """
        deadline = self.clock.now() + timeout
        last: Screen | None = None
        missed: list[NotRecorded] = []
        while True:
            screen = self._steady_screen(deadline) if steady else self.device.screen()
            if screen is None:  # never still until the deadline
                if last is None:
                    raise StepFailed(
                        f"The screen never stopped changing in {timeout:g} s: Jev is only asked about a screen "
                        "that holds still, since one that keeps changing (a clock, a counter) can't be recorded "
                        "or replayed"
                    )
                screen = last  # it did hold still before: that one's answer stands
            if screen != last:
                last = screen
                try:
                    result = attempt(screen)
                except NotRecorded as e:
                    missed[:], result = [e], None
                else:
                    missed.clear()
                if result is not None:
                    return result
            left = deadline - self.clock.now()
            if left <= 0:
                if missed:
                    raise missed[0]
                raise StepFailed(failure(screen))
            self.device.wait_change(left)

    def _steady_screen(self, deadline: float) -> Screen | None:
        """The screen once two reads a still moment apart agree; None if that doesn't happen by `deadline`.

        A still moment alone can come in the middle of an animation, like a keyboard sliding up, and a screen
        read then is never seen again: its recorded answer would never be replayed.
        """
        screen = self.device.screen()
        while (left := deadline - self.clock.now()) > 0:
            self.device.wait_idle(left)
            again = self.device.screen()
            if again == screen:
                return screen
            screen = again
        return None

    def _near(self, text: str, elements: Sequence[Element]) -> str:
        """The close-but-not-exact texts on screen, for an error. Never matched: shown so the test can be fixed.

        A ``${NAME}`` value in them is shown as its name, like everywhere else in the output.
        """
        near = near_names(text, elements)
        if not near:
            return ""
        return "; close but not exact: " + ", ".join(f"'{n}'" for n in near)

    def _masked(self, text: str) -> str:
        """`text` as output shows it: a ``${NAME}`` value the screen shows is written as its name."""
        return hide(text, self.suite.variables)

    def _masked_decision(self, decision: Decision) -> Decision:
        """The decision as output shows it: its element's texts masked like everything else."""
        move = decision.move
        if not isinstance(move, ElementMove):
            return decision
        el = move.element
        masked = replace(
            el,
            text=self._masked(el.text),
            parts=tuple(self._masked(part) for part in el.parts),
            hint=self._masked(el.hint),
            value=self._masked(el.value),
            resource_id=self._masked(el.resource_id),
        )
        return replace(decision, move=replace(move, element=masked))

    def _locate(
        self, target: str, timeout: float, *, editable: bool = False, typing: bool = False
    ) -> tuple[Located, Screen]:
        """The element `target` names, and the screen it was found on.

        Never one under the keyboard, except, when `typing`, a field that already takes the keys (no tap needed).
        """
        wanted = self._value(target)

        def pool(screen: Screen) -> Sequence[Element]:
            return screen.editable if editable else screen.elements

        covered: list[Located] = []  # found, but under the keyboard: touching it would hit a key

        def attempt(screen: Screen) -> tuple[Located, Screen] | None:
            found = self.brain.locate(wanted, screen, pool(screen))
            if found is None:
                covered.clear()
                return None
            ready = typing and screen.takes_keys(found.element)
            covered[:] = [found] if screen.under_keyboard(found.element) and not ready else []
            return None if covered else (found, screen)

        def failure(screen: Screen) -> str:
            if covered:
                return f"{covered[0].describe()} is under the keyboard: close it first with a `hide_keyboard` step"
            what = "text field" if editable else "element"
            return f"Could not find {what} '{target}' on screen{self._near(wanted, pool(screen))}"

        return self._poll(attempt, timeout, failure)

    def _check_app(self, action: Action) -> None:
        """Fail if the app crashed or left the foreground during the action."""
        if not self._app_should_run or action.app_may_leave:
            return
        state = self.device.app_state()
        if state is AppState.NOT_RUNNING:
            raise StepFailed("The app is no longer running (crashed or closed)")
        if state is AppState.BACKGROUND:
            raise StepFailed("The app left the foreground")

    def _check(self, check: Check, settings: Settings) -> str | None:
        """`expect:` asks the model; `see:` and `not_see:` compare text. Retries until the step's timeout."""
        timeout = settings.timeout
        wanted = self._value(check.text)
        if isinstance(check, Expect):
            last: list[float] = []

            def judged(screen: Screen) -> str | None:
                last.append(self.brain.check(wanted, screen))
                return f"Jev {last[-1]:.2f}" if last[-1] > settings.confidence else None

            return self._poll(judged, timeout, lambda _: f"Jev says false ({last[-1]:.2f})", steady=True)
        present = isinstance(check, See)

        def seen(screen: Screen) -> str | None:
            return "" if screen.shows(wanted) == present else None

        def failure(screen: Screen) -> str:
            return f"not on screen{self._near(wanted, screen.elements)}" if present else "still on screen"

        return self._poll(seen, timeout, failure) or None

    # --- actions -----------------------------------------------------------------------------------------------
    def _act(self, step: Step, action: Action, decisions: list[Decision]) -> str | None:
        """Do one action; return what it acted on, for the log."""
        match action:
            case Launch() | Stop() | Restart() | ClearData() | Reinstall():
                self._lifecycle(action)
            case Touch() | Clear() | TypeText() | Swipe():
                return self._on_element(action, step.settings.timeout)
            case Wait() | Background():
                self._pause(action)
            case ScrollTo(text, direction):
                return self._scroll_to(text, direction, step.settings)
            case Do(goal):
                return self._achieve(goal, decisions, step.settings)
            case Screenshot(name):
                return f"saved {self._screenshot(name)}"
            case Back() | Home() | HideKeyboard() | Key() | Scroll() | OpenUrl():
                self._navigate(action)
            case Rotate() | Location() | DarkMode() | Grant() | Network():
                self._set_device(action)
            case Use():  # pragma: no cover - `use:` steps are run by _run_step
                raise AssertionError("use: steps are not actions")
            case _:  # pragma: no cover - every action is handled above
                assert_never(action)
        return None

    def _pause(self, action: Wait | Background) -> None:
        """Let time pass: in the app (`wait:`), or with the app in the background (`background:`)."""
        match action:
            case Wait(seconds):
                self.clock.sleep(seconds)
            case Background(seconds):
                self.device.home()
                self.clock.sleep(seconds)
                self.device.resume()
            case _:  # pragma: no cover - both pauses are handled above
                assert_never(action)

    def _lifecycle(self, action: Launch | Stop | Restart | ClearData | Reinstall) -> None:
        """Start, stop or reset the app, and remember whether it should now be running."""
        d = self.device
        match action:
            case Launch():
                d.launch()
            case Stop():
                d.stop()
            case Restart():
                d.stop()
                d.launch()
            case ClearData():
                d.stop()
                d.clear_data()
            case Reinstall():
                d.reinstall()
            case _:  # pragma: no cover - every lifecycle action is handled above
                assert_never(action)
        self._app_should_run = isinstance(action, Launch | Restart)

    def _on_element(self, action: Touch | Clear | TypeText | Swipe, timeout: float) -> str | None:
        """An action on an element the step names: find it (waiting up to `timeout`), then act on it."""
        d = self.device
        match action:
            case Touch(gesture, target):
                found, _ = self._locate(target, timeout)
                self._touch(gesture, found.element)
            case Clear(target):
                found, _ = self._locate(target, timeout, editable=True)
                d.clear_text(found.element)
            case TypeText(text, into):
                if into is None:
                    d.type_text(self._value(text))
                    return None
                found, screen = self._locate(into, timeout, editable=True, typing=True)
                # the device focuses the field; one that already takes the keys isn't tapped (see `takes_keys`)
                d.type_text(self._value(text), at=None if screen.takes_keys(found.element) else found.element.center)
                return f"into {found.describe()}"
            case Swipe(direction, target):
                if target is None:
                    d.swipe(direction)
                    return None
                found, _ = self._locate(target, timeout)
                d.swipe(direction, element=found.element)
            case _:  # pragma: no cover - every element action is handled above
                assert_never(action)
        return f"on {found.describe()}"

    def _navigate(self, action: Back | Home | HideKeyboard | Key | Scroll | OpenUrl) -> None:
        """Move around the app or the system: one device call each."""
        d = self.device
        match action:
            case Back():
                d.back()
            case Home():
                d.home()
            case HideKeyboard():
                d.hide_keyboard()
            case Key(name):
                d.key(name)
            case Scroll(direction):
                d.scroll(direction)
            case OpenUrl(url):
                d.open_url(self._value(url))
            case _:  # pragma: no cover - every navigation is handled above
                assert_never(action)

    def _set_device(self, action: Rotate | Location | DarkMode | Grant | Network) -> None:
        """Change a device setting; the device puts each back at the end of the run."""
        d = self.device
        match action:
            case Rotate(orientation):
                d.rotate(orientation)
            case Location(latitude, longitude):
                d.set_location(latitude, longitude)
            case DarkMode(on):
                d.dark_mode(on=on)
            case Grant(permission):
                d.grant(permission)
            case Network(on):
                d.network(on=on)
            case _:  # pragma: no cover - every device setting is handled above
                assert_never(action)

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

    def _scroll_to(self, text: str, direction: Direction, settings: Settings) -> str | None:
        """Scroll until an element says exactly the text, or the content stops moving, or `max_scrolls` scrolls.

        The text is matched in code, exactly, like `see:`: a model asked whether absent text is there tends to
        pick something similar.
        """
        wanted = self._value(text)
        screen = self.device.screen()
        scrolls, unmoved = 0, 0  # unmoved: scrolls in a row that moved nothing
        while True:
            if screen.shows(wanted):
                return f"{scrolls} scroll(s)" if scrolls else None
            if scrolls == settings.max_scrolls:
                raise StepFailed(
                    f"Scrolled {direction} {_count(scrolls, 'time')} (max_scrolls) but never found '{text}'"
                    f"{self._near(wanted, screen.elements)}"
                )
            self.device.scroll(direction, screen=screen)
            self._settle(settings)
            before, screen = screen, self.device.screen()
            scrolls, unmoved = scrolls + 1, (unmoved + 1 if screen == before else 0)
            if unmoved == END_OF_CONTENT:
                raise StepFailed(
                    f"Scrolled {direction} to the end but never found '{text}'{self._near(wanted, screen.elements)}"
                )

    # --- the goal loop -----------------------------------------------------------------------------------------
    def _achieve(self, goal: str, decisions: list[Decision], settings: Settings) -> str:
        """Look at the screen, let the model pick the next move, make it; repeat until the goal is done."""
        taken: list[str] = []
        while True:
            decision, screen = self._poll(  # every screen gets a decision; only one not recorded waits for the next
                lambda s: (self.brain.next_action(goal, s, taken), s),
                settings.timeout,
                lambda _: "",
                steady=True,
            )
            decisions.append(decision)
            move = decision.move
            if isinstance(move, Finished):
                return f"{len(taken)} action(s)"
            if isinstance(move, Impossible):
                raise StepFailed("Jev says the goal is impossible from this screen")
            if len(taken) == settings.max_actions:
                raise StepFailed(f"Goal not reached after {_count(len(taken), 'action')} (max_actions)")
            if taken[-2:] == [move.describe()] * 2:
                raise StepFailed(f"Stuck repeating: {move.describe()}")
            self._make(move, screen, settings)
            taken.append(move.describe())
            self._settle(settings)

    def _make(self, move: ElementMove | PageMove, screen: Screen, settings: Settings) -> None:
        """Make one of the model's moves on the device.

        A move on an element under the keyboard closes the keyboard first: a touch there would hit a key.
        """
        d = self.device
        ready = isinstance(move, TypeInto) and screen.takes_keys(move.element)
        if isinstance(move, ElementMove) and not ready and screen.under_keyboard(move.element):
            move = replace(move, element=self._uncovered(move.element, settings))
        match move:
            case TouchElement(gesture, element):
                self._touch(gesture, element)
            case SwipeElement(direction, element):
                d.swipe(direction, element=element)
            case TypeInto(element, text):
                d.type_text(self._value(text), at=None if ready else element.center)
            case ClearField(element):
                d.clear_text(element)
            case _:
                self._make_on_page(move, screen, settings)

    def _uncovered(self, element: Element, settings: Settings) -> Element:
        """Close the keyboard over `element`, then find it again: the screen may move once the keyboard is gone.

        Raises:
            StepFailed: The keyboard stays up, or the element isn't on screen exactly once without it.
        """
        self.device.hide_keyboard()
        same = (element.kind, element.text, element.hint, element.resource_id)

        def found(screen: Screen) -> Element | None:
            matches = [el for el in screen.elements if (el.kind, el.text, el.hint, el.resource_id) == same]
            return matches[0] if not screen.keyboard_visible and len(matches) == 1 else None

        def failure(screen: Screen) -> str:
            if screen.keyboard_visible:
                return f"{element.label()} is under the keyboard, and the keyboard didn't close"
            count = sum((el.kind, el.text, el.hint, el.resource_id) == same for el in screen.elements)
            if count:
                return (
                    f"{element.label()} was under the keyboard, and once it closed the screen shows it {count} "
                    "times: jevtest won't guess which one Jev meant"
                )
            return f"{element.label()} was under the keyboard, and isn't on screen once it closed"

        return self._poll(found, settings.timeout, failure)

    def _make_on_page(self, move: PageMove, screen: Screen, settings: Settings) -> None:
        d = self.device
        match move:
            case ScrollPage(direction):
                d.scroll(direction, screen=screen)
            case GoBack():
                d.back()
            case PressEnter():
                d.key("enter")
            case CloseKeyboard():
                d.hide_keyboard()
            case WaitForScreen():
                d.wait_change(settings.settle)
            case _:  # pragma: no cover - every page move is handled above
                assert_never(move)
