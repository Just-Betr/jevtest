from pathlib import Path

import pytest

from jevtest.spec import Settings, SpecError, fill, is_test_file, load, parse_step, platform_of

EXAMPLE = Path(__file__).parent.parent / "examples" / "demo.yaml"


# --- steps -------------------------------------------------------------------------

@pytest.mark.parametrize("raw,kind,value", [
    ("back", "back", None),
    ("  home  ", "home", None),
    ("Sign in as admin", "do", "Sign in as admin"),
    ({"back": None}, "back", None),
    ({"do": "Open settings"}, "do", "Open settings"),
    ({"use": "Sign in"}, "use", "Sign in"),
    ({"tap": "OK"}, "tap", "OK"),
    ({"tap": 42}, "tap", "42"),
    ({"wait": 2}, "wait", 2.0),
    ({"wait": "0.5"}, "wait", 0.5),
    ({"background": 0}, "background", 0.0),
    ({"scroll": "DOWN"}, "scroll", "down"),
    ({"swipe": "left"}, "swipe", "left"),
    ({"swipe": None, "direction": "up"}, "swipe", "up"),
    ({"rotate": "Landscape"}, "rotate", "landscape"),
    ({"location": "37.7,-122.4"}, "location", (37.7, -122.4)),
    ({"location": [1, 2]}, "location", (1.0, 2.0)),
    ({"dark_mode": True}, "dark_mode", True),
    ({"dark_mode": "light"}, "dark_mode", False),
    ({"network": "off"}, "network", False),
    ({"type": "hello"}, "type", "hello"),
    ({"type": 123}, "type", "123"),
    ({"type": ""}, "type", ""),
    ({"type": {"text": "a", "into": "Email"}}, "type", "a"),
    ({"type": None, "text": "a", "into": "Email"}, "type", "a"),
])
def test_actions_parse_and_normalize(raw, kind, value):
    step = parse_step(raw)
    assert (step.kind, step.value) == (kind, value)
    assert step.raw == raw


def test_type_options_move_into_value():
    step = parse_step({"type": {"text": "a", "into": "Email", "timeout": 3}})
    assert step.opts == {"into": "Email", "timeout": 3.0}


def test_action_then_checks():
    s = parse_step({"do": "Sign in", "expect": "Home shows", "see": ["Welcome", "Log out"], "not_see": "Error"})
    assert s.kind == "do"
    assert s.checks == [("expect", "Home shows"), ("see", "Welcome"), ("see", "Log out"), ("not_see", "Error")]


def test_checks_only_step():
    s = parse_step({"expect": "Home shows", "timeout": 2})
    assert s.kind is None and s.checks == [("expect", "Home shows")] and s.opts == {"timeout": 2.0}
    assert s.title() == ""


@pytest.mark.parametrize("raw,message", [
    ("", "Empty step"),
    (["a"], "string or a mapping"),
    ({"tap": "x", "bogus": 1}, "unknown keys: bogus"),
    ({"tap": "x", "do": "y"}, "more than one action"),
    ({"timeout": 3}, "no action or check"),
    ({"tap": None}, "'tap' needs a text value"),
    ({"tap": "  "}, "'tap' needs a text value"),
    ({"tap": True}, "'tap' needs a text value"),
    ({"wait": "soon"}, "'wait' must be a number"),
    ({"wait": -1}, "at least 0"),
    ({"wait": float("nan")}, "finite"),
    ({"wait": True}, "must be a number"),
    ({"scroll": "sideways"}, "'scroll' must be one of"),
    ({"swipe": None}, "'swipe' must be one of"),
    ({"rotate": "upside"}, "'rotate' must be one of"),
    ({"location": "1"}, "lat,lon"),
    ({"location": "91,0"}, "out of range"),
    ({"location": "a,b"}, "must be a number"),
    ({"dark_mode": "maybe"}, "on or off"),
    ({"type": None}, "'type' needs text"),
    ({"type": {"txt": "a"}}, "unknown keys: txt"),
    ({"type": ["a"]}, "'type' needs text"),
    ({"do": "x", "max_actions": 0}, "max_actions must be at least 1"),
    ({"do": "x", "max_actions": 1.5}, "whole number"),
    ({"scroll_to": "x", "direction": "in"}, "direction must be one of"),
    ({"swipe": "left", "target": ""}, "target needs a text value"),
    ({"tap": "x", "timeout": "long"}, "timeout must be a number"),
    ({"expect": []}, "needs at least one value"),
    ({"see": ""}, "'see' needs a text value"),
    ({"see": {"a": 1}}, "'see' needs a text value"),
])
def test_bad_steps_are_rejected(raw, message):
    with pytest.raises(SpecError, match=message):
        parse_step(raw)


def test_step_titles():
    assert parse_step("back").title() == "back"
    assert parse_step({"tap": "OK"}).title() == "tap: OK"
    assert parse_step({"dark_mode": "on"}).title() == "dark_mode: on"
    assert parse_step({"location": "1,2"}).title() == "location: 1.0,2.0"
    assert parse_step({"type": {"text": "a", "into": "Email", "timeout": 2}}).title() == "type: a into='Email'"


# --- files -------------------------------------------------------------------------

def write(tmp_path, body: str, name="t.yaml") -> Path:
    for app in ("a.apk", "b.aab"):
        (tmp_path / app).write_text("")
    f = tmp_path / name
    f.write_text(body)
    return f


def minimal(tests="  - name: T\n    steps: [back]\n", extra=""):
    return f"app: a.apk\n{extra}tests:\n{tests}"


def test_minimal_file(tmp_path):
    spec = load(write(tmp_path, minimal()))
    assert spec.apps == {"android": (tmp_path / "a.apk").resolve()}
    assert spec.settings == Settings()
    assert spec.tests[0].fresh is True
    assert spec.devices == {}  # no device named: the running one


def test_example_files_load():
    env = {"DEMO_EMAIL": "e", "DEMO_PASSWORD": "p", "IPHONE": "BH"}  # CI has no examples/.env
    assert load(EXAMPLE.with_name("demo_iphone.yaml"), env).devices == {"ios": ["BH"]}
    spec = load(EXAMPLE, env)
    assert set(spec.apps) == {"android", "ios"}
    assert len({t.name for t in spec.tests}) == len(spec.tests)


def test_devices(tmp_path):
    body = ("app: {android: a.apk, ios: x.zip}\ndevice: {android: Pixel 4a, ios: 'BH'}\n"
            "tests:\n  - {name: T, steps: [back]}\n")
    (tmp_path / "x.zip").write_text("")
    spec = load(write(tmp_path, body))
    assert list(spec.apps) == ["android", "ios"] and spec.devices == {"android": ["Pixel 4a"], "ios": ["BH"]}


def test_settings(tmp_path):
    spec = load(write(tmp_path, minimal(extra="settings: {model: m, max_actions: 3, timeout: 2, "
                                              "settle: 0, threshold: 0.7, ios_team: ABC123}\n")))
    assert spec.settings == Settings(model="m", max_actions=3, timeout=2.0, settle=0.0, threshold=0.7,
                                     ios_team="ABC123")


@pytest.mark.parametrize("body,message", [
    ("", "must be a YAML mapping"),
    ("app: [", "not valid YAML"),
    ("tests: []\n", "Missing `app:`"),
    ("app: a.txt\ntests: []\n", "Unknown app type"),
    ("app: {android: b.aab, ios: a.apk}\ntests: []\n", "app.ios points at a android build"),
    ("app: {web: a.apk}\ntests: []\n", "must be android and/or ios"),
    ("app: a.apk\nextra: 1\ntests: []\n", "Unknown top-level keys: extra"),
    (minimal(tests="  []\n"), "No tests found"),
    (minimal(tests="  - steps: [back]\n"), "needs a `name` and `steps`"),
    (minimal(tests="  - name: T\n    steps: []\n"), "at least one step"),
    (minimal(tests="  - name: T\n    steps: [back]\n    tags: [x]\n"), "unknown keys: tags"),
    (minimal(tests="  - name: T\n    steps: [{wait: x}]\n"), "Test 'T': 'wait' must be a number"),
    (minimal(tests="  - name: T\n    steps: [back]\n    fresh: sometimes\n"), "fresh must be on or off"),
    (minimal(tests="  - name: T\n    steps: [back]\n  - name: T\n    steps: [back]\n"), "unique .*: T"),
    (minimal(extra="settings: [1]\n"), "`settings` must be a mapping"),
    (minimal(extra="settings: {speed: 1}\n"), "Unknown settings: speed"),
    (minimal(extra="settings: {threshold: 1}\n"), "between 0 and 1"),
    (minimal(extra="settings: {max_actions: 0}\n"), "settings.max_actions must be at least 1"),
    (minimal(extra="settings: {settle: -1}\n"), "settings.settle must be at least 0"),
    (minimal(extra="settings: {model: ''}\n"), "settings.model needs a text value"),
    (minimal(extra="device: Pixel\n"), "must name a device per platform"),
    (minimal(extra="device: {web: x}\n"), "keys must be android and/or ios"),
    (minimal(extra="device: {ios: BH}\n"), "names a ios device, but `app` has no ios build"),
    (minimal(extra="device: {android: ''}\n"), "device.android needs a text value"),
])
def test_bad_files_are_rejected(tmp_path, body, message):
    with pytest.raises(SpecError, match=message):
        load(write(tmp_path, body))


def test_missing_file(tmp_path):
    with pytest.raises(SpecError, match="not found"):
        load(tmp_path / "nope.yaml")


def test_use_links_tests(tmp_path):
    spec = load(write(tmp_path, minimal(tests="""
  - name: Sign in
    steps: [{do: sign in}]
  - name: Counter
    fresh: false
    steps: [{use: Sign in}, {do: tap add, see: "Taps: 1"}]
""")))
    assert spec.tests[1].steps[0].used is spec.tests[0]
    assert spec.tests[1].fresh is False


def test_use_nests(tmp_path):
    spec = load(write(tmp_path, minimal(tests="""
  - {name: A, steps: [back]}
  - {name: B, steps: [{use: A}]}
  - {name: C, steps: [{use: B}, {use: A}]}
""")))
    assert spec.tests[2].steps[0].used.steps[0].used is spec.tests[0]


@pytest.mark.parametrize("tests,message", [
    ("  - {name: A, steps: [{use: Nope}]}\n", "no test has that name"),
    ("  - {name: A, steps: [{use: A}]}\n", "loop: A -> A"),
    ("  - {name: A, steps: [{use: B}]}\n  - {name: B, steps: [{use: A}]}\n", "loop: A -> B -> A"),
])
def test_use_errors(tmp_path, tests, message):
    with pytest.raises(SpecError, match=message):
        load(write(tmp_path, minimal(tests=tests)))


@pytest.mark.parametrize("name,platform", [
    ("x.apk", "android"), ("x.AAB", "android"), ("x.app", "ios"), ("x.zip", "ios"), ("x.ipa", "ios")])
def test_platform_of(name, platform):
    assert platform_of(Path(name)) == platform


def test_platform_of_unknown():
    with pytest.raises(SpecError):
        platform_of(Path("x.exe"))


# --- device lists ----------------------------------------------------------------------

def test_several_devices_per_platform(tmp_path):
    spec = load(write(tmp_path, minimal(extra="device: {android: [Pixel 4a, Pixel 8]}\n")))
    assert spec.devices == {"android": ["Pixel 4a", "Pixel 8"]}


@pytest.mark.parametrize("extra,message", [
    ("device: {android: []}\n", "at least one device"),
    ("device: {android: [A, A]}\n", "lists a device twice"),
    ("device: {android: [A, '']}\n", "device.android needs a text value"),
])
def test_bad_device_lists(tmp_path, extra, message):
    with pytest.raises(SpecError, match=message):
        load(write(tmp_path, minimal(extra=extra)))


# --- ${NAME} values ----------------------------------------------------------------------

VARS = """app: ${APP}
device: {android: "${PHONE}"}
settings: {ios_team: "${TEAM}"}
tests:
  - name: Sign in
    steps:
      - type: {text: "${PASSWORD}", into: Password}
        see: Hi ${USER_NAME}
"""


def test_variables_come_from_env(tmp_path):
    env = {"APP": "a.apk", "PHONE": "Pixel", "TEAM": "T1", "PASSWORD": "pw", "USER_NAME": "Ann", "OTHER": "x"}
    spec = load(write(tmp_path, VARS), env)
    assert spec.variables == {k: v for k, v in env.items() if k != "OTHER"}  # only what the file uses
    assert spec.apps["android"].name == "a.apk" and spec.devices == {"android": ["Pixel"]}
    assert spec.settings.ios_team == "T1"
    step = spec.tests[0].steps[0]
    assert step.value == "${PASSWORD}" and step.checks == [("see", "Hi ${USER_NAME}")]  # filled only when used
    assert fill(step.value, spec.variables) == "pw"


def test_variables_default_to_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("JEVTEST_T_APP", "a.apk")
    assert load(write(tmp_path, "app: ${JEVTEST_T_APP}\ntests: [{name: T, steps: [back]}]\n")).apps


def test_missing_variables_are_named(tmp_path):
    with pytest.raises(SpecError, match=r"Not set: \$\{APP\}, \$\{PASSWORD\}, \$\{PHONE\}.*\.env"):
        load(write(tmp_path, VARS), {"TEAM": "x", "USER_NAME": "y"})


# --- include ------------------------------------------------------------------------------

LIB = """tests:
  - name: Sign in
    steps: [{type: {text: "${PASSWORD}", into: Password}}]
"""


def test_included_tests_can_be_used_but_do_not_run(tmp_path):
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "auth.yaml").write_text("include: common.yaml\n" + LIB)
    (tmp_path / "lib" / "common.yaml").write_text("tests: [{name: Home, steps: [home]}]\n")
    spec = load(write(tmp_path, minimal(extra="include: [lib/auth.yaml, lib/common.yaml]\n",
                                        tests="  - {name: Counter, steps: [{use: Sign in}, {use: Home}]}\n")),
                {"PASSWORD": "pw"})
    assert [t.name for t in spec.tests] == ["Counter"]
    assert [s.used.name for s in spec.tests[0].steps] == ["Sign in", "Home"]
    assert spec.variables == {"PASSWORD": "pw"}  # a library's ${NAME}s count too


@pytest.mark.parametrize("files,message", [
    ({"a.yaml": "include: b.yaml\ntests: [{name: A, steps: [back]}]\n",
      "b.yaml": "include: a.yaml\ntests: [{name: B, steps: [back]}]\n"}, "loop: t.yaml -> a.yaml -> b.yaml -> a.yaml"),
    ({"a.yaml": "app: x.apk\ntests: [{name: A, steps: [back]}]\n"}, "can only have `include` and `tests`.*app"),
    ({"a.yaml": "tests: [{name: T, steps: [back]}]\n"}, "unique .*: T"),
    ({"a.yaml": "tests: []\n"}, "No tests found under `tests:` in a.yaml"),
    ({}, "Test file not found: .*a.yaml"),
])
def test_bad_includes(tmp_path, files, message):
    for name, body in files.items():
        (tmp_path / name).write_text(body)
    with pytest.raises(SpecError, match=message):
        load(write(tmp_path, minimal(extra="include: a.yaml\n")))


def test_include_cannot_include_the_main_file(tmp_path):
    (tmp_path / "a.yaml").write_text("include: t.yaml\ntests: [{name: A, steps: [back]}]\n")
    with pytest.raises(SpecError, match="loop: t.yaml -> a.yaml -> t.yaml"):
        load(write(tmp_path, minimal(extra="include: a.yaml\n")))


def test_is_test_file(tmp_path):
    assert is_test_file(write(tmp_path, minimal()))
    assert not is_test_file(write(tmp_path, "tests: []\n", name="lib.yaml"))
    assert is_test_file(write(tmp_path, "app: [", name="broken.yaml"))  # run it so the error is shown
