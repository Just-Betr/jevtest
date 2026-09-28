"""Running tests: each step's action on the device, then its checks, until the first failure.

The runner reaches the device, the model and time only through the domain's ports, reports progress to a
`RunListener` as it goes, and returns plain result records. It prints nothing and writes no files except the
screenshots it is asked to take.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
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
    SavedStep,
    ScrollPage,
    SwipeElement,
    Target,
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

END_OF_CONTENT = 2
"""Scrolls in a row that must move nothing before `scroll_to:` calls it the end. One isn't enough: a real
phone's web view sometimes ignores a single scroll."""

T = TypeVar("T")


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


class TestRunner:
    """Runs a suite's tests on one device.

    Every wait is one `wait_until`: a condition on the screen, checked every `interval` seconds, for at most
    `timeout` seconds; if it isn't met by then, the step fails and says what it waited for.

    Args:
        suite: The test file: its tests, the tests `use:` can name, and its ``${NAME}`` values.
        device: The device, with the app installed.
        brain: jevtest's judgement, backed by the decision model.
        screenshots: Where failure screenshots (and `screenshot:` steps) are saved.
        platform: The device's platform (``android``, ``ios``): part of the key a `do:` goal's steps are saved
            under, since the same goal takes different steps on each.
        clock: Time.
        listener: Told about each test, step and check as it finishes.
    """

    __test__ = False  # not a pytest test class

    def __init__(
        self,
        suite: Suite,
        device: Device,
        brain: Brain,
        screenshots: Path,
        *,
        platform: str,
        clock: Clock,
        listener: RunListener,
    ) -> None:
        self.suite = suite
        self.device = device
        self.brain = brain
        self.screenshots = screenshots
        self.platform = platform
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
        steps, status = self._run_steps(test.steps, 0, test.name)
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
            self.device.launch()  # returns once the app is in the foreground; each step waits for what it needs
        self._app_should_run = True

    def _run_steps(self, steps: Sequence[Step], depth: int, owner: str) -> tuple[tuple[StepResult, ...], Status]:
        """Run steps in order until one fails. `owner` is the test that defines them (for saved `do:` steps)."""
        done: list[StepResult] = []
        for number, step in enumerate(steps, 1):
            done.append(self._run_step(step, depth, f"{owner} · step {number}"))
            if done[-1].status is Status.FAIL:
                return tuple(done), Status.FAIL
        return tuple(done), Status.PASS

    def _run_step(self, step: Step, depth: int, where: str) -> StepResult:
        started = self.clock.now()
        if isinstance(step.action, Use):
            self.listener.use_started(step.action.test, depth)
            used = step.action.test
            inner, status = self._run_steps(self.suite.library[used].steps, depth + 1, used)
            result = StepResult(step, status, self._since(started), steps=inner)
        elif step.action is not None:
            result = self._run_action(step, step.action, started, where)
            self.listener.step_done(result, depth)
        else:
            result = StepResult(step, Status.PASS, 0.0)
        if result.status is Status.PASS and step.checks:
            checks = self._run_checks(step, depth + 2 if step.action is not None else depth)
            status = Status.FAIL if checks[-1].status is Status.FAIL else Status.PASS
            result = replace(result, status=status, checks=checks)
        return replace(result, seconds=self._since(started))

    def _run_action(self, step: Step, action: Action, started: float, where: str) -> StepResult:
        mark = len(self.brain.model.calls)
        decisions: list[Decision] = []
        ran: list[str] = []
        status, detail = Status.PASS, None
        try:
            detail = self._act(step, action, _Record(decisions, ran, where))
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
            ran=tuple(self._masked(r) for r in ran),
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

    # --- waiting -----------------------------------------------------------------------------------------------
    def _wait_until(
        self, check: Callable[[Screen], T | None], settings: Settings, until: str, why: Callable[[Screen], str]
    ) -> T:
        """Read the screen and `check` it; if it returns nothing, wait `interval` seconds and check again.

        Stops at the step's `timeout`: the step fails with "Waited N s until `until`" and, from the last screen
        read, `why`. A check that needs an answer Jev hasn't given for this screen (``--lock frozen``) counts as not
        met yet; if the last screen's answer is missing too at the timeout, that is the error.

        Raises:
            StepFailed: The timeout passed without the check being met.
            NotRecorded: The timeout passed, and the last screen's answer isn't in the lockfile.
        """
        deadline = self.clock.now() + settings.timeout
        missed: list[NotRecorded] = []
        while True:
            screen = self.device.screen()
            try:
                result = check(screen)
            except NotRecorded as e:
                missed[:], result = [e], None
            else:
                missed.clear()
            if result is not None:
                return result
            if self.clock.now() + settings.interval > deadline:
                if missed:
                    raise missed[0]
                raise StepFailed(f"Waited {settings.timeout:g}s until {until}{why(screen)}")
            self.clock.sleep(settings.interval)

    def _still_screen(self, settings: Settings) -> Screen:
        """`wait_until` the screen reads the same at two checks in a row: it has stopped moving.

        For `do:` before Jev looks at it, and `scroll_to:` after each scroll.
        """
        previous: list[Screen] = []

        def still(screen: Screen) -> Screen | None:
            if previous and previous[0] == screen:
                return screen
            previous[:] = [screen]
            return None

        return self._wait_until(still, settings, "the screen stopped moving", lambda _: "")

    # --- helpers -----------------------------------------------------------------------------------------------
    def _since(self, started: float) -> float:
        return round(self.clock.now() - started, 1)

    def _calls_since(self, mark: int) -> tuple[ModelCall, ...]:
        return tuple(self.brain.model.calls[mark:])

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

    def _near(self, text: str, elements: Sequence[Element]) -> str:
        """The close-but-not-exact texts on screen, for an error. Never matched: shown so the test can be fixed."""
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

    def _find(
        self, target: str, settings: Settings, *, editable: bool = False, typing: bool = False
    ) -> tuple[Located, Screen]:
        """`wait_until` an element says `target` exactly, has stopped moving, and isn't under the keyboard.

        Stopped moving: found in the same place at two checks in a row, so a tap never lands where an element
        sliding in (a page, a list coming back) was a moment ago. Under the keyboard is allowed when `typing` into a
        field that already takes the keys (no tap needed).
        """
        wanted = self._value(target)
        asked: dict[Screen, Located | None] = {}  # Jev picks among exact matches once per screen
        covered: list[Located] = []  # found, but under the keyboard: touching it would hit a key
        last: list[Element] = []  # where it was at the previous check

        def pool(screen: Screen) -> Sequence[Element]:
            return screen.editable if editable else screen.elements

        def found(screen: Screen) -> tuple[Located, Screen] | None:
            if screen not in asked:
                asked[screen] = self.brain.locate(wanted, screen, pool(screen))
            located = asked[screen]
            covered.clear()
            if located is None:
                return None
            if screen.under_keyboard(located.element) and not (typing and screen.takes_keys(located.element)):
                covered.append(located)
                return None
            return (located, screen) if _still(located.element, last) else None

        def why(screen: Screen) -> str:
            if covered:
                return f"; {covered[0].describe()} is under the keyboard: close it first with a `hide_keyboard` step"
            return self._near(wanted, pool(screen))

        what = "a text field" if editable else "an element"
        return self._wait_until(found, settings, f"{what} says '{target}' on screen and stopped moving", why)

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
        """`wait_until` the check holds: `see:` / `not_see:` compare text, `expect:` asks Jev about each screen."""
        wanted = self._value(check.text)
        if isinstance(check, Expect):
            answers: dict[Screen, float] = {}  # one question per screen, however often it's checked

            def judged(screen: Screen) -> str | None:
                if screen not in answers:
                    answers[screen] = self.brain.check(wanted, screen)
                yes = answers[screen]
                return f"Jev {yes:.2f}" if yes > settings.confidence else None

            def last_answer(screen: Screen) -> str:
                return f"; Jev says false ({answers[screen]:.2f})" if screen in answers else ""

            return self._wait_until(judged, settings, "Jev judged it true", last_answer)
        present = isinstance(check, See)

        def seen(screen: Screen) -> str | None:
            return "" if screen.shows(wanted) == present else None

        def why(screen: Screen) -> str:
            return self._near(wanted, screen.elements) if present else ""

        until = f"'{check.text}' is on screen" if present else f"'{check.text}' is gone"
        return self._wait_until(seen, settings, until, why) or None

    # --- actions -----------------------------------------------------------------------------------------------
    def _act(self, step: Step, action: Action, record: _Record) -> str | None:
        """Do one action; return what it acted on, for the log."""
        match action:
            case Launch() | Stop() | Restart() | ClearData() | Reinstall():
                self._lifecycle(action)
            case Touch() | Clear() | TypeText() | Swipe():
                return self._on_element(action, step.settings)
            case Wait() | Background():
                self._pause(action)
            case ScrollTo(text, direction):
                return self._scroll_to(text, direction, step.settings)
            case Do(goal):
                return self._do(goal, step.settings, record)
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

    def _on_element(self, action: Touch | Clear | TypeText | Swipe, settings: Settings) -> str | None:
        """An action on the element a step names: `wait_until` it's on screen, then act on it."""
        d = self.device
        match action:
            case Touch(gesture, target):
                found, _ = self._find(target, settings)
                self._touch(gesture, found.element)
            case Clear(target):
                found, _ = self._find(target, settings, editable=True)
                d.clear_text(found.element)
            case TypeText(text, into):
                if into is None:
                    d.type_text(self._value(text))
                    return None
                found, screen = self._find(into, settings, editable=True, typing=True)
                # the device focuses the field; one that already takes the keys isn't tapped (see `takes_keys`)
                d.type_text(self._value(text), at=None if screen.takes_keys(found.element) else found.element.center)
                return f"into {found.describe()}"
            case Swipe(direction, target):
                if target is None:
                    d.swipe(direction)
                    return None
                found, _ = self._find(target, settings)
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

        After each scroll it waits until the screen stopped moving (a scroll glides on for a moment).
        """
        wanted = self._value(text)
        screen = self._still_screen(settings)  # a page still sliding in isn't what there is to scroll
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
            before, screen = screen, self._still_screen(settings)
            scrolls, unmoved = scrolls + 1, (unmoved + 1 if screen == before else 0)
            if unmoved == END_OF_CONTENT:
                raise StepFailed(
                    f"Scrolled {direction} to the end but never found '{text}'{self._near(wanted, screen.elements)}"
                )

    # --- do: goals ---------------------------------------------------------------------------------------------
    def _do(self, goal: str, settings: Settings, record: _Record) -> str:
        """Repeat the steps saved for this goal; if there are none, work them out with Jev and save them.

        With ``--lock record``, saved steps that no longer fit the app (a step's element never shows up) are
        worked out again from where they stopped. With ``--lock frozen`` that's a failure.
        """
        key = f"{self.platform} · {record.where} · {goal}"
        model = self.brain.model
        saved = model.saved_steps(key)
        if saved is not None:
            try:
                for step in saved:
                    self._repeat(step, settings)
                    record.ran.append(step.describe())
                return f"{_count(len(saved), 'saved step')}"
            except StepFailed:
                if model.replays_only:
                    raise
        done = saved[: len(record.ran)] if saved else ()
        steps = self._work_out(goal, settings, record)
        model.save_steps(key, [*done, *steps])
        return f"{_count(len(done) + len(steps), 'step')}, worked out by Jev"

    def _repeat(self, step: SavedStep, settings: Settings) -> None:
        """One saved step: `wait_until` its element is on screen (the same one of the same count), then act."""
        target = step.target
        if target is None:
            self._make(_page_move(step), self.device.screen(), settings)
            return
        found: list[int] = []  # how many elements have its kind and name, on the last screen read
        last: list[Element] = []  # where it was at the previous check

        def match(screen: Screen) -> tuple[Element, Screen] | None:
            same = [el for el in screen.elements if el.kind == target.kind and self._name(el) == target.name]
            found[:] = [len(same)]
            if len(same) != target.count:
                last.clear()
                return None
            element = same[target.nth - 1]
            return (element, screen) if _still(element, last) else None

        def why(_: Screen) -> str:
            if not found or found[0] == 0:
                return ""
            return f"; the screen shows {found[0]}, the saved step was made with {target.count}"

        element, screen = self._wait_until(match, settings, f"{target.describe()} is on screen and stopped moving", why)
        self._make(_element_move(step, element), screen, settings)

    def _work_out(self, goal: str, settings: Settings, record: _Record) -> list[SavedStep]:
        """Let Jev pick moves toward the goal, each from a screen that has stopped moving; return them as steps."""
        taken = list(record.ran)
        steps: list[SavedStep] = []
        while True:
            screen = self._still_screen(settings)
            decision = self.brain.next_action(goal, screen, taken)
            record.decisions.append(decision)
            move = decision.move
            if isinstance(move, Finished):
                return steps
            if isinstance(move, Impossible):
                raise StepFailed("Jev says the goal is impossible from this screen")
            if len(taken) == settings.max_actions:
                raise StepFailed(f"Goal not reached after {_count(len(taken), 'action')} (max_actions)")
            if taken[-2:] == [move.describe()] * 2:
                raise StepFailed(f"Stuck repeating: {move.describe()}")
            if isinstance(move, WaitForScreen):  # still loading: wait_until it changes; nothing to save
                self._wait_until(_changed_from(screen), settings, "the screen changed", lambda _: "")
            else:
                steps.append(self._saved(move, screen))
                self._make(move, screen, settings)
            taken.append(move.describe())

    def _name(self, el: Element) -> str:
        """What a saved step calls an element: its text, else its hint, else its id; ``${NAME}`` values masked."""
        return self._masked(el.text or el.hint or el.resource_id)

    def _saved(self, move: ElementMove | PageMove, screen: Screen) -> SavedStep:
        """The move as a step later runs can repeat: its element by kind, name and place among its namesakes."""
        if isinstance(move, ScrollPage):
            return SavedStep(f"scroll_{move.direction}")
        if not isinstance(move, ElementMove):
            return SavedStep(next(action for action, page in _PAGE_MOVES.items() if page == move))
        el = move.element
        name = self._name(el)
        same = [e.id for e in screen.elements if e.kind == el.kind and self._name(e) == name]
        target = Target(el.kind, name, same.index(el.id) + 1, len(same))
        match move:
            case TouchElement(gesture, _):
                return SavedStep(str(gesture), target)
            case SwipeElement(direction, _):
                return SavedStep(f"swipe_{direction}", target)
            case TypeInto(_, text):
                return SavedStep("type", target, text)
            case ClearField():
                return SavedStep("clear", target)
            case _:  # pragma: no cover - every element move is handled above
                assert_never(move)

    def _make(self, move: ElementMove | PageMove, screen: Screen, settings: Settings) -> None:
        """Make a move on the device.

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
                self._make_on_page(move, screen)

    def _make_on_page(self, move: PageMove, screen: Screen) -> None:
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
            case WaitForScreen():  # pragma: no cover - _work_out waits itself, and it is never saved
                raise AssertionError("wait is not made on the device")
            case _:  # pragma: no cover - every page move is handled above
                assert_never(move)

    def _uncovered(self, element: Element, settings: Settings) -> Element:
        """Close the keyboard over `element`, then `wait_until` it's on screen once, with the keyboard gone."""
        self.device.hide_keyboard()
        same = (element.kind, element.text, element.hint, element.resource_id)

        def found(screen: Screen) -> Element | None:
            matches = [el for el in screen.elements if (el.kind, el.text, el.hint, el.resource_id) == same]
            return matches[0] if not screen.keyboard_visible and len(matches) == 1 else None

        def why(screen: Screen) -> str:
            if screen.keyboard_visible:
                return "; the keyboard didn't close"
            count = sum((el.kind, el.text, el.hint, el.resource_id) == same for el in screen.elements)
            if count:
                return f"; the screen shows it {count} times: jevtest won't guess which one Jev meant"
            return "; it isn't on screen once the keyboard closed"

        return self._wait_until(found, settings, f"{element.label()} is clear of the keyboard", why)


@dataclass
class _Record:
    """What an action reports as it goes, and where its step is (for a `do:` goal's saved steps).

    Attributes:
        decisions: The moves Jev chose, for a `do:` it worked out.
        ran: The saved steps repeated, for a `do:` that had them.
        where: The test that defines the step, and its number there, e.g. ``Sign in · step 1``.
    """

    decisions: list[Decision]
    ran: list[str]
    where: str


_PAGE_MOVES: dict[str, PageMove] = {"back": GoBack(), "press_enter": PressEnter(), "hide_keyboard": CloseKeyboard()}
"""The saved steps without a target, other than scrolls, and the moves they repeat."""


def _page_move(step: SavedStep) -> PageMove:
    """The page move a saved step without a target repeats."""
    if step.action.startswith("scroll_"):
        return ScrollPage(Direction(step.action.removeprefix("scroll_")))
    return _PAGE_MOVES[step.action]


def _still(element: Element, last: list[Element]) -> bool:
    """Whether `element` is where it was at the previous check (`last`, which is then updated).

    It has stopped moving when two checks in a row find it in the same place.
    """
    here = (element.kind, element.text, element.bounds)
    still = bool(last) and (last[0].kind, last[0].text, last[0].bounds) == here
    last[:] = [element]
    return still


def _changed_from(before: Screen) -> Callable[[Screen], Screen | None]:
    """A `wait_until` check: met by any screen other than `before`."""
    return lambda screen: screen if screen != before else None


def _element_move(step: SavedStep, element: Element) -> ElementMove:
    """The element move a saved step with a target repeats, on the element found for it."""
    if step.action in (Gesture.TAP, Gesture.DOUBLE_TAP, Gesture.LONG_PRESS):
        return TouchElement(Gesture(step.action), element)
    if step.action.startswith("swipe_"):
        return SwipeElement(Direction(step.action.removeprefix("swipe_")), element)
    if step.action == "clear":
        return ClearField(element)
    return TypeInto(element, step.text or "")
