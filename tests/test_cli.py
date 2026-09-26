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

    def make_driver(platform, device):
        drivers.append(FakeDriver())
        return drivers[-1]
    monkeypatch.setattr(cli, "make_driver", make_driver)
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    return tmp_path, drivers


def fake_jev(monkeypatch, *answers):
    made = []

    def factory(model):
        made.append(FakeJev(*answers, model=model))
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
    junit = ET.parse(run_dir / "junit.xml").getroot().find("testsuite")
    assert junit.attrib["failures"] == "1"
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
    assert (stamp / "android" / "junit.xml").exists()


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

    def make_driver(platform, device):
        asked.append((platform, device))
        return FakeDriver()
    monkeypatch.setattr(cli, "make_driver", make_driver)
    fake_jev(monkeypatch)
    assert run_cli() == 0
    assert asked == [("android", None), ("ios", "iPhone 17 Pro")]  # no device named: the running one
    [stamp] = (tmp_path / "res").iterdir()
    assert sorted(p.name for p in stamp.iterdir()) == ["android", "ios"]
    assert capsys.readouterr().out.count("1/1 passed") == 2


def test_a_failure_on_any_platform_fails_the_run(tmp_path, monkeypatch):
    (tmp_path / "a.apk").write_text("")
    app = tmp_path / "A.app"
    app.mkdir()
    (tmp_path / "t.yaml").write_text("app: {android: a.apk, ios: A.app}\nsettings: {settle: 0, timeout: 0}\n"
                                     "tests:\n  - name: T\n    steps: [{see: Welcome}]\n")
    monkeypatch.chdir(tmp_path)
    screens = iter([screen_with("Welcome"), screen_with("Nope")])
    monkeypatch.setattr(cli, "make_driver", lambda platform, device: FakeDriver(next(screens)))
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
    monkeypatch.setattr(cli, "make_driver", lambda p, d: boom)
    assert run_cli() == 2
    assert boom.closed


def test_interrupt_exits_130(project, monkeypatch):
    def interrupted(p, d):
        raise KeyboardInterrupt
    monkeypatch.setattr(cli, "make_driver", interrupted)
    fake_jev(monkeypatch)
    assert run_cli() == 130


def test_summary_with_no_time(tmp_path):
    text = cli.summary({"passed": 0, "failed": 0, "tests": []}, [], tmp_path)
    assert "0/0 passed in 0s" in text and "of run time" not in text


# --- .env -------------------------------------------------------------------------------------------

def test_load_env(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("# comment\n\nexport A_KEY='one'\nB_KEY=\"two\"\nnot a pair\nC_KEY=x=y\n")
    monkeypatch.delenv("A_KEY", raising=False)
    monkeypatch.delenv("C_KEY", raising=False)
    monkeypatch.setenv("B_KEY", "real")
    cli.load_env(tmp_path, tmp_path / "missing")
    assert (os.environ["A_KEY"], os.environ["B_KEY"], os.environ["C_KEY"]) == ("one", "real", "x=y")
    for k in ("A_KEY", "C_KEY"):
        monkeypatch.delenv(k)


# --- other commands ---------------------------------------------------------------------------------









def test_make_driver_picks_platform(monkeypatch):
    from jevtest.drivers import android, ios
    monkeypatch.setattr(android, "AndroidDriver", lambda d: ("android", d))
    monkeypatch.setattr(ios, "IOSDriver", lambda d: ("ios", d))
    assert cli.make_driver("android", "x") == ("android", "x")
    assert cli.make_driver("ios", None) == ("ios", None)


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
