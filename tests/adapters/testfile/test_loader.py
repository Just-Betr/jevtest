import dataclasses
import datetime
import re
from pathlib import Path

import pytest

from jevtest.adapters.testfile.loader import is_test_file, load, parse_step, platform_of
from jevtest.domain.failures import TestFileError
from jevtest.domain.kinds import Direction, Gesture, Orientation
from jevtest.domain.settings import DEFAULTS, Settings
from jevtest.domain.steps import (
    Back,
    Background,
    DarkMode,
    Do,
    Expect,
    Location,
    Network,
    NotSee,
    Rotate,
    Scroll,
    ScrollTo,
    See,
    Swipe,
    Touch,
    TypeText,
    Use,
    Wait,
)
from jevtest.domain.variables import fill

EXAMPLES = Path(__file__).parents[3] / "examples"


# --- steps -------------------------------------------------------------------------

@pytest.mark.parametrize(("raw", "action"), [
    ("back", Back()),
    ({"back": None}, Back()),
    ({"do": "Open settings"}, Do("Open settings")),
    ({"use": "Sign in"}, Use("Sign in")),
    ({"tap": "OK"}, Touch(Gesture.TAP, "OK")),
    ({"tap": "42"}, Touch(Gesture.TAP, "42")),
    ({"long_press": "Hold"}, Touch(Gesture.LONG_PRESS, "Hold")),
    ({"wait": 2}, Wait(2.0)),
    ({"wait": 0.5}, Wait(0.5)),
    ({"background": 0}, Background(0.0)),
    ({"scroll": "down"}, Scroll(Direction.DOWN)),
    ({"swipe": "left"}, Swipe(Direction.LEFT)),
    ({"swipe": "left", "target": "Item 3"}, Swipe(Direction.LEFT, "Item 3")),
    ({"rotate": "landscape"}, Rotate(Orientation.LANDSCAPE)),
    ({"location": [37.7, -122.4]}, Location(37.7, -122.4)),
    ({"dark_mode": True}, DarkMode(True)),
    ({"network": False}, Network(False)),
    ({"type": "hello"}, TypeText("hello")),
    ({"type": " two  spaces "}, TypeText(" two  spaces ")),  # typed exactly as written
    ({"type": ""}, TypeText("")),
    ({"type": {"text": "a", "into": "Email"}}, TypeText("a", "Email")),
    ({"type": {"text": "a"}}, TypeText("a")),
    ({"scroll_to": "Item 3", "direction": "down"}, ScrollTo("Item 3", Direction.DOWN)),
])
def test_actions_parse(raw, action):
    step = parse_step(raw)
    assert step.action == action
    assert step.source == raw


def test_type_into_with_a_timeout():
    step = parse_step({"type": {"text": "a", "into": "Email"}, "timeout": 3})
    assert (step.action, step.settings.timeout) == (TypeText("a", "Email"), 3.0)


def test_action_then_checks():
    s = parse_step({"do": "Sign in", "expect": "Home shows", "see": ["Welcome", "Log out"], "not_see": "Error"})
    assert s.action == Do("Sign in")
    assert s.checks == (Expect("Home shows"), See("Welcome"), See("Log out"), NotSee("Error"))


def test_checks_only_step():
    s = parse_step({"expect": "Home shows", "timeout": 2})
    assert s.action is None and s.checks == (Expect("Home shows"),) and s.settings.timeout == 2.0


@pytest.mark.parametrize("raw", [
    {"tap": "x", "timeout": 1}, {"clear": "x", "timeout": 1}, {"type": {"text": "a", "into": "E"}, "timeout": 1},
    {"swipe": "left", "target": "Item", "timeout": 1}, {"back": None, "see": "x", "timeout": 1},
    {"scroll_to": "x", "direction": "up"},
])
def test_options_where_they_apply(raw):
    parse_step(raw)


@pytest.mark.parametrize(("raw", "message"), [
    ("", "Empty step"),
    ("bakc", r"Unknown step 'bakc'.*one of back, .*write `- do: bakc`"),
    ("Sign in", r"write `- do: Sign in`"),
    (["a"], "action word or a mapping"),
    ({"tap": "x", "bogus": 1}, "unknown keys: bogus"),
    ({"tap": "x", "do": "y"}, "more than one action"),
    ({"timeout": 3}, "no action or check"),
    ({"back": "now"}, "'back' takes no value, got 'now'"),
    ({"tap": None}, "'tap' needs text, got nothing"),
    ({"tap": 42}, r"'tap' needs text, got a number \(to use 42 as text, put it in quotes\)"),
    ({"tap": True}, r"'tap' needs text, got true/false \(to use True as text"),
    ({"tap": ["a"]}, "'tap' needs text, got a list$"),
    ({"tap": {"a": 1}}, "'tap' needs text, got a mapping"),
    ({"tap": datetime.date(2026, 1, 1)}, "'tap' needs text, got date"),
    ({"tap": "  "}, "without leading or trailing spaces"),
    ({"tap": " OK"}, "without leading or trailing spaces"),
    ({"wait": "0.5"}, r"'wait' must be a number, got '0.5' \(remove the quotes\)"),
    ({"wait": "soon"}, r"'wait' must be a number, got 'soon'$"),
    ({"wait": -1}, "at least 0"),
    ({"wait": float("nan")}, "finite"),
    ({"wait": True}, "must be a number, got True$"),
    ({"scroll": "DOWN"}, "'scroll' must be one of up, down, left, right; got 'DOWN'"),
    ({"swipe": None}, "'swipe' must be one of"),
    ({"rotate": "upside"}, "'rotate' must be one of"),
    ({"location": "1,2"}, r"location must be \[latitude, longitude\]"),
    ({"location": [1]}, r"location must be \[latitude, longitude\]"),
    ({"location": [91, 0]}, "out of range"),
    ({"location": [0, 181]}, "out of range"),
    ({"location": [-91, 0]}, "latitude must be at least -90"),
    ({"location": ["1", 0]}, "latitude must be a number"),
    ({"dark_mode": "light"}, r"'dark_mode' must be on or off \(true or false\), got 'light'"),
    ({"type": None}, "'type' needs text .*got nothing"),
    ({"type": 5}, "'type' needs text .*got a number"),
    ({"type": {"txt": "a"}}, r"type has unknown keys: txt \(it takes text and into\)"),
    ({"type": {"into": "E"}}, "type needs `text:`"),
    ({"type": {"text": "a", "into": "E"}, "into": "F"}, "`into` is given twice"),
    ({"type": "a", "text": "b"}, "`text` goes inside type"),
    ({"tap": "x", "max_actions": 3}, "`max_actions` only applies to a do: step"),
    ({"do": "x", "max_scrolls": 3}, "`max_scrolls` only applies to a scroll_to: step"),
    ({"do": "x", "see": "y", "confidence": 0.9}, "`confidence` only applies to a step with an expect: check"),
    ({"wait": 1, "settle": 5}, "`settle` only applies to a step whose action changes the screen"),
    ({"see": "x", "settle": 5}, "`settle` only applies to a step whose action changes the screen"),
    ({"do": "x", "max_actions": 51}, "`max_actions` must be from 1 to 50, got 51"),
    ({"do": "x", "max_actions": 2.5}, "`max_actions` must be a whole number from 1 to 50, got 2.5"),
    ({"do": "x", "max_actions": True}, "`max_actions` must be a whole number"),
    ({"expect": "x", "confidence": 0.3}, "`confidence` must be from 0.5 to 0.99, got 0.3"),
    ({"tap": "x", "timeout": 0}, "`timeout` must be from 1 to 300, got 0"),
    ({"back": None, "settle": 0.5}, "`settle` must be from 1 to 30, got 0.5"),
    ({"tap": "x", "direction": "up"}, "`direction` belongs to scroll_to, not to tap"),
    ({"see": "x", "direction": "up"}, "`direction` belongs to scroll_to, not to a checks-only step"),
    ({"scroll_to": "x"}, "'scroll_to' needs `direction:`"),
    ({"scroll_to": "x", "direction": "in"}, "direction must be one of"),
    ({"swipe": "left", "target": ""}, "target needs text without"),
    ({"type": "a", "into": 3}, "into needs text"),
    ({"back": None, "timeout": 3}, "`timeout` only applies to a step that finds an element or has checks"),
    ({"type": "a", "timeout": 3}, "`timeout` only applies"),
    ({"tap": "x", "timeout": "long"}, "`timeout` must be a number"),
    ({"expect": []}, "needs at least one value"),
    ({"see": ""}, "'see' needs text without"),
    ({"see": {"a": 1}}, "'see' needs text, got a mapping"),
])
def test_bad_steps_are_rejected(raw, message):
    with pytest.raises(TestFileError, match=message):
        parse_step(raw)


# --- files -------------------------------------------------------------------------

def write(tmp_path, body: str, name="t.yaml") -> Path:
    for app in ("a.apk", "b.aab"):
        (tmp_path / app).write_text("")
    f = tmp_path / name
    f.write_text(body)
    return f


def minimal(tests="  - {name: T, fresh: true, steps: [back]}\n", extra="", device="device: {android: Pixel}\n"):
    return f"app: a.apk\n{device}{extra}tests:\n{tests}"


def test_minimal_file(tmp_path):
    spec = load(write(tmp_path, minimal()), {})
    assert spec.apps == {"android": (tmp_path / "a.apk").resolve()}
    assert spec.devices == {"android": ("Pixel",)} and spec.tests[0].fresh is True
    assert spec.variables == {} and spec.includes == () and list(spec.library) == ["T"]


def test_example_files_load():
    env = {"DEMO_EMAIL": "e", "DEMO_PASSWORD": "p", "ANDROID_DEVICE": "P", "IOS_DEVICE": "BH",  # CI has no .env
           "IOS_APP": "Runner.app"}
    spec = load(EXAMPLES / "demo.yaml", env)
    assert spec.devices == {"android": ("P",), "ios": ("BH",)}
    assert set(spec.apps) == {"android", "ios"}
    assert len({t.name for t in spec.tests}) == len(spec.tests)


def test_both_platforms_and_several_devices(tmp_path):
    (tmp_path / "x.zip").write_text("")
    spec = load(write(tmp_path, minimal(device="device: {android: [Pixel 4a, Pixel 8], ios: BH}\n")
                      .replace("app: a.apk", "app: {android: a.apk, ios: x.zip}")), {})
    assert list(spec.apps) == ["android", "ios"]
    assert spec.devices == {"android": ("Pixel 4a", "Pixel 8"), "ios": ("BH",)}


def test_no_settings_means_the_defaults(tmp_path):
    suite = load(write(tmp_path, minimal()), {})
    assert suite.settings == DEFAULTS and suite.tests[0].steps[0].settings == DEFAULTS


def test_a_files_settings_apply_to_every_step_and_a_step_can_change_its_own(tmp_path):
    body = minimal(tests="  - {name: T, fresh: true, steps: [back, {tap: x, timeout: 30}]}\n",
                   extra="settings: {model: typesafe/jev-2, timeout: 20, settle: 5, max_actions: 20, "
                         "max_scrolls: 100, confidence: 0.8}\n")
    suite = load(write(tmp_path, body), {})
    expected = Settings("typesafe/jev-2", 20, 5, 20, 100, 0.8)
    back, tap = suite.tests[0].steps
    assert suite.settings == expected and back.settings == expected
    assert tap.settings == dataclasses.replace(expected, timeout=30)


def test_included_tests_run_with_the_settings_of_the_file_being_run(tmp_path):
    (tmp_path / "lib.yaml").write_text("tests:\n  - {name: L, fresh: true, steps: [back]}\n")
    body = minimal(extra="include: lib.yaml\nsettings: {settle: 9}\n")
    assert load(write(tmp_path, body), {}).library["L"].steps[0].settings.settle == 9


def test_every_bad_step_in_a_test_is_reported_at_once(tmp_path):
    body = minimal(tests="  - {name: T, fresh: true, steps: [{tap: x, max_actions: 3}, back, {wait: 1, settle: 5}]}\n")
    with pytest.raises(TestFileError, match="2 problems") as e:
        load(write(tmp_path, body), {})
    assert "Test 'T', step 1: `max_actions`" in str(e.value) and "Test 'T', step 3: `settle`" in str(e.value)


def test_every_bad_setting_is_reported_at_once(tmp_path):
    body = minimal(extra="settings: {timeout: 1000, confidence: 0.3, model: gpt-5}\n")
    with pytest.raises(TestFileError, match="3 problems") as e:
        load(write(tmp_path, body), {})
    assert "`timeout` must be from 1 to 300" in str(e.value) and "`confidence` must be" in str(e.value)
    assert "`model` must be a Jev model" in str(e.value)


@pytest.mark.parametrize(("settings", "message"), [
    ("settings: 5", "`settings` must be a mapping of model, confidence, max_actions, max_scrolls, settle, timeout"),
    ("settings: {}", "`settings` must be a mapping"),
    ("settings: {wait: 5}", "`settings` has an unknown key: wait (it takes model, confidence"),
    ("settings: {model: gpt-5}", "`model` must be a Jev model (typesafe/jev-...), got 'gpt-5'"),
    ("settings: {model: 5}", "`model` needs text"),
    ("settings: {timeout: 1000}", "`timeout` must be from 1 to 300, got 1000"),
    ("settings: {timeout: '5'}", "`timeout` must be a number, got '5' (remove the quotes)"),
])
def test_bad_settings_are_rejected(tmp_path, settings, message):
    with pytest.raises(TestFileError, match=re.escape(message)):
        load(write(tmp_path, minimal(extra=settings + "\n")), {})


@pytest.mark.parametrize(("body", "message"), [
    ("", "must be a YAML mapping"),
    ("app: [", "not valid YAML"),
    (minimal().replace("app: a.apk\n", ""), "Missing `app:`"),
    (minimal().replace("a.apk", "a.txt"), "Unknown app type"),
    (minimal().replace("app: a.apk", "app: {android: b.aab, ios: a.apk}"), "app.ios points at a android build"),
    (minimal().replace("app: a.apk", "app: {web: a.apk}"), "must be android and/or ios"),
    (minimal(extra="extra: 1\n"), "Unknown top-level keys: extra"),
    # device: required for every platform
    (minimal(device=""), r"Missing `device:`.*e\.g\. \{android: Pixel 4a"),
    (minimal(device="device: Pixel\n"), "must name a device per platform"),
    (minimal(device="device: {web: x}\n"), "keys must be android and/or ios"),
    (minimal(device="device: {android: P, ios: BH}\n"), "names an ios device, but `app` has no ios build"),
    (minimal(device="device: {android: ''}\n"), "device.android needs text"),
    (minimal(device="device: {android: []}\n"), "at least one device"),
    (minimal(device="device: {android: [A, A]}\n"), "lists a device twice"),
    (minimal(device="device: {}\n"), "`device` has no android device, but `app` has an android build"),
    # tests
    (minimal(tests="  []\n"), "No tests found"),
    (minimal(tests="  - {name: T, steps: [back]}\n"), r"needs `name`, `fresh` \(true: start from a clean install"),
    (minimal(tests="  - {fresh: true, steps: [back]}\n"), "needs `name`, `fresh`"),
    (minimal(tests="  - {name: 7, fresh: true, steps: [back]}\n"), "Test #1 in t.yaml: name needs text"),
    (minimal(tests="  - {name: T, fresh: true, steps: []}\n"), "at least one step"),
    (minimal(tests="  - {name: T, fresh: true, steps: [back], tags: [x]}\n"), "unknown keys: tags"),
    (minimal(tests="  - {name: T, fresh: true, steps: [{wait: x}]}\n"),
     r"Test 'T', step 1: 'wait' must be a number, got 'x'$"),
    (minimal(tests="  - {name: T, fresh: sometimes, steps: [back]}\n"), "Test 'T': fresh must be on or off"),
    (minimal(tests="  - {name: T, fresh: true, steps: [back]}\n" * 2), "unique .*: T"),
])
def test_bad_files_are_rejected(tmp_path, body, message):
    with pytest.raises(TestFileError, match=message):
        load(write(tmp_path, body), {})


def test_every_problem_is_reported_at_once(tmp_path):
    body = minimal(device="", tests="  - {name: A, steps: [back]}\n  - {name: B, fresh: true, steps: [back, bakc]}\n")
    with pytest.raises(TestFileError) as e:
        load(write(tmp_path, body), {})
    lines = str(e.value).splitlines()
    assert lines[0] == "t.yaml has 3 problems:"
    assert [line.split(":")[0] for line in lines[1:]] == [
        "  - Missing `device", "  - Test #1 in t.yaml needs `name`, `fresh` (true", "  - Test 'B', step 2"]


def test_uses_are_checked_once_the_tests_are_valid(tmp_path):
    body = minimal(tests="  - {name: A, fresh: true, steps: [{use: Nope}]}\n  - {name: B, steps: [back]}\n")
    with pytest.raises(TestFileError, match="needs `name`, `fresh`") as e:
        load(write(tmp_path, body), {})
    assert "Nope" not in str(e.value)


def test_unquoted_variable_in_braces_gets_a_hint(tmp_path):
    with pytest.raises(TestFileError, match=r'(?s)not valid YAML.*\nA value starting with \$\{ must be quoted'):
        load(write(tmp_path, "app: a.apk\ndevice: {android: ${PHONE}}\n"), {})


def test_missing_file(tmp_path):
    with pytest.raises(TestFileError, match="not found"):
        load(tmp_path / "nope.yaml", {})


def test_use_links_tests(tmp_path):
    spec = load(write(tmp_path, minimal(tests="""
  - name: Sign in
    fresh: true
    steps: [{do: sign in}]
  - name: Counter
    fresh: false
    steps: [{use: Sign in}, {do: tap add, see: "Taps: 1"}]
""")), {})
    assert spec.tests[1].steps[0].action == Use("Sign in") and spec.library["Sign in"] is spec.tests[0]
    assert spec.tests[1].fresh is False


def test_use_nests(tmp_path):
    spec = load(write(tmp_path, minimal(tests="""
  - {name: A, fresh: true, steps: [back]}
  - {name: B, fresh: true, steps: [{use: A}]}
  - {name: C, fresh: true, steps: [{use: B}, {use: A}]}
""")), {})
    assert [s.action for s in spec.tests[2].steps] == [Use("B"), Use("A")]


@pytest.mark.parametrize(("tests", "message"), [
    ("  - {name: A, fresh: true, steps: [{use: Nope}]}\n", "no test has that name"),
    ("  - {name: A, fresh: true, steps: [{use: A}]}\n", "loop: A -> A"),
    ("  - {name: A, fresh: true, steps: [{use: B}]}\n  - {name: B, fresh: true, steps: [{use: A}]}\n",
     "loop: A -> B -> A"),
])
def test_use_errors(tmp_path, tests, message):
    with pytest.raises(TestFileError, match=message):
        load(write(tmp_path, minimal(tests=tests)), {})


@pytest.mark.parametrize(("name", "platform"), [
    ("x.apk", "android"), ("x.AAB", "android"), ("x.app", "ios"), ("x.zip", "ios"), ("x.ipa", "ios")])
def test_platform_of(name, platform):
    assert platform_of(Path(name)) == platform


def test_platform_of_unknown():
    with pytest.raises(TestFileError):
        platform_of(Path("x.exe"))


# --- ${NAME} values ----------------------------------------------------------------------

VARS = ("app: ${APP}\ndevice: {android: \"${PHONE}\"}\n" +
        """tests:
  - name: Sign in
    fresh: true
    steps:
      - type: {text: "${PASSWORD}", into: Password}
        see: Hi ${USER_NAME}
""")


def test_variables_come_from_env(tmp_path):
    env = {"APP": "a.apk", "PHONE": "Pixel", "PASSWORD": "pw", "USER_NAME": "Ann", "OTHER": "x"}
    spec = load(write(tmp_path, VARS), env)
    assert spec.variables == {k: v for k, v in env.items() if k != "OTHER"}  # only what the file uses
    assert spec.apps["android"].name == "a.apk" and spec.devices == {"android": ("Pixel",)}
    step = spec.tests[0].steps[0]
    assert step.action == TypeText("${PASSWORD}", "Password") and step.checks == (See("Hi ${USER_NAME}"),)
    assert fill(step.action.text, spec.variables) == "pw"  # filled only when used


def test_only_the_given_env_counts(tmp_path, monkeypatch):
    monkeypatch.setenv("APP", "a.apk")  # the process environment is not looked at by load()
    with pytest.raises(TestFileError, match=r"Not set: \$\{APP\}"):
        load(write(tmp_path, minimal().replace("a.apk", "${APP}")), {})


def test_missing_variables_are_named(tmp_path):
    with pytest.raises(TestFileError, match=r"Not set: \$\{APP\}, \$\{PASSWORD\}, \$\{PHONE\}.*\.env"):
        load(write(tmp_path, VARS), {"USER_NAME": "y"})


# --- include ------------------------------------------------------------------------------

LIB = """tests:
  - name: Sign in
    fresh: true
    steps: [{type: {text: "${PASSWORD}", into: Password}}]
"""


def test_included_tests_can_be_used_but_do_not_run(tmp_path):
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "auth.yaml").write_text("include: common.yaml\n" + LIB)
    (tmp_path / "lib" / "common.yaml").write_text("tests: [{name: Home, fresh: true, steps: [home]}]\n")
    tests = "  - {name: Counter, fresh: true, steps: [{use: Sign in}, {use: Home}]}\n"
    spec = load(write(tmp_path, minimal(extra="include: [lib/auth.yaml, lib/common.yaml]\n", tests=tests)),
                {"PASSWORD": "pw"})
    assert [t.name for t in spec.tests] == ["Counter"]
    assert [s.action for s in spec.tests[0].steps] == [Use("Sign in"), Use("Home")]
    assert set(spec.library) == {"Counter", "Sign in", "Home"}
    assert spec.variables == {"PASSWORD": "pw"}  # a library's ${NAME}s count too
    assert [p.name for p in spec.includes] == ["common.yaml", "auth.yaml"]


@pytest.mark.parametrize(("files", "message"), [
    ({"a.yaml": "include: b.yaml\ntests: [{name: A, fresh: true, steps: [back]}]\n",
      "b.yaml": "include: a.yaml\ntests: [{name: B, fresh: true, steps: [back]}]\n"},
     "loop: t.yaml -> a.yaml -> b.yaml -> a.yaml"),
    ({"a.yaml": "app: x.apk\ntests: [{name: A, fresh: true, steps: [back]}]\n"},
     "can only have `include` and `tests`.*app"),
    ({"a.yaml": "tests: [{name: T, fresh: true, steps: [back]}]\n"}, "unique .*: T"),
    ({"a.yaml": "tests: []\n"}, "No tests found under `tests:` in a.yaml"),
    ({"a.yaml": "include: 3\ntests: [{name: A, fresh: true, steps: [back]}]\n"}, "include in a.yaml needs text"),
    ({}, "Test file not found: .*a.yaml"),
])
def test_bad_includes(tmp_path, files, message):
    for name, body in files.items():
        (tmp_path / name).write_text(body)
    with pytest.raises(TestFileError, match=message):
        load(write(tmp_path, minimal(extra="include: a.yaml\n")), {})


def test_include_cannot_include_the_main_file(tmp_path):
    (tmp_path / "a.yaml").write_text("include: t.yaml\ntests: [{name: A, fresh: true, steps: [back]}]\n")
    with pytest.raises(TestFileError, match="loop: t.yaml -> a.yaml -> t.yaml"):
        load(write(tmp_path, minimal(extra="include: a.yaml\n")), {})


def test_is_test_file(tmp_path):
    assert is_test_file(write(tmp_path, minimal()))
    assert not is_test_file(write(tmp_path, "tests: []\n", name="lib.yaml"))
    assert is_test_file(write(tmp_path, "app: [", name="broken.yaml"))  # run it so the error is shown
