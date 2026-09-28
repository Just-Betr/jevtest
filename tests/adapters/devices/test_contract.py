"""Agent contract tests on a real emulator / simulator (the Swift and Java agents can't be faked).

Skipped unless JEVTEST_DEVICE (android or ios) and JEVTEST_DEVICE_NAME (the exact device name) are set,
with the demo app built:
    JEVTEST_DEVICE=android JEVTEST_DEVICE_NAME=emulator-5554 pytest tests/adapters/devices/test_contract.py
"""

import os
import time
from pathlib import Path

import pytest

from jevtest.domain.kinds import Direction

PLATFORM = os.environ.get("JEVTEST_DEVICE")
NAME = os.environ.get("JEVTEST_DEVICE_NAME")
DEMO = Path(__file__).parents[3] / "demo_app/build"
APPS = {
    "android": DEMO / "app/outputs/flutter-apk/app-debug.apk",
    "ios": DEMO / "ios_sim/Build/Products/Debug-iphonesimulator/Runner.app",
}

pytestmark = pytest.mark.skipif(
    PLATFORM not in APPS or not NAME, reason="set JEVTEST_DEVICE=android|ios and JEVTEST_DEVICE_NAME to run on a device"
)


@pytest.fixture(scope="module")
def device():
    from jevtest.cli.main import make_device
    from jevtest.domain.kinds import Platform

    assert PLATFORM is not None and NAME is not None  # the module is skipped otherwise
    d = make_device(Platform(PLATFORM), NAME, APPS[PLATFORM], print)
    d.install(APPS[PLATFORM])
    yield d
    d.close()


@pytest.fixture
def fresh(device):
    device.stop()
    device.clear_data()
    device.launch()
    return device


def until(check, what, timeout=5):
    """Like the runner's wait_until: check every 0.25 s until `check` returns something truthy, or fail."""
    deadline = time.monotonic() + timeout
    while True:
        found = check()
        if found:
            return found
        if time.monotonic() + 0.25 > deadline:
            raise AssertionError(f"waited {timeout}s until {what}")
        time.sleep(0.25)


def find(driver, name, timeout=5, **match):
    """The element that says exactly `name` (its text, a part of it such as iOS's label in "Email: value", its
    hint or id), waiting until it's on screen the way the runner does."""

    def found():
        for el in driver.screen().elements:
            if el.says(name) and all(getattr(el, k) == v for k, v in match.items()):
                return el
        return None

    return until(found, f"{name!r} is on screen", timeout)


def sign_in(d):
    d.type_text("me@x.dev", at=find(d, "Email", editable=True).center)
    d.type_text("hunter22", at=find(d, "Password", editable=True).center)
    d.hide_keyboard()  # returns once the keyboard is gone
    d.tap(*find(d, "Sign in", clickable=True).center)
    find(d, "Load data", timeout=10)


def test_keyboard_is_not_part_of_the_screen(fresh):
    fresh.type_text("a", at=find(fresh, "Email", editable=True).center)  # returns once the keyboard is up
    s = fresh.screen()
    assert s.keyboard_visible
    assert not {"Emoji", "Dictate", "delete", "space", "return"} & {e.text for e in s.elements}


def test_tree_has_the_login_form(fresh):
    find(fresh, "Sign in", clickable=True)
    assert [e.editable for e in fresh.screen().elements].count(True) == 2


def test_typing_and_clearing_are_exact(fresh):
    fresh.type_text("abc@x.dev", at=find(fresh, "Email", editable=True).center)
    until(lambda: find(fresh, "Email", editable=True).value == "abc@x.dev", "the field holds what was typed")
    fresh.clear_text(find(fresh, "Email", editable=True))
    until(lambda: find(fresh, "Email", editable=True).value == "", "the field is empty")


def test_reading_the_screen_answers_at_once(fresh):
    """The agent never waits: a step's wait_until reads the screen every interval."""
    find(fresh, "Sign in")
    start = time.monotonic()
    for _ in range(5):
        fresh.screen()
    assert time.monotonic() - start < 2.5


def is_deny(text):
    """The prompt's deny button: "Don't allow" (Android) or "Don\u2019t Allow" (iOS, a curly apostrophe)."""
    return text.lower().replace("\u2019", "'") == "don't allow"


def test_system_permission_prompt_is_on_screen(fresh):
    sign_in(fresh)
    fresh.scroll(Direction.DOWN)
    fresh.tap(*find(fresh, "Open native screen").center)
    fresh.tap(*find(fresh, "Ask for camera").center)
    deny = until(
        lambda: [e for e in fresh.screen().elements if is_deny(e.text)],  # Android: "Don't allow"; iOS: "Don't Allow"
        "the system permission prompt is in the tree",
        timeout=10,
    )
    # Answer it: a prompt left open outlives the app and would be on every screen of the next run.
    fresh.tap(*deny[0].center)
    until(lambda: not any(is_deny(e.text) for e in fresh.screen().elements), "the prompt is gone")
