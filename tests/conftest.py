"""Shared fakes: a scripted decision model, a scripted device, and a clock that never really sleeps."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from jevtest.adapters.devices.common import BaseDevice
from jevtest.adapters.jev.wire import answer_from_wire, questions_to_wire
from jevtest.cli.console import ConsoleListener, Printer
from jevtest.domain.failures import DeviceError
from jevtest.domain.kinds import AppState
from jevtest.domain.model import ModelCall
from jevtest.domain.screen import Element, Screen


class FakeModel:
    """Answers with scripted answers, in order. Each entry is a dict of Jev-format answers, or an exception.

    Only the scripted answers to questions actually asked are returned; `asked` keeps each request in Jev's
    format, so tests can read what was asked.
    """

    def __init__(self, *answers, model="typesafe/jev-1.13"):
        self.answers = list(answers)
        self.asked: list[tuple] = []
        self.calls: list[ModelCall] = []
        self.model = model

    def ask(self, state, questions):
        self.asked.append((state, questions_to_wire(questions)))
        if not self.answers:
            raise AssertionError(f"FakeModel ran out of answers; asked {list(questions)}")
        scripted = self.answers.pop(0)
        if isinstance(scripted, Exception):
            raise scripted
        answers = {qid: answer_from_wire(qid, raw, questions[qid]) for qid, raw in scripted.items() if qid in questions}
        self.calls.append(ModelCall(state, questions, answers, recorded=False, ms=7, cost=0.0001,
                                    served_by="typesafe/jev-1.13-test"))
        return answers


def act(action, target=None, field=None, value=None, confidence=0.9):
    """Jev's answers for Brain.next_action."""
    out = {"action": {"type": "choice", "choice": action, "confidence": confidence,
                      "probabilities": {action: confidence, "other": round(1 - confidence, 2)}},
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


def confirm(p=0.95):
    """Jev's answer to 'is the element it picked really the target?'"""
    return {"is_target": {"type": "noul", "noul": p}}


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
    return Screen(1000, 2000, (
        el("text_field", hint="Email", editable=True, bounds=(0, 100, 1000, 200)),
        el("password_field", hint="Password", editable=True, bounds=(0, 250, 1000, 350)),
        el("button", "Sign in", clickable=True, bounds=(0, 400, 1000, 500)),
    ), **kw)


def screen_with(*texts, **kw) -> Screen:
    return Screen(1000, 2000, tuple(el("text", t, bounds=(0, 100 * i, 1000, 100 * i + 80))
                                    for i, t in enumerate(texts, 1)), **kw)


class FakeDevice(BaseDevice):
    """Records every call. `screens` is consumed one per screen() call; the last one repeats."""

    CHANGE_AFTER = 0.5  # fake seconds until "the screen changed" when waiting for a change

    def __init__(self, *screens: Screen, state=AppState.FOREGROUND):
        self.screens = list(screens) or [login_screen()]
        self.calls: list[tuple] = []
        self.state = AppState(state)
        self.fail: dict[str, Exception] = {}
        self.app_id = "dev.fake"
        self.clock: FakeClock | None = None  # set by the runner tests
        self.closed = False

    def _rec(self, name, *args):
        self.calls.append((name, *args))
        if name in self.fail:
            raise self.fail[name]

    def names(self):
        return [c[0] for c in self.calls]

    def install(self, app):
        self._rec("install", app)
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

    def long_press(self, x, y):
        self._rec("long_press", x, y)

    def drag(self, x1, y1, x2, y2):
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

    def set_location(self, latitude, longitude):
        self._rec("set_location", latitude, longitude)

    def open_url(self, url):
        self._rec("open_url", url)

    def dark_mode(self, on):
        self._rec("dark_mode", on)

    def grant(self, permission):
        self._rec("grant", permission)

    def network(self, on):
        self._rec("network", on)

    def check_ready(self):  # not logged: only matters when a test makes it fail
        if "check_ready" in self.fail:
            raise self.fail["check_ready"]

    def wait_idle(self, timeout, quiet=None):
        self._rec("wait_idle", timeout) if quiet is None else self._rec("wait_idle", timeout, quiet)

    def wait_change(self, timeout):
        self._rec("wait_change")
        if self.clock:
            self.clock.sleep(min(timeout, self.CHANGE_AFTER))

    def restore(self):
        self._rec("restore")

    def close(self):
        self.closed = True


PROGRESS_MESSAGES: list[str] = []


def PROGRESS(message: str) -> None:  # noqa: N802 - a constant-like callback the device tests pass
    """Collects the devices' progress messages (building an agent, ...)."""
    PROGRESS_MESSAGES.append(message)


def console(out: io.StringIO, *, verbose: bool = False) -> ConsoleListener:
    """A listener that renders to `out`, like the command line does for a single device."""
    return ConsoleListener(Printer(parallel=False, out=out), "device", verbose=verbose)


# The iOS device's fixtures (env, drv) are shared by test_ios.py and test_ios_device.py.
pytest_plugins = ["tests.adapters.devices.test_ios"]


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def out():
    return io.StringIO()


__all__ = ["PROGRESS", "PROGRESS_MESSAGES", "DeviceError", "FakeClock", "FakeDevice", "FakeModel", "act", "confirm",
           "console", "el", "login_screen",
           "pick", "screen_with", "yes"]
