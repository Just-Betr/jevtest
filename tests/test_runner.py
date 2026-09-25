import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from jevtest.brain import Brain
from jevtest.jev import JevError
from jevtest.runner import FOCUS_DELAY, POLL, Clock, Runner, failure_of, write_junit, write_report
from jevtest.screen import Screen
from jevtest.spec import Settings, Spec, Test, parse_step

from .conftest import DriverError, FakeClock, FakeDriver, FakeJev, act, el, login_screen, pick, screen_with, yes


def make(tmp_path, clock, out, *steps, driver=None, jev=None, verbose=False, tests=None, **settings):
    tests = tests or [Test("T", [parse_step(s) for s in steps])]
    spec = Spec(path=tmp_path / "t.yaml", apps={}, tests=tests, settings=Settings(**settings))
    driver = driver or FakeDriver()
    jev = jev or FakeJev()
    return Runner(spec, driver, Brain(jev), tmp_path, verbose=verbose, clock=clock, out=out), driver, jev


def run1(tmp_path, clock, out, *steps, **kw):
    r, driver, jev = make(tmp_path, clock, out, *steps, **kw)
    return r.run()["tests"][0], driver, jev


# --- test lifecycle ------------------------------------------------------------------

def test_fresh_test_resets_app(tmp_path, clock, out):
    res, d, _ = run1(tmp_path, clock, out, "back")
    assert res["status"] == "pass"
    assert d.names()[:3] == ["stop", "clear_data", "launch"]
    assert "PASS T" in out.getvalue()
    assert res["failure"] is None and res["log"][0] == "\n▶ T"


def test_not_fresh_keeps_running_app(tmp_path, clock, out):
    t = Test("T", [parse_step("back")], fresh=False)
    res, d, _ = run1(tmp_path, clock, out, tests=[t])
    assert "launch" not in d.names() and "clear_data" not in d.names()


def test_not_fresh_launches_closed_app(tmp_path, clock, out):
    t = Test("T", [parse_step({"see": "Sign in"})], fresh=False)
    res, d, _ = run1(tmp_path, clock, out, tests=[t], driver=FakeDriver(state="not_running"))
    assert d.names()[:2] == ["app_state", "launch"]
    assert res["status"] == "pass"


def test_app_that_cannot_start_fails_the_test(tmp_path, clock, out):
    d = FakeDriver()
    d.fail["launch"] = DriverError("boom")
    res, _, _ = run1(tmp_path, clock, out, "back", driver=d)
    assert res["status"] == "fail" and res["failure"] == "(start app) — boom"
    assert "could not start app: boom" in out.getvalue()
    assert res["steps"][0]["screenshot"] == "001_FAIL_T.png"


def test_run_totals(tmp_path, clock, out):
    tests = [Test("A", [parse_step("back")]), Test("B", [parse_step({"see": "missing"})])]
    r, _, _ = make(tmp_path, clock, out, tests=tests, timeout=0)
    results = r.run()
    assert (results["passed"], results["failed"]) == (1, 1)


def test_first_failure_stops_the_test_and_screenshots(tmp_path, clock, out):
    res, d, _ = run1(tmp_path, clock, out, {"see": "Nope"}, "back", timeout=0)
    assert res["status"] == "fail"
    assert "back" not in d.names()
    assert res["steps"][-1]["screenshot"] == "001_FAIL_T.png"
    assert res["failure"] == "see: Nope — not on screen"


def test_screenshot_failure_is_reported_not_raised(tmp_path, clock, out):
    d = FakeDriver()
    d.fail["screenshot"] = DriverError("no display")
    res, _, _ = run1(tmp_path, clock, out, {"screenshot": "x"}, driver=d)
    assert res["steps"][0]["detail"] == "saved (screenshot failed: no display)"


# --- simple actions -------------------------------------------------------------------

@pytest.mark.parametrize("step,call", [
    ("launch", ("launch",)),
    ("back", ("back",)),
    ("hide_keyboard", ("hide_keyboard",)),
    ({"key": "enter"}, ("key", "enter")),
    ({"rotate": "landscape"}, ("rotate", "landscape")),
    ({"location": "1,2"}, ("set_location", 1.0, 2.0)),
    ({"open_url": "app://x"}, ("open_url", "app://x")),
    ({"dark_mode": "on"}, ("dark_mode", True)),
    ({"grant": "CAMERA"}, ("grant", "CAMERA")),
    ({"network": "off"}, ("network", False)),
    ({"swipe": "up"}, ("drag", 500, 1600, 500, 400)),
    ({"scroll": "down"}, ("drag", 500, 1600, 500, 400)),
])
def test_simple_actions_call_the_driver(tmp_path, clock, out, step, call):
    res, d, _ = run1(tmp_path, clock, out, step)
    assert res["status"] == "pass", res
    assert call in d.calls


def test_lifecycle_actions(tmp_path, clock, out):
    res, d, _ = run1(tmp_path, clock, out, "stop", "clear_data", "reinstall", "launch", "restart", "home")
    assert res["status"] == "pass"
    assert d.names()[3:] == ["stop", "stop", "clear_data", "reinstall", "launch", "app_state",
                             "stop", "launch", "app_state", "home"]


def test_wait_and_background_use_the_clock(tmp_path, clock, out):
    res, d, _ = run1(tmp_path, clock, out, {"wait": 3}, {"background": 2}, settle=0)
    assert 3.0 in clock.slept and 2.0 in clock.slept
    assert [n for n in d.names() if n in ("home", "resume")] == ["home", "resume"]


def test_settle_after_actions(tmp_path, clock, out):
    run1(tmp_path, clock, out, "back", settle=0.7)
    assert clock.slept.count(0.7) == 2  # after launch, after back


def test_screenshot_step(tmp_path, clock, out):
    res, d, _ = run1(tmp_path, clock, out, {"screenshot": "home page!"})
    assert res["steps"][0]["detail"] == "saved 001_home_page.png"
    assert (tmp_path / "001_home_page.png").exists()


def test_screenshot_name_falls_back(tmp_path, clock, out):
    res, _, _ = run1(tmp_path, clock, out, {"screenshot": "!!!"})
    assert res["steps"][0]["detail"] == "saved 001_screen.png"


# --- element actions ----------------------------------------------------------------------

@pytest.mark.parametrize("kind", ["tap", "double_tap", "long_press"])
def test_touch_actions_find_the_element(tmp_path, clock, out, kind):
    res, d, _ = run1(tmp_path, clock, out, {kind: "Sign in"})
    assert (kind, 500, 450) in d.calls
    assert res["steps"][0]["detail"] == "on button 'Sign in'"


def test_tap_asks_jev_when_no_exact_match(tmp_path, clock, out):
    res, d, jev = run1(tmp_path, clock, out, {"tap": "the login button"}, jev=FakeJev(pick("e3")))
    assert ("tap", 500, 450) in d.calls and len(jev.asked) == 1


def test_tap_waits_for_the_element(tmp_path, clock, out):
    d = FakeDriver(screen_with("Loading"), screen_with("Loading"), login_screen())
    jev = FakeJev(pick("not_on_screen"), pick("not_on_screen"))
    res, d, _ = run1(tmp_path, clock, out, {"tap": "Sign in"}, driver=d, jev=jev)
    assert res["status"] == "pass"
    assert clock.slept.count(POLL) == 2


def test_tap_gives_up_after_timeout(tmp_path, clock, out):
    jev = FakeJev(*[pick("not_on_screen")] * 10)
    res, d, _ = run1(tmp_path, clock, out, {"tap": "Ghost", "timeout": 1}, jev=jev)
    assert res["status"] == "fail"
    assert res["failure"] == "{'tap': 'Ghost', 'timeout': 1} — Could not find element 'Ghost' on screen"


def test_swipe_on_element(tmp_path, clock, out):
    res, d, _ = run1(tmp_path, clock, out, {"swipe": "left", "target": "Sign in"})
    assert ("drag", 850, 450, 150, 450) in d.calls
    assert res["steps"][0]["detail"] == "on button 'Sign in'"


def test_clear_finds_a_text_field(tmp_path, clock, out):
    res, d, _ = run1(tmp_path, clock, out, {"clear": "Email"})
    assert ("clear_text", "Email") in d.calls


def test_type_into_field(tmp_path, clock, out):
    res, d, _ = run1(tmp_path, clock, out, {"type": {"text": "a@b.c", "into": "Email"}})
    i = d.calls.index(("tap", 500, 150))
    assert d.calls[i + 1] == ("type_text", "a@b.c", (500, 150))
    assert FOCUS_DELAY in clock.slept
    assert res["steps"][0]["detail"] == "into text_field 'Email'"


def test_type_into_missing_field(tmp_path, clock, out):
    res, _, _ = run1(tmp_path, clock, out, {"type": {"text": "a", "into": "Phone"}, "timeout": 0},
                     jev=FakeJev(pick("not_on_screen")))
    assert "Could not find text field 'Phone'" in res["failure"]


def test_type_into_focused_field(tmp_path, clock, out):
    res, d, _ = run1(tmp_path, clock, out, {"type": "hi"})
    assert ("type_text", "hi", None) in d.calls


def test_scroll_to_scrolls_until_found(tmp_path, clock, out):
    d = FakeDriver(screen_with("Item 1"), screen_with("Item 1"), screen_with("Item 30"))
    jev = FakeJev(pick("not_on_screen"), pick("not_on_screen"))
    res, d, _ = run1(tmp_path, clock, out, {"scroll_to": "Item 30"}, driver=d, jev=jev)
    assert res["status"] == "pass"
    assert d.names().count("drag") == 2


def test_scroll_to_gives_up(tmp_path, clock, out):
    jev = FakeJev(*[pick("not_on_screen")] * 3)
    res, d, _ = run1(tmp_path, clock, out, {"scroll_to": "Item 99", "max_scrolls": 3, "direction": "up"},
                     driver=FakeDriver(screen_with("Item 1")), jev=jev)
    assert "Scrolled up but never found 'Item 99'" in res["failure"]
    assert d.names().count("drag") == 3


# --- crash / foreground detection ---------------------------------------------------------

@pytest.mark.parametrize("state,message", [
    ("not_running", "no longer running"), ("background", "left the foreground")])
def test_app_leaving_fails_the_step(tmp_path, clock, out, state, message):
    d = FakeDriver()
    res, _, _ = run1(tmp_path, clock, out, "back", driver=d)
    assert res["status"] == "pass"
    d2 = FakeDriver(state=state)
    res, _, _ = run1(tmp_path, clock, out, "back", driver=d2)
    assert message in res["failure"]


def test_leaving_the_app_on_purpose_is_allowed(tmp_path, clock, out):
    res, _, _ = run1(tmp_path, clock, out, "home", {"open_url": "https://x"}, "stop",
                     driver=FakeDriver(state="background"))
    assert res["status"] == "pass"


def test_driver_errors_fail_the_step(tmp_path, clock, out):
    d = FakeDriver()
    d.fail["rotate"] = DriverError("no sensor")
    res, _, _ = run1(tmp_path, clock, out, {"rotate": "landscape"}, driver=d)
    assert res["failure"] == "{'rotate': 'landscape'} — no sensor"
    assert "✗ rotate: landscape" in out.getvalue()


# --- checks ---------------------------------------------------------------------------------

def test_see_and_not_see(tmp_path, clock, out):
    res, _, _ = run1(tmp_path, clock, out, {"see": ["sign IN", "Email"], "not_see": "Error"})
    assert res["status"] == "pass"
    assert [c["check"] for c in res["steps"][0]["checks"]] == ["see", "see", "not_see"]


def test_see_waits_for_text(tmp_path, clock, out):
    d = FakeDriver(screen_with("Loading"), screen_with("Loading"), screen_with("Welcome"))
    res, _, _ = run1(tmp_path, clock, out, {"see": "Welcome"}, driver=d)
    assert res["status"] == "pass"


def test_not_see_fails_when_text_stays(tmp_path, clock, out):
    res, _, _ = run1(tmp_path, clock, out, {"not_see": "Sign in", "timeout": 1})
    assert res["failure"] == "not_see: Sign in — still on screen"


def test_expect_passes_above_threshold(tmp_path, clock, out):
    res, _, _ = run1(tmp_path, clock, out, {"expect": "Login form"}, jev=FakeJev(yes(0.3), yes(0.8)))
    check = res["steps"][0]["checks"][0]
    assert check == {"check": "expect", "text": "Login form", "status": "pass", "detail": "Jev 0.80"}


def test_expect_uses_threshold_setting(tmp_path, clock, out):
    res, _, _ = run1(tmp_path, clock, out, {"expect": "x", "timeout": 0}, jev=FakeJev(yes(0.8)), threshold=0.9)
    assert res["failure"] == "expect: x — Jev says false (0.80)"


def test_checks_run_after_the_action_and_stop_at_first_failure(tmp_path, clock, out):
    res, d, _ = run1(tmp_path, clock, out, {"tap": "Sign in", "see": ["Nope", "Sign in"], "timeout": 0})
    step = res["steps"][0]
    assert len(step["checks"]) == 1 and step["status"] == "fail"
    assert d.names().index("tap") < len(d.names()) - 1


def test_checks_skipped_when_action_fails(tmp_path, clock, out):
    d = FakeDriver()
    d.fail["back"] = DriverError("x")
    res, _, _ = run1(tmp_path, clock, out, {"back": None, "see": "Sign in"}, driver=d)
    assert "checks" not in res["steps"][0]


def test_jev_errors_fail_the_check(tmp_path, clock, out):
    res, _, _ = run1(tmp_path, clock, out, {"expect": "x"}, jev=FakeJev(JevError("HTTP 500")))
    assert res["failure"] == "expect: x — HTTP 500"


def test_output_nests_checks_under_actions(tmp_path, clock, out):
    run1(tmp_path, clock, out, {"tap": "Sign in", "see": "Email"}, {"see": "Password"})
    lines = out.getvalue().splitlines()
    assert any(line.startswith("  ✓ tap: Sign in") for line in lines)
    assert "      ✓ see: Email" in lines     # under its action
    assert "  ✓ see: Password" in lines      # checks-only step


# --- use -----------------------------------------------------------------------------------

def test_use_runs_the_other_tests_steps(tmp_path, clock, out):
    sign_in = Test("Sign in", [parse_step("back")])
    use = parse_step({"use": "Sign in", "see": "Email"})
    use.used = sign_in
    main = Test("Main", [use, parse_step("home")])
    r, d, _ = make(tmp_path, clock, out, tests=[main])
    res = r.run()["tests"][0]
    assert res["status"] == "pass"
    assert res["steps"][0]["steps"][0]["step"] == "back"
    assert "  ▸ use: Sign in" in out.getvalue() and "    ✓ back" in out.getvalue()


def test_failure_inside_use_is_reported(tmp_path, clock, out):
    inner = Test("Inner", [parse_step({"see": "Nope"})])
    use = parse_step({"use": "Inner"})
    use.used = inner
    r, d, _ = make(tmp_path, clock, out, tests=[Test("Outer", [use, parse_step("home")])], timeout=0)
    res = r.run()["tests"][0]
    assert res["failure"] == "see: Nope — not on screen"
    assert "home" not in d.names()


# --- the Jev loop ----------------------------------------------------------------------------

def test_do_types_and_taps_until_done(tmp_path, clock, out):
    email = login_screen()
    focused = login_screen()
    focused.elements[0].focused = True
    jev = FakeJev(act("type", field="e1", value="v0"), act("type", field="e1", value="v0"), act("done"))
    d = FakeDriver(email, focused)
    res, d, _ = run1(tmp_path, clock, out, 'Type "me@x.dev" into email', driver=d, jev=jev)
    assert res["status"] == "pass" and res["steps"][0]["detail"] == "2 action(s)"
    assert d.names().count("type_text") == 2
    assert d.calls.count(("tap", 500, 150)) == 1  # second time the field already had focus
    assert [x["did"] for x in res["steps"][0]["decisions"]][-1] == "done"
    assert "→ done  (confidence 0.90)" in out.getvalue()


@pytest.mark.parametrize("action,call", [
    ("tap", ("tap", 500, 450)), ("double_tap", ("double_tap", 500, 450)),
    ("long_press", ("long_press", 500, 450)), ("swipe_left_on", ("drag", 850, 450, 150, 450)),
    ("swipe_right_on", ("drag", 150, 450, 850, 450)), ("clear", ("clear_text", "Email")),
    ("scroll_down", ("drag", 500, 1600, 500, 400)), ("back", ("back",)), ("press_enter", ("key", "enter")),
    ("hide_keyboard", ("hide_keyboard",))])
def test_do_performs_each_action(tmp_path, clock, out, action, call):
    field = "e1" if action == "clear" else None
    jev = FakeJev(act(action, target="e3", field=field), act("done"))
    res, d, _ = run1(tmp_path, clock, out, "Do it", jev=jev, driver=FakeDriver(login_screen(keyboard_visible=True)))
    assert res["status"] == "pass", res
    assert call in d.calls


def test_do_wait_sleeps(tmp_path, clock, out):
    jev = FakeJev({"action": {"type": "choice", "choice": "wait", "confidence": 1, "probabilities": {}},
                   "target": {"type": "choice", "choice": "e1"}}, act("done"))
    run1(tmp_path, clock, out, "Do it", jev=jev, settle=0)
    assert 1.0 in clock.slept


def test_do_impossible(tmp_path, clock, out):
    res, _, _ = run1(tmp_path, clock, out, "Fly", jev=FakeJev(act("impossible")))
    assert "impossible" in res["failure"]


def test_do_gives_up_after_max_actions(tmp_path, clock, out):
    jev = FakeJev(act("tap", target="e3"), act("back"), act("tap", target="e3"))
    res, d, _ = run1(tmp_path, clock, out, {"do": "Loop", "max_actions": 2}, jev=jev)
    assert res["failure"].endswith("Goal not reached after 2 actions")
    assert len(res["steps"][0]["decisions"]) == 3


def test_do_detects_being_stuck(tmp_path, clock, out):
    jev = FakeJev(*[act("tap", target="e3")] * 3)
    res, d, _ = run1(tmp_path, clock, out, "Loop", jev=jev)
    assert "Stuck repeating: tap button 'Sign in'" in res["failure"]
    assert d.names().count("tap") == 2


# --- verbose -----------------------------------------------------------------------------------

def test_verbose_prints_every_jev_answer(tmp_path, clock, out):
    run1(tmp_path, clock, out, "Press sign in", {"expect": "x"}, verbose=True,
         jev=FakeJev(act("done"), yes(0.9)))
    text = out.getvalue()
    assert "jev action: done  [done 0.90, other 0.10]" in text
    assert "jev check: yes=0.90" in text
    assert "jev call 7 ms, 3 question(s)" in text and "jev call 7 ms, 1 question(s)" in text


def test_verbose_marks_lockfile_answers(tmp_path, clock, out):
    r, _, jev = make(tmp_path, clock, out, {"expect": "x"}, verbose=True, jev=FakeJev(yes(0.9)))
    real_ask = jev.ask

    def cached_ask(state, questions):
        answer = real_ask(state, questions)
        jev.calls[-1]["cached"] = True
        return answer
    jev.ask = cached_ask
    r.run()
    assert "jev call from lockfile" in out.getvalue()


def test_quiet_by_default(tmp_path, clock, out):
    run1(tmp_path, clock, out, {"expect": "x"}, jev=FakeJev(yes(0.9)))
    assert "jev call" not in out.getvalue()


# --- results ---------------------------------------------------------------------------------------

def test_failure_of():
    assert failure_of([{"status": "pass"}]) == "failed"
    assert failure_of([{"status": "fail", "step": "back"}]) == "back — "
    checks = [{"check": "see", "text": "A", "status": "pass", "detail": None},
              {"check": "see", "text": "B", "status": "fail", "detail": "not on screen"}]
    assert failure_of([{"status": "fail", "step": {}, "checks": checks}]) == "see: B — not on screen"


def test_junit_and_report(tmp_path):
    results = {"passed": 1, "failed": 1, "tests": [
        {"name": "A", "status": "pass", "seconds": 1.25, "failure": None, "log": ["ok"]},
        {"name": "B <x>", "status": "fail", "seconds": 2.0, "failure": "see: X — not on screen", "log": ["bad"]}]}
    path = tmp_path / "sub" / "junit.xml"
    write_junit(path, "jevtest.ios", results)
    suite = ET.parse(path).getroot().find("testsuite")
    assert suite.attrib == {"name": "jevtest.ios", "tests": "2", "failures": "1", "errors": "0", "time": "3.2"}
    cases = suite.findall("testcase")
    assert cases[0].find("failure") is None and cases[0].find("system-out").text == "ok"
    assert cases[1].attrib["name"] == "B <x>"
    assert cases[1].find("failure").attrib["message"] == "see: X — not on screen"

    write_report(tmp_path, {"platform": "ios"}, results, [{"ms": 1}])
    assert (tmp_path / "report.json").read_text().count('"platform": "ios"') == 1


def test_real_clock():
    c = Clock()
    before = c.now()
    c.sleep(0)
    assert c.now() >= before


def test_default_output_is_stdout(tmp_path, capsys):
    spec = Spec(path=Path("t"), apps={}, tests=[Test("T", [parse_step("back")])], settings=Settings(settle=0))
    Runner(spec, FakeDriver(), Brain(FakeJev()), tmp_path).run()
    assert "PASS T" in capsys.readouterr().out


def test_empty_screen_do(tmp_path, clock, out):
    d = FakeDriver(Screen(width=10, height=10))
    jev = FakeJev({"action": {"type": "choice", "choice": "done", "confidence": 1, "probabilities": {}}})
    res, _, _ = run1(tmp_path, clock, out, "Nothing to do", driver=d, jev=jev)
    assert res["status"] == "pass"


def test_element_helper():
    assert el("button", "x").text == "x"


def test_same_inputs_give_identical_runs(tmp_path):
    """Determinism: identical screens and Jev answers give identical logs and results."""
    import io

    def once():
        buf = io.StringIO()
        jev = FakeJev(act("type", field="e1", value="v0"), act("tap", target="e3"), act("done"), yes(0.9))
        steps = ['Sign in as "me@x.dev"', {"expect": "Home"}, {"see": "Sign in"}, {"swipe": "up"}]
        r, driver, _ = make(tmp_path, FakeClock(), buf, *steps, jev=jev, verbose=True)
        return buf.getvalue(), r.run(), driver.calls

    first, second = once(), once()
    assert first == second
    assert first[1]["passed"] == 1
