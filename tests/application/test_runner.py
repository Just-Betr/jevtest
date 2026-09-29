import dataclasses
import io
import json
from typing import Any

import pytest

from jevtest.adapters.devices._typing import override
from jevtest.adapters.testfile.steps import parse_step
from jevtest.application.brain import Brain
from jevtest.application.runner import TestRunner
from jevtest.cli.console import ConsoleListener
from jevtest.domain.decisions import SavedStep, Target
from jevtest.domain.failures import DeviceError, ModelError, NotRecorded
from jevtest.domain.kinds import AppState, Platform, Status
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
    runner = TestRunner(
        suite,
        device,
        Brain(model),
        tmp_path,
        platform=Platform.ANDROID,
        clock=clock,
        listener=console(out, verbose=verbose),
    )
    return runner, device, model


def held(*screens):
    """Each screen twice in a row: read twice, it has stopped moving (a do: or scroll_to: waits for that)."""
    return [s for screen in screens for s in (screen, screen)]


def listed(text: str, y: int = 900) -> Screen:
    """A screen with one element saying `text`, at height `y` of 2000 (mid-screen: clear of the edges)."""
    return Screen(1000, 2000, (el("text", text, bounds=(0, y, 1000, y + 80)),))


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
    assert res.failure == "see: Nope — Waited 1s until 'Nope' is on screen"


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


def test_an_action_does_not_wait_afterwards(tmp_path, clock, out):
    """Nothing waits for the screen to settle: the next step waits until what it needs is there."""
    _, d, _ = run1(tmp_path, clock, out, "back")
    assert d.names() == ["restore", "stop", "clear_data", "launch", "back", "app_state"]
    assert clock.slept == []


def test_screenshot_step(tmp_path, clock, out):
    res, _, _ = run1(tmp_path, clock, out, {"screenshot": "home page!"})
    assert res.steps[0].detail == "saved 001_home_page.png"
    assert (tmp_path / "001_home_page.png").exists()


def test_screenshot_waits_until_the_screen_stopped_moving(tmp_path, clock, out):
    splash, app = Screen(1000, 2000, ()), login_screen()
    d = FakeDevice(splash, app, app)  # a fresh launch: the splash, then the app drawn
    res, d, _ = run1(tmp_path, clock, out, {"screenshot": "home"}, device=d)
    assert res.status is Status.PASS
    reads = [n for n in d.names()[4:] if n not in ("looks", "app_state")]
    assert reads == ["screen", "screen", "screen", "screenshot"]


def test_screenshot_names_keep_letters_of_any_script(tmp_path, clock, out):
    res, _, _ = run1(tmp_path, clock, out, {"screenshot": "Anmeldung ü / ログイン"})
    assert res.steps[0].detail == "saved 001_Anmeldung_ü_ログイン.png"


def test_screenshot_name_falls_back(tmp_path, clock, out):
    res, _, _ = run1(tmp_path, clock, out, {"screenshot": "!!!"})
    assert res.steps[0].detail == "saved 001_screen.png"


# --- element actions ----------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["tap", "double_tap", "long_press"])
def test_touch_actions_find_the_element(tmp_path, clock, out, kind):
    res, d, _ = run1(tmp_path, clock, out, {kind: "Sign in"})
    assert (kind, 500, 450) in d.calls
    assert res.steps[0].detail == "on button 'Sign in'"


def test_a_tap_never_guesses_an_element_no_one_names(tmp_path, clock, out):
    """Only exact text counts: a described target goes in a do: step, not a tap:."""
    model = FakeModel()  # asking it anything fails the test
    res, d, _ = run1(tmp_path, clock, out, {"tap": "the login button", "timeout": 1}, model=model)
    assert failure_of(res).endswith("Waited 1s until an element says 'the login button' on screen and stopped moving")
    assert "tap" not in d.names() and not model.asked


def test_tap_waits_until_the_element_is_on_screen(tmp_path, clock, out):
    d = FakeDevice(screen_with("Loading"), screen_with("Loading"), login_screen())
    res, d, _ = run1(tmp_path, clock, out, {"tap": "Sign in"}, device=d)
    assert res.status is Status.PASS
    # checked every interval; found at the 3rd check, and in the same place at the 4th: it stopped moving
    assert d.names().count("screen") == 4 and clock.slept == [0.25] * 3


def test_tap_gives_up_after_timeout(tmp_path, clock, out):
    res, _, _ = run1(tmp_path, clock, out, {"tap": "Ghost", "timeout": 1})
    assert res.status is Status.FAIL
    assert (
        res.failure == "tap: Ghost (timeout: 1) — Waited 1s until an element says 'Ghost' on screen and stopped moving"
    )
    assert clock.now() <= 1


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
    assert "hide_keyboard" not in d.names()  # the app closed it here; an exact step never does
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
    res, _, _ = run1(tmp_path, clock, out, {"type": {"text": "a", "into": "Phone"}, "timeout": 1})
    assert "Waited 1s until a text field says 'Phone' on screen and stopped moving" in failure_of(res)


def test_type_into_focused_field(tmp_path, clock, out):
    typing = dataclasses.replace(login_screen(), keyboard_visible=True)
    _, d, _ = run1(tmp_path, clock, out, {"type": "hi"}, device=FakeDevice(login_screen(), typing))
    assert ("type_text", "hi", None) in d.calls  # once the keyboard came up


@pytest.mark.parametrize("step", [{"clear": "Sign in"}, {"type": {"text": "hi", "into": "Sign in"}}])
def test_typing_into_a_button_says_it_is_a_button(tmp_path, clock, out, step):
    res, _, _ = run1(tmp_path, clock, out, {**step, "timeout": 1})
    assert failure_of(res).endswith(
        "says 'Sign in' on screen and stopped moving; what says 'Sign in' doesn't take text (button)"
    )


def test_type_with_no_field_taking_keys_fails_instead_of_typing_into_nothing(tmp_path, clock, out):
    res, d, _ = run1(tmp_path, clock, out, {"type": "hi", "timeout": 1})
    assert failure_of(res).endswith(
        "Waited 1s until the keyboard is up (a field takes typed text); tap the field first, or name it with `into:`"
    )
    assert not [c for c in d.calls if c[0] == "type_text"]


def test_scroll_to_scrolls_until_the_text_is_on_screen(tmp_path, clock, out):
    # after each scroll: wait_until two reads agree (the scroll has stopped gliding)
    d = FakeDevice(*held(listed("Item 1"), listed("Item 300"), listed("Item 30")))
    res, d, model = run1(tmp_path, clock, out, {"scroll_to": "Item 30", "direction": "down"}, device=d)
    assert res.status is Status.PASS and res.steps[0].detail == "2 scrolls"
    assert d.names().count("drag") == 2
    assert not model.asked  # matched in code, never by the model


def test_scroll_to_already_on_screen(tmp_path, clock, out):
    res, d, _ = run1(tmp_path, clock, out, {"scroll_to": "Sign in", "direction": "down"})
    assert res.status is Status.PASS and res.steps[0].detail is None and "drag" not in d.names()


def test_scroll_to_stops_at_the_end_of_the_content(tmp_path, clock, out):
    d = FakeDevice(*held(screen_with("Item 1")), screen_with("Item 2"))  # then Item 2 for good
    res, d, _ = run1(tmp_path, clock, out, {"scroll_to": "Item 99", "direction": "up"}, device=d)
    assert failure_of(res).endswith("Scrolled up to the end but never found 'Item 99'")
    assert d.names().count("drag") == 3  # the last two scrolls moved nothing: that's the end


def test_scroll_to_keeps_going_after_one_scroll_that_moved_nothing(tmp_path, clock, out):
    """A real phone's web view sometimes ignores a single scroll: that isn't the end."""
    d = FakeDevice(*held(listed("Item 1"), listed("Item 1"), listed("Back to top")))
    res, d, _ = run1(tmp_path, clock, out, {"scroll_to": "Back to top", "direction": "down"}, device=d)
    assert res.status is Status.PASS and d.names().count("drag") == 2


def test_scroll_to_gives_up_after_50_scrolls(tmp_path, clock, out):
    d = FakeDevice(*held(screen_with("Item 0"), *[screen_with(f"Item {i}") for i in range(1, 60)]))  # endless
    res, d, _ = run1(tmp_path, clock, out, {"scroll_to": "Item 99", "direction": "down"}, device=d)
    assert failure_of(res).endswith("Scrolled down 50 times (max_scrolls) but never found 'Item 99'")
    assert d.names().count("drag") == 50


def test_scroll_to_found_after_the_last_allowed_scroll(tmp_path, clock, out):
    d = FakeDevice(*held(screen_with("Item 0"), *[screen_with(f"Item {i}") for i in range(1, 51)]))
    res, _, _ = run1(tmp_path, clock, out, {"scroll_to": "Item 50", "direction": "down"}, device=d)
    assert res.status is Status.PASS and res.steps[0].detail == "50 scrolls"


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
    assert res.failure == "not_see: Sign in — Waited 1s until 'Sign in' is gone"


def test_expect_passes_above_threshold(tmp_path, clock, out):
    loading = screen_with("Loading")
    d = FakeDevice(loading, loading, login_screen())  # Jev is asked about a screen only once it holds still
    res, _, _ = run1(tmp_path, clock, out, {"expect": "Login form"}, device=d, model=FakeModel(yes(0.3), yes(0.8)))
    check = res.steps[0].checks[0]
    assert (check.check, check.status, check.detail) == (Expect("Login form"), Status.PASS, "Jev 0.80")
    assert len(check.model_calls) == 2


MISS = NotRecorded("This screen and question are not in t.lock.json, and --lock frozen only replays recorded decisions")


def test_expect_asks_jev_about_each_screen_that_stopped_moving_until_it_holds(tmp_path, clock, out):
    d = FakeDevice(*held(screen_with("Loading"), login_screen()))
    res, _, model = run1(tmp_path, clock, out, {"expect": "Login form"}, device=d, model=FakeModel(yes(0.2), yes(0.9)))
    assert res.status is Status.PASS and len(model.asked) == 2


def test_expect_never_asks_about_a_screen_caught_moving(tmp_path, clock, out):
    """A frame mid-animation is never seen again: an answer about it couldn't be replayed."""
    frames = [screen_with(f"Turning {i}") for i in range(30)]
    model = FakeModel()  # asking it anything fails the test
    res, _, _ = run1(tmp_path, clock, out, {"expect": "Home", "timeout": 2}, device=FakeDevice(*frames), model=model)
    assert failure_of(res).endswith("Waited 2s until Jev judged it true of a screen that stopped moving")
    assert not model.asked


def test_expect_waits_while_the_text_is_still_being_drawn_elsewhere(tmp_path, clock, out):
    """Android reports a sliding dialog at its first place for about half a second: only its pixels show it moving."""
    d = FakeDevice(login_screen())
    d.drawn = ["low", "low2", "centered", "centered"]
    res, d, model = run1(tmp_path, clock, out, {"expect": "Login form"}, device=d, model=FakeModel(yes(0.9)))
    assert res.status is Status.PASS and len(model.asked) == 1 and clock.slept == [0.25] * 3
    assert {c[1] for c in d.calls if c[0] == "looks"} == {("Sign in",)}  # the fields show only hints


def test_a_screen_is_still_whatever_its_untexted_or_editable_parts_do(tmp_path, clock, out):
    """A spinner or a blinking cursor never stops moving: only text that isn't being typed is looked at."""
    screen = Screen(1000, 2000, (el("text", "Loading"), el("progress"), el("text_field", "Guest", editable=True)))
    d = FakeDevice(screen)
    res, d, _ = run1(tmp_path, clock, out, {"expect": "Loading", "timeout": 1}, device=d, model=FakeModel(yes(0.9)))
    assert res.status is Status.PASS
    assert {c[1] for c in d.calls if c[0] == "looks"} == {("Loading",)}


def test_frozen_expect_looks_again_once_the_screen_changes(tmp_path, clock, out):
    loading = screen_with("Loading")
    d = FakeDevice(loading, loading, login_screen())
    res, _, _ = run1(tmp_path, clock, out, {"expect": "Login form"}, device=d, model=FakeModel(MISS, yes(0.8)))
    assert res.status is Status.PASS


def test_frozen_expect_reports_the_last_answer_when_a_later_screen_was_recorded(tmp_path, clock, out):
    """A miss on an early screen isn't the reason when a later, recorded screen said no."""
    d = FakeDevice(*held(screen_with("Loading")), login_screen())
    res, _, _ = run1(tmp_path, clock, out, {"expect": "Home", "timeout": 5}, device=d, model=FakeModel(MISS, yes(0.2)))
    assert failure_of(res).endswith(
        "Waited 5s until Jev judged it true of a screen that stopped moving; Jev says false (0.20)"
    )


def test_frozen_expect_fails_with_the_lockfile_message_when_no_recorded_screen_comes(tmp_path, clock, out):
    res, _, _ = run1(tmp_path, clock, out, {"expect": "Login form", "timeout": 1}, model=FakeModel(*[MISS] * 5))
    assert failure_of(res).endswith(str(MISS))


def test_frozen_do_without_saved_steps_fails(tmp_path, clock, out):
    model = FakeModel(frozen=True)
    res, _, _ = run1(tmp_path, clock, out, {"do": "Sign in"}, model=model)
    assert failure_of(res).endswith("No steps are saved for android · T · step 1 · Sign in")
    assert not model.asked


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
    assert res.failure == "use: Inner > see: Nope — Waited 1s until 'Nope' is on screen"
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
    goal = 'Type "a" then "b" into email'
    res, d, _ = run1(tmp_path, clock, out, {"do": goal}, device=FakeDevice(*held(*screens)), model=model)
    assert res.status is Status.PASS and res.steps[0].detail == "3 steps, worked out by Jev"
    typed = [c for c in d.calls if c[0] == "type_text"]
    # focused with the keyboard up: type without tapping (tapping would move the caret)
    assert [(c[1], c[2]) for c in typed] == [("a", (500, 150)), ("b", None), ("a", (500, 150))]
    assert res.steps[0].decisions[-1].move.describe() == "done"
    assert "→ done  (Jev, confidence 0.90)" in out.getvalue()
    email = Target("text_field", "Email")
    assert model.saved[f"android · T · step 1 · {goal}"] == (
        SavedStep("type", email, "a"),
        SavedStep("type", email, "b"),
        SavedStep("type", email, "a"),
    )


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
    assert failure_of(res).endswith(
        "Waited 10s until button 'Sign in' is clear of the keyboard; the keyboard didn't close"
    )
    assert "tap" not in d.names()


def test_do_fails_when_the_element_is_there_twice_once_the_keyboard_closes(tmp_path, clock, out):
    covered = login_screen(keyboard_visible=True, keyboard_top=300)
    s = login_screen()
    twice = dataclasses.replace(
        s, elements=(*s.elements, dataclasses.replace(s.elements[2], bounds=(0, 600, 1000, 700)))
    )
    model = FakeModel(act("tap", target="e3"))
    device = FakeDevice(covered, covered, twice)
    res, d, _ = run1(tmp_path, clock, out, {"do": "Sign in"}, model=model, device=device)
    assert failure_of(res).endswith("the screen shows it 2 times: jevtest won't guess which one Jev meant")
    assert "tap" not in d.names()


def test_do_fails_when_the_element_is_gone_once_the_keyboard_closes(tmp_path, clock, out):
    covered = login_screen(keyboard_visible=True, keyboard_top=300)
    model = FakeModel(act("tap", target="e3"))
    device = FakeDevice(covered, covered, screen_with("Elsewhere"))
    res, d, _ = run1(tmp_path, clock, out, {"do": "Sign in"}, model=model, device=device)
    assert failure_of(res).endswith("; it isn't on screen once the keyboard closed")
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


def test_do_wait_waits_until_the_screen_changes_and_saves_nothing(tmp_path, clock, out):
    loading = screen_with("Loading")
    model = FakeModel(
        {"action": {"type": "choice", "choice": "wait", "confidence": 1, "probabilities": {}}}, act("done")
    )
    d = FakeDevice(loading, loading, loading, login_screen())
    res, _, _ = run1(tmp_path, clock, out, {"do": "Do it"}, device=d, model=model)
    assert res.status is Status.PASS
    assert model.saved["android · T · step 1 · Do it"] == ()  # waiting isn't a step to repeat


def test_do_impossible(tmp_path, clock, out):
    res, _, _ = run1(tmp_path, clock, out, {"do": "Fly"}, model=FakeModel(act("impossible")))
    assert "impossible" in failure_of(res)


def test_do_gives_up_after_10_actions(tmp_path, clock, out):
    model = FakeModel(*[act("tap", target="e3") if i % 2 else act("back") for i in range(11)])
    res, _, _ = run1(tmp_path, clock, out, {"do": "Loop"}, model=model)
    assert "Goal not reached after 10 actions (max_actions); Jev's next would be " in failure_of(res)
    assert len(res.steps[0].decisions) == 10  # the 11th, not made, isn't listed


@pytest.mark.parametrize(
    ("state", "said"),
    [
        (AppState.BACKGROUND, "; the app is in the background"),
        (AppState.NOT_RUNNING, "; the app isn't running"),
        (AppState.FOREGROUND, ""),
    ],
)
def test_a_wait_that_times_out_says_when_the_app_isnt_showing(tmp_path, clock, out, state, said):
    res, _, _ = run1(tmp_path, clock, out, {"see": "Welcome", "timeout": 1}, device=FakeDevice(state=state))
    assert failure_of(res) == f"see: Welcome — Waited 1s until 'Welcome' is on screen{said}"


def test_a_device_that_cant_say_where_the_app_is_adds_nothing(tmp_path, clock, out):
    d = FakeDevice()
    d.fail["app_state"] = DeviceError("adb gone")
    res, _, _ = run1(tmp_path, clock, out, {"see": "Welcome", "timeout": 1}, device=d)
    assert failure_of(res).endswith("Waited 1s until 'Welcome' is on screen")


def test_a_do_move_that_leaves_the_app_ends_the_step_at_once(tmp_path, clock, out):
    """Back on the app's first screen goes to the phone's home screen: the next move would tap an app there."""

    class LeavesOnBack(FakeDevice):
        @override
        def back(self):
            super().back()
            self.state = AppState.BACKGROUND

    model = FakeModel(act("back"), act("tap", target="e3"))
    res, d, _ = run1(tmp_path, clock, out, {"do": "Buy a laptop"}, device=LeavesOnBack(), model=model)
    assert failure_of(res).endswith("The app left the foreground after back")
    assert "tap" not in d.names() and len(model.asked) == 1


def test_a_failing_goal_with_nothing_quoted_says_jev_cant_type(tmp_path, clock, out):
    model = FakeModel(*[act("tap", target="e1")] * 3)
    res, _, _ = run1(tmp_path, clock, out, {"do": "Type hello into Email"}, model=model)
    assert failure_of(res).endswith(
        "Stuck repeating: tap text_field 'Email'. The goal has no \"quoted\" values, so Jev can't type anything"
    )


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
    assert "jev call 7 ms, 3 questions" in text and "jev call 7 ms, 1 question" in text


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
    """Checking an expect every interval on an identical screen must not spend model calls."""
    res, d, model = run1(tmp_path, clock, out, {"expect": "x", "timeout": 5}, model=FakeModel(yes(0.1)))
    assert failure_of(res).endswith(
        "Waited 5s until Jev judged it true of a screen that stopped moving; Jev says false (0.10)"
    )
    assert len(model.asked) == 1 and d.names().count("screen") == 21  # every 0.25 s for 5 s


# --- saved do: steps ---------------------------------------------------------------------------

SIGN_IN = "android · T · step 1 · Sign in"
TAP_SIGN_IN = SavedStep("tap", Target("button", "Sign in"))


def test_do_repeats_its_saved_steps_without_asking_jev(tmp_path, clock, out):
    model = FakeModel(saved={SIGN_IN: (SavedStep("type", Target("text_field", "Email"), "${E}"), TAP_SIGN_IN)})
    res, d, _ = run1(tmp_path, clock, out, {"do": "Sign in"}, model=model, variables={"E": "ann@x.io"})
    assert res.status is Status.PASS and res.steps[0].detail == "2 saved steps"
    assert [c for c in d.calls if c[0] in ("type_text", "tap")] == [
        ("type_text", "ann@x.io", (500, 150)),
        ("tap", 500, 450),
    ]
    assert res.steps[0].ran == ("type \"${E}\" into text_field 'Email'", "tap button 'Sign in'")
    assert not model.asked
    assert "→ tap button 'Sign in'" in out.getvalue() and "ann@x.io" not in out.getvalue()


def test_a_saved_step_waits_until_its_element_is_on_screen(tmp_path, clock, out):
    d = FakeDevice(screen_with("Loading"), screen_with("Loading"), login_screen())
    model = FakeModel(saved={SIGN_IN: (TAP_SIGN_IN,)})
    res, d, _ = run1(tmp_path, clock, out, {"do": "Sign in"}, device=d, model=model)
    assert res.status is Status.PASS and ("tap", 500, 450) in d.calls and clock.slept == [0.25] * 3


def test_a_saved_step_takes_the_same_one_of_several_namesakes(tmp_path, clock, out):
    s = login_screen()
    twice = dataclasses.replace(
        s, elements=(*s.elements, dataclasses.replace(s.elements[2], bounds=(0, 600, 1000, 700)))
    )
    model = FakeModel(saved={SIGN_IN: (SavedStep("tap", Target("button", "Sign in", 2, 2)),)})
    res, d, _ = run1(tmp_path, clock, out, {"do": "Sign in"}, device=FakeDevice(twice), model=model)
    assert res.status is Status.PASS and ("tap", 500, 650) in d.calls
    assert res.steps[0].ran == ("tap the 2nd of 2 button 'Sign in'",)


def test_frozen_fails_when_the_saved_steps_element_is_not_there_as_saved(tmp_path, clock, out):
    model = FakeModel(saved={SIGN_IN: (SavedStep("tap", Target("button", "Sign in", 2, 2)),)}, frozen=True)
    res, d, _ = run1(tmp_path, clock, out, {"do": "Sign in", "timeout": 1}, model=model)
    assert failure_of(res).endswith(
        "Waited 1s until the 2nd of 2 button 'Sign in' is on screen and stopped moving; the screen shows 1, the saved step "
        "was made with 2 (saved step 1 of 1: if the app changed since this do: was worked out, run --lock record to work "
        "it out again from there)"
    )
    assert "tap" not in d.names() and not model.asked


def test_record_works_a_goal_out_again_from_where_its_saved_steps_stopped_fitting(tmp_path, clock, out):
    """The app changed (here the button's text): Jev picks up from where the saved steps got to, and it's saved."""
    key = 'android · T · step 1 · Sign in as "a"'
    typed = SavedStep("type", Target("text_field", "Email"), "a")
    model = FakeModel(
        act("tap", target="e3"), act("done"), saved={key: (typed, SavedStep("tap", Target("button", "Log in")))}
    )
    res, _, _ = run1(tmp_path, clock, out, {"do": 'Sign in as "a"', "timeout": 1}, model=model)
    assert res.status is Status.PASS, res
    assert res.steps[0].detail == "2 steps, worked out by Jev"
    assert model.saved[key] == (typed, TAP_SIGN_IN)
    assert model.asked[0][0]["actions_taken"] == ["type \"a\" into text_field 'Email'"]


def test_a_used_tests_do_is_saved_under_that_test(tmp_path, clock, out):
    inner = make_test("Sign in", {"do": "Sign in"})
    model = FakeModel(act("tap", target="e3"), act("done"))
    runner, _, _ = make(tmp_path, clock, out, tests=[make_test("T", {"use": "Sign in"})], library=[inner], model=model)
    assert runner.run().tests[0].status is Status.PASS
    assert list(model.saved) == ["android · Sign in · step 1 · Sign in"]


def test_saved_steps_name_elements_as_the_output_does(tmp_path, clock, out):
    """By kind and name, a ${NAME} value by its name, and which of several with that name."""
    s = login_screen()
    email = dataclasses.replace(s.elements[0], text="ann@x.io", hint="")
    twice = dataclasses.replace(
        s,
        elements=(email, s.elements[1], s.elements[2], dataclasses.replace(s.elements[2], bounds=(0, 600, 1000, 700))),
    )
    model = FakeModel(act("clear", field="e1"), act("tap", target="e4"), act("done"))
    run1(
        tmp_path,
        clock,
        out,
        {"do": "Clear it and sign in"},
        device=FakeDevice(twice),
        model=model,
        variables={"E": "ann@x.io"},
    )
    assert model.saved["android · T · step 1 · Clear it and sign in"] == (
        SavedStep("clear", Target("text_field", "${E}")),
        SavedStep("tap", Target("button", "Sign in", 2, 2)),
    )


@pytest.mark.parametrize(
    ("action", "saved"),
    [
        ("scroll_down", SavedStep("scroll_down")),
        ("back", SavedStep("back")),
        ("press_enter", SavedStep("press_enter")),
        ("hide_keyboard", SavedStep("hide_keyboard")),
        ("double_tap", SavedStep("double_tap", Target("button", "Sign in"))),
        ("long_press", SavedStep("long_press", Target("button", "Sign in"))),
        ("swipe_left_on", SavedStep("swipe_left", Target("button", "Sign in"))),
        ("clear", SavedStep("clear", Target("text_field", "Email"))),
    ],
)
def test_every_move_is_saved_and_repeated_the_same(tmp_path, clock, out, action, saved):
    model = FakeModel(act(action, target="e3", field="e1"), act("done"))
    _, d1, _ = run1(
        tmp_path, clock, out, {"do": "Go"}, device=FakeDevice(login_screen(keyboard_visible=True)), model=model
    )
    assert model.saved["android · T · step 1 · Go"] == (saved,)
    again = FakeModel(saved=model.saved, frozen=True)
    second, d2, _ = run1(
        tmp_path, FakeClock(), out, {"do": "Go"}, device=FakeDevice(login_screen(keyboard_visible=True)), model=again
    )
    assert second.status is Status.PASS and not again.asked
    looking = ("screen", "app_state", "looks")  # reading the screen, not acting on it
    assert [c for c in d1.calls if c[0] not in looking] == [c for c in d2.calls if c[0] not in looking]


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
        keyboard_visible=True,
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


@pytest.mark.parametrize("field", ["text", "hint", "resource_id", "value", "parts"])
def test_a_value_is_masked_in_every_field_of_a_decision(tmp_path, clock, out, field):
    s = login_screen()
    fields: dict[str, Any] = {"text": "", "hint": ""}
    fields[field] = ("ann@x.io",) if field == "parts" else "ann@x.io"
    email = dataclasses.replace(s.elements[0], **fields)
    s = dataclasses.replace(s, elements=(email, *s.elements[1:]))
    model = FakeModel(act("tap", target="e1"), act("done"))
    res, _, _ = run1(
        tmp_path, clock, out, {"do": "Tap it"}, model=model, device=FakeDevice(s), variables={"E": "ann@x.io"}
    )
    assert res.status is Status.PASS
    assert "ann@x.io" not in repr(res.steps[0].decisions) and "ann@x.io" not in out.getvalue()


def test_a_value_in_a_start_failure_is_shown_by_its_name(tmp_path, clock, out):
    d = FakeDevice()
    d.fail["launch"] = DeviceError("could not open ann@x.io")
    res, _, _ = run1(tmp_path, clock, out, "back", device=d, variables={"E": "ann@x.io"})
    assert res.failure == "(start app) — could not open ${E}"
    assert "ann@x.io" not in out.getvalue()


def test_missing_target_is_reported_by_its_placeholder(tmp_path, clock, out):
    res, _, _ = run1(tmp_path, clock, out, {"tap": "${WHO}", "timeout": 1}, variables={"WHO": "Bob"})
    assert "Waited 1s until an element says '${WHO}' on screen and stopped moving" in failure_of(res)


# --- settings ------------------------------------------------------------------------


def test_a_files_settings_reach_every_step(tmp_path, clock, out):
    settings = Settings(timeout=2, interval=0.5)
    test = Test("T", True, (parse_step({"tap": "Ghost"}, settings),))
    suite = Suite(tmp_path / "t.yaml", {}, {}, (test,), {"T": test}, {}, settings=settings)
    d = FakeDevice()
    d.clock = clock
    res = TestRunner(
        suite, d, Brain(FakeModel()), tmp_path, platform=Platform.ANDROID, clock=clock, listener=console(out)
    ).run_test(test)
    assert failure_of(res).endswith("Waited 2s until an element says 'Ghost' on screen and stopped moving")
    assert clock.slept == [0.5] * 4  # checked at 0, 0.5, 1, 1.5 and 2 seconds


def test_a_step_can_check_less_often(tmp_path, clock, out):
    run1(tmp_path, clock, out, {"tap": "Ghost", "timeout": 1, "interval": 0.5})
    assert clock.slept == [0.5, 0.5]


@pytest.mark.parametrize(("p", "passes"), [(0.8, False), (0.81, True)])
def test_expect_can_ask_for_more_confidence(tmp_path, clock, out, p, passes):
    res, _, _ = run1(tmp_path, clock, out, {"expect": "x", "confidence": 0.8, "timeout": 1}, model=FakeModel(yes(p)))
    assert (res.status is Status.PASS) is passes


def test_do_can_allow_fewer_actions(tmp_path, clock, out):
    model = FakeModel(*[act("tap", target="e3") if i % 2 else act("back") for i in range(3)])
    res, _, _ = run1(tmp_path, clock, out, {"do": "Loop", "max_actions": 2}, model=model)
    assert "Goal not reached after 2 actions (max_actions); Jev's next would be " in failure_of(res)


def test_scroll_to_can_allow_fewer_scrolls(tmp_path, clock, out):
    d = FakeDevice(*held(screen_with("Item 0"), *[screen_with(f"Item {i}") for i in range(1, 10)]))
    res, d, _ = run1(tmp_path, clock, out, {"scroll_to": "Item 9", "direction": "down", "max_scrolls": 3}, device=d)
    assert failure_of(res).endswith("Scrolled down 3 times (max_scrolls) but never found 'Item 9'")


def test_a_limit_of_one_reads_as_one(tmp_path, clock, out):
    res, _, _ = run1(
        tmp_path, clock, out, {"do": "Loop", "max_actions": 1}, model=FakeModel(act("back"), act("tap", target="e3"))
    )
    assert "Goal not reached after 1 action (max_actions); Jev's next would be " in failure_of(res)
    d = FakeDevice(*held(screen_with("Item 0"), *[screen_with(f"Item {i}") for i in range(1, 5)]))
    res, _, _ = run1(tmp_path, clock, out, {"scroll_to": "Item 4", "direction": "down", "max_scrolls": 1}, device=d)
    assert failure_of(res).endswith("Scrolled down 1 time (max_scrolls) but never found 'Item 4'")


# --- exact matching ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("step", "on_screen", "failure"),
    [
        (
            {"tap": "Save", "timeout": 1},
            ["Unsaved changes", "Save draft"],
            "Waited 1s until an element says 'Save' on screen and stopped moving; close but not exact: 'Unsaved changes', 'Save draft'",
        ),
        (
            {"see": "Taps: 2", "timeout": 1},
            ["Taps: 20"],
            "Waited 1s until 'Taps: 2' is on screen; close but not exact: 'Taps: 20'",
        ),
        (
            {"see": "Sign in", "timeout": 1},
            ["Sign in now"],
            "Waited 1s until 'Sign in' is on screen; close but not exact: 'Sign in now'",
        ),
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


def test_the_output_says_when_jev_chose_between_exact_matches(tmp_path, clock, out):
    s = login_screen()
    twice = dataclasses.replace(
        s, elements=(*s.elements, dataclasses.replace(s.elements[2], bounds=(0, 600, 1000, 700)))
    )
    model = FakeModel(pick("e4"), confirm())
    res, d, _ = run1(tmp_path, clock, out, {"tap": "Sign in"}, device=FakeDevice(twice), model=model)
    assert res.steps[0].detail == "on button 'Sign in' (chosen by Jev among 2 exact matches)"
    assert ("tap", 500, 650) in d.calls


def test_an_element_is_touched_only_once_it_has_stopped_moving(tmp_path, clock, out):
    """A page sliding in: a tap where the button is now would land where it was a moment ago."""
    s = login_screen()
    sliding = [
        dataclasses.replace(
            s, elements=(*s.elements[:2], dataclasses.replace(s.elements[2], bounds=(x, 400, x + 1000, 500)))
        )
        for x in (300, 100)
    ]
    res, d, _ = run1(tmp_path, clock, out, {"tap": "Sign in"}, device=FakeDevice(*sliding, s))
    assert res.status is Status.PASS
    assert [c for c in d.calls if c[0] == "tap"] == [("tap", 500, 450)]  # not at 800 or 600, where it passed by


def test_an_element_is_touched_only_once_it_is_drawn_the_same_twice(tmp_path, clock, out):
    """A system dialog fading in on Android reports its final bounds at once: only its pixels show it moving."""
    d = FakeDevice(login_screen())
    d.drawn = ["faint", "fainter", "solid", "solid"]
    res, d, _ = run1(tmp_path, clock, out, {"tap": "Sign in"}, device=d)
    assert res.status is Status.PASS and d.names().count("looks") == 4 and clock.slept == [0.25] * 3


def test_scroll_to_brings_an_element_clear_of_the_edges(tmp_path, clock, out):
    """Just peeking in at the bottom it sits on the home-gesture strip, where a tap goes home."""
    d = FakeDevice(*held(listed("Show more", y=1960), listed("Show more", y=1200)))
    res, d, _ = run1(tmp_path, clock, out, {"scroll_to": "Show more", "direction": "down"}, device=d)
    assert res.status is Status.PASS and res.steps[0].detail == "1 scroll"


def test_scroll_to_takes_an_element_at_an_edge_at_the_end_of_the_content(tmp_path, clock, out):
    d = FakeDevice(listed("Show more", y=1960))  # it never moves: the content ends there
    res, d, _ = run1(tmp_path, clock, out, {"scroll_to": "Show more", "direction": "down"}, device=d)
    assert res.status is Status.PASS and d.names().count("drag") == 2


def test_scroll_to_takes_an_element_at_an_edge_after_its_last_scroll(tmp_path, clock, out):
    d = FakeDevice(*held(listed("Item 0"), listed("Item 1"), listed("Show more", y=1960)))
    res, _, _ = run1(tmp_path, clock, out, {"scroll_to": "Show more", "direction": "down", "max_scrolls": 2}, device=d)
    assert res.status is Status.PASS and res.steps[0].detail == "2 scrolls"
