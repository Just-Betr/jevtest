import json
import os
import shutil
import signal
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from jevtest import __version__
from jevtest.adapters.devices._typing import override
from jevtest.adapters.jev.client import JevClient, Reply
from jevtest.cli import main as cli
from jevtest.cli.run import slug
from jevtest.domain.failures import DeviceError
from jevtest.domain.kinds import Platform
from jevtest.domain.screen import Screen
from tests.conftest import FakeClock, FakeDevice, act, screen_with, yes

TWO_TESTS = """  - name: Sign in
    fresh: true
    steps:
      - do: Press sign in
        expect: Home is showing
  - name: Broken
    fresh: true
    steps:
      - see: Nothing like this
        timeout: 1
"""


def spec_file(
    folder,
    rel="t.yaml",
    *,
    tests="  - {name: T, fresh: true, steps: [back]}\n",
    device="device: {android: emulator-5554}\n",
    app="app: a.apk\n",
    extra="",
):
    f = folder / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    (f.parent / "a.apk").write_text("")
    f.write_text(f"{app}{device}{extra}tests:\n{tests}")
    return f


class FakeJevClient:
    """Answers with scripted Jev answers (only those to questions actually asked)."""

    def __init__(self, api_key, answers):
        self.api_key = api_key
        self.answers = answers
        self.requests: list[str] = []  # each request as JSON, to check what Jev was sent

    def ask(self, state, questions):
        self.requests.append(json.dumps({"state": state, "questions": questions}))
        if not self.answers:
            raise AssertionError(f"FakeJevClient ran out of answers; asked {list(questions)}")
        scripted = self.answers.pop(0)
        answers = {qid: scripted.get(qid) or self._first_option(q) for qid, q in questions.items()}
        return Reply(answers, ms=7, served_by="jev-1.13.0", cost=0.0001)

    @staticmethod
    def _first_option(question):
        """Real Jev answers every question; a test only scripts the ones it cares about."""
        return {"type": "choice", "choice": next(iter(question["criteria"])), "confidence": 1.0, "probabilities": {}}


class Fakes:
    """The implementations the command line is given: fake devices, a scripted Jev."""

    def __init__(self):
        self.devices: list[FakeDevice] = []
        self.clients: list[FakeJevClient] = []
        self.models: list[str] = []
        self.answers: list[object] = []  # scripted Jev answers (dicts) or errors
        self.make_device = self.default_device
        self.clock = FakeClock()  # waiting for a screen takes no real time
        self.missing: set[str] = set()  # device names no running device has
        self.iphones: set[str] = set()  # device names that are real iPhones
        self.looked_for: list[tuple[str, str]] = []

    def find(self, platform, device, app):
        self.looked_for.append((str(platform), device))
        if device in self.missing:
            raise DeviceError(f"No connected device called '{device}'")
        return device in self.iphones

    def default_device(self, platform, device, app, progress):
        self.devices.append(FakeDevice())
        return self.devices[-1]

    def client(self, model, api_key):
        self.models.append(model)
        if not api_key:  # the real client's own check, and its message
            return JevClient(model, api_key, print)
        self.clients.append(FakeJevClient(api_key, self.answers))
        return self.clients[-1]

    def script(self, *answers):
        self.answers = list(answers)
        return self.clients

    def run(self, *args, files=("t.yaml",), lock="record"):
        return cli.main(
            ["run", *files, "--out", "res", "--lock", lock, *args],
            devices=self._device,
            find=self.find,
            client=self.client,
            clock=self.clock,
        )

    def _device(self, platform, device, app, progress):
        made = self.make_device(platform, device, app, progress)  # looked up per call: tests swap it
        if isinstance(made, FakeDevice):
            made.clock = self.clock  # waiting for a change moves the fake clock on
        return made


@pytest.fixture
def fakes():
    return Fakes()


@pytest.fixture
def project(tmp_path, monkeypatch, fakes):
    spec_file(tmp_path, tests=TWO_TESTS)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    return tmp_path


def stamp_of(tmp_path):
    [stamp] = (tmp_path / "res").iterdir()
    return stamp


# --- one file ---------------------------------------------------------------------------------------


def test_run_writes_report_junit_and_lockfile(project, fakes, capsys):
    clients = fakes.script(act("done"), yes(0.95))
    assert fakes.run() == 1  # "Broken" fails
    out = capsys.readouterr().out
    assert "1/2 passed" in out
    assert "FAILED Broken: see: Nothing like this — Waited 1s until 'Nothing like this' is on screen" in out
    assert "Jev: 2 decisions, 0 from lockfile, 2 asked live" in out and "$0.0002" in out
    assert fakes.devices[0].calls[0][0] == "install" and clients[0].api_key == "test-key"
    assert fakes.devices[0].closed

    stamp = stamp_of(project)
    report = json.loads((stamp / "android" / "emulator-5554" / "report.json").read_text())
    assert (report["platform"], report["device"], report["app_id"]) == ("android", "emulator-5554", "dev.fake")
    assert [t["status"] for t in report["tests"]] == ["pass", "fail"]
    assert report["tests"][0]["log"][0] == "\n▶ Sign in" and len(report["model_calls"]) == 2
    junit = ET.parse(stamp / "junit.xml").getroot().find("testsuite")
    assert junit is not None
    assert (junit.attrib["name"], junit.attrib["failures"]) == ("jevtest.android.emulator-5554", "1")
    lockfile = json.loads((project / "t.lock.json").read_text())
    assert len(lockfile["decisions"]) == 2
    assert lockfile["steps"] == {"android · Sign in · step 1 · Press sign in": []}  # Jev said done at once


def test_second_run_replays_lockfile_without_jev(project, fakes, capsys):
    fakes.script(act("done"), yes(0.95))
    fakes.run("--test", "Sign in")
    capsys.readouterr()
    clients = fakes.script()  # no answers: any live call would fail
    before = len(clients)
    assert fakes.run("--test", "Sign in", lock="frozen") == 0
    out = capsys.readouterr().out
    assert "do: Press sign in" in out and "0 saved steps" in out  # the do: repeats its saved steps
    assert "1 decision, 1 from lockfile, 0 asked live" in out  # the expect: is a recorded answer
    assert len(fakes.clients) == before  # Jev was never connected to


def test_the_files_model_asks_jev_and_is_reported(tmp_path, monkeypatch, fakes, capsys):
    spec_file(
        tmp_path,
        tests="  - {name: T, fresh: true, steps: [{expect: Home is showing}]}\n",
        extra="settings: {model: jev-2.0.0}\n",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    fakes.script(yes(0.95))
    assert fakes.run() == 0
    assert fakes.models == ["jev-2.0.0"] and "· jev-2.0.0 ·" in capsys.readouterr().out
    report = json.loads((stamp_of(tmp_path) / "android" / "emulator-5554" / "report.json").read_text())
    assert report["model"] == "jev-2.0.0"


def test_a_value_the_app_shows_reaches_jev_only_as_its_name(tmp_path, monkeypatch, fakes):
    spec_file(tmp_path, tests="  - {name: T, fresh: true, steps: [{expect: 'Welcome ${EMAIL} is showing'}]}\n")
    (tmp_path / ".env").write_text("EMAIL=ann@x.io\nTYPESAFE_API_KEY=k\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    fakes.make_device = lambda *_: FakeDevice(screen_with("Welcome ann@x.io"))
    clients = fakes.script(yes(0.95))
    assert fakes.run() == 0
    [request] = clients[0].requests
    assert "ann@x.io" not in request and "Welcome ${EMAIL}" in request
    lock = (tmp_path / "t.lock.json").read_text()
    assert "ann@x.io" not in lock


def test_frozen_fails_on_unrecorded_screen(project, fakes, capsys):
    assert fakes.run("--test", "Sign in", lock="frozen") == 1
    assert "No steps are saved for this do: in t.lock.json, and --lock frozen" in capsys.readouterr().out


def test_lock_off_leaves_no_file(project, fakes):
    fakes.script(act("done"), yes(0.95))
    fakes.run("--test", "Sign in", lock="off")
    assert not (project / "t.lock.json").exists()


def test_all_pass_exits_zero(project, fakes, capsys):
    fakes.script(act("done"), yes(0.95))
    assert fakes.run("--test", "Sign in", "-v") == 0
    out = capsys.readouterr().out
    assert "jev action: done" in out and f"jevtest {__version__} · android · emulator-5554" in out


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["run", "t.yaml", "--out", "res"], "the following arguments are required: --lock"),
        (["run", "t.yaml", "--lock", "record"], "the following arguments are required: --out"),
        (["run", "t.yaml", "--out", "res", "--lock", "maybe"], "invalid choice: 'maybe'"),
    ],
)
def test_lock_and_out_must_be_given(project, capsys, args, message):
    with pytest.raises(SystemExit) as e:
        cli.main(args)
    assert e.value.code == 2 and message in capsys.readouterr().err


def test_the_file_chooses_platforms_and_devices(tmp_path, monkeypatch, fakes, capsys):
    (tmp_path / "A.app").mkdir()
    spec_file(
        tmp_path,
        app="app: {android: a.apk, ios: A.app}\n",
        device="device: {android: emulator-5554, ios: iPhone 17 Pro}\n",
    )
    monkeypatch.chdir(tmp_path)
    asked = []

    def make_device(platform, device, app, progress):
        asked.append((platform, device, app))
        return FakeDevice()

    fakes.make_device = make_device
    assert fakes.run() == 0
    assert sorted(asked) == [
        (Platform.ANDROID, "emulator-5554", tmp_path / "a.apk"),
        (Platform.IOS, "iPhone 17 Pro", tmp_path / "A.app"),
    ]
    assert sorted(p.name for p in stamp_of(tmp_path).iterdir()) == ["android", "ios", "junit.xml"]
    out = capsys.readouterr().out
    assert "[android · emulator-5554] 1/1 passed" in out and "[ios · iPhone 17 Pro] 1/1 passed" in out
    assert "All: 2/2 passed (1 file, 2 device runs)" in out


def test_progress_messages_are_shown(project, fakes, capsys):
    def make_device(platform, device, app, progress):
        progress("building the Android agent")
        return FakeDevice()

    fakes.make_device = make_device
    fakes.script(act("done"), yes(0.95))
    fakes.run("--test", "Sign in")
    assert "  building the Android agent..." in capsys.readouterr().out


def test_a_failure_on_any_device_fails_the_run(tmp_path, monkeypatch, fakes):
    test = "  - {name: %s, fresh: true, steps: [{see: Welcome, timeout: 1}]}\n"
    spec_file(tmp_path, tests=test % "T" + test % "U", device="device: {android: [A, B]}\n")
    monkeypatch.chdir(tmp_path)
    screens = iter([screen_with("Welcome"), screen_with("Nope")])
    lock = threading.Lock()

    def make_device(*_):
        with lock:
            return FakeDevice(next(screens))

    fakes.make_device = make_device
    assert fakes.run() == 1


def test_unknown_test_name(project, fakes, capsys):
    assert fakes.run("--test", "Sign in", "--test", "Nope") == 2
    assert "No test named: Nope" in capsys.readouterr().err


def test_missing_app(project, fakes, capsys):
    (project / "a.apk").unlink()
    assert fakes.run() == 2
    assert "App not found" in capsys.readouterr().err


def test_missing_api_key_is_an_error(project, fakes, monkeypatch, capsys):
    monkeypatch.delenv("TYPESAFE_API_KEY")
    assert fakes.run("--test", "Sign in") == 1  # the test fails, saying why
    assert "TYPESAFE_API_KEY is not set: put it in the .env next to the test file" in capsys.readouterr().out


@pytest.mark.parametrize("lock", ["refresh", "off"])
def test_a_run_that_always_asks_jev_needs_the_key_before_it_starts(project, fakes, monkeypatch, capsys, lock):
    monkeypatch.delenv("TYPESAFE_API_KEY")
    assert fakes.run(lock=lock) == 2  # nothing installed or run
    assert (
        f"--lock {lock} asks Jev about every do: and expect:, and t.yaml has them, but TYPESAFE_API_KEY is not set"
        in (capsys.readouterr().err)
    )


@pytest.mark.parametrize(
    "tests",
    [
        "  - {name: T, fresh: true, steps: [back, {see: x}]}\n",  # no do: or expect:
        "  - {name: T, fresh: true, steps: [{use: U}, {use: U}]}\n  - {name: U, fresh: true, steps: [back]}\n",
    ],
)
def test_a_run_that_never_asks_jev_needs_no_key(tmp_path, monkeypatch, fakes, tests):
    spec_file(tmp_path, tests=tests)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    assert fakes.run(lock="off") != 2  # it runs


def test_a_used_tests_do_needs_the_key_too(tmp_path, monkeypatch, fakes):
    spec_file(
        tmp_path, tests="  - {name: T, fresh: true, steps: [{use: U}]}\n  - {name: U, fresh: true, steps: [{do: x}]}\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    assert fakes.run("--test", "T", lock="refresh") == 2


def test_device_closed_and_lock_saved_even_on_crash(project, fakes):
    class Boom(FakeDevice):
        @override
        def install(self, app):
            raise DeviceError("install failed")

    boom = Boom()
    fakes.make_device = lambda *_: boom
    assert fakes.run() == 2
    assert boom.closed


def test_interrupt_exits_130(project, fakes, capsys):
    def interrupted(*_):
        raise KeyboardInterrupt

    fakes.make_device = interrupted
    assert fakes.run() == 130
    assert "stopped (SIGINT): devices put back; this run wrote no report" in capsys.readouterr().err
    assert not list(project.glob("res/*/**/report.json")) and not list(project.glob("res/*/junit.xml"))


@pytest.mark.parametrize(("signum", "code"), [(signal.SIGTERM, 143), (signal.SIGHUP, 129)])
def test_a_stop_signal_puts_the_device_back_like_ctrl_c(project, fakes, signum, code):
    """CI cancels a job with SIGTERM; a closed terminal sends SIGHUP. Either used to leave the agent running."""
    previous = signal.signal(signum, signal.SIG_DFL)  # as a fresh process starts, whatever ran before

    class Stopped(FakeDevice):
        @override
        def install(self, app):
            assert signal.getsignal(signum) is cli._stop  # else the kill below would end pytest itself
            os.kill(os.getpid(), signum)
            return super().install(app)

    made: list[Stopped] = []

    def make_device(*_):
        made.append(Stopped())
        return made[-1]

    fakes.make_device = make_device
    try:
        assert fakes.run() == code
        assert made[0].closed
        assert signal.getsignal(signum) is signal.SIG_DFL  # jevtest's handler is gone once it returns
    finally:
        signal.signal(signum, previous)


def test_a_stop_signal_that_is_ignored_stays_ignored(project, fakes):
    """``nohup`` ignores SIGHUP so a run survives its terminal; jevtest keeps it that way."""
    seen = []

    def make_device(*_):
        seen.append(signal.getsignal(signal.SIGHUP))
        raise DeviceError("seen enough")

    fakes.make_device = make_device
    previous = signal.signal(signal.SIGHUP, signal.SIG_IGN)
    try:
        assert fakes.run() == 2
    finally:
        signal.signal(signal.SIGHUP, previous)
    assert seen == [signal.SIG_IGN]


def test_ctrl_c_during_a_multi_device_run_closes_every_device_once(tmp_path, monkeypatch, fakes):
    """Each device runs in a daemon thread, which never runs its own cleanup when the run is interrupted."""
    tests = "  - {name: A, fresh: true, steps: [back]}\n  - {name: B, fresh: true, steps: [back]}\n"
    f = spec_file(tmp_path, tests=tests, device="device: {android: [One, Two]}\n")
    monkeypatch.chdir(tmp_path)
    started = threading.Barrier(3, timeout=10)  # both devices are installing, and the main thread knows it
    release = threading.Event()

    class Busy(FakeDevice):
        closes = 0

        @override
        def install(self, app):
            started.wait()
            release.wait(10)  # still busy when Ctrl-C comes
            return super().install(app)

        @override
        def close(self):
            self.closes += 1
            super().close()

    made: list[Busy] = []

    def make_device(platform, device, app, progress):
        made.append(Busy())
        return made[-1]

    real_join = threading.Thread.join

    def interrupted_join(thread, timeout=None):
        started.wait()
        raise KeyboardInterrupt

    fakes.make_device = make_device
    monkeypatch.setattr(threading.Thread, "join", interrupted_join)
    try:
        assert fakes.run(files=(str(f),)) == 130
    finally:
        monkeypatch.setattr(threading.Thread, "join", real_join)
        release.set()
    assert [d.closes for d in made] == [1, 1]  # put back and stopped by the interrupted run itself
    for thread in threading.enumerate():  # the worker threads finish their own close without closing again
        if thread is not threading.current_thread() and thread.daemon:
            thread.join(5)
    assert [d.closes for d in made] == [1, 1]


def test_only_the_env_next_to_the_test_file_is_read(tmp_path, monkeypatch, fakes):
    spec_file(tmp_path / "suite", tests="  - {name: T, fresh: true, steps: [{type: '${SECRET}'}]}\n")
    (tmp_path / ".env").write_text("SECRET=from-the-folder-you-run-in\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SECRET", raising=False)
    assert fakes.run(files=("suite/t.yaml",)) == 2  # not set: the .env in the current folder is not used


# --- folders -----------------------------------------------------------------------------------------


def test_every_device_is_found_before_any_test_runs(tmp_path, monkeypatch, fakes, capsys):
    spec_file(tmp_path, "a.yaml")
    spec_file(
        tmp_path,
        "b.yaml",
        device="device: {android: [emulator-5554, Pixel 9], ios: iPhone 17}\n",
        app="app: {android: a.apk, ios: a.zip}\n",
    )
    (tmp_path / "a.zip").write_text("")
    monkeypatch.chdir(tmp_path)
    fakes.missing = {"Pixel 9"}
    assert fakes.run(files=(".",)) == 2
    assert "error: android · Pixel 9: No connected device called 'Pixel 9'" in capsys.readouterr().err
    assert fakes.devices == []  # nothing installed, nothing run
    assert fakes.looked_for == [("android", "emulator-5554"), ("android", "Pixel 9")]  # each once; stops at the first


def test_running_a_folder(tmp_path, monkeypatch, fakes, capsys):
    spec_file(
        tmp_path,
        "suite/login.yaml",
        tests="  - {name: Login, fresh: true, steps: [{use: Home}]}\n",
        extra="include: shared/nav.yaml\n",
    )
    (tmp_path / "suite" / "shared").mkdir()
    (tmp_path / "suite" / "shared" / "nav.yaml").write_text("tests: [{name: Home, fresh: true, steps: [home]}]\n")
    spec_file(
        tmp_path, "suite/cart/checkout.yaml", tests="  - {name: Pay, fresh: true, steps: [{see: Nope, timeout: 1}]}\n"
    )
    monkeypatch.chdir(tmp_path)
    assert fakes.run(files=("suite",)) == 1
    out = capsys.readouterr().out
    assert "=== cart/checkout.yaml ===" in out and "=== login.yaml ===" in out
    assert "All: 1/2 passed (2 files, 2 device runs)" in out
    stamp = stamp_of(tmp_path)
    assert (stamp / "login" / "android" / "emulator-5554" / "report.json").exists()
    assert (stamp / "cart" / "checkout" / "android" / "emulator-5554" / "report.json").exists()
    names = [s.attrib["name"] for s in ET.parse(stamp / "junit.xml").getroot()]
    assert names == ["jevtest.cart/checkout.android.emulator-5554", "jevtest.login.android.emulator-5554"]


def test_a_yaml_file_that_is_neither_run_nor_included_is_an_error(tmp_path, monkeypatch, fakes, capsys):
    spec_file(tmp_path, "suite/login.yaml")
    (tmp_path / "suite" / "stray.yaml").write_text("tests: [{name: S, fresh: true, steps: [back]}]\n")
    monkeypatch.chdir(tmp_path)
    assert fakes.run(files=("suite",)) == 2
    assert "stray.yaml: no `app:` and not included by any test file" in capsys.readouterr().err


def test_test_filter_spans_files(tmp_path, monkeypatch, fakes, capsys):
    spec_file(tmp_path, "s/a.yaml", tests="  - {name: A, fresh: true, steps: [back]}\n")
    spec_file(tmp_path, "s/b.yaml", tests="  - {name: B, fresh: true, steps: [back]}\n")
    monkeypatch.chdir(tmp_path)
    assert fakes.run("--test", "B", files=("s",)) == 0
    out = capsys.readouterr().out
    assert "▶ B" in out and "▶ A" not in out and "=== a.yaml ===" not in out


def test_each_file_gets_its_own_env(tmp_path, monkeypatch, fakes, capsys):
    for name, secret in (("one", "first"), ("two", "second")):
        test = "  - {name: T" + name + ", fresh: true, steps: [{type: '${SECRET}'}]}\n"
        spec_file(tmp_path, f"{name}/t.yaml", tests=test)
        (tmp_path / name / ".env").write_text(f"SECRET={secret}\nTYPESAFE_API_KEY=key-{name}\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SECRET", raising=False)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    typing = Screen(1000, 2000, keyboard_visible=True)  # a field has the keys
    fakes.make_device = lambda *_: fakes.devices.append(FakeDevice(typing)) or fakes.devices[-1]
    assert fakes.run(files=("one", "two")) == 0
    assert [c[1] for d in fakes.devices for c in d.calls if c[0] == "type_text"] == ["first", "second"]
    assert "first" not in capsys.readouterr().out


# --- several devices ------------------------------------------------------------------------------------


def test_tests_are_split_across_devices_and_run_at_once(tmp_path, monkeypatch, fakes, capsys):
    tests = "".join(f"  - {{name: T{i}, fresh: true, steps: [back]}}\n" for i in range(3))
    f = spec_file(tmp_path, tests=tests, device="device: {android: [Pixel 4a, Pixel 8, Pixel 9, Pixel 10]}\n")
    monkeypatch.chdir(tmp_path)
    ran, threads = {}, set()

    def make_device(platform, device, app, progress):
        threads.add(threading.get_ident())
        ran[device] = FakeDevice()
        return ran[device]

    fakes.make_device = make_device
    assert fakes.run(files=(str(f),)) == 0
    assert sorted(ran) == ["Pixel 4a", "Pixel 8", "Pixel 9"] and len(threads) == 3
    out = capsys.readouterr().out
    assert "[android · Pixel 10] no tests left for this device (4 devices, fewer groups of tests)" in out
    for i, phone in enumerate(["Pixel 4a", "Pixel 8", "Pixel 9"]):
        assert f"[android · {phone}] ▶ T{i}\n[android · {phone}]   ✓ back" in out  # each test's log whole
    assert sorted(p.name for p in (stamp_of(tmp_path) / "android").iterdir()) == ["Pixel_4a", "Pixel_8", "Pixel_9"]


@pytest.mark.parametrize(("error", "code"), [(DeviceError("no such phone"), 2), (RuntimeError("bug"), None)])
def test_a_device_that_fails_to_start_is_reported_after_the_others(tmp_path, monkeypatch, fakes, capsys, error, code):
    tests = "  - {name: A, fresh: true, steps: [back]}\n  - {name: B, fresh: true, steps: [back]}\n"
    f = spec_file(tmp_path, tests=tests, device="device: {android: [Good, Bad]}\n")
    monkeypatch.chdir(tmp_path)
    good = FakeDevice()

    def make_device(platform, device, app, progress):
        if device == "Bad":
            raise error
        return good

    fakes.make_device = make_device
    if code is None:
        with pytest.raises(RuntimeError, match="bug"):
            fakes.run(files=(str(f),))
    else:
        assert fakes.run(files=(str(f),)) == code
        assert "error: android · Bad: no such phone" in capsys.readouterr().err
    assert "back" in good.names()  # the good phone still ran its test


def test_slug():
    assert slug("iPhone 17 Pro (2)") == "iPhone_17_Pro_2" and slug("///") == "device"


# --- pruning the lockfile ----------------------------------------------------------------------------


def test_prune_lock_after_a_passing_run(project, fakes, capsys):
    fakes.script(act("done"), yes(0.95))
    fakes.run("--test", "Sign in")
    lock = project / "t.lock.json"
    data = json.loads(lock.read_text())
    data["decisions"]["stale"] = {"answers": {}}
    lock.write_text(json.dumps(data))
    spec_file(project, tests=TWO_TESTS.split("  - name: Broken", maxsplit=1)[0])  # only the passing test
    assert fakes.run("--prune-lock") == 0
    # the stale one, and Jev's answer while working out the do: (its saved steps repeat without asking again)
    assert "t.lock.json: pruned 2 unused entries" in capsys.readouterr().out
    data = json.loads(lock.read_text())
    data["decisions"]["stale again"] = {"answers": {}}
    lock.write_text(json.dumps(data))
    assert fakes.run("--prune-lock") == 0
    assert "t.lock.json: pruned 1 unused entry\n" in capsys.readouterr().out
    assert "stale" not in json.loads(lock.read_text())["decisions"]


def test_prune_lock_skipped_when_a_test_failed(project, fakes, capsys):
    fakes.script(act("done"), yes(0.95))
    assert fakes.run("--prune-lock") == 1
    assert "t.lock.json: not pruned, because a test failed" in capsys.readouterr().out


@pytest.mark.parametrize("args", [["--test", "Sign in", "--prune-lock"], ["--prune-lock"]])
def test_prune_lock_needs_a_full_run_with_a_lockfile(project, fakes, capsys, args):
    lock = "off" if args == ["--prune-lock"] else "record"
    assert fakes.run(*args, lock=lock) == 2
    assert "--prune-lock needs every test to run" in capsys.readouterr().err


# --- wiring -------------------------------------------------------------------------------------------


def test_make_device_picks_the_platform(monkeypatch):
    monkeypatch.setattr(cli, "AndroidDevice", lambda d, progress: ("android", d))
    monkeypatch.setattr(cli, "IOSDevice", lambda d, app, progress: ("ios", d, app))
    # the swapped-in constructors return what they were given, so the result is compared as a plain object
    android: object = cli.make_device(Platform.ANDROID, "x", Path("a.apk"), print)
    ios: object = cli.make_device(Platform.IOS, "BH", Path("R.app"), print)
    assert android == ("android", "x") and ios == ("ios", "BH", Path("R.app"))


def test_make_client_reports_retries_on_stderr(capsys):
    client = cli.make_client("jev-1.13.0", "k")
    assert client.api_key == "k" and client.model == "jev-1.13.0"
    client._log("Jev HTTP 503: trying again")
    assert "Jev HTTP 503: trying again" in capsys.readouterr().err


def test_version(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--version"])
    assert __version__ in capsys.readouterr().out


def test_module_entry_point(monkeypatch):
    import runpy

    monkeypatch.setattr("sys.argv", ["jevtest", "run", "missing.yaml", "--lock", "off", "--out", "x"])
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_module("jevtest", run_name="__main__")
    assert exit_info.value.code == 2


def test_a_step_a_platform_cant_run_is_an_error_before_any_device_is_touched(tmp_path, monkeypatch, fakes, capsys):
    spec_file(
        tmp_path,
        app="app: {android: a.apk, ios: a.zip}\n",
        device="device: {android: emulator-5554, ios: iPhone 17}\n",
        tests="  - {name: T, fresh: true, steps: [back]}\n  - {name: Offline, fresh: true, steps: [{network: false}]}\n",
    )
    (tmp_path / "a.zip").write_text("")
    monkeypatch.chdir(tmp_path)
    assert fakes.run("--test", "T") == 2  # checked for the whole file, whichever tests run
    assert "Test 'Offline': network: can't run on iOS" in capsys.readouterr().err
    assert fakes.devices == []


def test_a_network_step_on_android_only_runs(tmp_path, monkeypatch, fakes):
    spec_file(tmp_path, tests="  - {name: T, fresh: true, steps: [{network: false}]}\n")
    monkeypatch.chdir(tmp_path)
    assert fakes.run() == 0


def test_a_results_folder_that_cant_be_made_is_an_error_before_the_run(project, fakes, capsys):
    (project / "res").write_text("a file, not a folder")
    assert fakes.run() == 2
    assert "error: --out res: can't make the results folder res/" in capsys.readouterr().err
    assert fakes.devices == []


def test_runs_that_start_in_the_same_second_get_their_own_results_folders(project, fakes, monkeypatch):
    monkeypatch.setattr(time, "strftime", lambda _: "20260928-120000")
    fakes.run("--test", "Broken")
    fakes.run("--test", "Broken")
    fakes.run("--test", "Broken")
    folders = sorted(p.name for p in (project / "res").iterdir())
    assert folders == ["20260928-120000", "20260928-120000-2", "20260928-120000-3"]
    assert all((project / "res" / f / "junit.xml").exists() for f in folders)


@pytest.mark.parametrize(
    ("tests", "message"),
    [
        (
            "  - {name: A, fresh: true, steps: [{use: B}]}\n",
            "'Sign in' is a library test: it runs only where a test uses it; --test one that does: A",
        ),
        (
            "  - {name: A, fresh: true, steps: [back]}\n",
            "'Sign in' is a library test: it runs only where a test uses it; and no test does",
        ),
    ],
)
def test_asking_for_a_library_test_says_what_runs_it(tmp_path, monkeypatch, fakes, capsys, tests, message):
    spec_file(tmp_path, tests=tests, extra="include: lib.yaml\n")
    (tmp_path / "lib.yaml").write_text(
        "tests:\n  - {name: B, fresh: true, steps: [{use: Sign in}]}\n  - {name: Sign in, fresh: true, steps: [back]}\n"
    )
    monkeypatch.chdir(tmp_path)
    assert fakes.run("--test", "Sign in") == 2
    assert message in capsys.readouterr().err


BOTH = {"app": "app: {android: a.apk, ios: a.zip}\n", "device": "device: {android: emulator-5554, ios: iPhone 17}\n"}


def test_each_platform_gets_its_own_names(tmp_path, monkeypatch, fakes):
    grant = "{android: [android.permission.CAMERA, android.permission.RECORD_AUDIO], ios: [camera, microphone]}"
    spec_file(tmp_path, tests=f"  - {{name: T, fresh: true, steps: [{{grant: {grant}}}]}}\n", **BOTH)
    (tmp_path / "a.zip").write_text("")
    monkeypatch.chdir(tmp_path)
    fakes.run()
    granted = sorted(c[1:] for d in fakes.devices for c in d.calls if c[0] == "grant")
    assert granted == [("android.permission.CAMERA", "android.permission.RECORD_AUDIO"), ("camera", "microphone")]


def test_a_grant_on_a_real_iphone_is_an_error_before_the_run(tmp_path, monkeypatch, fakes, capsys):
    spec_file(
        tmp_path,
        app="app: a.zip\n",
        device="device: {ios: [iPhone 17, BH]}\n",
        tests="  - {name: Photo, fresh: true, steps: [{grant: camera}]}\n",
    )
    (tmp_path / "a.zip").write_text("")
    monkeypatch.chdir(tmp_path)
    fakes.iphones = {"BH"}
    assert fakes.run() == 2
    assert "t.yaml runs on the iPhone BH, where jevtest can't pre-grant permissions" in capsys.readouterr().err
    assert fakes.devices == []
    fakes.iphones = set()
    assert fakes.run() != 2  # on simulators only, it runs


def test_an_aab_without_bundletool_is_an_error_before_the_run(tmp_path, monkeypatch, fakes, capsys):
    spec_file(tmp_path, app="app: b.aab\n")
    (tmp_path / "b.aab").write_text("")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert fakes.run() == 2
    assert "b.aab: bundletool is required to install .aab files" in capsys.readouterr().err
    assert fakes.devices == []
    monkeypatch.setattr(shutil, "which", lambda name: f"/bin/{name}")
    assert fakes.run() != 2
