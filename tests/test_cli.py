import json
import os
import threading
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from jevtest import __version__, cli
from jevtest.drivers.base import DriverError
from jevtest.spec import SpecError, Test

from .conftest import FakeDriver, FakeJev, act, screen_with, yes

TWO_TESTS = """  - name: Sign in
    fresh: true
    steps:
      - do: Press sign in
        expect: Home is showing
  - name: Broken
    fresh: true
    steps:
      - see: Nothing like this
        timeout: 0
"""


def spec_file(folder, rel="t.yaml", tests="  - {name: T, fresh: true, steps: [back]}\n",
              device="device: {android: emulator-5554}\n", app="app: a.apk\n", extra=""):
    f = folder / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    (f.parent / "a.apk").write_text("")
    f.write_text(f"{app}{device}{extra}tests:\n{tests}")
    return f


@pytest.fixture
def project(tmp_path, monkeypatch):
    spec_file(tmp_path, tests=TWO_TESTS)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    drivers = []

    def make_driver(platform, device, app):
        drivers.append(FakeDriver())
        return drivers[-1]
    monkeypatch.setattr(cli, "make_driver", make_driver)
    return tmp_path, drivers


def fake_jev(monkeypatch, *answers):
    made = []

    def factory(model, api_key):
        made.append(FakeJev(*answers, model=model))
        made[-1].api_key = api_key
        return made[-1]
    monkeypatch.setattr(cli, "Jev", factory)
    return made


def fake_drivers(monkeypatch, make=FakeDriver):
    monkeypatch.setattr(cli, "make_driver", lambda platform, device, app: make())


def run_cli(*args, files=("t.yaml",), lock="record"):
    return cli.main(["run", *files, "--out", "res", "--lock", lock, *args])


def stamp_of(tmp_path):
    [stamp] = (tmp_path / "res").iterdir()
    return stamp


# --- one file ---------------------------------------------------------------------------------------

def test_run_writes_report_junit_and_lockfile(project, monkeypatch, capsys):
    tmp, drivers = project
    made = fake_jev(monkeypatch, act("done"), yes(0.95))
    assert run_cli() == 1  # "Broken" fails
    out = capsys.readouterr().out
    assert "1/2 passed" in out and "FAILED Broken: see: Nothing like this — not on screen" in out
    assert "Jev: 2 decisions, 0 from lockfile, 2 asked live" in out and "$0.0002" in out
    assert drivers[0].calls[0][0] == "install" and made[0].model == "typesafe/jev-1.13"
    assert made[0].api_key == "test-key"
    assert (drivers[0].settle, drivers[0].timeout) == (3.0, 10.0)  # jevtest's fixed rules reach the driver

    stamp = stamp_of(tmp)
    report = json.loads((stamp / "android" / "emulator-5554" / "report.json").read_text())
    assert (report["platform"], report["device"], report["app_id"]) == ("android", "emulator-5554", "dev.fake")
    assert [t["status"] for t in report["tests"]] == ["pass", "fail"]
    junit = ET.parse(stamp / "junit.xml").getroot().find("testsuite")
    assert (junit.attrib["name"], junit.attrib["failures"]) == ("jevtest.android.emulator-5554", "1")
    assert len(json.loads((tmp / "t.lock.json").read_text())["decisions"]) == 2


def test_second_run_replays_lockfile_without_jev(project, monkeypatch, capsys):
    fake_jev(monkeypatch, act("done"), yes(0.95))
    run_cli("--test", "Sign in")
    capsys.readouterr()
    made = fake_jev(monkeypatch)  # no answers: any live call would fail
    assert run_cli("--test", "Sign in", lock="frozen") == 0
    assert "2 from lockfile, 0 asked live" in capsys.readouterr().out
    assert not made


def test_frozen_fails_on_unrecorded_screen(project, monkeypatch, capsys):
    fake_jev(monkeypatch)
    assert run_cli("--test", "Sign in", lock="frozen") == 1
    assert "not in t.lock.json, and --lock frozen only replays" in capsys.readouterr().out


def test_lock_off_leaves_no_file(project, monkeypatch):
    tmp, _ = project
    fake_jev(monkeypatch, act("done"), yes(0.95))
    run_cli("--test", "Sign in", lock="off")
    assert not (tmp / "t.lock.json").exists()


def test_all_pass_exits_zero(project, monkeypatch, capsys):
    fake_jev(monkeypatch, act("done"), yes(0.95))
    assert run_cli("--test", "Sign in", "-v") == 0
    out = capsys.readouterr().out
    assert "jev action: done" in out and f"jevtest {__version__} · android · emulator-5554" in out


@pytest.mark.parametrize("args,message", [
    (["run", "t.yaml", "--out", "res"], "the following arguments are required: --lock"),
    (["run", "t.yaml", "--lock", "record"], "the following arguments are required: --out"),
    (["run", "t.yaml", "--out", "res", "--lock", "maybe"], "invalid choice: 'maybe'"),
])
def test_lock_and_out_must_be_given(project, capsys, args, message):
    with pytest.raises(SystemExit) as e:
        cli.main(args)
    assert e.value.code == 2 and message in capsys.readouterr().err


def test_the_file_chooses_platforms_and_devices(tmp_path, monkeypatch, capsys):
    (tmp_path / "A.app").mkdir()
    spec_file(tmp_path, app="app: {android: a.apk, ios: A.app}\n",
              device="device: {android: emulator-5554, ios: iPhone 17 Pro}\n")
    monkeypatch.chdir(tmp_path)
    asked = []

    def make_driver(platform, device, app):
        asked.append((platform, device, app))
        return FakeDriver()
    monkeypatch.setattr(cli, "make_driver", make_driver)
    fake_jev(monkeypatch)
    assert run_cli() == 0
    assert sorted(asked) == [("android", "emulator-5554", tmp_path / "a.apk"),
                             ("ios", "iPhone 17 Pro", tmp_path / "A.app")]
    assert sorted(p.name for p in stamp_of(tmp_path).iterdir()) == ["android", "ios", "junit.xml"]
    out = capsys.readouterr().out
    assert "[android · emulator-5554] 1/1 passed" in out and "[ios · iPhone 17 Pro] 1/1 passed" in out
    assert "All: 2/2 passed (1 file(s), 2 device run(s))" in out


def test_a_failure_on_any_device_fails_the_run(tmp_path, monkeypatch):
    test = "  - {name: %s, fresh: true, steps: [{see: Welcome, timeout: 0}]}\n"
    spec_file(tmp_path, tests=test % "T" + test % "U", device="device: {android: [A, B]}\n")
    monkeypatch.chdir(tmp_path)
    screens = iter([screen_with("Welcome"), screen_with("Nope")])
    lock = threading.Lock()

    def make():
        with lock:
            return FakeDriver(next(screens))
    fake_drivers(monkeypatch, make)
    fake_jev(monkeypatch)
    assert run_cli() == 1


def test_unknown_test_name(project, capsys):
    assert run_cli("--test", "Sign in", "--test", "Nope") == 2
    assert "No test named: Nope" in capsys.readouterr().err


def test_missing_app(project, capsys):
    tmp, _ = project
    (tmp / "a.apk").unlink()
    assert run_cli() == 2
    assert "App not found" in capsys.readouterr().err


def test_missing_api_key_is_an_error(project, monkeypatch, capsys):
    monkeypatch.delenv("OPENROUTER_API_KEY")
    assert run_cli("--test", "Sign in") == 1  # the test fails, saying why
    assert "OPENROUTER_API_KEY is not set: put it in the .env next to the test file" in capsys.readouterr().out


def test_driver_closed_and_lock_saved_even_on_crash(project, monkeypatch):
    fake_jev(monkeypatch, act("done"))

    class Boom(FakeDriver):
        def close(self):
            self.closed = True

        def install(self, app_path):
            raise DriverError("install failed")
    boom = Boom()
    monkeypatch.setattr(cli, "make_driver", lambda p, d, a: boom)
    assert run_cli() == 2
    assert boom.closed


def test_interrupt_exits_130(project, monkeypatch):
    def interrupted(p, d, a):
        raise KeyboardInterrupt
    monkeypatch.setattr(cli, "make_driver", interrupted)
    fake_jev(monkeypatch)
    assert run_cli() == 130


def test_summary_with_no_time(tmp_path):
    text = cli.summary({"passed": 0, "failed": 0, "tests": []}, [], tmp_path)
    assert "0/0 passed in 0s" in text and "of run time" not in text


# --- .env -------------------------------------------------------------------------------------------

def test_read_env(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("# comment\n\nexport A_KEY='one'\nB_KEY=\"two\"\nC_KEY=x=y\nD_KEY='a\"\n"
                                   "  # indented comment\nSAME=1\n")
    for k in ("A_KEY", "B_KEY", "C_KEY", "D_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SAME", "1")  # set in both, to the same value: fine
    env = cli.read_env(tmp_path)
    assert (env["A_KEY"], env["B_KEY"], env["C_KEY"], env["D_KEY"]) == ("one", "two", "x=y", "'a\"")
    assert env["SAME"] == "1" and env["PATH"] == os.environ["PATH"]
    assert "A_KEY" not in os.environ  # nothing leaks into the process: each file gets its own values


def test_read_env_without_a_file(tmp_path):
    assert cli.read_env(tmp_path) == dict(os.environ)


@pytest.mark.parametrize("body,message", [
    ("A=1\nnot a pair\n", r"\.env:2 is not a KEY=value line"),
    ("A 1\n", r"\.env:1 is not a KEY=value line"),
    ("A=1\nA=2\n", r"\.env:2 sets A a second time"),
    ("JEVTEST_T_KEY=file\n", "JEVTEST_T_KEY is set in the environment and in .*/.env to different values"),
])
def test_bad_env_files(tmp_path, monkeypatch, body, message):
    monkeypatch.setenv("JEVTEST_T_KEY", "env")
    (tmp_path / ".env").write_text(body)
    with pytest.raises(SpecError, match=message):
        cli.read_env(tmp_path)


def test_only_the_env_next_to_the_test_file_is_read(tmp_path, monkeypatch):
    spec_file(tmp_path / "suite", tests="  - {name: T, fresh: true, steps: [{type: '${SECRET}'}]}\n")
    (tmp_path / ".env").write_text("SECRET=from-the-folder-you-run-in\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SECRET", raising=False)
    fake_drivers(monkeypatch)
    fake_jev(monkeypatch)
    assert run_cli(files=("suite/t.yaml",)) == 2  # not set: the .env in the current folder is not used


# --- folders -----------------------------------------------------------------------------------------

def test_test_files_finds_every_yaml_in_a_folder(tmp_path):
    a = spec_file(tmp_path, "suite/login.yaml")
    b = spec_file(tmp_path, "suite/deep/cart.yml")
    c = spec_file(tmp_path, "suite/.hidden/x.yaml")  # hidden folders are not skipped
    lib = tmp_path / "suite" / "shared.yaml"
    lib.write_text("tests: [{name: S, fresh: true, steps: [back]}]\n")
    (tmp_path / "suite" / "notes.txt").write_text("")
    files, others = cli.test_files([str(tmp_path / "suite")])
    assert files == [c.resolve(), b.resolve(), a.resolve()] and others == [lib.resolve()]
    assert cli.test_files([str(a), str(tmp_path / "suite")])[0][0] == a.resolve()  # each once, as given first


def test_test_files_errors(tmp_path):
    with pytest.raises(SpecError, match="Test file not found"):
        cli.test_files([str(tmp_path / "nope.yaml")])
    with pytest.raises(SpecError, match="No test files"):
        cli.test_files([str(tmp_path)])


def test_running_a_folder(tmp_path, monkeypatch, capsys):
    spec_file(tmp_path, "suite/login.yaml", tests="  - {name: Login, fresh: true, steps: [{use: Home}]}\n",
              extra="include: shared/nav.yaml\n")
    (tmp_path / "suite" / "shared").mkdir()
    (tmp_path / "suite" / "shared" / "nav.yaml").write_text("tests: [{name: Home, fresh: true, steps: [home]}]\n")
    spec_file(tmp_path, "suite/cart/checkout.yaml",
              tests="  - {name: Pay, fresh: true, steps: [{see: Nope, timeout: 0}]}\n")
    monkeypatch.chdir(tmp_path)
    fake_drivers(monkeypatch)
    fake_jev(monkeypatch)
    assert run_cli(files=("suite",)) == 1
    out = capsys.readouterr().out
    assert "=== checkout.yaml ===" in out and "=== login.yaml ===" in out
    assert "All: 1/2 passed (2 file(s), 2 device run(s))" in out
    stamp = stamp_of(tmp_path)
    assert (stamp / "login" / "android" / "emulator-5554" / "report.json").exists()
    assert (stamp / "cart" / "checkout" / "android" / "emulator-5554" / "report.json").exists()
    names = [s.attrib["name"] for s in ET.parse(stamp / "junit.xml").getroot()]
    assert names == ["jevtest.cart/checkout.android.emulator-5554", "jevtest.login.android.emulator-5554"]


def test_a_yaml_file_that_is_neither_run_nor_included_is_an_error(tmp_path, monkeypatch, capsys):
    spec_file(tmp_path, "suite/login.yaml")
    (tmp_path / "suite" / "stray.yaml").write_text("tests: [{name: S, fresh: true, steps: [back]}]\n")
    monkeypatch.chdir(tmp_path)
    fake_drivers(monkeypatch)
    assert run_cli(files=("suite",)) == 2
    assert "stray.yaml: no `app:` and not included by any test file" in capsys.readouterr().err


def test_test_filter_spans_files(tmp_path, monkeypatch, capsys):
    spec_file(tmp_path, "s/a.yaml", tests="  - {name: A, fresh: true, steps: [back]}\n")
    spec_file(tmp_path, "s/b.yaml", tests="  - {name: B, fresh: true, steps: [back]}\n")
    monkeypatch.chdir(tmp_path)
    fake_drivers(monkeypatch)
    fake_jev(monkeypatch)
    assert run_cli("--test", "B", files=("s",)) == 0
    out = capsys.readouterr().out
    assert "▶ B" in out and "▶ A" not in out and "=== a.yaml ===" not in out


def test_each_file_gets_its_own_env(tmp_path, monkeypatch, capsys):
    for name, secret in (("one", "first"), ("two", "second")):
        test = "  - {name: T" + name + ", fresh: true, steps: [{type: '${SECRET}'}]}\n"
        spec_file(tmp_path, f"{name}/t.yaml", tests=test)
        (tmp_path / name / ".env").write_text(f"SECRET={secret}\nOPENROUTER_API_KEY=key-{name}\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SECRET", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    drivers = []
    monkeypatch.setattr(cli, "make_driver", lambda p, d, a: drivers.append(FakeDriver()) or drivers[-1])
    fake_jev(monkeypatch)
    assert run_cli(files=("one", "two")) == 0
    assert [c[1] for d in drivers for c in d.calls if c[0] == "type_text"] == ["first", "second"]
    assert "first" not in capsys.readouterr().out


# --- several devices ------------------------------------------------------------------------------------

def make_tests(*names):
    return [Test(name, [], fresh=not name.endswith("+")) for name in names]


@pytest.mark.parametrize("names,n,shards", [
    (["A", "B", "C"], 2, [["A", "C"], ["B"]]),
    (["A", "B+", "C", "D+", "E+"], 2, [["A", "B+"], ["C", "D+", "E+"]]),  # a chain stays on one device
    (["A+", "B"], 3, [["A+"], ["B"], []]),  # the first test always starts a group
])
def test_shard(names, n, shards):
    assert [[t.name for t in s] for s in cli.shard(make_tests(*names), n)] == shards


def test_tests_are_split_across_devices_and_run_at_once(tmp_path, monkeypatch, capsys):
    tests = "".join(f"  - {{name: T{i}, fresh: true, steps: [back]}}\n" for i in range(3))
    f = spec_file(tmp_path, tests=tests, device="device: {android: [Pixel 4a, Pixel 8, Pixel 9, Pixel 10]}\n")
    monkeypatch.chdir(tmp_path)
    ran, threads = {}, set()

    def make_driver(platform, device, app):
        threads.add(threading.get_ident())
        ran[device] = FakeDriver()
        return ran[device]
    monkeypatch.setattr(cli, "make_driver", make_driver)
    fake_jev(monkeypatch)
    assert run_cli(files=(str(f),)) == 0
    assert sorted(ran) == ["Pixel 4a", "Pixel 8", "Pixel 9"] and len(threads) == 3
    out = capsys.readouterr().out
    assert "[android · Pixel 10] no tests left for this device (4 devices, fewer groups of tests)" in out
    for i, phone in enumerate(["Pixel 4a", "Pixel 8", "Pixel 9"]):
        assert f"[android · {phone}] ▶ T{i}\n[android · {phone}]   ✓ back" in out  # each test's log whole
    assert sorted(p.name for p in (stamp_of(tmp_path) / "android").iterdir()) == ["Pixel_4a", "Pixel_8", "Pixel_9"]


@pytest.mark.parametrize("error,code", [(DriverError("no such phone"), 2), (RuntimeError("bug"), None)])
def test_a_device_that_fails_to_start_is_reported_after_the_others(tmp_path, monkeypatch, capsys, error, code):
    tests = "  - {name: A, fresh: true, steps: [back]}\n  - {name: B, fresh: true, steps: [back]}\n"
    f = spec_file(tmp_path, tests=tests, device="device: {android: [Good, Bad]}\n")
    monkeypatch.chdir(tmp_path)
    good = FakeDriver()

    def make_driver(platform, device, app):
        if device == "Bad":
            raise error
        return good
    monkeypatch.setattr(cli, "make_driver", make_driver)
    fake_jev(monkeypatch)
    if code is None:
        with pytest.raises(RuntimeError, match="bug"):
            run_cli(files=(str(f),))
    else:
        assert run_cli(files=(str(f),)) == code
        assert "error: android · Bad: no such phone" in capsys.readouterr().err
    assert "back" in good.names()  # the good phone still ran its test


def test_slug():
    assert cli.slug("iPhone 17 Pro (2)") == "iPhone_17_Pro_2" and cli.slug("///") == "device"


# --- other -------------------------------------------------------------------------------------------

def test_make_driver_picks_platform(monkeypatch):
    from jevtest.drivers import android, ios
    monkeypatch.setattr(android, "AndroidDriver", lambda d: ("android", d))
    monkeypatch.setattr(ios, "IOSDriver", lambda d, app: ("ios", d, app))
    assert cli.make_driver("android", "x", Path("a.apk")) == ("android", "x")
    assert cli.make_driver("ios", "BH", Path("R.app")) == ("ios", "BH", Path("R.app"))


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


# --- pruning the lockfile ----------------------------------------------------------------------------

def test_prune_lock_after_a_passing_run(project, monkeypatch, capsys):
    tmp, _ = project
    fake_jev(monkeypatch, act("done"), yes(0.95))
    run_cli("--test", "Sign in")
    lock = tmp / "t.lock.json"
    data = json.loads(lock.read_text())
    data["decisions"]["stale"] = {"answers": {}}
    lock.write_text(json.dumps(data))
    spec_file(tmp, tests=TWO_TESTS.split("  - name: Broken")[0])  # only the passing test
    fake_jev(monkeypatch)
    assert run_cli("--prune-lock") == 0
    assert "t.lock.json: pruned 1 unused decision(s)" in capsys.readouterr().out
    assert "stale" not in json.loads(lock.read_text())["decisions"]


def test_prune_lock_skipped_when_a_test_failed(project, monkeypatch, capsys):
    fake_jev(monkeypatch, act("done"), yes(0.95))
    assert run_cli("--prune-lock") == 1
    assert "t.lock.json: not pruned, because a test failed" in capsys.readouterr().out


@pytest.mark.parametrize("args", [["--test", "Sign in", "--prune-lock"], ["--prune-lock"]])
def test_prune_lock_needs_a_full_run_with_a_lockfile(project, capsys, args):
    lock = "off" if args == ["--prune-lock"] else "record"
    assert run_cli(*args, lock=lock) == 2
    assert "--prune-lock needs every test to run" in capsys.readouterr().err
