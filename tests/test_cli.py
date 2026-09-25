import json
import os
import xml.etree.ElementTree as ET

import pytest

from jevtest import __version__, cli
from jevtest.drivers.base import DriverError

from .conftest import FakeDriver, FakeJev, act, yes

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

    [run_dir] = (tmp / "res").iterdir()
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


def test_all_pass_exits_zero_and_custom_junit(project, monkeypatch, capsys):
    tmp, _ = project
    fake_jev(monkeypatch, act("done"), yes(0.95))
    assert run_cli("--test", "Sign in", "--junit", "ci/junit.xml", "-v") == 0
    out = capsys.readouterr().out
    assert "jev action: done" in out and f"jevtest {__version__} · android" in out
    assert (tmp / "ci/junit.xml").exists()


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
    results = {"passed": 0, "failed": 0, "tests": []}

    class J:
        calls, hits = [], 0
    text = cli.summary(results, J(), tmp_path, tmp_path / "j.xml")
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

def test_screen_command(tmp_path, monkeypatch, capsys):
    (tmp_path / "app.apk").write_text("")
    d = FakeDriver()
    monkeypatch.setattr(cli, "make_driver", lambda p, dev: d)
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    assert cli.main(["screen", "--app", str(tmp_path / "app.apk")]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["screen"][0]["hint"] == "Email" and d.names()[:2] == ["install", "launch"]
    assert cli.main(["screen", "--platform", "ios"]) == 0


def test_screen_needs_app_or_platform(capsys):
    assert cli.main(["screen"]) == 2
    assert "Pass --app or --platform" in capsys.readouterr().err


def test_devices_command(monkeypatch, capsys):
    from jevtest.drivers import android, ios
    monkeypatch.setattr(android, "devices", lambda: ["emulator-5554"])
    monkeypatch.setattr(ios, "simulators", lambda: [{"name": "iPhone 17", "runtime": "iOS-26-5", "udid": "U",
                                                     "state": "Booted"}])
    assert cli.main(["devices"]) == 0
    out = capsys.readouterr().out
    assert "Android: emulator-5554" in out and "iOS: iPhone 17 (iOS-26-5)  U  Booted" in out


def test_devices_command_reports_errors(monkeypatch, capsys):
    from jevtest.drivers import android, ios

    def broken():
        raise DriverError("tool missing")
    monkeypatch.setattr(android, "devices", lambda: [])
    monkeypatch.setattr(ios, "simulators", broken)
    cli.main(["devices"])
    out = capsys.readouterr().out
    assert "Android: (none connected)" in out and "iOS: tool missing" in out
    monkeypatch.setattr(android, "devices", broken)
    cli.main(["devices"])
    assert "Android: tool missing" in capsys.readouterr().out


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
    monkeypatch.setattr("sys.argv", ["jevtest", "screen"])
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_module("jevtest", run_name="__main__")
    assert exit_info.value.code == 2
