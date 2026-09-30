"""Shared fakes: a scripted decision model, a scripted device, and a clock that never really sleeps."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest

from jevtest.adapters.devices._typing import override
from jevtest.adapters.devices.common import BaseDevice
from jevtest.adapters.jev.wire import answer_from_wire, questions_to_wire
from jevtest.cli.console import ConsoleListener, Printer
from jevtest.domain.decisions import SavedStep
from jevtest.domain.failures import DeviceError, NotRecorded
from jevtest.domain.kinds import AppState
from jevtest.domain.model import ModelCall, State
from jevtest.domain.screen import Element, Screen


class FakeModel:
    """Answers with scripted answers, in order. Each entry is a dict of Jev-format answers, or an exception.

    Only the scripted answers to questions actually asked are returned; `asked` keeps each request in Jev's
    format, so tests can read what was asked.
    """

    def __init__(self, *answers, model="jev-1.13.0", saved=None, frozen=False):
        self.answers = list(answers)
        self.asked: list[tuple[State, dict[str, dict[str, Any]]]] = []  # (state, the questions as Jev's JSON)
        self.calls: list[ModelCall] = []
        self.model = model
        self.saved: dict[str, tuple[SavedStep, ...]] = dict(saved or {})  # a do: goal's saved steps, by key
        self.frozen = frozen

    @property
    def replays_only(self):
        return self.frozen

    def saved_steps(self, key):
        if key not in self.saved and self.frozen:
            raise NotRecorded(f"No steps are saved for {key}")
        return self.saved.get(key)

    def save_steps(self, key, steps):
        self.saved[key] = tuple(steps)

    def ask(self, state, questions):
        self.asked.append((state, json.loads(json.dumps(questions_to_wire(questions)))))
        if not self.answers:
            raise AssertionError(f"FakeModel ran out of answers; asked {list(questions)}")
        scripted = self.answers.pop(0)
        if isinstance(scripted, Exception):
            raise scripted
        answers = {qid: answer_from_wire(qid, raw, questions[qid]) for qid, raw in scripted.items() if qid in questions}
        self.calls.append(
            ModelCall(state, questions, answers, recorded=False, ms=7, cost=0.0001, served_by="jev-1.13.0")
        )
        return answers


@pytest.fixture(autouse=True)
def _own_cache(tmp_path_factory, monkeypatch):
    """No test reads or writes the real ~/.cache/jevtest (agents, device claims, undo records)."""
    monkeypatch.setenv("JEVTEST_CACHE", str(tmp_path_factory.mktemp("cache")))


def act(action, target=None, field=None, value=None, confidence=0.9):
    """Jev's answers for Brain.next_action."""
    out = {
        "action": {
            "type": "choice",
            "choice": action,
            "confidence": confidence,
            "probabilities": {action: confidence, "other": round(1 - confidence, 2)},
        },
        "target": {"type": "choice", "choice": target or "e1", "confidence": 1, "probabilities": {}},
    }
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

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.t += seconds


def el(kind="button", text="", **kw) -> Element:
    return Element(kind=kind, text=text, **kw)


def login_screen(**kw) -> Screen:
    return Screen(
        1000,
        2000,
        (
            el("text_field", hint="Email", editable=True, bounds=(0, 100, 1000, 200)),
            el("password_field", hint="Password", editable=True, bounds=(0, 250, 1000, 350)),
            el("button", "Sign in", clickable=True, bounds=(0, 400, 1000, 500)),
        ),
        **kw,
    )


def screen_with(*texts, **kw) -> Screen:
    return Screen(
        1000, 2000, tuple(el("text", t, bounds=(0, 100 * i, 1000, 100 * i + 80)) for i, t in enumerate(texts, 1)), **kw
    )


class FakeDevice(BaseDevice):
    """Records every call. `screens` is consumed one per screen() call; the last one repeats.

    Time passes only through the runner's clock (each `interval` between two checks of a `wait_until`).
    """

    def __init__(self, *screens: Screen, state=AppState.FOREGROUND):
        self.screens = list(screens) or [login_screen()]
        self.calls: list[tuple[object, ...]] = []
        self.state = AppState(state)
        self.fail: dict[str, Exception] = {}
        self.app_id = "dev.fake"
        self.clock: FakeClock | None = None  # set by the runner tests
        self.closed = False
        self.drawn = [""]  # what looks() says, one per call; the last one repeats

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

    @override
    def app_state(self):
        self._rec("app_state")
        return self.state

    def resume(self):
        self._rec("resume")

    def looks(self, elements):
        self._rec("looks", tuple(e.text for e in elements))
        return self.drawn.pop(0) if len(self.drawn) > 1 else self.drawn[0]

    @override
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

    @override
    def drag(self, x1, y1, x2, y2):
        self._rec("drag", x1, y1, x2, y2)  # how a scroll drags is the device's concern (see its tests)

    def type_text(self, text, at=None):
        self._rec("type_text", text, at)

    def clear_text(self, element):
        self._rec("clear_text", element.text or element.hint)

    def key(self, name):
        self._rec("key", name)

    def back(self):
        self._rec("back")

    @override
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

    def dark_mode(self, *, on):
        self._rec("dark_mode", on)

    def grant(self, permissions):
        self._rec("grant", *permissions)

    def network(self, *, on):
        self._rec("network", on)

    def autofill_off(self):
        self._rec("autofill_off")

    @override
    def prepare_for_test(self):  # not logged: only matters when a test makes it fail
        if "prepare_for_test" in self.fail:
            raise self.fail["prepare_for_test"]

    @override
    def restore(self):
        self._rec("restore")

    @override
    def _put_back(self, entry):  # never called: restore() is recorded instead
        raise NotImplementedError

    @override
    def _press_home(self):  # never called: home() is recorded instead
        raise NotImplementedError

    @override
    def close(self):
        self.closed = True


PROGRESS_MESSAGES: list[str] = []


def PROGRESS(message: str) -> None:
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


__all__ = [
    "PROGRESS",
    "PROGRESS_MESSAGES",
    "DeviceError",
    "FakeClock",
    "FakeDevice",
    "FakeModel",
    "act",
    "confirm",
    "console",
    "el",
    "login_screen",
    "pick",
    "screen_with",
    "yes",
]
