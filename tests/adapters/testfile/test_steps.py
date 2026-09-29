import datetime

import pytest

from jevtest.adapters.testfile.steps import ACTIONS, OPTIONS, label, parse_step
from jevtest.domain.failures import TestFileError
from jevtest.domain.kinds import Direction, Gesture, Orientation, Platform
from jevtest.domain.steps import (
    Back,
    Background,
    DarkMode,
    Do,
    Expect,
    Grant,
    Key,
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

# --- steps -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "action"),
    [
        ("back", Back()),
        ({"back": None}, Back()),
        ({"do": "Open settings"}, Do("Open settings")),
        ({"use": "Sign in"}, Use("Sign in")),
        ({"tap": "OK"}, Touch(Gesture.TAP, "OK")),
        ({"tap": "42"}, Touch(Gesture.TAP, "42")),
        ({"long_press": "Hold"}, Touch(Gesture.LONG_PRESS, "Hold")),
        ({"wait": 2}, Wait(2.0)),
        ({"wait": 300}, Wait(300.0)),
        ({"grant": "camera"}, Grant(((None, ("camera",)),))),
        ({"grant": ["photos", "camera"]}, Grant(((None, ("photos", "camera")),))),
        (
            {"grant": {"ios": "camera", "android": ["android.permission.CAMERA", "android.permission.RECORD_AUDIO"]}},
            Grant(
                (
                    (Platform.ANDROID, ("android.permission.CAMERA", "android.permission.RECORD_AUDIO")),
                    (Platform.IOS, ("camera",)),
                )
            ),
        ),
        (  # an app's own permission: Android records every permission with its package
            {"grant": {"android": "com.example.app.SCAN"}},
            Grant(((Platform.ANDROID, ("com.example.app.SCAN",)),)),
        ),
        ({"key": "enter"}, Key("enter")),
        ({"key": "menu"}, Key("menu")),
        ({"key": "66"}, Key("66")),
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
    ],
)
def test_actions_parse(raw, action):
    step = parse_step(raw)
    assert step.action == action


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


@pytest.mark.parametrize(
    "raw",
    [
        {"tap": "x", "timeout": 1},
        {"clear": "x", "timeout": 1},
        {"type": {"text": "a", "into": "E"}, "timeout": 1},
        {"swipe": "left", "target": "Item", "timeout": 1},
        {"back": None, "see": "x", "timeout": 1},
        {"scroll_to": "x", "direction": "up"},
    ],
)
def test_options_where_they_apply(raw):
    parse_step(raw)


@pytest.mark.parametrize(
    ("raw", "message"),
    [
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
        ({"wait": 301}, "'wait' must be from 0 to 300 seconds, got 301"),
        ({"background": 99999}, "'background' must be from 0 to 300 seconds, got 99999"),
        ({"grant": []}, "'grant' needs at least one permission"),
        ({"grant": {}}, "grant takes permission names, or names per platform"),
        ({"grant": {"windows": "x"}}, "or names per platform: .*; got windows"),
        (
            {"grant": {"android": "camera"}},
            (
                "^'camera' isn't a full Android permission name: write it with its package, e.g. "
                r"android.permission.CAMERA \(names per platform: grant: \{android: android.permission.CAMERA, ios: camera\}\)$"
            ),
        ),
        ({"grant": {"android": ".SCAN"}}, "^'.SCAN' isn't a full Android permission name: .* android.permission.SCAN "),
        ({"grant": {"ios": 5}}, "grant's ios needs text"),
        ({"key": "bogus"}, r"Unknown key 'bogus'. Keys: backspace, .*; on Android also app_switch, .*, or a key code"),
        ({"key": "Enter"}, "Unknown key 'Enter'"),
        ({"key": 66}, "'key' needs text, got a number"),
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
        ({"wait": 1, "interval": 1}, "`interval` only applies to a step that waits"),
        ({"back": None, "interval": 1}, "`interval` only applies to a step that waits"),
        ({"do": "x", "max_actions": 51}, "`max_actions` must be from 1 to 50, got 51"),
        ({"do": "x", "max_actions": 2.5}, "`max_actions` must be a whole number from 1 to 50, got 2.5"),
        ({"do": "x", "max_actions": True}, "`max_actions` must be a whole number"),
        ({"expect": "x", "confidence": 0.3}, "`confidence` must be from 0.5 to 0.99, got 0.3"),
        ({"tap": "x", "timeout": 0}, "`timeout` must be from 1 to 300, got 0"),
        ({"tap": "x", "interval": 0.01}, "`interval` must be from 0.05 to 2, got 0.01"),
        ({"tap": "x", "timeout": 1, "interval": 2}, r"`interval` \(2s\) is longer than `timeout` \(1s\)"),
        ({"back": None, "settle": 3}, "`settle` is gone"),
        ({"tap": "x", "direction": "up"}, "`direction` belongs to scroll_to, not to tap"),
        ({"see": "x", "direction": "up"}, "`direction` belongs to scroll_to, not to a checks-only step"),
        ({"scroll_to": "x"}, "'scroll_to' needs `direction:`"),
        ({"scroll_to": "x", "direction": "in"}, "direction must be one of"),
        ({"swipe": "left", "target": ""}, "target needs text without"),
        ({"type": "a", "into": 3}, "into needs text"),
        ({"back": None, "timeout": 3}, "`timeout` only applies to a step that waits .for its element, its checks"),
        ({"wait": 1, "timeout": 3}, "`timeout` only applies"),
        ({"tap": "x", "timeout": "long"}, "`timeout` must be a number"),
        ({"expect": []}, "needs at least one value"),
        ({"see": ""}, "'see' needs text without"),
        ({"see": {"a": 1}}, "'see' needs text, got a mapping"),
    ],
)
def test_bad_steps_are_rejected(raw, message):
    with pytest.raises(TestFileError, match=message):
        parse_step(raw)


# --- files -------------------------------------------------------------------------


# --- labels ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "written"),
    [
        ("back", "back"),
        ({"clear_data": None}, "clear_data"),
        ("hide_keyboard", "hide_keyboard"),
        ({"do": "Sign in"}, "do: Sign in"),
        ({"use": "Sign in"}, "use: Sign in"),
        ({"tap": "OK"}, "tap: OK"),
        ({"double_tap": "OK"}, "double_tap: OK"),
        ({"clear": "Email"}, "clear: Email"),
        ({"type": "a"}, "type: a"),
        ({"type": {"text": "a", "into": "Email"}}, "type: a (into: Email)"),
        ({"type": "a", "into": "Email"}, "type: a (into: Email)"),
        ({"scroll": "up"}, "scroll: up"),
        ({"swipe": "left"}, "swipe: left"),
        ({"swipe": "left", "target": "Row"}, "swipe: left (target: Row)"),
        (
            {"scroll_to": "End", "direction": "down", "max_scrolls": 5},
            "scroll_to: End (direction: down, max_scrolls: 5)",
        ),
        (
            {"grant": {"android": "android.permission.CAMERA", "ios": ["camera", "photos"]}},
            "grant: android: android.permission.CAMERA, ios: camera, photos",
        ),
        ({"key": "enter"}, "key: enter"),
        ({"wait": 2}, "wait: 2"),
        ({"background": 0.5}, "background: 0.5"),
        ({"rotate": "landscape"}, "rotate: landscape"),
        ({"location": [1, 2.5]}, "location: 1, 2.5"),
        ({"open_url": "app://x"}, "open_url: app://x"),
        ({"dark_mode": True}, "dark_mode: on"),
        ({"network": False}, "network: off"),
        ({"grant": "p"}, "grant: p"),
        ({"screenshot": "s"}, "screenshot: s"),
        ({"tap": "Save", "timeout": 30, "see": "Saved"}, "tap: Save (timeout: 30)"),
    ],
)
def test_a_step_is_labelled_as_written(raw, written):
    assert parse_step(raw).label == written


def test_a_checks_only_step_has_no_label():
    assert parse_step({"see": "x"}).label == ""


def test_every_action_key_has_a_label_and_every_option_belongs_to_an_action():
    assert label("launch", None, {}) == "launch"
    assert {"direction", "target", "into"} <= OPTIONS
    assert all(
        spec.takes_value
        is (key not in {"launch", "stop", "restart", "clear_data", "reinstall", "back", "home", "hide_keyboard"})
        for key, spec in ACTIONS.items()
    )


def test_text_that_is_not_valid_unicode_is_an_error():
    with pytest.raises(TestFileError, match="'tap' has characters that aren't valid Unicode"):
        parse_step({"tap": "\ud800"})
