import datetime
from pathlib import Path

import pytest

from jevtest.spec import Settings, SpecError, fill, is_test_file, load, parse_step, platform_of

EXAMPLES = Path(__file__).parent.parent / "examples"
SETTINGS = "settings: {model: m, max_actions: 8, max_scrolls: 15, timeout: 10, settle: 3, threshold: 0.5}\n"


# --- steps -------------------------------------------------------------------------

@pytest.mark.parametrize("raw,kind,value", [
    ("back", "back", None),
    ({"back": None}, "back", None),
    ({"do": "Open settings"}, "do", "Open settings"),
    ({"use": "Sign in"}, "use", "Sign in"),
    ({"tap": "OK"}, "tap", "OK"),
    ({"tap": "42"}, "tap", "42"),
    ({"wait": 2}, "wait", 2.0),
    ({"wait": 0.5}, "wait", 0.5),
    ({"background": 0}, "background", 0.0),
    ({"scroll": "down"}, "scroll", "down"),
    ({"swipe": "left"}, "swipe", "left"),
    ({"rotate": "landscape"}, "rotate", "landscape"),
    ({"location": [37.7, -122.4]}, "location", (37.7, -122.4)),
    ({"dark_mode": True}, "dark_mode", True),
    ({"network": False}, "network", False),
    ({"type": "hello"}, "type", "hello"),
    ({"type": " two  spaces "}, "type", " two  spaces "),  # typed exactly as written
    ({"type": ""}, "type", ""),
    ({"type": {"text": "a", "into": "Email"}}, "type", "a"),
    ({"scroll_to": "Item 3", "direction": "down"}, "scroll_to", "Item 3"),
])
def test_actions_parse(raw, kind, value):
    step = parse_step(raw)
    assert (step.kind, step.value) == (kind, value)
    assert step.raw == raw


def test_type_into_becomes_an_option():
    step = parse_step({"type": {"text": "a", "into": "Email"}, "timeout": 3})
    assert step.opts == {"into": "Email", "timeout": 3.0}


def test_action_then_checks():
    s = parse_step({"do": "Sign in", "expect": "Home shows", "see": ["Welcome", "Log out"], "not_see": "Error"})
    assert s.kind == "do"
    assert s.checks == [("expect", "Home shows"), ("see", "Welcome"), ("see", "Log out"), ("not_see", "Error")]


def test_checks_only_step():
    s = parse_step({"expect": "Home shows", "timeout": 2})
    assert s.kind is None and s.checks == [("expect", "Home shows")] and s.opts == {"timeout": 2.0}
    assert s.title() == ""


@pytest.mark.parametrize("raw", [
    {"tap": "x", "timeout": 1}, {"clear": "x", "timeout": 1}, {"type": {"text": "a", "into": "E"}, "timeout": 1},
    {"swipe": "left", "target": "Item", "timeout": 1}, {"back": None, "see": "x", "timeout": 1},
    {"do": "x", "max_actions": 2}, {"scroll_to": "x", "direction": "up", "max_scrolls": 3},
])
def test_options_where_they_apply(raw):
    parse_step(raw)


@pytest.mark.parametrize("raw,message", [
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
    ({"do": "x", "max_actions": 0}, "max_actions must be at least 1"),
    ({"do": "x", "max_actions": 1.5}, "whole number"),
    ({"tap": "x", "max_actions": 2}, "`max_actions` belongs to do, not to tap"),
    ({"see": "x", "direction": "up"}, "`direction` belongs to scroll_to, not to a checks-only step"),
    ({"scroll_to": "x"}, "'scroll_to' needs `direction:`"),
    ({"scroll_to": "x", "direction": "in"}, "direction must be one of"),
    ({"swipe": "left", "target": ""}, "target needs text without"),
    ({"type": "a", "into": 3}, "into needs text"),
    ({"back": None, "timeout": 3}, "timeout only applies to a step that finds an element or has checks"),
    ({"type": "a", "timeout": 3}, "timeout only applies"),
    ({"tap": "x", "timeout": "long"}, "timeout must be a number"),
    ({"expect": []}, "needs at least one value"),
    ({"see": ""}, "'see' needs text without"),
    ({"see": {"a": 1}}, "'see' needs text, got a mapping"),
])
def test_bad_steps_are_rejected(raw, message):
    with pytest.raises(SpecError, match=message):
        parse_step(raw)


def test_step_titles():
    assert parse_step("back").title() == "back"
    assert parse_step({"tap": "OK"}).title() == "tap: OK"
    assert parse_step({"dark_mode": True}).title() == "dark_mode: on"
    assert parse_step({"network": False}).title() == "network: off"
    assert parse_step({"location": [1, 2]}).title() == "location: 1.0,2.0"
    assert parse_step({"type": {"text": "a", "into": "Email"}, "timeout": 2}).title() == "type: a into='Email'"


# --- files -------------------------------------------------------------------------

def write(tmp_path, body: str, name="t.yaml") -> Path:
    for app in ("a.apk", "b.aab"):
        (tmp_path / app).write_text("")
    f = tmp_path / name
    f.write_text(body)
    return f


def minimal(tests="  - {name: T, fresh: true, steps: [back]}\n", extra="", device="device: {android: Pixel}\n",
            settings=SETTINGS):
    return f"app: a.apk\n{device}{settings}{extra}tests:\n{tests}"


def test_minimal_file(tmp_path):
    spec = load(write(tmp_path, minimal()), {})
    assert spec.apps == {"android": (tmp_path / "a.apk").resolve()}
    assert spec.settings == Settings(model="m", max_actions=8, max_scrolls=15, timeout=10.0, settle=3.0,
                                     threshold=0.5)
    assert spec.devices == {"android": ["Pixel"]} and spec.tests[0].fresh is True
    assert spec.variables == {} and spec.includes == []


def test_example_files_load():
    env = {"DEMO_EMAIL": "e", "DEMO_PASSWORD": "p", "ANDROID_DEVICE": "P", "IOS_DEVICE": "BH",  # CI has no .env
           "IOS_APP": "Runner.app", "IOS_TEAM": "T"}
    spec = load(EXAMPLES / "demo.yaml", env)
    assert spec.devices == {"android": ["P"], "ios": ["BH"]} and spec.settings.ios_team == "T"
    assert set(spec.apps) == {"android", "ios"}
    assert len({t.name for t in spec.tests}) == len(spec.tests)


def test_both_platforms_and_several_devices(tmp_path):
    (tmp_path / "x.zip").write_text("")
    spec = load(write(tmp_path, minimal(device="device: {android: [Pixel 4a, Pixel 8], ios: BH}\n")
                      .replace("app: a.apk", "app: {android: a.apk, ios: x.zip}")), {})
    assert list(spec.apps) == ["android", "ios"]
    assert spec.devices == {"android": ["Pixel 4a", "Pixel 8"], "ios": ["BH"]}


def test_ios_team(tmp_path):
    spec = load(write(tmp_path, minimal(settings=SETTINGS.replace("}", ", ios_team: ABC123}"))), {})
    assert spec.settings.ios_team == "ABC123"


@pytest.mark.parametrize("body,message", [
    ("", "must be a YAML mapping"),
    ("app: [", "not valid YAML"),
    (minimal().replace("app: a.apk\n", ""), "Missing `app:`"),
    (minimal().replace("a.apk", "a.txt"), "Unknown app type"),
    (minimal().replace("app: a.apk", "app: {android: b.aab, ios: a.apk}"), "app.ios points at a android build"),
    (minimal().replace("app: a.apk", "app: {web: a.apk}"), "must be android and/or ios"),
    (minimal(extra="extra: 1\n"), "Unknown top-level keys: extra"),
    # settings: every value is required
    (minimal(settings=""), r"Missing `settings:`.*e\.g\. settings: \{model: typesafe/jev-1.13"),
    (minimal(settings="settings: [1]\n"), "`settings` must be a mapping"),
    (minimal(settings=SETTINGS.replace("}", ", speed: 1}")), "Unknown settings: speed"),
    (minimal(settings="settings: {model: m, timeout: 10}\n"),
     "Missing settings: max_actions, max_scrolls, settle, threshold"),
    (minimal(settings=SETTINGS.replace("threshold: 0.5", "threshold: 1")), "between 0 and 1"),
    (minimal(settings=SETTINGS.replace("max_actions: 8", "max_actions: 0")),
     "settings.max_actions must be at least 1"),
    (minimal(settings=SETTINGS.replace("settle: 3", "settle: -1")), "settings.settle must be at least 0"),
    (minimal(settings=SETTINGS.replace("timeout: 10", "timeout: '10'")), "settings.timeout must be a number"),
    (minimal(settings=SETTINGS.replace("model: m", "model: ''")), "settings.model needs text"),
    (minimal(settings=SETTINGS.replace("}", ", ios_team: 5}")), "settings.ios_team needs text"),
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
    with pytest.raises(SpecError, match=message):
        load(write(tmp_path, body), {})


def test_every_problem_is_reported_at_once(tmp_path):
    body = minimal(device="", settings="settings: {model: m}\n",
                   tests="  - {name: A, steps: [back]}\n  - {name: B, fresh: true, steps: [back, bakc]}\n")
    with pytest.raises(SpecError) as e:
        load(write(tmp_path, body), {})
    lines = str(e.value).splitlines()
    assert lines[0] == "t.yaml has 4 problems:"
    assert [line.split(":")[0] for line in lines[1:]] == [
        "  - Missing `device", "  - Missing settings", "  - Test #1 in t.yaml needs `name`, `fresh` (true",
        "  - Test 'B', step 2"]


def test_uses_are_checked_once_the_tests_are_valid(tmp_path):
    body = minimal(tests="  - {name: A, fresh: true, steps: [{use: Nope}]}\n  - {name: B, steps: [back]}\n")
    with pytest.raises(SpecError, match="needs `name`, `fresh`") as e:
        load(write(tmp_path, body), {})
    assert "Nope" not in str(e.value)


def test_unquoted_variable_in_braces_gets_a_hint(tmp_path):
    with pytest.raises(SpecError, match=r'(?s)not valid YAML.*\nA value starting with \$\{ must be quoted'):
        load(write(tmp_path, "app: a.apk\ndevice: {android: ${PHONE}}\n"), {})


def test_missing_file(tmp_path):
    with pytest.raises(SpecError, match="not found"):
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
    assert spec.tests[1].steps[0].used is spec.tests[0]
    assert spec.tests[1].fresh is False


def test_use_nests(tmp_path):
    spec = load(write(tmp_path, minimal(tests="""
  - {name: A, fresh: true, steps: [back]}
  - {name: B, fresh: true, steps: [{use: A}]}
  - {name: C, fresh: true, steps: [{use: B}, {use: A}]}
""")), {})
    assert spec.tests[2].steps[0].used.steps[0].used is spec.tests[0]


@pytest.mark.parametrize("tests,message", [
    ("  - {name: A, fresh: true, steps: [{use: Nope}]}\n", "no test has that name"),
    ("  - {name: A, fresh: true, steps: [{use: A}]}\n", "loop: A -> A"),
    ("  - {name: A, fresh: true, steps: [{use: B}]}\n  - {name: B, fresh: true, steps: [{use: A}]}\n",
     "loop: A -> B -> A"),
])
def test_use_errors(tmp_path, tests, message):
    with pytest.raises(SpecError, match=message):
        load(write(tmp_path, minimal(tests=tests)), {})


@pytest.mark.parametrize("name,platform", [
    ("x.apk", "android"), ("x.AAB", "android"), ("x.app", "ios"), ("x.zip", "ios"), ("x.ipa", "ios")])
def test_platform_of(name, platform):
    assert platform_of(Path(name)) == platform


def test_platform_of_unknown():
    with pytest.raises(SpecError):
        platform_of(Path("x.exe"))


# --- ${NAME} values ----------------------------------------------------------------------

VARS = ("app: ${APP}\ndevice: {android: \"${PHONE}\"}\n" + SETTINGS.replace("}", ', ios_team: "${TEAM}"}') +
        """tests:
  - name: Sign in
    fresh: true
    steps:
      - type: {text: "${PASSWORD}", into: Password}
        see: Hi ${USER_NAME}
""")


def test_variables_come_from_env(tmp_path):
    env = {"APP": "a.apk", "PHONE": "Pixel", "TEAM": "T1", "PASSWORD": "pw", "USER_NAME": "Ann", "OTHER": "x"}
    spec = load(write(tmp_path, VARS), env)
    assert spec.variables == {k: v for k, v in env.items() if k != "OTHER"}  # only what the file uses
    assert spec.apps["android"].name == "a.apk" and spec.devices == {"android": ["Pixel"]}
    assert spec.settings.ios_team == "T1"
    step = spec.tests[0].steps[0]
    assert step.value == "${PASSWORD}" and step.checks == [("see", "Hi ${USER_NAME}")]  # filled only when used
    assert fill(step.value, spec.variables) == "pw"


def test_only_the_given_env_counts(tmp_path, monkeypatch):
    monkeypatch.setenv("APP", "a.apk")  # the process environment is not looked at by load()
    with pytest.raises(SpecError, match=r"Not set: \$\{APP\}"):
        load(write(tmp_path, minimal().replace("a.apk", "${APP}")), {})


def test_missing_variables_are_named(tmp_path):
    with pytest.raises(SpecError, match=r"Not set: \$\{APP\}, \$\{PASSWORD\}, \$\{PHONE\}.*\.env"):
        load(write(tmp_path, VARS), {"TEAM": "x", "USER_NAME": "y"})


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
    assert [s.used.name for s in spec.tests[0].steps] == ["Sign in", "Home"]
    assert spec.variables == {"PASSWORD": "pw"}  # a library's ${NAME}s count too
    assert [p.name for p in spec.includes] == ["common.yaml", "auth.yaml"]


@pytest.mark.parametrize("files,message", [
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
    with pytest.raises(SpecError, match=message):
        load(write(tmp_path, minimal(extra="include: a.yaml\n")), {})


def test_include_cannot_include_the_main_file(tmp_path):
    (tmp_path / "a.yaml").write_text("include: t.yaml\ntests: [{name: A, fresh: true, steps: [back]}]\n")
    with pytest.raises(SpecError, match="loop: t.yaml -> a.yaml -> t.yaml"):
        load(write(tmp_path, minimal(extra="include: a.yaml\n")), {})


def test_is_test_file(tmp_path):
    assert is_test_file(write(tmp_path, minimal()))
    assert not is_test_file(write(tmp_path, "tests: []\n", name="lib.yaml"))
    assert is_test_file(write(tmp_path, "app: [", name="broken.yaml"))  # run it so the error is shown
