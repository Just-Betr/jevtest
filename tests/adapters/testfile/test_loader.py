import dataclasses
import re
from pathlib import Path

import pytest

from jevtest.adapters.testfile.loader import is_test_file, load, platform_of
from jevtest.domain.failures import TestFileError
from jevtest.domain.kinds import Platform
from jevtest.domain.settings import DEFAULTS, Settings
from jevtest.domain.steps import (
    See,
    TypeText,
    Use,
)
from jevtest.domain.variables import fill

EXAMPLES = Path(__file__).parents[3] / "examples"


def write(tmp_path, body: str, name="t.yaml") -> Path:
    for app in ("a.apk", "b.aab"):
        (tmp_path / app).write_text("")
    f: Path = tmp_path / name
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
    env = {
        "DEMO_EMAIL": "e",
        "DEMO_PASSWORD": "p",
        "ANDROID_DEVICE": "P",
        "IOS_DEVICE": "BH",  # CI has no .env
        "IOS_APP": "Runner.app",
    }
    spec = load(EXAMPLES / "demo.yaml", env)
    assert spec.devices == {"android": ("P",), "ios": ("BH",)}
    assert set(spec.apps) == {"android", "ios"}
    assert len({t.name for t in spec.tests}) == len(spec.tests)


def test_both_platforms_and_several_devices(tmp_path):
    (tmp_path / "x.zip").write_text("")
    spec = load(
        write(
            tmp_path,
            minimal(device="device: {android: [Pixel 4a, Pixel 8], ios: BH}\n").replace(
                "app: a.apk", "app: {android: a.apk, ios: x.zip}"
            ),
        ),
        {},
    )
    assert list(spec.apps) == ["android", "ios"]
    assert spec.devices == {"android": ("Pixel 4a", "Pixel 8"), "ios": ("BH",)}


def test_no_settings_means_the_defaults(tmp_path):
    suite = load(write(tmp_path, minimal()), {})
    assert suite.settings == DEFAULTS and suite.tests[0].steps[0].settings == DEFAULTS


def test_a_files_settings_apply_to_every_step_and_a_step_can_change_its_own(tmp_path):
    body = minimal(
        tests="  - {name: T, fresh: true, steps: [back, {tap: x, timeout: 30}]}\n",
        extra="settings: {model: jev-2.0.0, timeout: 20, interval: 0.5, max_actions: 20, "
        "max_scrolls: 100, confidence: 0.8}\n",
    )
    suite = load(write(tmp_path, body), {})
    expected = Settings("jev-2.0.0", 20, 0.5, 20, 100, 0.8)
    back, tap = suite.tests[0].steps
    assert suite.settings == expected and back.settings == expected
    assert tap.settings == dataclasses.replace(expected, timeout=30)


def test_included_tests_run_with_the_settings_of_the_file_being_run(tmp_path):
    (tmp_path / "lib.yaml").write_text("tests:\n  - {name: L, fresh: true, steps: [back]}\n")
    body = minimal(extra="include: lib.yaml\nsettings: {interval: 1}\n")
    assert load(write(tmp_path, body), {}).library["L"].steps[0].settings.interval == 1


@pytest.mark.parametrize(
    "body",
    [
        "settings: {settle: 3}\n",
        None,  # on a step
    ],
)
def test_the_removed_settle_setting_says_what_to_do(tmp_path, body):
    text = (
        minimal(extra=body) if body else minimal(tests="  - {name: T, fresh: true, steps: [{back: null, settle: 3}]}\n")
    )
    with pytest.raises(TestFileError, match="`settle` is gone .jevtest 0.9.: each step waits until what it needs"):
        load(write(tmp_path, text), {})


def test_every_bad_step_in_a_test_is_reported_at_once(tmp_path):
    body = minimal(
        tests="  - {name: T, fresh: true, steps: [{tap: x, max_actions: 3}, back, {wait: 1, interval: 1}]}\n"
    )
    with pytest.raises(TestFileError, match="2 problems") as e:
        load(write(tmp_path, body), {})
    assert "Test 'T', step 1: `max_actions`" in str(e.value) and "Test 'T', step 3: `interval`" in str(e.value)


def test_every_bad_setting_is_reported_at_once(tmp_path):
    body = minimal(extra="settings: {timeout: 1000, confidence: 0.3, model: gpt-5}\n")
    with pytest.raises(TestFileError, match="3 problems") as e:
        load(write(tmp_path, body), {})
    assert "`timeout` must be from 1 to 300" in str(e.value) and "`confidence` must be" in str(e.value)
    assert "`model` must be a pinned Jev version" in str(e.value)


@pytest.mark.parametrize(
    ("settings", "message"),
    [
        (
            "settings: 5",
            "`settings` must be a mapping of model, confidence, interval, max_actions, max_scrolls, timeout",
        ),
        ("settings: {}", "`settings` must be a mapping"),
        ("settings: {wait: 5}", "`settings` has an unknown key: wait (it takes model, confidence"),
        ("settings: {model: gpt-5}", "`model` must be a pinned Jev version like jev-1.13.0, got 'gpt-5'"),
        ("settings: {model: jev-latest}", "an alias such as jev-latest moves when a new Jev ships"),
        ("settings: {model: typesafe/jev-1.13}", "must be a pinned Jev version like jev-1.13.0"),
        ("settings: {model: 5}", "`model` needs text"),
        ("settings: {timeout: 1000}", "`timeout` must be from 1 to 300, got 1000"),
        ("settings: {timeout: '5'}", "`timeout` must be a number, got '5' (remove the quotes)"),
    ],
)
def test_bad_settings_are_rejected(tmp_path, settings, message):
    with pytest.raises(TestFileError, match=re.escape(message)):
        load(write(tmp_path, minimal(extra=settings + "\n")), {})


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("", "must be a YAML mapping"),
        ("app: [", "not valid YAML"),
        (minimal().replace("app: a.apk\n", ""), "Missing `app:`"),
        (minimal().replace("a.apk", "a.txt"), "Unknown app type"),
        (
            minimal().replace("app: a.apk", "app: {android: b.aab, ios: a.apk}"),
            "app.ios points at a.apk, which is an Android build",
        ),
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
        (
            minimal(tests="  - {name: T, fresh: true, steps: [{wait: x}]}\n"),
            r"Test 'T', step 1: 'wait' must be a number, got 'x'$",
        ),
        (minimal(tests="  - {name: T, fresh: sometimes, steps: [back]}\n"), "Test 'T': fresh must be on or off"),
        (minimal(tests="  - {name: T, fresh: true, steps: [back]}\n" * 2), "unique .*: T"),
    ],
)
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
        "  - Missing `device",
        "  - Test #1 in t.yaml needs `name`, `fresh` (true",
        "  - Test 'B', step 2",
    ]


def test_uses_are_checked_once_the_tests_are_valid(tmp_path):
    body = minimal(tests="  - {name: A, fresh: true, steps: [{use: Nope}]}\n  - {name: B, steps: [back]}\n")
    with pytest.raises(TestFileError, match="needs `name`, `fresh`") as e:
        load(write(tmp_path, body), {})
    assert "Nope" not in str(e.value)


def test_unquoted_variable_in_braces_gets_a_hint(tmp_path):
    with pytest.raises(TestFileError, match=r"(?s)not valid YAML.*\nA value starting with \$\{ must be quoted"):
        load(write(tmp_path, "app: a.apk\ndevice: {android: ${PHONE}}\n"), {})


def test_a_bare_action_with_checks_gets_a_hint(tmp_path):
    body = "tests:\n  - name: T\n    fresh: true\n    steps:\n      - launch\n        see: Sign in\n"
    with pytest.raises(TestFileError, match=r"(?s)not valid YAML.*\nOn line 5, .* needs a colon .*: `- launch:`$"):
        load(write(tmp_path, body), {})


@pytest.mark.parametrize(
    "body",
    [
        "a: b: c\n",  # the same problem on the first line: nothing above it
        "steps:\n  - tap: A\n    see: y: z\n",  # the line above is not a bare word
        "app: [\n",  # another problem
        "a: b, ${X}: c\n",  # a ${ outside { } or [ ]
        "? [a, b]\n: c\n",  # a key that isn't a plain scalar: not a key a test file has
        "a: \x07\n",  # a control character: PyYAML's reader refuses it, with no line to point at
    ],
)
def test_other_yaml_errors_get_no_bare_word_hint(tmp_path, body):
    with pytest.raises(TestFileError, match="not valid YAML") as e:
        load(write(tmp_path, body), {})
    assert "needs a colon" not in str(e.value) and "must be quoted" not in str(e.value)


def test_a_bare_action_with_a_value_under_it_gets_only_the_bare_word_hint(tmp_path):
    body = "tests:\n  - name: T\n    fresh: true\n    steps:\n      - back\n        see: Hi, ${NAME}\n"
    with pytest.raises(TestFileError, match="`- back:`$"):
        load(write(tmp_path, body), {})


def test_missing_file(tmp_path):
    with pytest.raises(TestFileError, match="not found"):
        load(tmp_path / "nope.yaml", {})


def test_use_links_tests(tmp_path):
    spec = load(
        write(
            tmp_path,
            minimal(
                tests="""
  - name: Sign in
    fresh: true
    steps: [{do: sign in}]
  - name: Counter
    fresh: false
    steps: [{use: Sign in}, {do: tap add, see: "Taps: 1"}]
"""
            ),
        ),
        {},
    )
    assert spec.tests[1].steps[0].action == Use("Sign in") and spec.library["Sign in"] is spec.tests[0]
    assert spec.tests[1].fresh is False


def test_use_nests(tmp_path):
    spec = load(
        write(
            tmp_path,
            minimal(
                tests="""
  - {name: A, fresh: true, steps: [back]}
  - {name: B, fresh: true, steps: [{use: A}]}
  - {name: C, fresh: true, steps: [{use: B}, {use: A}]}
"""
            ),
        ),
        {},
    )
    assert [s.action for s in spec.tests[2].steps] == [Use("B"), Use("A")]


@pytest.mark.parametrize(
    ("tests", "message"),
    [
        ("  - {name: A, fresh: true, steps: [{use: Nope}]}\n", "no test has that name"),
        ("  - {name: A, fresh: true, steps: [{use: A}]}\n", "loop: A -> A"),
        (
            "  - {name: A, fresh: true, steps: [{use: B}]}\n  - {name: B, fresh: true, steps: [{use: A}]}\n",
            "loop: A -> B -> A",
        ),
    ],
)
def test_use_errors(tmp_path, tests, message):
    with pytest.raises(TestFileError, match=message):
        load(write(tmp_path, minimal(tests=tests)), {})


@pytest.mark.parametrize(
    ("name", "platform"),
    [("x.apk", "android"), ("x.AAB", "android"), ("x.app", "ios"), ("x.zip", "ios"), ("x.ipa", "ios")],
)
def test_platform_of(name, platform):
    assert platform_of(Path(name)) == platform


def test_platform_of_unknown():
    with pytest.raises(TestFileError):
        platform_of(Path("x.exe"))


# --- ${NAME} values ----------------------------------------------------------------------

VARS = (
    'app: ${APP}\ndevice: {android: "${PHONE}"}\n'
    """tests:
  - name: Sign in
    fresh: true
    steps:
      - type: {text: "${PASSWORD}", into: Password}
        see: Hi ${USER_NAME}
"""
)


def test_variables_come_from_env(tmp_path):
    env = {"APP": "a.apk", "PHONE": "Pixel", "PASSWORD": "pw", "USER_NAME": "Ann", "OTHER": "x"}
    spec = load(write(tmp_path, VARS), env)
    assert spec.variables == {k: v for k, v in env.items() if k != "OTHER"}  # only what the file uses
    assert spec.apps[Platform.ANDROID].name == "a.apk" and spec.devices == {Platform.ANDROID: ("Pixel",)}
    step = spec.tests[0].steps[0]
    assert step.action == TypeText("${PASSWORD}", "Password") and step.checks == (See("Hi ${USER_NAME}"),)
    assert fill("${PASSWORD}", spec.variables) == "pw"  # the step keeps the name; the value is filled when used


def test_only_the_given_env_counts(tmp_path, monkeypatch):
    monkeypatch.setenv("APP", "a.apk")  # the process environment is not looked at by load()
    with pytest.raises(
        TestFileError, match=r"t\.yaml uses \$\{APP\}, which is not set\. Add it to .*/\.env \(the \.env next"
    ):
        load(write(tmp_path, minimal().replace("a.apk", "${APP}")), {})


def test_missing_variables_are_named(tmp_path):
    with pytest.raises(
        TestFileError, match=r"uses \$\{APP\}, \$\{PASSWORD\}, \$\{PHONE\}, which are not set\. Add them to "
    ):
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
    spec = load(
        write(tmp_path, minimal(extra="include: [lib/auth.yaml, lib/common.yaml]\n", tests=tests)), {"PASSWORD": "pw"}
    )
    assert [t.name for t in spec.tests] == ["Counter"]
    assert [s.action for s in spec.tests[0].steps] == [Use("Sign in"), Use("Home")]
    assert set(spec.library) == {"Counter", "Sign in", "Home"}
    assert spec.variables == {"PASSWORD": "pw"}  # a library's ${NAME}s count too
    assert [p.name for p in spec.includes] == ["common.yaml", "auth.yaml"]


@pytest.mark.parametrize(
    ("files", "message"),
    [
        (
            {
                "a.yaml": "include: b.yaml\ntests: [{name: A, fresh: true, steps: [back]}]\n",
                "b.yaml": "include: a.yaml\ntests: [{name: B, fresh: true, steps: [back]}]\n",
            },
            "loop: t.yaml -> a.yaml -> b.yaml -> a.yaml",
        ),
        (
            {"a.yaml": "app: x.apk\ntests: [{name: A, fresh: true, steps: [back]}]\n"},
            "can only have `include` and `tests`.*app",
        ),
        ({"a.yaml": "tests: [{name: T, fresh: true, steps: [back]}]\n"}, "unique .*: T"),
        ({"a.yaml": "tests: []\n"}, "No tests found under `tests:` in a.yaml"),
        ({"a.yaml": "include: 3\ntests: [{name: A, fresh: true, steps: [back]}]\n"}, "include in a.yaml needs text"),
        ({}, "includes a.yaml, which isn't there"),
    ],
)
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


def test_a_missing_library_names_the_file_that_includes_it(tmp_path):
    with pytest.raises(
        TestFileError, match=r"t\.yaml includes shared/auth\.yaml, which isn't there: .*/shared/auth\.yaml"
    ):
        load(write(tmp_path, minimal() + "include: shared/auth.yaml\n"), {})


def test_file_settings_must_make_sense_together(tmp_path):
    with pytest.raises(TestFileError, match="is longer than `timeout`"):
        load(write(tmp_path, minimal(extra="settings: {timeout: 1, interval: 1.5}\n")), {})


def test_a_test_file_with_a_bom_and_windows_line_endings_loads(tmp_path):
    f = write(tmp_path, minimal())
    f.write_bytes(b"\xef\xbb\xbf" + f.read_bytes().replace(b"\n", b"\r\n"))
    assert load(f, {}).tests[0].name == "T"


def test_a_test_file_that_isnt_utf8_says_so(tmp_path):
    f = write(tmp_path, minimal())
    f.write_bytes(f.read_bytes().replace(b"name: T", b"name: \xe9"))  # Latin-1
    with pytest.raises(TestFileError, match="t.yaml isn't UTF-8 text: save it as UTF-8"):
        load(f, {})


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (
            "tests:\n  - name: T\n    fresh: true\n    steps:\n      - see: A\n        see: B\n",
            r"t\.yaml, line 6: `see` is given twice \(first on line 5\), .*: see: \[A, B\]$",
        ),
        ("app: a.apk\napp: b.aab\n", r"line 2: `app` is given twice \(first on line 1\), .*: remove one of them$"),
    ],
)
def test_a_key_given_twice_is_an_error_not_dropped(tmp_path, body, message):
    """Plain YAML keeps the last and drops the rest: a second see: would replace the first check without a word."""
    with pytest.raises(TestFileError, match=message):
        load(write(tmp_path, body), {})


def test_tabs_get_a_hint(tmp_path):
    with pytest.raises(TestFileError, match="Indent with spaces: YAML doesn't allow tabs$"):
        load(write(tmp_path, "app: a.apk\ntests:\n\t- name: T\n"), {})


BOTH = "app: {android: a.apk, ios: x.zip}\ndevice: {android: Pixel, ios: iPhone 17}\n"
FOR_ONE = "Put tests for one platform in a file whose `app:` has only that platform's build$"


@pytest.mark.parametrize(
    ("app", "step", "message"),
    [
        (BOTH, "{network: false}", f"^Test 'U': network: can't run on iOS, .* network on or off. {FOR_ONE}"),
        (BOTH, "{key: home}", rf"^Test 'U': key: home is Android only \(iOS presses backspace, .*\). {FOR_ONE}"),
        (BOTH, "{key: '66'}", r"^Test 'U': key: 66 is Android only"),
        (
            BOTH,
            "{grant: {android: android.permission.CAMERA}}",
            r"^Test 'U': grant: has no ios permission, and the file runs on ios. Give it: grant: \{android: ",
        ),
        (
            BOTH,
            "{grant: camera}",
            "^Test 'U': 'camera' isn't a full Android permission name: .* android.permission.CAMERA",
        ),
        (minimal(tests=""), "{grant: camera}", "^Test 'U': 'camera' isn't a full Android permission name"),
        (minimal(tests=""), "{grant: {ios: camera}}", "^Test 'U': grant: has no android permission"),
    ],
)
def test_a_step_that_cant_run_on_a_platform_the_file_runs_on_is_an_error(tmp_path, app, step, message):
    (tmp_path / "x.zip").write_text("")
    head = app.split("tests:")[0]
    body = f"{head}tests:\n  - {{name: T, fresh: true, steps: [back, {{use: U}}]}}\n  - {{name: U, fresh: true, steps: [{step}]}}\n"
    with pytest.raises(TestFileError, match=message):
        load(write(tmp_path, body), {})


@pytest.mark.parametrize(
    ("app", "step"),
    [
        (minimal(tests=""), "{network: false}"),
        (minimal(tests=""), "{key: home}"),
        (minimal(tests=""), "{grant: com.example.app.SCAN}"),  # an app's own permission
        ("app: x.zip\ndevice: {ios: iPhone 17}\n", "{grant: camera}"),
        (BOTH, "{grant: {android: android.permission.CAMERA, ios: camera}}"),
        (BOTH, "{key: enter}"),
    ],
)
def test_a_step_that_runs_on_every_platform_the_file_runs_on_loads(tmp_path, app, step):
    (tmp_path / "x.zip").write_text("")
    head = app.split("tests:")[0]
    load(write(tmp_path, f"{head}tests:\n  - {{name: T, fresh: true, steps: [{step}]}}\n"), {})


def test_a_library_test_no_test_uses_isnt_checked_for_the_files_platforms(tmp_path):
    """A library may hold Android-only tests that only files running on Android use."""
    (tmp_path / "x.zip").write_text("")
    (tmp_path / "lib.yaml").write_text("tests: [{name: Offline, fresh: true, steps: [{network: false}]}]\n")
    load(write(tmp_path, f"{BOTH}include: lib.yaml\ntests:\n  - {{name: T, fresh: true, steps: [back]}}\n"), {})
