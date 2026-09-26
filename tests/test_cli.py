import json
import os
import xml.etree.ElementTree as ET

import pytest

from jevtest import __version__, cli
from jevtest.drivers.base import DriverError

from .conftest import FakeDriver, FakeJev, act, screen_with, yes

SPEC = """app: app.apk
settings: {settle: 0, timeout: 0}
tests:
  - name: Sign in
    steps:
      - do: Press sign in
        expect: Home is showing
  - name: Broken
    steps:
      - see: Nothing like this
"""


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / "app.apk").write_text("")
    (tmp_path / "t.yaml").write_text(SPEC)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    drivers = []

    def make_driver(platform, device, ios_team=""):
        drivers.append(FakeDriver())
        return drivers[-1]
    monkeypatch.setattr(cli, "make_driver", make_driver)
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    return tmp_path, drivers


def fake_jev(monkeypatch, *answers):
    made = []

    def factory(model, api_key=None):
        made.append(FakeJev(*answers, model=model))
        made[-1].api_key = api_key
        return made[-1]
    monkeypatch.setattr(cli, "Jev", factory)
    return made


def run_cli(*args):
    return cli.main(["run", "t.yaml", "--out", "res", *args])


def test_run_writes_report_junit_and_lockfile(project, monkeypatch, capsys):
    tmp, drivers = project
    made = fake_jev(monkeypatch, act("done"), yes(0.95))
    assert run_cli() == 1  # "Broken" fails
    out = capsys.readouterr().out
    assert "1/2 passed" in out and "FAILED Broken: see: Nothing like this — not on screen" in out
    assert "Jev: 2 decisions, 0 from lockfile, 2 asked live" in out and "$0.0002" in out
    assert drivers[0].calls[0][0] == "install" and made[0].model == "typesafe/jev-1.13"
    assert (drivers[0].settle, drivers[0].timeout) == (0.0, 0.0)  # the test file's settings reach the driver

    [stamp] = (tmp / "res").iterdir()
    run_dir = stamp / "android"
    report = json.loads((run_dir / "report.json").read_text())
    assert report["platform"] == "android" and report["app_id"] == "dev.fake"
    assert [t["status"] for t in report["tests"]] == ["pass", "fail"]
    junit = ET.parse(stamp / "junit.xml").getroot().find("testsuite")
    assert (junit.attrib["name"], junit.attrib["failures"]) == ("jevtest.android", "1")
    assert made[0].api_key == "test-key"
    lock = json.loads((tmp / "t.lock.json").read_text())
    assert len(lock["decisions"]) == 2


def test_second_run_replays_lockfile_without_jev(project, monkeypatch, capsys):
    fake_jev(monkeypatch, act("done"), yes(0.95))
    run_cli("--test", "Sign in")
    capsys.readouterr()
    made = fake_jev(monkeypatch)  # no answers: any live call would fail
    monkeypatch.delenv("OPENROUTER_API_KEY")
    assert run_cli("--test", "Sign in", "--frozen") == 0
    assert "2 from lockfile, 0 asked live" in capsys.readouterr().out
    assert not made


def test_frozen_fails_on_unrecorded_screen(project, monkeypatch, capsys):
    fake_jev(monkeypatch)
    assert run_cli("--test", "Sign in", "--frozen") == 1
    assert "not in t.lock.json and --frozen is set" in capsys.readouterr().out


def test_no_lock_leaves_no_file(project, monkeypatch):
    tmp, _ = project
    fake_jev(monkeypatch, act("done"), yes(0.95))
    run_cli("--test", "Sign in", "--no-lock")
    assert not (tmp / "t.lock.json").exists()


def test_all_pass_exits_zero(project, monkeypatch, capsys):
    tmp, _ = project
    fake_jev(monkeypatch, act("done"), yes(0.95))
    assert run_cli("--test", "Sign in", "-v") == 0
    out = capsys.readouterr().out
    assert "jev action: done" in out and f"jevtest {__version__} · android" in out
    [stamp] = (tmp / "res").iterdir()
    assert (stamp / "junit.xml").exists() and (stamp / "android" / "report.json").exists()


def test_the_file_chooses_platforms_and_devices(tmp_path, monkeypatch, capsys):
    (tmp_path / "a.apk").write_text("")
    app = tmp_path / "A.app"
    app.mkdir()
    (tmp_path / "t.yaml").write_text("app: {android: a.apk, ios: A.app}\n"
                                     "device: {ios: iPhone 17 Pro}\n"
                                     "settings: {settle: 0}\n"
                                     "tests:\n  - name: T\n    steps: [back]\n")
    monkeypatch.chdir(tmp_path)
    asked = []

    def make_driver(platform, device, ios_team=""):
        asked.append((platform, device))
        return FakeDriver()
    monkeypatch.setattr(cli, "make_driver", make_driver)
    fake_jev(monkeypatch)
    assert run_cli() == 0
    assert asked == [("android", None), ("ios", "iPhone 17 Pro")]  # no device named: the running one
    [stamp] = (tmp_path / "res").iterdir()
    assert sorted(p.name for p in stamp.iterdir()) == ["android", "ios", "junit.xml"]
    out = capsys.readouterr().out
    assert "[android] 1/1 passed" in out and "[ios · iPhone 17 Pro] 1/1 passed" in out
    assert "All: 2/2 passed (1 file(s), 2 device run(s))" in out


def test_a_failure_on_any_platform_fails_the_run(tmp_path, monkeypatch):
    (tmp_path / "a.apk").write_text("")
    app = tmp_path / "A.app"
    app.mkdir()
    (tmp_path / "t.yaml").write_text("app: {android: a.apk, ios: A.app}\nsettings: {settle: 0, timeout: 0}\n"
                                     "tests:\n  - name: T\n    steps: [{see: Welcome}]\n")
    monkeypatch.chdir(tmp_path)
    screens = iter([screen_with("Welcome"), screen_with("Nope")])
    monkeypatch.setattr(cli, "make_driver", lambda platform, device, ios_team="": FakeDriver(next(screens)))
    fake_jev(monkeypatch)
    assert run_cli() == 1


def test_unknown_test_name(project, capsys):
    assert run_cli("--test", "Sign in", "--test", "Nope") == 2
    assert "No test named: Nope" in capsys.readouterr().err


def test_conflicting_lock_flags(project, capsys):
    assert run_cli("--frozen", "--no-lock") == 2
    assert "only one of" in capsys.readouterr().err


def test_missing_app(project, capsys):
    tmp, _ = project
    (tmp / "app.apk").unlink()
    assert run_cli() == 2
    assert "App not found" in capsys.readouterr().err


def test_driver_closed_and_lock_saved_even_on_crash(project, monkeypatch):
    tmp, drivers = project
    fake_jev(monkeypatch, act("done"))

    class Boom(FakeDriver):
        def close(self):
            self.closed = True

        def install(self, app_path):
            raise DriverError("install failed")
    boom = Boom()
    monkeypatch.setattr(cli, "make_driver", lambda p, d, t="": boom)
    assert run_cli() == 2
    assert boom.closed


def test_interrupt_exits_130(project, monkeypatch):
    def interrupted(p, d, t=""):
        raise KeyboardInterrupt
    monkeypatch.setattr(cli, "make_driver", interrupted)
    fake_jev(monkeypatch)
    assert run_cli() == 130


def test_summary_with_no_time(tmp_path):
    text = cli.summary({"passed": 0, "failed": 0, "tests": []}, [], tmp_path)
    assert "0/0 passed in 0s" in text and "of run time" not in text


# --- .env -------------------------------------------------------------------------------------------

def test_read_env(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("# comment\n\nexport A_KEY='one'\nB_KEY=\"two\"\nnot a pair\nC_KEY=x=y\n")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / ".env").write_text("C_KEY=closer\n")
    monkeypatch.delenv("A_KEY", raising=False)
    monkeypatch.delenv("C_KEY", raising=False)
    monkeypatch.setenv("B_KEY", "real")
    env = cli.read_env(tmp_path, tmp_path / "missing", tmp_path / "sub")
    assert (env["A_KEY"], env["B_KEY"], env["C_KEY"]) == ("one", "real", "closer")  # the environment wins
    assert "A_KEY" not in os.environ  # nothing leaks into the process: each file gets its own values


# --- other commands ---------------------------------------------------------------------------------









def test_make_driver_picks_platform(monkeypatch):
    from jevtest.drivers import android, ios
    monkeypatch.setattr(android, "AndroidDriver", lambda d: ("android", d))
    monkeypatch.setattr(ios, "IOSDriver", lambda d, team="": ("ios", d, team))
    assert cli.make_driver("android", "x") == ("android", "x")
    assert cli.make_driver("ios", "BH", "TEAM1") == ("ios", "BH", "TEAM1")


def test_version(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--version"])
    assert __version__ in capsys.readouterr().out


def test_module_entry_point(monkeypatch):
    import runpy
    monkeypatch.setattr("sys.argv", ["jevtest", "run", "missing.yaml"])
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_module("jevtest", run_name="__main__")
    assert exit_info.value.code == 2


# --- folders -----------------------------------------------------------------------------------------

def suite(tmp_path, rel, tests="  - {name: T, steps: [back]}\n", app="a.apk", extra=""):
    f = tmp_path / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    (f.parent / app).write_text("")
    f.write_text(f"app: {app}\nsettings: {{settle: 0}}\n{extra}tests:\n{tests}")
    return f


def test_test_files_finds_every_test_file_in_a_folder(tmp_path):
    a = suite(tmp_path, "suite/login.yaml")
    b = suite(tmp_path, "suite/deep/cart.yml")
    (tmp_path / "suite" / "shared.yaml").write_text("tests: [{name: S, steps: [back]}]\n")  # a library
    suite(tmp_path, "suite/.hidden/x.yaml")
    (tmp_path / "suite" / "notes.txt").write_text("")
    assert cli.test_files([str(tmp_path / "suite")]) == [b.resolve(), a.resolve()]
    assert cli.test_files([str(a), str(tmp_path / "suite")]) == [a.resolve(), b.resolve()]  # each once


def test_test_files_errors(tmp_path):
    with pytest.raises(cli.SpecError, match="Test file not found"):
        cli.test_files([str(tmp_path / "nope.yaml")])
    with pytest.raises(cli.SpecError, match="No test files"):
        cli.test_files([str(tmp_path)])


def test_running_a_folder(tmp_path, monkeypatch, capsys):
    suite(tmp_path, "suite/login.yaml", tests="  - {name: Login, steps: [back]}\n")
    suite(tmp_path, "suite/cart/checkout.yaml", tests="  - {name: Pay, steps: [{see: Nope, timeout: 0}]}\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "make_driver", lambda p, d, t="": FakeDriver())
    fake_jev(monkeypatch)
    assert cli.main(["run", "suite", "--out", "res"]) == 1
    out = capsys.readouterr().out
    assert "=== checkout.yaml ===" in out and "=== login.yaml ===" in out
    assert "All: 1/2 passed (2 file(s), 2 device run(s))" in out
    [stamp] = (tmp_path / "res").iterdir()
    assert (stamp / "login" / "android" / "report.json").exists()
    assert (stamp / "cart" / "checkout" / "android" / "report.json").exists()
    names = [s.attrib["name"] for s in ET.parse(stamp / "junit.xml").getroot()]
    assert names == ["jevtest.cart/checkout.android", "jevtest.login.android"]
    assert (tmp_path / "suite" / "login.lock.json").exists() is False  # nothing asked, nothing recorded


def test_test_filter_spans_files(tmp_path, monkeypatch, capsys):
    suite(tmp_path, "s/a.yaml", tests="  - {name: A, steps: [back]}\n")
    suite(tmp_path, "s/b.yaml", tests="  - {name: B, steps: [back]}\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "make_driver", lambda p, d, t="": FakeDriver())
    fake_jev(monkeypatch)
    assert cli.main(["run", "s", "--out", "res", "--test", "B"]) == 0
    out = capsys.readouterr().out
    assert "▶ B" in out and "▶ A" not in out and "=== a.yaml ===" not in out


def test_each_file_gets_its_own_env(tmp_path, monkeypatch, capsys):
    for name, secret in (("one", "first"), ("two", "second")):
        suite(tmp_path, f"{name}/t.yaml", tests="  - {name: T" + name + ", steps: [{type: '${SECRET}'}]}\n")
        (tmp_path / name / ".env").write_text(f"SECRET={secret}\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SECRET", raising=False)
    drivers = []
    monkeypatch.setattr(cli, "make_driver", lambda p, d, t="": drivers.append(FakeDriver()) or drivers[-1])
    fake_jev(monkeypatch)
    assert cli.main(["run", "one", "two", "--out", "res"]) == 0
    assert [c[1] for d in drivers for c in d.calls if c[0] == "type_text"] == ["first", "second"]
    assert "first" not in capsys.readouterr().out


# --- several devices ------------------------------------------------------------------------------------

def make_tests(*specs):
    from jevtest.spec import Test
    return [Test(name, [], fresh=not name.endswith("+")) for name in specs]


@pytest.mark.parametrize("names,n,shards", [
    (["A", "B", "C"], 2, [["A", "C"], ["B"]]),
    (["A", "B+", "C", "D+", "E+"], 2, [["A", "B+"], ["C", "D+", "E+"]]),  # a chain stays on one device
    (["A+", "B"], 3, [["A+"], ["B"], []]),  # the first test always starts a group
])
def test_shard(names, n, shards):
    assert [[t.name for t in s] for s in cli.shard(make_tests(*names), n)] == shards


def test_tests_are_split_across_devices_and_run_at_once(tmp_path, monkeypatch, capsys):
    tests = "".join(f"  - {{name: T{i}, steps: [back]}}\n" for i in range(3))
    f = suite(tmp_path, "t.yaml", tests=tests, extra="device: {android: [Pixel 4a, Pixel 8, Pixel 9, Pixel 10]}\n")
    monkeypatch.chdir(tmp_path)
    ran, threads = {}, set()

    def make_driver(platform, device, ios_team=""):
        import threading
        threads.add(threading.get_ident())
        d = FakeDriver()
        ran[device] = d
        return d
    monkeypatch.setattr(cli, "make_driver", make_driver)
    fake_jev(monkeypatch)
    assert cli.main(["run", str(f), "--out", "res"]) == 0
    assert sorted(ran) == ["Pixel 4a", "Pixel 8", "Pixel 9"]  # three tests: the fourth phone has nothing to do
    assert len(threads) == 3
    out = capsys.readouterr().out
    for i, phone in enumerate(["Pixel 4a", "Pixel 8", "Pixel 9"]):
        block = f"[android · {phone}] ▶ T{i}\n[android · {phone}]   ✓ back"
        assert block in out  # each test's log is printed whole, tagged with its device
    [stamp] = (tmp_path / "res").iterdir()
    assert sorted(p.name for p in (stamp / "android").iterdir()) == ["Pixel_4a", "Pixel_8", "Pixel_9"]


def test_a_named_device_gets_its_own_folder(tmp_path, monkeypatch):
    f = suite(tmp_path, "t.yaml", extra="device: {android: Pixel 4a}\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "make_driver", lambda p, d, t="": FakeDriver())
    fake_jev(monkeypatch)
    cli.main(["run", str(f), "--out", "res"])
    [stamp] = (tmp_path / "res").iterdir()
    assert (stamp / "android" / "Pixel_4a" / "report.json").exists()


@pytest.mark.parametrize("error,code", [(DriverError("no such phone"), 2), (RuntimeError("bug"), None)])
def test_a_device_that_fails_to_start_is_reported_after_the_others(tmp_path, monkeypatch, capsys, error, code):
    f = suite(tmp_path, "t.yaml", tests="  - {name: A, steps: [back]}\n  - {name: B, steps: [back]}\n",
              extra="device: {android: [Good, Bad]}\n")
    monkeypatch.chdir(tmp_path)
    good = FakeDriver()

    def make_driver(platform, device, ios_team=""):
        if device == "Bad":
            raise error
        return good
    monkeypatch.setattr(cli, "make_driver", make_driver)
    fake_jev(monkeypatch)
    if code is None:
        with pytest.raises(RuntimeError, match="bug"):
            cli.main(["run", str(f), "--out", "res"])
    else:
        assert cli.main(["run", str(f), "--out", "res"]) == code
        assert "error: android · Bad: no such phone" in capsys.readouterr().err
    assert "back" in good.names()  # the good phone still ran its test


def test_slug():
    assert cli.slug("iPhone 17 Pro (2)") == "iPhone_17_Pro_2" and cli.slug("///") == "device"
