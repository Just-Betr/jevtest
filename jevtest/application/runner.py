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
from jevtest.domain.kinds import AppState, Direction, Gesture, Platform, Status
from jevtest.domain.model import ModelCall
from jevtest.domain.ports import Clock, Device, RunListener
from jevtest.domain.results import CheckResult, RunResult, StepResult, TestResult
from jevtest.domain.screen import Bounds, Element, Screen, near_names
from jevtest.domain.settings import Settings
from jevtest.domain.steps import (
    Action,
    AutofillOff,
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
from jevtest.domain.words import number_text, plural

from .brain import Brain, Located, quoted_values

STUCK = 2
"""Times in a row a `do:` move may leave the screen as it was before the goal fails: the move does nothing."""

END_OF_CONTENT = 2
"""Scrolls in a row that must move nothing before `scroll_to:` calls it the end. One isn't enough: a real
phone's web view sometimes ignores a single scroll."""

KEYBOARD_UP = (
    "; the keyboard is up, and the app may not show it while it is (in landscape, an Android keyboard leaves the app "
    "a strip): close it first with a `hide_keyboard` step"
)
"""Added when what a step looks for isn't on screen and the keyboard is up."""

OFF_SCREEN = "; the app has it off screen: bring it on screen first, e.g. with `scroll_to:`"
"""Added when what a step looks for isn't on screen but the app reports it where the screen doesn't show it."""

LEFT_CONFIRM = 4
"""Checks, `LEFT_INTERVAL` apart, that another app is still on top before the app counts as having left. A touch in
Android 15's gesture strip puts the home screen on top for a moment while the phone decides whether it's a swipe home
(measured: 20-65 ms after a tap, then the app again)."""

LEFT_INTERVAL = 0.25
"""Seconds between the checks of `LEFT_CONFIRM`."""

PICKER = "picker"
"""A picker wheel: typing into one turns it to that value (`Device.choose`)."""

UNDER_SYSTEM_BAR = (
    "a system bar, like the status bar, which takes a touch there instead of the app (the app draws under it, "
    "so what it wants touched must be kept clear of the bars)"
)

T = TypeVar("T")


class Stillness:
    """Whether something has stopped moving: it looks the same at two checks in a row."""

    def __init__(self) -> None:
        self._previous: list[object] = []  # what the previous check saw; empty before the first

    def still(self, now: object) -> bool:
        """Whether `now` is what the previous check saw; `now` is what the next check compares with."""
        still = bool(self._previous) and self._previous[0] == now
        self._previous = [now]
        return still

    def forget(self) -> None:
        """Start again: the next check has nothing to compare with."""
        self._previous = []


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
        platform: Platform,
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
        self.device.prepare_for_test()
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
                raise StepFailed(
                    f"Waited {number_text(settings.timeout)}s until {until}{why(screen)}{self._app_gone()}"
                )
            self.clock.sleep(settings.interval)

    def _still_screen(self, settings: Settings) -> Screen:
        """`wait_until` the screen stopped moving (`_drawn` the same at two checks in a row).

        For `do:` before Jev looks at it, `scroll_to:` after each scroll, and `screenshot:`. How the text is drawn
        shows what the tree can't: a system dialog still sliding in. Only text counts: a spinner or a field's
        blinking cursor never stops moving, but the screen they're on has.
        """
        drawn = Stillness()

        def still(screen: Screen) -> Screen | None:
            return screen if drawn.still(self._drawn(screen)) else None

        return self._wait_until(still, settings, "the screen stopped moving", lambda _: "")

    def _drawn(self, screen: Screen) -> tuple[Screen, str]:
        """The screen and how its text is drawn: equal at two checks in a row, the screen has stopped moving."""
        return screen, self.device.looks([e for e in screen.elements if e.text and not e.editable])

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
        safe = re.sub(r"[^\w-]+", "_", name)[:60].strip("_") or "screen"
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
        covered: list[tuple[Located, str]] = []  # found, but under something a touch there would hit instead
        place = Stillness()  # where it is, and how it looks

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
                covered.append((located, "the keyboard: close it first with a `hide_keyboard` step"))
                return None
            if screen.under_system_bar(located.element):
                covered.append((located, UNDER_SYSTEM_BAR))
                return None
            return (located, screen) if self._still(located.element, place) else None

        def why(screen: Screen) -> str:
            if covered:
                located, under = covered[0]
                return f"; {located.describe()} is under {under}"
            kinds = dict.fromkeys(e.kind for e in screen.elements if editable and not e.editable and e.says(wanted))
            if kinds:
                return f"; what says '{target}' doesn't take text ({', '.join(kinds)})"
            off = OFF_SCREEN if screen.off_screen(wanted) else ""
            return self._near(wanted, pool(screen)) + off + (KEYBOARD_UP if screen.keyboard_visible else "")

        what = "a text field" if editable else "an element"
        return self._wait_until(found, settings, f"{what} says '{target}' on screen and stopped moving", why)

    def _still(self, element: Element, place: Stillness) -> bool:
        """Whether `element` is where it was, and looks as it did, at the previous check.

        How it looks matters where its bounds can't show it moving (a system dialog fading in on Android reports its
        final bounds at once), but not for a text field: its blinking cursor never stops moving (measured: a focused
        Flutter field on a Pixel looked one of two ways, switching about every half second), as in `_drawn`.
        """
        looks = "" if element.editable else self.device.looks((element,))
        return place.still((element.kind, element.text, element.bounds, looks))

    def _check_app(self, action: Action) -> None:
        """Fail if the app crashed or left the foreground during the action."""
        if not self._app_should_run or action.app_may_leave:
            return
        self._in_app("")

    def _app_gone(self) -> str:
        """Why nothing of the app was on screen, when it isn't in the foreground; "" when it is."""
        try:
            state = self.device.app_state()
        except DeviceError:
            return ""
        return {
            AppState.BACKGROUND: "; the app is in the background",
            AppState.NOT_RUNNING: "; the app isn't running",
        }.get(state, "")

    def _in_app(self, after: str) -> None:
        """Fail unless the app is in the foreground (`after` says what just happened, for the message).

        Another app on top counts only if it's still there `LEFT_CONFIRM` checks later.
        """
        state = self.device.app_state()
        for _ in range(LEFT_CONFIRM):
            if state is not AppState.BACKGROUND:
                break
            self.clock.sleep(LEFT_INTERVAL)
            state = self.device.app_state()
        if state is AppState.NOT_RUNNING:
            raise StepFailed(f"The app is no longer running (crashed or closed){after}")
        if state is AppState.BACKGROUND:
            raise StepFailed(f"The app left the foreground{after}")

    def _check(self, check: Check, settings: Settings) -> str | None:
        """`wait_until` the check holds: `see:` / `not_see:` compare text, `expect:` asks Jev about each screen."""
        wanted = self._value(check.text)
        if isinstance(check, Expect):
            answers: dict[Screen, float] = {}  # one question per screen, however often it's checked
            drawn = Stillness()

            def judged(screen: Screen) -> str | None:
                # only a screen that stopped moving (the same at two checks in a row) is judged: its answer is
                # recorded for that exact screen, and a replay sees it again; a frame mid-animation it never would
                if not drawn.still(self._drawn(screen)):
                    return None
                if screen not in answers:
                    answers[screen] = self.brain.check(wanted, screen)
                yes = answers[screen]
                return f"Jev {yes:.2f}" if yes > settings.confidence else None

            def last_answer(screen: Screen) -> str:
                return f"; Jev says false ({answers[screen]:.2f})" if screen in answers else ""

            return self._wait_until(judged, settings, "Jev judged it true of a screen that stopped moving", last_answer)
        present = isinstance(check, See)

        def seen(screen: Screen) -> str | None:
            return "" if screen.shows(wanted) == present else None

        def why(screen: Screen) -> str:
            if not present:
                return ""
            off = OFF_SCREEN if screen.off_screen(wanted) else ""
            return self._near(wanted, screen.elements) + off + (KEYBOARD_UP if screen.keyboard_visible else "")

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
            case Scroll() | ScrollTo():
                return self._scrolling(action, step.settings)
            case Do(goal):
                return self._do(goal, step.settings, record)
            case Screenshot(name):
                self._still_screen(step.settings)  # not a frame of the app still drawing or sliding in
                return f"saved {self._screenshot(name)}"
            case Back() | Home() | HideKeyboard() | Key() | OpenUrl():
                self._navigate(action)
            case Rotate() | Location() | DarkMode() | Grant() | Network() | AutofillOff():
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
                if into is None:  # into the field that has focus: keys go nowhere unless the keyboard is up
                    self._wait_until(
                        lambda screen: screen if screen.keyboard_visible else None,
                        settings,
                        "the keyboard is up (a field takes typed text)",
                        lambda _: "; tap the field first, or name it with `into:`",
                    )
                    d.type_text(self._value(text))
                    return None
                found, screen = self._find(into, settings, editable=True, typing=True)
                if found.element.kind == PICKER:
                    d.choose(found.element, self._value(text))
                else:  # the device focuses the field; one that already takes the keys isn't tapped (`takes_keys`)
                    d.type_text(
                        self._value(text), at=None if screen.takes_keys(found.element) else found.element.center
                    )
                return f"into {found.describe()}"
            case Swipe(direction, target, distance):
                if target is None:
                    d.swipe(direction, distance=distance)
                    return None
                found, screen = self._find(target, settings)
                if distance is not None and found.element.slider and direction in {Direction.LEFT, Direction.RIGHT}:
                    raise StepFailed(
                        f"`distance` doesn't apply to {found.describe()}: a left or right swipe on a slider takes it "
                        "all the way to that end"
                    )
                d.swipe(direction, element=found.element, screen=screen, distance=distance)
            case _:  # pragma: no cover - every element action is handled above
                assert_never(action)
        return f"on {found.describe()}"

    def _navigate(self, action: Back | Home | HideKeyboard | Key | OpenUrl) -> None:
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
            case OpenUrl(url):
                d.open_url(self._value(url))
            case _:  # pragma: no cover - every navigation is handled above
                assert_never(action)

    def _set_device(self, action: Rotate | Location | DarkMode | Grant | Network | AutofillOff) -> None:
        """Change a device setting; the device puts each back at the end of the run."""
        d = self.device
        match action:
            case Rotate(orientation):
                d.rotate(orientation)
            case Location(latitude, longitude):
                d.set_location(latitude, longitude)
            case DarkMode(on):
                d.dark_mode(on=on)
            case Grant() as grant:
                names = grant.names_on(self.platform)
                if names is None:  # pragma: no cover - checked before the run starts
                    raise StepFailed(f"grant has no {self.platform} permission")
                d.grant(names)
            case Network(on):
                d.network(on=on)
            case AutofillOff():
                d.autofill_off()
            case _:  # pragma: no cover - every device setting is handled above
                assert_never(action)

    def _touch(self, gesture: Gesture, el: Element) -> None:
        x, y = el.tap_point
        match gesture:
            case Gesture.TAP:
                self.device.tap(x, y)
            case Gesture.DOUBLE_TAP:
                self.device.double_tap(x, y)
            case Gesture.LONG_PRESS:
                self.device.long_press(x, y)
            case _:  # pragma: no cover - every gesture is handled above
                assert_never(gesture)

    def _scrolling(self, action: Scroll | ScrollTo, settings: Settings) -> str | None:
        """Scroll the page, or along what a text is in; `scroll_to` until it finds its text."""
        lane, along = None, None
        if action.along is not None:  # found once: scrolling moves it away
            found, screen = self._find(action.along, settings)
            lane, along = screen.swiped(found.element, action.direction)[0], f"along {found.describe()}"
        if isinstance(action, ScrollTo):
            return self._scroll_to(action.text, action.direction, lane, settings)
        self.device.scroll(action.direction, lane=lane)
        return along

    def _scroll_to(self, text: str, direction: Direction, lane: Bounds | None, settings: Settings) -> str | None:
        """Scroll until an element says exactly the text where a tap reaches it, or it can't scroll further.

        Where a tap reaches it: clear of the screen's edges, and inside `lane`, else the page (`Screen.in_lane`): a
        scroller reports an element at its end with its middle past it, under what comes after, such as a tab bar
        (measured with Compose, in large text: a slider at y 754-801 in a list ending at 765). It can't: the content
        stopped moving, or `max_scrolls` scrolls. Clear of the edges (`Screen.clear_of_edges`), as a person scrolls: an
        element just peeking in at the bottom sits on the phone's home-gesture strip, where a tap goes home. When it
        can't scroll further, an element on screen at an edge is where it is. After each scroll it waits until the
        screen stopped moving (a scroll glides on for a moment). It drags along `lane`, found before the first scroll
        moved what it was found by, else across the page.
        """
        wanted = self._value(text)
        screen = self._still_screen(settings)  # a page still sliding in isn't what there is to scroll
        scrolls, unmoved = 0, 0  # unmoved: scrolls in a row that moved nothing
        while True:
            found = [el for el in screen.elements if el.says(wanted)]
            last = scrolls == settings.max_scrolls or unmoved == END_OF_CONTENT
            if found and (last or any(screen.in_lane(el, lane or screen.page, direction) for el in found)):
                return plural(scrolls, "scroll") if scrolls else None
            if scrolls == settings.max_scrolls:
                raise StepFailed(
                    f"Scrolled {direction} {plural(scrolls, 'time')} (max_scrolls) but never found '{text}'"
                    f"{self._near(wanted, screen.elements)}"
                )
            self.device.scroll(direction, screen=screen, lane=lane)
            before, screen = screen, self._still_screen(settings)
            scrolls, unmoved = scrolls + 1, (unmoved + 1 if screen == before else 0)
            if unmoved == END_OF_CONTENT and not screen.shows(wanted):
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
                for n, step in enumerate(saved, 1):
                    self._repeat(step, settings)
                    record.ran.append(step.describe())
                    self._scroll_on(step, saved[n] if n < len(saved) else None, settings)
                return f"{plural(len(saved), 'saved step')}"
            except StepFailed as e:
                if model.replays_only:
                    n = len(record.ran) + 1
                    raise StepFailed(
                        f"{e} (saved step {n} of {len(saved)}: if the app changed since this do: was worked out, run "
                        "--lock record to work it out again from there; if this device shows the app differently from "
                        "the one it was worked out on, give it a test file of its own, and so a lockfile of its own)"
                    ) from None
        done = saved[: len(record.ran)] if saved else ()
        steps = self._work_out(goal, settings, record)
        model.save_steps(key, [*done, *steps])
        return f"{plural(len(done) + len(steps), 'step')}, worked out by Jev"

    def _scroll_on(self, scroll: SavedStep, after: SavedStep | None, settings: Settings) -> None:
        """After a saved scroll, scroll on until the next saved step's element is on screen, then back if need be.

        On the same way first; at the end of the content without it, back the other way. A scroll's length isn't the
        app's: it changes with the screen and with jevtest (measured: two saved scrolls that reached Item 30 stopped at
        Item 29 after jevtest's drags got shorter), and a web page's content can report late, so a scroll goes by it
        (measured: a saved scroll on a web page ended at its end, past the button the next step taps, 1 run in 2). The
        element counts as there once it's clear of the screen's edges, and no further: a replay keeps as close as it
        can to the screens it was recorded on, as an `expect:` answer is recorded for its screen (measured: requiring
        it inside the page, as `scroll_to:` does, scrolled a saved goal further and met an `expect:` screen not
        recorded). Each way stops at the end of the content, or after `max_scrolls` scrolls.
        """
        target = after.target if after is not None else None
        if target is None or not scroll.action.startswith("scroll_"):
            return
        direction = Direction(scroll.action.removeprefix("scroll_"))
        screen = self._still_screen(settings)
        for way in (direction, direction.opposite):
            for _ in range(settings.max_scrolls):
                same = self._namesakes(screen, target)
                if len(same) == target.count and screen.clear_of_edges(same[target.nth - 1]):
                    return
                self.device.scroll(way, screen=screen)
                before, screen = screen, self._still_screen(settings)
                if screen == before:  # the end of the content that way
                    break

    def _namesakes(self, screen: Screen, target: Target) -> list[Element]:
        """The elements on the screen a saved step's target could be: its kind and name, in screen order."""
        return [el for el in screen.elements if el.kind == target.kind and self._name(el) == target.name]

    def _repeat(self, step: SavedStep, settings: Settings) -> None:
        """One saved step: `wait_until` its element is on screen (the same one of the same count), then act."""
        target = step.target
        if target is None:  # on a screen that stopped moving, as Jev's moves are: a page still loading takes no scroll
            self._make(_page_move(step), self._still_screen(settings), settings)
            return
        found: list[int] = []  # how many elements have its kind and name, on the last screen read
        place = Stillness()  # where it is, and how it looks

        def match(screen: Screen) -> tuple[Element, Screen] | None:
            same = self._namesakes(screen, target)
            found[:] = [len(same)]
            if len(same) != target.count:
                place.forget()
                return None
            element = same[target.nth - 1]
            return (element, screen) if self._still(element, place) else None

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
        # Jev types only the goal's quoted values: say so when it gives up on a goal that has none
        cant_type = (
            ""
            if quoted_values(goal)
            else ' (Jev types only a goal\'s "quoted" values, and this goal has none: if it needs to type, quote them)'
        )
        last: tuple[str, Screen] | None = None  # the last move made, and the screen it was made on
        no_effect = 0  # times in a row that move left the screen as it was

        while True:
            screen = self._still_screen(settings)
            if last is not None and last[1] == screen:  # tell Jev, so it tries something else
                taken[-1] = f"{taken[-1]} (it changed nothing on the screen)"
            decision = self.brain.next_action(goal, screen, taken)
            move = decision.move
            if isinstance(move, Finished | Impossible):
                record.decisions.append(decision)
            if isinstance(move, Finished):
                return steps
            if isinstance(move, Impossible):
                raise StepFailed(f"Jev says the goal is impossible from this screen{cant_type}")
            # a move not made isn't listed with the ones that were
            if len(taken) == settings.max_actions:
                raise StepFailed(
                    f"Goal not reached after {plural(len(taken), 'action')} (max_actions); "
                    f"Jev's next would be {move.describe()}{cant_type}"
                )
            # the same move again on the screen it didn't change: a scroll that moves the list is progress
            no_effect = no_effect + 1 if last == (move.describe(), screen) else 0
            if no_effect == STUCK:
                # quoting helps only where it's stuck on a field (measured: tapping a chosen radio got the hint)
                on_field = isinstance(move, ElementMove) and move.element.editable
                hint = cant_type if on_field else ""
                raise StepFailed(f"Stuck repeating: {move.describe()}, which changes nothing on the screen{hint}")
            record.decisions.append(decision)
            if isinstance(move, WaitForScreen):  # still loading: wait_until it changes; nothing to save
                self._wait_until(_changed_from(screen), settings, "the screen changed", lambda _: "")
            else:
                steps.append(self._saved(move, screen))
                self._make(move, screen, settings)
            taken.append(move.describe())
            last = (move.describe(), screen)

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
            element, screen = self._uncovered(move.element, settings)
            move = replace(move, element=element)
        match move:
            case TouchElement(gesture, element):
                self._touch(gesture, element)
            case SwipeElement(direction, element):
                d.swipe(direction, element=element, screen=screen)
            case TypeInto(element, text) if element.kind == PICKER:
                d.choose(element, self._value(text))
            case TypeInto(element, text):
                d.type_text(self._value(text), at=None if ready else element.center)
            case ClearField(element):
                d.clear_text(element)
            case _:
                self._make_on_page(move, screen)
        # a do: goal is reached in the app: a move that leaves it (back on the first screen) ends the step
        # before the next move lands on whatever is showing instead, such as the phone's home screen
        self._in_app(f" after {move.describe()}")

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

    def _uncovered(self, element: Element, settings: Settings) -> tuple[Element, Screen]:
        """Close the keyboard over `element`, then `wait_until` it's on screen once, with the keyboard gone."""
        self.device.hide_keyboard()
        same = (element.kind, element.text, element.hint, element.resource_id)

        def found(screen: Screen) -> tuple[Element, Screen] | None:
            matches = [el for el in screen.elements if (el.kind, el.text, el.hint, el.resource_id) == same]
            return (matches[0], screen) if not screen.keyboard_visible and len(matches) == 1 else None

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
