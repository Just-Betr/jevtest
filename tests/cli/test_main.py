import json
import threading
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from jevtest import __version__
from jevtest.adapters.jev.client import JevClient, Reply
from jevtest.cli import main as cli
from jevtest.cli.run import slug
from jevtest.domain.failures import DeviceError
from jevtest.domain.kinds import Platform

from ..conftest import FakeDevice, act, screen_with, yes

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


def spec_file(folder, rel="t.yaml", *, tests="  - {name: T, fresh: true, steps: [back]}\n",
              device="device: {android: emulator-5554}\n", app="app: a.apk\n", extra=""):
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

    def ask(self, state, questions):
        if not self.answers:
            raise AssertionError(f"FakeJevClient ran out of answers; asked {list(questions)}")
        scripted = self.answers.pop(0)
        answers = {qid: scripted.get(qid) or self._first_option(q) for qid, q in questions.items()}
        return Reply(answers, ms=7, served_by="typesafe/jev-1.13-test", cost=0.0001)

    @staticmethod
    def _first_option(question):
        """Real Jev answers every question; a test only scripts the ones it cares about."""
        return {"type": "choice", "choice": next(iter(question["criteria"])), "confidence": 1.0,
                "probabilities": {}}


class Fakes:
    """The implementations the command line is given: fake devices, a scripted Jev."""

    def __init__(self):
        self.devices: list[FakeDevice] = []
        self.clients: list[FakeJevClient] = []
        self.models: list[str] = []
        self.answers: list = []
        self.make_device = self.default_device

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
        return cli.main(["run", *files, "--out", "res", "--lock", lock, *args],
                        devices=self._device, client=self.client)

    def _device(self, platform, device, app, progress):
        return self.make_device(platform, device, app, progress)  # looked up per call: tests swap it


@pytest.fixture
def fakes():
    return Fakes()


@pytest.fixture
def project(tmp_path, monkeypatch, fakes):
    spec_file(tmp_path, tests=TWO_TESTS)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    return tmp_path


def stamp_of(tmp_path):
    [stamp] = (tmp_path / "res").iterdir()
    return stamp


# --- one file ---------------------------------------------------------------------------------------

def test_run_writes_report_junit_and_lockfile(project, fakes, capsys):
    clients = fakes.script(act("done"), yes(0.95))
    assert fakes.run() == 1  # "Broken" fails
    out = capsys.readouterr().out
    assert "1/2 passed" in out and "FAILED Broken: see: Nothing like this — not on screen" in out
    assert "Jev: 2 decisions, 0 from lockfile, 2 asked live" in out and "$0.0002" in out
    assert fakes.devices[0].calls[0][0] == "install" and clients[0].api_key == "test-key"
    assert fakes.devices[0].closed

    stamp = stamp_of(project)
    report = json.loads((stamp / "android" / "emulator-5554" / "report.json").read_text())
    assert (report["platform"], report["device"], report["app_id"]) == ("android", "emulator-5554", "dev.fake")
    assert [t["status"] for t in report["tests"]] == ["pass", "fail"]
    assert report["tests"][0]["log"][0] == "\n▶ Sign in" and len(report["model_calls"]) == 2
    junit = ET.parse(stamp / "junit.xml").getroot().find("testsuite")
    assert (junit.attrib["name"], junit.attrib["failures"]) == ("jevtest.android.emulator-5554", "1")
    assert len(json.loads((project / "t.lock.json").read_text())["decisions"]) == 2


def test_second_run_replays_lockfile_without_jev(project, fakes, capsys):
    fakes.script(act("done"), yes(0.95))
    fakes.run("--test", "Sign in")
    capsys.readouterr()
    clients = fakes.script()  # no answers: any live call would fail
    before = len(clients)
    assert fakes.run("--test", "Sign in", lock="frozen") == 0
    assert "2 from lockfile, 0 asked live" in capsys.readouterr().out
    assert len(fakes.clients) == before  # Jev was never connected to


def test_the_files_model_asks_jev_and_is_reported(tmp_path, monkeypatch, fakes, capsys):
    spec_file(tmp_path, tests="  - {name: T, fresh: true, steps: [{expect: Home is showing}]}\n",
              extra="settings: {model: typesafe/jev-2}\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    fakes.script(yes(0.95))
    assert fakes.run() == 0
    assert fakes.models == ["typesafe/jev-2"] and "· typesafe/jev-2 ·" in capsys.readouterr().out
    report = json.loads((stamp_of(tmp_path) / "android" / "emulator-5554" / "report.json").read_text())
    assert report["model"] == "typesafe/jev-2"


def test_frozen_fails_on_unrecorded_screen(project, fakes, capsys):
    assert fakes.run("--test", "Sign in", lock="frozen") == 1
    assert "not in t.lock.json, and --lock frozen only replays" in capsys.readouterr().out


def test_lock_off_leaves_no_file(project, fakes):
    fakes.script(act("done"), yes(0.95))
    fakes.run("--test", "Sign in", lock="off")
    assert not (project / "t.lock.json").exists()


def test_all_pass_exits_zero(project, fakes, capsys):
    fakes.script(act("done"), yes(0.95))
    assert fakes.run("--test", "Sign in", "-v") == 0
    out = capsys.readouterr().out
    assert "jev action: done" in out and f"jevtest {__version__} · android · emulator-5554" in out


@pytest.mark.parametrize(("args", "message"), [
    (["run", "t.yaml", "--out", "res"], "the following arguments are required: --lock"),
    (["run", "t.yaml", "--lock", "record"], "the following arguments are required: --out"),
    (["run", "t.yaml", "--out", "res", "--lock", "maybe"], "invalid choice: 'maybe'"),
])
def test_lock_and_out_must_be_given(project, capsys, args, message):
    with pytest.raises(SystemExit) as e:
        cli.main(args)
    assert e.value.code == 2 and message in capsys.readouterr().err


def test_the_file_chooses_platforms_and_devices(tmp_path, monkeypatch, fakes, capsys):
    (tmp_path / "A.app").mkdir()
    spec_file(tmp_path, app="app: {android: a.apk, ios: A.app}\n",
              device="device: {android: emulator-5554, ios: iPhone 17 Pro}\n")
    monkeypatch.chdir(tmp_path)
    asked = []

    def make_device(platform, device, app, progress):
        asked.append((platform, device, app))
        return FakeDevice()

    fakes.make_device = make_device
    assert fakes.run() == 0
    assert sorted(asked) == [(Platform.ANDROID, "emulator-5554", tmp_path / "a.apk"),
                             (Platform.IOS, "iPhone 17 Pro", tmp_path / "A.app")]
    assert sorted(p.name for p in stamp_of(tmp_path).iterdir()) == ["android", "ios", "junit.xml"]
    out = capsys.readouterr().out
    assert "[android · emulator-5554] 1/1 passed" in out and "[ios · iPhone 17 Pro] 1/1 passed" in out
    assert "All: 2/2 passed (1 file(s), 2 device run(s))" in out


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
    monkeypatch.delenv("OPENROUTER_API_KEY")
    assert fakes.run("--test", "Sign in") == 1  # the test fails, saying why
    assert "OPENROUTER_API_KEY is not set: put it in the .env next to the test file" in capsys.readouterr().out


def test_device_closed_and_lock_saved_even_on_crash(project, fakes):
    class Boom(FakeDevice):
        def install(self, app):
            raise DeviceError("install failed")

    boom = Boom()
    fakes.make_device = lambda *_: boom
    assert fakes.run() == 2
    assert boom.closed


def test_interrupt_exits_130(project, fakes):
    def interrupted(*_):
        raise KeyboardInterrupt

    fakes.make_device = interrupted
    assert fakes.run() == 130


def test_only_the_env_next_to_the_test_file_is_read(tmp_path, monkeypatch, fakes):
    spec_file(tmp_path / "suite", tests="  - {name: T, fresh: true, steps: [{type: '${SECRET}'}]}\n")
    (tmp_path / ".env").write_text("SECRET=from-the-folder-you-run-in\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SECRET", raising=False)
    assert fakes.run(files=("suite/t.yaml",)) == 2  # not set: the .env in the current folder is not used


# --- folders -----------------------------------------------------------------------------------------

def test_running_a_folder(tmp_path, monkeypatch, fakes, capsys):
    spec_file(tmp_path, "suite/login.yaml", tests="  - {name: Login, fresh: true, steps: [{use: Home}]}\n",
              extra="include: shared/nav.yaml\n")
    (tmp_path / "suite" / "shared").mkdir()
    (tmp_path / "suite" / "shared" / "nav.yaml").write_text("tests: [{name: Home, fresh: true, steps: [home]}]\n")
    spec_file(tmp_path, "suite/cart/checkout.yaml",
              tests="  - {name: Pay, fresh: true, steps: [{see: Nope, timeout: 1}]}\n")
    monkeypatch.chdir(tmp_path)
    assert fakes.run(files=("suite",)) == 1
    out = capsys.readouterr().out
    assert "=== checkout.yaml ===" in out and "=== login.yaml ===" in out
    assert "All: 1/2 passed (2 file(s), 2 device run(s))" in out
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
        (tmp_path / name / ".env").write_text(f"SECRET={secret}\nOPENROUTER_API_KEY=key-{name}\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SECRET", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
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
def test_a_device_that_fails_to_start_is_reported_after_the_others(tmp_path, monkeypatch, fakes, capsys, error,
                                                                   code):
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
    assert "t.lock.json: pruned 1 unused decision(s)" in capsys.readouterr().out
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
    assert cli.make_device(Platform.ANDROID, "x", Path("a.apk"), print) == ("android", "x")
    assert cli.make_device(Platform.IOS, "BH", Path("R.app"), print) == ("ios", "BH", Path("R.app"))


def test_make_client_reports_retries_on_stderr(capsys):
    client = cli.make_client("typesafe/jev-1.13", "k")
    assert client.api_key == "k" and client.model == "typesafe/jev-1.13"
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
