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
from jevtest.domain.settings import DEFAULTS

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

    d = make_device(Platform(PLATFORM), NAME, APPS[PLATFORM], print)
    d.install(APPS[PLATFORM])
    yield d
    d.close()


@pytest.fixture
def fresh(device):
    device.stop()
    device.clear_data()
    device.launch()
    device.wait_idle(DEFAULTS.settle, quiet=0.5)
    return device


def find(driver, name, timeout=5, **match):
    """The element that says exactly `name` (its text, a part of it such as iOS's label in "Email: value", its
    hint or id), waiting for it the way the runner does: re-read when the screen changes."""
    deadline = time.monotonic() + timeout
    while True:
        elements = driver.screen().elements
        for el in elements:
            if el.says(name) and all(getattr(el, k) == v for k, v in match.items()):
                return el
        if time.monotonic() >= deadline:
            raise AssertionError(f"{name!r} not on screen: {[e.text or e.hint for e in elements]}")
        driver.wait_change(deadline - time.monotonic())


def settle(driver):
    driver.wait_idle(DEFAULTS.settle)


def sign_in(d):
    email = find(d, "Email", editable=True)
    d.type_text("me@x.dev", at=email.center)
    d.type_text("hunter22", at=find(d, "Password", editable=True).center)
    d.hide_keyboard()
    settle(d)
    d.tap(*find(d, "Sign in", clickable=True).center)
    settle(d)


def test_keyboard_is_not_part_of_the_screen(fresh):
    fresh.type_text("a", at=find(fresh, "Email", editable=True).center)
    settle(fresh)
    s = fresh.screen()
    assert s.keyboard_visible
    assert not {"Emoji", "Dictate", "delete", "space", "return"} & {e.text for e in s.elements}


def test_tree_has_the_login_form(fresh):
    s = fresh.screen()
    assert [e.editable for e in s.elements].count(True) == 2
    assert any(e.clickable and e.text == "Sign in" for e in s.elements)


def test_typing_and_clearing_are_exact(fresh):
    email = find(fresh, "Email", editable=True)
    fresh.type_text("abc@x.dev", at=email.center)
    settle(fresh)
    assert find(fresh, "Email", editable=True).value == "abc@x.dev"
    fresh.clear_text(find(fresh, "Email", editable=True))
    settle(fresh)
    assert find(fresh, "Email", editable=True).value == ""


def test_idle_returns_quickly_on_a_still_screen(fresh):
    start = time.monotonic()
    fresh.wait_idle(5)
    assert time.monotonic() - start < 1.0


def test_change_wait_returns_early_on_a_change_and_times_out_without_one(fresh):
    fresh.screen()  # "changed" means: differs from the screen the caller last saw
    start = time.monotonic()
    fresh.wait_change(1.0)  # nothing happens
    assert time.monotonic() - start >= 0.9
    sign_in(fresh)
    fresh.scroll(Direction.DOWN)
    settle(fresh)
    fresh.tap(*find(fresh, "Load data", clickable=True).center)  # "Data loaded" appears 1 s later
    settle(fresh)
    assert find(fresh, "Loading...")
    fresh.screen()
    start = time.monotonic()
    fresh.wait_change(5)
    assert time.monotonic() - start < 2.0  # back as soon as the data arrived, long before the timeout
    assert find(fresh, "Data loaded", timeout=0)


def test_a_change_before_the_wait_is_not_missed(fresh):
    before = fresh.screen()
    fresh.tap(*find(fresh, "Email", editable=True).center)  # changes focus and shows the keyboard
    settle(fresh)
    start = time.monotonic()
    fresh.wait_change(3)  # the change happened before this call; it must still count
    assert time.monotonic() - start < 0.5
    assert fresh.screen() != before


def is_deny(text):
    """The prompt's deny button: "Don't allow" (Android) or "Don\u2019t Allow" (iOS, a curly apostrophe)."""
    return text.lower().replace("\u2019", "'") == "don't allow"


def test_system_permission_prompt_is_on_screen(fresh):
    sign_in(fresh)
    fresh.scroll(Direction.DOWN)
    settle(fresh)
    fresh.tap(*find(fresh, "Open native screen").center)
    settle(fresh)
    fresh.tap(*find(fresh, "Ask for camera").center)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        deny = [e for e in fresh.screen().elements if is_deny(e.text)]
        if deny:  # Android: "Don't allow"; iOS: "Don't Allow"
            # Answer it: a prompt left open outlives the app and would be on every screen of the next run.
            fresh.tap(*deny[0].center)
            settle(fresh)
            assert not any(is_deny(e.text) for e in fresh.screen().elements)
            return
        fresh.wait_change(deadline - time.monotonic())
    raise AssertionError("the system permission prompt never appeared in the tree")
