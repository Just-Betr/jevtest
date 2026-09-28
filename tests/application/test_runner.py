import dataclasses
import io
import json

import pytest

from jevtest.adapters.testfile.steps import parse_step
from jevtest.application.brain import Brain
from jevtest.application.runner import TestRunner
from jevtest.cli.console import ConsoleListener
from jevtest.domain.failures import DeviceError, ModelError, NotRecorded
from jevtest.domain.kinds import AppState, Status
from jevtest.domain.results import TestResult
from jevtest.domain.screen import Screen
from jevtest.domain.settings import Settings
from jevtest.domain.steps import Expect, Suite, Test
from tests.conftest import (
    FakeClock,
    FakeDevice,
    FakeModel,
    act,
    confirm,
    console,
    el,
    login_screen,
    pick,
    screen_with,
    yes,
)


def make_test(name, *steps, fresh=True):
    return Test(name, fresh, tuple(parse_step(s) for s in steps))


def make(tmp_path, clock, out, *steps, device=None, model=None, verbose=False, tests=None, library=(), variables=None):
    tests = tests or [make_test("T", *steps)]
    suite = Suite(tmp_path / "t.yaml", {}, {}, tuple(tests), {t.name: t for t in (*tests, *library)}, variables or {})
    device = device or FakeDevice()
    device.clock = clock
    model = model or FakeModel()
    runner = TestRunner(suite, device, Brain(model), tmp_path, clock=clock, listener=console(out, verbose=verbose))
    return runner, device, model


def failure_of(result: TestResult) -> str:
    """Why the test failed; the check fails here if it passed."""
    assert result.failure is not None
    return result.failure


def run1(tmp_path, clock, out, *steps, **kw):
    runner, device, model = make(tmp_path, clock, out, *steps, **kw)
    return runner.run().tests[0], device, model


# --- test lifecycle ------------------------------------------------------------------


def test_fresh_test_resets_app(tmp_path, clock, out):
    runner, d, _ = make(tmp_path, clock, out, "back")
    res = runner.run().tests[0]
    assert res.status is Status.PASS and res.failure is None
    assert d.names()[:4] == ["restore", "stop", "clear_data", "launch"]  # earlier tests' device changes go too
    listener = runner.listener
    assert isinstance(listener, ConsoleListener)
    assert "PASS T" in out.getvalue() and listener.logs["T"][0] == "\n▶ T"


def test_not_fresh_keeps_running_app(tmp_path, clock, out):
    _, d, _ = run1(tmp_path, clock, out, tests=[make_test("T", "back", fresh=False)])
    assert "launch" not in d.names() and "clear_data" not in d.names()


def test_not_fresh_launches_closed_app(tmp_path, clock, out):
    res, d, _ = run1(
        tmp_path,
        clock,
        out,
        tests=[make_test("T", {"see": "Sign in"}, fresh=False)],
        device=FakeDevice(state=AppState.NOT_RUNNING),
    )
    assert d.names()[:2] == ["app_state", "launch"]
    assert res.status is Status.PASS


def test_locked_device_fails_the_test_clearly(tmp_path, clock, out):
    d = FakeDevice()
    d.fail["check_ready"] = DeviceError("Android device X is asleep or locked: unlock it")
    res, _, _ = run1(tmp_path, clock, out, "back", device=d)
    assert res.failure == "(start app) — Android device X is asleep or locked: unlock it"
    assert "launch" not in d.names()


def test_app_that_cannot_start_fails_the_test(tmp_path, clock, out):
    d = FakeDevice()
    d.fail["launch"] = DeviceError("boom")
    res, _, _ = run1(tmp_path, clock, out, "back", device=d)
    assert res.status is Status.FAIL and res.failure == "(start app) — boom"
    assert "could not start app: boom" in out.getvalue()
    assert res.screenshot == "001_FAIL_T.png" and res.steps == ()


def test_run_totals(tmp_path, clock, out):
    runner, _, _ = make(
        tmp_path, clock, out, tests=[make_test("A", "back"), make_test("B", {"see": "missing", "timeout": 1})]
    )
    results = runner.run()
    assert (results.passed, results.failed) == (1, 1)


def test_first_failure_stops_the_test_and_screenshots(tmp_path, clock, out):
    res, d, _ = run1(tmp_path, clock, out, {"see": "Nope", "timeout": 1}, "back")
    assert res.status is Status.FAIL
    assert "back" not in d.names()
    assert res.steps[-1].screenshot == "001_FAIL_T.png"
    assert res.failure == "see: Nope — not on screen"


def test_screenshot_failure_is_reported_not_raised(tmp_path, clock, out):
    d = FakeDevice()
    d.fail["screenshot"] = DeviceError("no display")
    res, _, _ = run1(tmp_path, clock, out, {"screenshot": "x"}, device=d)
    assert res.steps[0].detail == "saved (screenshot failed: no display)"


# --- simple actions -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("step", "call"),
    [
        ("launch", ("launch",)),
        ("back", ("back",)),
        ("hide_keyboard", ("hide_keyboard",)),
        ({"key": "enter"}, ("key", "enter")),
        ({"rotate": "landscape"}, ("rotate", "landscape")),
        ({"location": [1, 2]}, ("set_location", 1.0, 2.0)),
        ({"open_url": "app://x"}, ("open_url", "app://x")),
        ({"dark_mode": True}, ("dark_mode", True)),
        ({"grant": "CAMERA"}, ("grant", "CAMERA")),
        ({"network": False}, ("network", False)),
        ({"swipe": "up"}, ("drag", 500, 1600, 500, 400)),
        ({"scroll": "down"}, ("drag", 500, 1600, 500, 400)),
    ],
)
def test_simple_actions_call_the_device(tmp_path, clock, out, step, call):
    res, d, _ = run1(tmp_path, clock, out, step)
    assert res.status is Status.PASS, res
    assert call in d.calls


def test_lifecycle_actions(tmp_path, clock, out):
    res, d, _ = run1(tmp_path, clock, out, "stop", "clear_data", "reinstall", "launch", "restart", "home")
    assert res.status is Status.PASS
    names = [n for n in d.names() if n != "wait_idle"]
    assert names[4:] == [
        "stop",
        "stop",
        "clear_data",
        "reinstall",
        "launch",
        "app_state",
        "stop",
        "launch",
        "app_state",
        "home",
    ]


def test_wait_and_background_use_the_clock(tmp_path, clock, out):
    _, d, _ = run1(tmp_path, clock, out, {"wait": 3}, {"background": 2})
    assert 3.0 in clock.slept and 2.0 in clock.slept
    assert [n for n in d.names() if n in ("home", "resume")] == ["home", "resume"]


def test_settle_waits_for_idle_not_a_fixed_time(tmp_path, clock, out):
    _, d, _ = run1(tmp_path, clock, out, "back")
    assert ("wait_idle", 3.0, 0.5) in d.calls  # after launch: a longer quiet window
    assert d.calls.count(("wait_idle", 3.0)) == 1  # after back
    assert clock.slept == []  # no fixed sleeps


def test_screenshot_step(tmp_path, clock, out):
    res, _, _ = run1(tmp_path, clock, out, {"screenshot": "home page!"})
    assert res.steps[0].detail == "saved 001_home_page.png"
    assert (tmp_path / "001_home_page.png").exists()


def test_screenshot_name_falls_back(tmp_path, clock, out):
    res, _, _ = run1(tmp_path, clock, out, {"screenshot": "!!!"})
    assert res.steps[0].detail == "saved 001_screen.png"


# --- element actions ----------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["tap", "double_tap", "long_press"])
def test_touch_actions_find_the_element(tmp_path, clock, out, kind):
    res, d, _ = run1(tmp_path, clock, out, {kind: "Sign in"})
    assert (kind, 500, 450) in d.calls
    assert res.steps[0].detail == "on button 'Sign in'"


def test_tap_asks_the_model_when_no_exact_match(tmp_path, clock, out):
    _, d, model = run1(tmp_path, clock, out, {"tap": "the login button"}, model=FakeModel(pick("e3"), confirm()))
    assert ("tap", 500, 450) in d.calls and len(model.asked) == 2  # pick, then confirm


def test_tap_waits_for_the_element(tmp_path, clock, out):
    d = FakeDevice(screen_with("Loading"), screen_with("Loading"), login_screen())
    res, d, model = run1(
        tmp_path, clock, out, {"tap": "Sign in"}, device=d, model=FakeModel(pick("not_on_screen"))
    )  # the unchanged second screen is not re-asked
    assert res.status is Status.PASS
    assert d.names().count("wait_change") == 2 and len(model.asked) == 1


def test_tap_gives_up_after_timeout(tmp_path, clock, out):
    res, _, _ = run1(
        tmp_path, clock, out, {"tap": "Ghost", "timeout": 1}, model=FakeModel(*[pick("not_on_screen")] * 10)
    )
    assert res.status is Status.FAIL
    assert res.failure == "tap: Ghost (timeout: 1) — Could not find element 'Ghost' on screen"


def test_an_element_under_the_keyboard_is_never_touched(tmp_path, clock, out):
    covered = login_screen(keyboard_visible=True, keyboard_top=300)
    res, d, _ = run1(tmp_path, clock, out, {"tap": "Sign in", "timeout": 1}, device=FakeDevice(covered))
    assert failure_of(res).endswith(
        "button 'Sign in' is under the keyboard: close it first with a `hide_keyboard` step"
    )
    assert "tap" not in d.names()  # a tap there would have typed a key


def test_type_into_a_field_that_takes_the_keys_does_not_tap_it(tmp_path, clock, out):
    """After `clear:` the field has focus and the keyboard is up; in landscape the keyboard also covers it."""
    for top in (0, 100):  # the keyboard's edge unknown, and over the field
        s = _login(focused=True, keyboard=True)
        screen = dataclasses.replace(s, keyboard_top=top)
        res, d, _ = run1(tmp_path, clock, out, {"type": {"text": "Bret", "into": "Email"}}, device=FakeDevice(screen))
        assert res.status is Status.PASS, res
        assert ("type_text", "Bret", None) in d.calls  # a tap would move the cursor, or hit a key


def test_an_element_is_touched_once_the_keyboard_is_closed(tmp_path, clock, out):
    covered = login_screen(keyboard_visible=True, keyboard_top=300)
    res, d, _ = run1(tmp_path, clock, out, {"tap": "Sign in"}, device=FakeDevice(covered, login_screen()))
    assert res.status is Status.PASS
    assert d.calls.count(("tap", 500, 450)) == 1


def test_swipe_on_element(tmp_path, clock, out):
    res, d, _ = run1(tmp_path, clock, out, {"swipe": "left", "target": "Sign in"})
    assert ("drag", 850, 450, 150, 450) in d.calls
    assert res.steps[0].detail == "on button 'Sign in'"


def test_clear_finds_a_text_field(tmp_path, clock, out):
    _, d, _ = run1(tmp_path, clock, out, {"clear": "Email"})
    assert ("clear_text", "Email") in d.calls


def test_type_into_field(tmp_path, clock, out):
    res, d, _ = run1(tmp_path, clock, out, {"type": {"text": "a@b.c", "into": "Email"}})
    assert ("type_text", "a@b.c", (500, 150)) in d.calls  # the device focuses the field itself
    assert "tap" not in d.names()
    assert res.steps[0].detail == "into text_field 'Email'"


def test_type_into_missing_field(tmp_path, clock, out):
    res, _, _ = run1(
        tmp_path,
        clock,
        out,
        {"type": {"text": "a", "into": "Phone"}, "timeout": 1},
        model=FakeModel(pick("not_on_screen")),
    )
    assert "Could not find text field 'Phone'" in failure_of(res)


def test_type_into_focused_field(tmp_path, clock, out):
    _, d, _ = run1(tmp_path, clock, out, {"type": "hi"})
    assert ("type_text", "hi", None) in d.calls


def test_scroll_to_scrolls_until_the_text_is_on_screen(tmp_path, clock, out):
    d = FakeDevice(screen_with("Item 1"), screen_with("Item 300"), screen_with("Item 30"))
    res, d, model = run1(tmp_path, clock, out, {"scroll_to": "Item 30", "direction": "down"}, device=d)
    assert res.status is Status.PASS and res.steps[0].detail == "2 scroll(s)"
    assert d.names().count("drag") == 2
    assert not model.asked  # matched in code, never by the model


def test_scroll_to_already_on_screen(tmp_path, clock, out):
    res, d, _ = run1(tmp_path, clock, out, {"scroll_to": "Sign in", "direction": "down"})
    assert res.status is Status.PASS and res.steps[0].detail is None and "drag" not in d.names()


def test_scroll_to_stops_at_the_end_of_the_content(tmp_path, clock, out):
    d = FakeDevice(screen_with("Item 1"), screen_with("Item 2"), screen_with("Item 2"), screen_with("Item 2"))
    res, d, _ = run1(tmp_path, clock, out, {"scroll_to": "Item 99", "direction": "up"}, device=d)
    assert failure_of(res).endswith("Scrolled up to the end but never found 'Item 99'")
    assert d.names().count("drag") == 3  # the last two scrolls moved nothing: that's the end


def test_scroll_to_keeps_going_after_one_scroll_that_moved_nothing(tmp_path, clock, out):
    """A real phone's web view sometimes ignores a single scroll: that isn't the end."""
    d = FakeDevice(screen_with("Item 1"), screen_with("Item 1"), screen_with("Back to top"))
    res, d, _ = run1(tmp_path, clock, out, {"scroll_to": "Back to top", "direction": "down"}, device=d)
    assert res.status is Status.PASS and d.names().count("drag") == 2


def test_scroll_to_gives_up_after_50_scrolls(tmp_path, clock, out):
    d = FakeDevice(*[screen_with(f"Item {i}") for i in range(60)])  # an endless feed
    res, d, _ = run1(tmp_path, clock, out, {"scroll_to": "Item 99", "direction": "down"}, device=d)
    assert failure_of(res).endswith("Scrolled down 50 times (max_scrolls) but never found 'Item 99'")
    assert d.names().count("drag") == 50


def test_scroll_to_found_after_the_last_allowed_scroll(tmp_path, clock, out):
    d = FakeDevice(*[screen_with(f"Item {i}") for i in range(51)])
    res, _, _ = run1(tmp_path, clock, out, {"scroll_to": "Item 50", "direction": "down"}, device=d)
    assert res.status is Status.PASS and res.steps[0].detail == "50 scroll(s)"


def test_steps_wait_10_seconds_unless_they_say_otherwise(tmp_path, clock, out):
    res, _, _ = run1(tmp_path, clock, out, {"see": "Nope"})
    assert res.seconds >= 10 and res.steps[0].seconds == 10.0
    res, _, _ = run1(tmp_path, FakeClock(), out, {"see": "Nope", "timeout": 2.5})
    assert res.steps[0].seconds == 2.5


# --- crash / foreground detection ---------------------------------------------------------


@pytest.mark.parametrize(
    ("state", "message"), [(AppState.NOT_RUNNING, "no longer running"), (AppState.BACKGROUND, "left the foreground")]
)
def test_app_leaving_fails_the_step(tmp_path, clock, out, state, message):
    res, _, _ = run1(tmp_path, clock, out, "back")
    assert res.status is Status.PASS
    res, _, _ = run1(tmp_path, clock, out, "back", device=FakeDevice(state=state))
    assert message in failure_of(res)


def test_leaving_the_app_on_purpose_is_allowed(tmp_path, clock, out):
    res, _, _ = run1(
        tmp_path, clock, out, "home", {"open_url": "https://x"}, "stop", device=FakeDevice(state=AppState.BACKGROUND)
    )
    assert res.status is Status.PASS


def test_device_errors_fail_the_step(tmp_path, clock, out):
    d = FakeDevice()
    d.fail["rotate"] = DeviceError("no sensor")
    res, _, _ = run1(tmp_path, clock, out, {"rotate": "landscape"}, device=d)
    assert res.failure == "rotate: landscape — no sensor"
    assert "✗ rotate: landscape" in out.getvalue()


# --- checks ---------------------------------------------------------------------------------


def test_see_and_not_see(tmp_path, clock, out):
    res, _, _ = run1(tmp_path, clock, out, {"see": ["Sign in", "Email"], "not_see": "Error"})
    assert res.status is Status.PASS
    assert [c.check.name for c in res.steps[0].checks] == ["see", "see", "not_see"]


def test_see_waits_for_text(tmp_path, clock, out):
    d = FakeDevice(screen_with("Loading"), screen_with("Loading"), screen_with("Welcome"))
    res, _, _ = run1(tmp_path, clock, out, {"see": "Welcome"}, device=d)
    assert res.status is Status.PASS


def test_not_see_fails_when_text_stays(tmp_path, clock, out):
    res, _, _ = run1(tmp_path, clock, out, {"not_see": "Sign in", "timeout": 1})
    assert res.failure == "not_see: Sign in — still on screen"


def test_expect_passes_above_threshold(tmp_path, clock, out):
    loading = screen_with("Loading")
    d = FakeDevice(loading, loading, login_screen())  # Jev is asked about a screen only once it holds still
    res, _, _ = run1(tmp_path, clock, out, {"expect": "Login form"}, device=d, model=FakeModel(yes(0.3), yes(0.8)))
    check = res.steps[0].checks[0]
    assert (check.check, check.status, check.detail) == (Expect("Login form"), Status.PASS, "Jev 0.80")
    assert len(check.model_calls) == 2


MISS = NotRecorded("This screen and question are not in t.lock.json, and --lock frozen only replays recorded decisions")


def test_jev_is_never_asked_about_a_screen_still_changing(tmp_path, clock, out):
    d = FakeDevice(screen_with("Loading"), login_screen())  # read once, then gone: caught mid-change
    model = FakeModel(yes(0.9))
    res, _, _ = run1(tmp_path, clock, out, {"expect": "Login form"}, device=d, model=model)
    assert res.status is Status.PASS
    assert len(model.asked) == 1 and "Loading" not in json.dumps(model.asked[0][0])


def test_frozen_expect_looks_again_once_the_screen_changes(tmp_path, clock, out):
    loading = screen_with("Loading")
    d = FakeDevice(loading, loading, login_screen())
    res, _, _ = run1(tmp_path, clock, out, {"expect": "Login form"}, device=d, model=FakeModel(MISS, yes(0.8)))
    assert res.status is Status.PASS


def test_frozen_expect_fails_with_the_lockfile_message_when_no_recorded_screen_comes(tmp_path, clock, out):
    res, _, _ = run1(tmp_path, clock, out, {"expect": "Login form", "timeout": 1}, model=FakeModel(MISS))
    assert failure_of(res).endswith(str(MISS))


def test_frozen_do_waits_for_a_recorded_screen(tmp_path, clock, out):
    loading = screen_with("Loading")
    d = FakeDevice(loading, loading, login_screen())
    res, _, _ = run1(tmp_path, clock, out, {"do": "Sign in"}, device=d, model=FakeModel(MISS, act("done")))
    assert res.status is Status.PASS and res.steps[0].detail == "0 action(s)"


@pytest.mark.parametrize(("p", "passes"), [(0.5, False), (0.51, True)])
def test_expect_passes_when_more_likely_true_than_false(tmp_path, clock, out, p, passes):
    res, _, _ = run1(tmp_path, clock, out, {"expect": "x", "timeout": 1}, model=FakeModel(yes(p)))
    assert (res.status is Status.PASS) is passes


def test_checks_run_after_the_action_and_stop_at_first_failure(tmp_path, clock, out):
    res, d, _ = run1(tmp_path, clock, out, {"tap": "Sign in", "see": ["Nope", "Sign in"], "timeout": 1})
    step = res.steps[0]
    assert len(step.checks) == 1 and step.status is Status.FAIL
    assert d.names().index("tap") < len(d.names()) - 1


def test_checks_skipped_when_action_fails(tmp_path, clock, out):
    d = FakeDevice()
    d.fail["back"] = DeviceError("x")
    res, _, _ = run1(tmp_path, clock, out, {"back": None, "see": "Sign in"}, device=d)
    assert res.steps[0].checks == ()


def test_model_errors_fail_the_check(tmp_path, clock, out):
    res, _, _ = run1(tmp_path, clock, out, {"expect": "x"}, model=FakeModel(ModelError("HTTP 500")))
    assert res.failure == "expect: x — HTTP 500"


def test_output_nests_checks_under_actions(tmp_path, clock, out):
    run1(tmp_path, clock, out, {"tap": "Sign in", "see": "Email"}, {"see": "Password"})
    lines = out.getvalue().splitlines()
    assert any(line.startswith("  ✓ tap: Sign in") for line in lines)
    assert "      ✓ see: Email" in lines  # under its action
    assert "  ✓ see: Password" in lines  # checks-only step


# --- use -----------------------------------------------------------------------------------


def test_use_runs_the_other_tests_steps(tmp_path, clock, out):
    sign_in = make_test("Sign in", "back")
    runner, _, _ = make(
        tmp_path, clock, out, tests=[make_test("Main", {"use": "Sign in", "see": "Email"}, "home")], library=[sign_in]
    )
    res = runner.run().tests[0]
    assert res.status is Status.PASS
    assert res.steps[0].steps[0].step.label == "back"
    assert "  ▸ use: Sign in" in out.getvalue() and "    ✓ back" in out.getvalue()


def test_failure_inside_use_is_reported(tmp_path, clock, out):
    inner = make_test("Inner", {"see": "Nope", "timeout": 1})
    runner, d, _ = make(tmp_path, clock, out, tests=[make_test("Outer", {"use": "Inner"}, "home")], library=[inner])
    res = runner.run().tests[0]
    assert res.failure == "see: Nope — not on screen"
    assert "home" not in d.names()


# --- the goal loop ----------------------------------------------------------------------------


def _login(*, focused: bool, keyboard: bool) -> Screen:
    s = login_screen(keyboard_visible=keyboard)
    email = dataclasses.replace(s.elements[0], focused=focused)
    return dataclasses.replace(s, elements=(email, *s.elements[1:]))


def test_do_types_and_taps_until_done(tmp_path, clock, out):
    screens = (
        _login(focused=False, keyboard=False),
        _login(focused=True, keyboard=True),
        _login(focused=True, keyboard=False),
    )  # the last: e.g. a web view, where everything reports focus
    model = FakeModel(
        act("type", field="e1", value="v0"),
        act("type", field="e1", value="v1"),
        act("type", field="e1", value="v0"),
        act("done"),
    )
    res, d, _ = run1(
        tmp_path,
        clock,
        out,
        {"do": 'Type "a" then "b" into email'},
        device=FakeDevice(*(s for screen in screens for s in (screen, screen))),  # each holds still
        model=model,
    )
    assert res.status is Status.PASS and res.steps[0].detail == "3 action(s)"
    typed = [c for c in d.calls if c[0] == "type_text"]
    # focused with the keyboard up: type without tapping (tapping would move the caret)
    assert [(c[1], c[2]) for c in typed] == [("a", (500, 150)), ("b", None), ("a", (500, 150))]
    assert res.steps[0].decisions[-1].move.describe() == "done"
    assert "→ done  (confidence 0.90)" in out.getvalue()


def test_do_closes_the_keyboard_over_the_element_jev_picked(tmp_path, clock, out):
    covered = login_screen(keyboard_visible=True, keyboard_top=300)
    s = login_screen()
    moved = dataclasses.replace(s.elements[2], bounds=(0, 600, 1000, 700))  # the layout moves once it closes
    closed = dataclasses.replace(s, elements=(*s.elements[:2], moved))
    model = FakeModel(act("tap", target="e3"), act("done"))
    device = FakeDevice(covered, covered, closed)
    res, d, _ = run1(tmp_path, clock, out, {"do": "Sign in"}, model=model, device=device)
    assert res.status is Status.PASS, res
    names = d.names()
    assert names.index("hide_keyboard") < names.index("tap")  # a tap on the keyboard would have typed a key
    assert ("tap", 500, 650) in d.calls  # where it is now, not where it was


def test_do_fails_when_the_keyboard_over_the_element_stays(tmp_path, clock, out):
    covered = login_screen(keyboard_visible=True, keyboard_top=300)
    model = FakeModel(act("tap", target="e3"))
    res, d, _ = run1(tmp_path, clock, out, {"do": "Sign in"}, model=model, device=FakeDevice(covered))
    assert failure_of(res).endswith("button 'Sign in' is under the keyboard, and the keyboard didn't close")
    assert "tap" not in d.names()


def test_do_fails_when_the_element_is_gone_once_the_keyboard_closes(tmp_path, clock, out):
    covered = login_screen(keyboard_visible=True, keyboard_top=300)
    model = FakeModel(act("tap", target="e3"))
    device = FakeDevice(covered, covered, screen_with("Elsewhere"))
    res, d, _ = run1(tmp_path, clock, out, {"do": "Sign in"}, model=model, device=device)
    assert failure_of(res).endswith("button 'Sign in' was under the keyboard, and isn't on screen once it closed")
    assert "tap" not in d.names()


def test_do_types_into_a_focused_field_under_the_keyboard(tmp_path, clock, out):
    s = _login(focused=True, keyboard=True)
    covered = dataclasses.replace(s, keyboard_top=100)  # e.g. a phone on its side: the keyboard covers it all
    model = FakeModel(act("type", field="e1", value="v0"), act("done"))
    res, d, _ = run1(tmp_path, clock, out, {"do": 'Type "a" into email'}, model=model, device=FakeDevice(covered))
    assert res.status is Status.PASS, res
    assert ("type_text", "a", None) in d.calls  # no tap: typing goes to the focused field


@pytest.mark.parametrize(
    ("action", "call"),
    [
        ("tap", ("tap", 500, 450)),
        ("double_tap", ("double_tap", 500, 450)),
        ("long_press", ("long_press", 500, 450)),
        ("swipe_left_on", ("drag", 850, 450, 150, 450)),
        ("swipe_right_on", ("drag", 150, 450, 850, 450)),
        ("clear", ("clear_text", "Email")),
        ("scroll_down", ("drag", 500, 1600, 500, 400)),
        ("back", ("back",)),
        ("press_enter", ("key", "enter")),
        ("hide_keyboard", ("hide_keyboard",)),
    ],
)
def test_do_performs_each_action(tmp_path, clock, out, action, call):
    field = "e1" if action == "clear" else None
    model = FakeModel(act(action, target="e3", field=field), act("done"))
    res, d, _ = run1(
        tmp_path, clock, out, {"do": "Do it"}, model=model, device=FakeDevice(login_screen(keyboard_visible=True))
    )
    assert res.status is Status.PASS, res
    assert call in d.calls


def test_do_wait_waits_for_a_change(tmp_path, clock, out):
    model = FakeModel(
        {"action": {"type": "choice", "choice": "wait", "confidence": 1, "probabilities": {}}}, act("done")
    )
    _, d, _ = run1(tmp_path, clock, out, {"do": "Do it"}, model=model)
    assert "wait_change" in d.names()


def test_do_impossible(tmp_path, clock, out):
    res, _, _ = run1(tmp_path, clock, out, {"do": "Fly"}, model=FakeModel(act("impossible")))
    assert "impossible" in failure_of(res)


def test_do_gives_up_after_10_actions(tmp_path, clock, out):
    model = FakeModel(*[act("tap", target="e3") if i % 2 else act("back") for i in range(11)])
    res, _, _ = run1(tmp_path, clock, out, {"do": "Loop"}, model=model)
    assert failure_of(res).endswith("Goal not reached after 10 actions (max_actions)")
    assert len(res.steps[0].decisions) == 11


def test_do_detects_being_stuck(tmp_path, clock, out):
    res, d, _ = run1(tmp_path, clock, out, {"do": "Loop"}, model=FakeModel(*[act("tap", target="e3")] * 3))
    assert "Stuck repeating: tap button 'Sign in'" in failure_of(res)
    assert d.names().count("tap") == 2


def test_empty_screen_do(tmp_path, clock, out):
    model = FakeModel({"action": {"type": "choice", "choice": "done", "confidence": 1, "probabilities": {}}})
    res, _, _ = run1(tmp_path, clock, out, {"do": "Nothing to do"}, device=FakeDevice(Screen(10, 10)), model=model)
    assert res.status is Status.PASS


# --- verbose -----------------------------------------------------------------------------------


def test_verbose_prints_every_model_answer(tmp_path, clock, out):
    run1(
        tmp_path,
        clock,
        out,
        {"do": "Press sign in"},
        {"expect": "x"},
        verbose=True,
        model=FakeModel(act("done"), yes(0.9)),
    )
    text = out.getvalue()
    assert "jev action: done  [done 0.90, other 0.10]" in text
    assert "jev check: yes=0.90" in text
    assert "jev call 7 ms, 3 question(s)" in text and "jev call 7 ms, 1 question(s)" in text


def test_verbose_marks_lockfile_answers(tmp_path, clock, out):
    runner, _, model = make(tmp_path, clock, out, {"expect": "x"}, verbose=True, model=FakeModel(yes(0.9)))
    real_ask = model.ask

    def recorded_ask(state, questions):
        answers = real_ask(state, questions)
        model.calls[-1] = dataclasses.replace(model.calls[-1], recorded=True)
        return answers

    model.ask = recorded_ask
    runner.run()
    assert "jev call from lockfile" in out.getvalue()


def test_quiet_by_default(tmp_path, clock, out):
    run1(tmp_path, clock, out, {"expect": "x"}, model=FakeModel(yes(0.9)))
    assert "jev call" not in out.getvalue()


# --- determinism -------------------------------------------------------------------------------


def test_same_inputs_give_identical_runs(tmp_path):
    """Identical screens and model answers give identical logs and results."""

    def once():
        buf = io.StringIO()
        model = FakeModel(act("type", field="e1", value="v0"), act("tap", target="e3"), act("done"), yes(0.9))
        steps = [{"do": 'Sign in as "me@x.dev"'}, {"expect": "Home"}, {"see": "Sign in"}, {"swipe": "up"}]
        runner, device, _ = make(tmp_path, FakeClock(), buf, *steps, model=model, verbose=True)
        return buf.getvalue(), runner.run(), device.calls

    first, second = once(), once()
    assert first == second
    assert first[1].passed == 1


def test_unchanged_screen_is_not_rejudged(tmp_path, clock, out):
    """Retrying an expect on an identical screen must not spend model calls."""
    res, d, model = run1(tmp_path, clock, out, {"expect": "x", "timeout": 5}, model=FakeModel(yes(0.1)))
    assert res.status is Status.FAIL and len(model.asked) == 1
    assert d.names().count("wait_change") >= 5


# --- ${NAME} values --------------------------------------------------------------------


def test_variables_are_filled_only_where_the_app_sees_them(tmp_path, clock, out):
    steps = [
        {"type": {"text": "${PASS}", "into": "${FIELD}"}, "see": "${NAME}"},
        {"type": "${PASS}", "not_see": "${SECRET_ERR}", "expect": "Signed in as ${NAME}"},
        {"open_url": "app://${HOST}/x"},
        {"scroll_to": "${NAME}", "direction": "down"},
    ]
    screen = Screen(
        1000,
        2000,
        (
            el("text_field", "Email", editable=True, bounds=(0, 100, 1000, 180)),
            el("text", "Ann", bounds=(0, 200, 1000, 280)),
        ),
    )
    variables = {"PASS": "hunter2", "FIELD": "Email", "NAME": "Ann", "SECRET_ERR": "Denied", "HOST": "h"}
    res, d, model = run1(
        tmp_path, clock, out, *steps, device=FakeDevice(screen), model=FakeModel(yes(0.9)), variables=variables
    )
    assert res.status is Status.PASS
    assert [c[1] for c in d.calls if c[0] == "type_text"] == ["hunter2", "hunter2"]
    assert ("open_url", "app://h/x") in d.calls
    assert "Signed in as Ann" in json.dumps(model.asked[0][1])  # the model judges the real statement
    log = out.getvalue()
    assert "hunter2" not in log and "type: ${PASS} (into: ${FIELD})" in log and "see: ${NAME}" in log


def test_the_model_sees_the_placeholder_and_the_app_gets_the_value(tmp_path, clock, out):
    model = FakeModel(act("type", field="e1", value="v0"), act("done"))
    res, d, _ = run1(
        tmp_path, clock, out, {"do": 'Type "${PASS}" into the password'}, model=model, variables={"PASS": "hunter2"}
    )
    assert res.status is Status.PASS and ("type_text", "hunter2", (500, 150)) in d.calls
    assert "hunter2" not in json.dumps(model.asked) and "hunter2" not in out.getvalue()


def test_values_the_screen_shows_are_written_as_their_names_in_all_output(tmp_path, clock, out):
    s = login_screen()
    email = dataclasses.replace(s.elements[0], text="ann@x.io", value="ann@x.io")  # a field shows what was typed
    s = dataclasses.replace(s, elements=(email, *s.elements[1:]))
    model = FakeModel(act("tap", target="e1"), act("done"))
    steps = ({"do": "Tap the email"}, {"tap": "${EMAIL}", "see": "Nope", "timeout": 1})
    res, _, _ = run1(tmp_path, clock, out, *steps, model=model, device=FakeDevice(s), variables={"EMAIL": "ann@x.io"})
    decision = res.steps[0].decisions[0]
    assert decision.move.describe() == "tap text_field '${EMAIL}'"
    assert res.steps[1].detail == "on text_field '${EMAIL}'"
    assert "ann@x.io" not in out.getvalue()


def test_missing_target_is_reported_by_its_placeholder(tmp_path, clock, out):
    res, _, _ = run1(
        tmp_path,
        clock,
        out,
        {"tap": "${WHO}", "timeout": 1},
        model=FakeModel(pick("not_on_screen")),
        variables={"WHO": "Bob"},
    )
    assert "Could not find element '${WHO}'" in failure_of(res)


# --- settings ------------------------------------------------------------------------


def test_a_files_settings_reach_launch_and_every_step(tmp_path, clock, out):
    settings = Settings(settle=7)
    test = Test("T", True, (parse_step("back", settings),))
    suite = Suite(tmp_path / "t.yaml", {}, {}, (test,), {"T": test}, {}, settings=settings)
    d = FakeDevice()
    d.clock = clock
    TestRunner(suite, d, Brain(FakeModel()), tmp_path, clock=clock, listener=console(out)).run()
    assert ("wait_idle", 7, 0.5) in d.calls and ("wait_idle", 7) in d.calls


def test_a_step_can_settle_longer(tmp_path, clock, out):
    _, d, _ = run1(tmp_path, clock, out, {"back": None, "settle": 5})
    assert ("wait_idle", 5) in d.calls


@pytest.mark.parametrize(("p", "passes"), [(0.8, False), (0.81, True)])
def test_expect_can_ask_for_more_confidence(tmp_path, clock, out, p, passes):
    res, _, _ = run1(tmp_path, clock, out, {"expect": "x", "confidence": 0.8, "timeout": 1}, model=FakeModel(yes(p)))
    assert (res.status is Status.PASS) is passes


def test_do_can_allow_fewer_actions(tmp_path, clock, out):
    model = FakeModel(*[act("tap", target="e3") if i % 2 else act("back") for i in range(3)])
    res, _, _ = run1(tmp_path, clock, out, {"do": "Loop", "max_actions": 2}, model=model)
    assert failure_of(res).endswith("Goal not reached after 2 actions (max_actions)")


def test_scroll_to_can_allow_fewer_scrolls(tmp_path, clock, out):
    d = FakeDevice(*[screen_with(f"Item {i}") for i in range(10)])
    res, d, _ = run1(tmp_path, clock, out, {"scroll_to": "Item 9", "direction": "down", "max_scrolls": 3}, device=d)
    assert failure_of(res).endswith("Scrolled down 3 times (max_scrolls) but never found 'Item 9'")


def test_a_limit_of_one_reads_as_one(tmp_path, clock, out):
    res, _, _ = run1(
        tmp_path, clock, out, {"do": "Loop", "max_actions": 1}, model=FakeModel(act("back"), act("tap", target="e3"))
    )
    assert failure_of(res).endswith("Goal not reached after 1 action (max_actions)")
    d = FakeDevice(*[screen_with(f"Item {i}") for i in range(5)])
    res, _, _ = run1(tmp_path, clock, out, {"scroll_to": "Item 4", "direction": "down", "max_scrolls": 1}, device=d)
    assert failure_of(res).endswith("Scrolled down 1 time (max_scrolls) but never found 'Item 4'")


# --- exact matching ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("step", "on_screen", "failure"),
    [
        (
            {"tap": "Save", "timeout": 1},
            ["Unsaved changes", "Save draft"],
            "Could not find element 'Save' on screen; close but not exact: 'Unsaved changes', 'Save draft'",
        ),
        ({"see": "Taps: 2", "timeout": 1}, ["Taps: 20"], "not on screen; close but not exact: 'Taps: 20'"),
        ({"see": "Sign in", "timeout": 1}, ["Sign in now"], "not on screen; close but not exact: 'Sign in now'"),
        (
            {"scroll_to": "Item 3", "direction": "down"},
            ["Item 30"],
            "Scrolled down to the end but never found 'Item 3'; close but not exact: 'Item 30'",
        ),
    ],
)
def test_a_near_match_fails_and_says_what_is_there(tmp_path, clock, out, step, on_screen, failure):
    screen = Screen(1000, 2000, tuple(el("button", t, clickable=True, bounds=(0, 0, 100, 100)) for t in on_screen))
    res, d, model = run1(tmp_path, clock, out, step, device=FakeDevice(screen))
    assert res.status is Status.FAIL and failure_of(res).endswith(failure)
    assert "tap" not in d.names() and not model.asked


def test_not_see_passes_when_only_a_longer_text_is_there(tmp_path, clock, out):
    screen = Screen(1000, 2000, (el("text", "Error: none", bounds=(0, 0, 100, 100)),))
    res, _, _ = run1(tmp_path, clock, out, {"not_see": "Error"}, device=FakeDevice(screen))
    assert res.status is Status.PASS


def test_a_value_in_a_near_match_is_shown_by_its_name(tmp_path, clock, out):
    screen = Screen(1000, 2000, (el("text", "Welcome Ann", bounds=(0, 0, 100, 100)),))
    res, _, _ = run1(
        tmp_path,
        clock,
        out,
        {"see": "Welcome", "timeout": 1},
        device=FakeDevice(screen),
        variables={"NAME": "Ann", "EMPTY": ""},
    )
    assert failure_of(res).endswith("close but not exact: 'Welcome ${NAME}'") and "Ann" not in out.getvalue()


def test_the_output_says_when_jev_chose_the_element(tmp_path, clock, out):
    res, _, _ = run1(tmp_path, clock, out, {"tap": "the login button"}, model=FakeModel(pick("e3"), confirm()))
    assert res.steps[0].detail == "on button 'Sign in' (chosen by Jev)"
    assert "✓ tap: the login button" in out.getvalue() and "(chosen by Jev)" in out.getvalue()
