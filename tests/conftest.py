"""Shared fakes: a scripted Jev, a scripted device, and a clock that never really sleeps."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from jevtest.drivers.base import Driver, DriverError
from jevtest.screen import Element, Screen


class FakeJev:
    """Returns scripted answers in order. Each entry is a dict of answers or an exception."""

    def __init__(self, *answers, model="typesafe/jev-1.13"):
        self.answers = list(answers)
        self.asked: list[tuple] = []
        self.calls: list[dict] = []
        self.model = model

    def ask(self, state, questions):
        self.asked.append((state, questions))
        if not self.answers:
            raise AssertionError(f"FakeJev ran out of answers; asked {list(questions)}")
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        self.calls.append({"ms": 7, "model": "typesafe/jev-1.13-test", "state": state, "questions": questions,
                           "answers": answer, "usage": {"cost": 0.0001, "input_tokens": 10}})
        return answer


def act(action, target=None, field=None, value=None, confidence=0.9):
    """Jev answer for Brain.next_action."""
    out = {"action": {"type": "choice", "choice": action, "confidence": confidence,
                      "probabilities": {action: confidence, "other": 1 - confidence}},
           "target": {"type": "choice", "choice": target or "e1", "confidence": 1, "probabilities": {}}}
    if field or value:
        out["field"] = {"type": "choice", "choice": field or "e1", "confidence": 1, "probabilities": {}}
    if value:
        out["value"] = {"type": "choice", "choice": value, "confidence": 1, "probabilities": {}}
    return out


def pick(element_id):
    return {"element": {"type": "choice", "choice": element_id, "confidence": 1, "probabilities": {}}}


def yes(p):
    return {"check": {"type": "noul", "noul": p}}


class FakeClock:
    def __init__(self):
        self.t = 0.0
        self.slept: list[float] = []

    def now(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


def el(kind="button", text="", **kw) -> Element:
    return Element(kind=kind, text=text, **kw)


def login_screen(**kw) -> Screen:
    return Screen(width=1000, height=2000, elements=[
        el("text_field", hint="Email", editable=True, bounds=(0, 100, 1000, 200)),
        el("password_field", hint="Password", editable=True, bounds=(0, 250, 1000, 350)),
        el("button", "Sign in", clickable=True, bounds=(0, 400, 1000, 500)),
    ], **kw)


def screen_with(*texts, **kw) -> Screen:
    return Screen(width=1000, height=2000, elements=[
        el("text", t, bounds=(0, 100 * i, 1000, 100 * i + 80)) for i, t in enumerate(texts, 1)], **kw)


class FakeDriver(Driver):
    """Records every call. `screens` is consumed one per screen() call; the last one repeats."""

    platform = "fake"

    CHANGE_AFTER = 0.5  # fake seconds until "the screen changed" when waiting for a change

    def __init__(self, *screens: Screen, state="foreground"):
        self.screens = list(screens) or [login_screen()]
        self.calls: list[tuple] = []
        self.state = state
        self.fail: dict[str, Exception] = {}
        self.app_id = "dev.fake"
        self.clock: FakeClock | None = None  # set by the runner tests

    def _rec(self, name, *args):
        self.calls.append((name, *args))
        if name in self.fail:
            raise self.fail[name]

    def names(self):
        return [c[0] for c in self.calls]

    def install(self, app_path):
        self._rec("install", app_path)
        return self.app_id

    def launch(self):
        self._rec("launch")

    def stop(self):
        self._rec("stop")

    def clear_data(self):
        self._rec("clear_data")

    def reinstall(self):
        self._rec("reinstall")

    def app_state(self):
        self._rec("app_state")
        return self.state

    def resume(self):
        self._rec("resume")

    def screen(self):
        self._rec("screen")
        return self.screens.pop(0) if len(self.screens) > 1 else self.screens[0]

    def screenshot(self, path: Path):
        self._rec("screenshot", path.name)
        path.write_bytes(b"png")

    def tap(self, x, y):
        self._rec("tap", x, y)

    def double_tap(self, x, y):
        self._rec("double_tap", x, y)

    def long_press(self, x, y, seconds=1.2):
        self._rec("long_press", x, y)

    def drag(self, x1, y1, x2, y2, seconds=0.3):
        self._rec("drag", x1, y1, x2, y2)

    def type_text(self, text, at=None):
        self._rec("type_text", text, at)

    def clear_text(self, element):
        self._rec("clear_text", element.text or element.hint)

    def key(self, name):
        self._rec("key", name)

    def back(self):
        self._rec("back")

    def home(self):
        self._rec("home")

    def hide_keyboard(self):
        self._rec("hide_keyboard")

    def rotate(self, orientation):
        self._rec("rotate", orientation)

    def set_location(self, lat, lon):
        self._rec("set_location", lat, lon)

    def open_url(self, url):
        self._rec("open_url", url)

    def dark_mode(self, on):
        self._rec("dark_mode", on)

    def grant(self, permission):
        self._rec("grant", permission)

    def network(self, on):
        self._rec("network", on)

    def wait_idle(self, timeout):
        self._rec("wait_idle", timeout)

    def wait_change(self, timeout):
        self._rec("wait_change")
        if self.clock:
            self.clock.sleep(min(timeout, self.CHANGE_AFTER))


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def out():
    return io.StringIO()


__all__ = ["FakeJev", "FakeDriver", "FakeClock", "DriverError", "act", "pick", "yes", "el",
           "login_screen", "screen_with"]
