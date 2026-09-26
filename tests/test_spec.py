from pathlib import Path

import pytest

from jevtest.spec import Settings, SpecError, load, parse_step, platform_of

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


def test_example_file_loads():
    spec = load(EXAMPLE)
    assert set(spec.apps) == {"android", "ios"}
    assert len({t.name for t in spec.tests}) == len(spec.tests)


def test_devices(tmp_path):
    body = ("app: {android: a.apk, ios: x.zip}\ndevice: {android: Pixel 4a, ios: 'BH'}\n"
            "tests:\n  - {name: T, steps: [back]}\n")
    (tmp_path / "x.zip").write_text("")
    spec = load(write(tmp_path, body))
    assert list(spec.apps) == ["android", "ios"] and spec.devices == {"android": "Pixel 4a", "ios": "BH"}


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
    (minimal(tests="  - name: T\n    steps: [back]\n  - name: T\n    steps: [back]\n"), "unique: T"),
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
