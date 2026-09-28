import json
import shutil
import time
from pathlib import Path

import pytest

from jevtest.adapters.devices.ios import IOSDevice
from jevtest.domain.failures import DeviceError
from jevtest.domain.screen import Element
from tests.adapters.devices.conftest import make_app, swap
from tests.conftest import PROGRESS

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def drv(env, tmp_path):
    d = IOSDevice("A", Path("Demo.app"), PROGRESS)
    d.install(make_app(tmp_path))
    env[0].cmds.clear()
    env[1].calls.clear()
    return d


# --- simulators -------------------------------------------------------------------------------------


# --- app bundles ----------------------------------------------------------------------------------


# --- agent lifecycle --------------------------------------------------------------------------------


def test_starts_agent_on_simulator(env):
    procs = env.procs
    d = IOSDevice("A", Path("Demo.app"), PROGRESS)
    cmd, ready, run_env = procs[0]
    assert cmd[:2] == ["xcodebuild", "test-without-building"] and cmd[-1] == "id=A"
    assert ready == "JEVTEST_AGENT_READY"  # returns the moment the agent says so, no polling
    assert run_env["TEST_RUNNER_JEVTEST_PORT"] == "8123"
    assert run_env["TEST_RUNNER_JEVTEST_TOKEN"] == d.token and len(d.token) >= 40  # random, per run
    assert run_env["TEST_RUNNER_JEVTEST_LOCAL_ONLY"] == "1"  # a simulator's agent listens on 127.0.0.1 only
    assert d.agent_log.name == "ios-agent-8123.log"


def test_each_device_gets_its_own_token(env):
    assert IOSDevice("A", Path("Demo.app"), PROGRESS).token != IOSDevice("A", Path("Demo.app"), PROGRESS).token


def test_agent_is_built_when_missing(env, monkeypatch, tmp_path):
    sim = env[0]
    monkeypatch.setenv("JEVTEST_CACHE", str(tmp_path / "fresh"))

    def build(cmd, **kw):
        if "build-for-testing" in cmd:
            out = Path(cmd[cmd.index("-derivedDataPath") + 1]) / "Build/Products"
            out.mkdir(parents=True)
            (out / "x.xctestrun").write_text("")
        return sim(cmd, **kw)

    swap(monkeypatch, "run", build)
    IOSDevice("A", Path("Demo.app"), PROGRESS)
    assert any("build-for-testing" in c for c in sim.cmds)


def test_agent_build_without_output_fails(env, monkeypatch, tmp_path):
    monkeypatch.setenv("JEVTEST_CACHE", str(tmp_path / "fresh"))
    with pytest.raises(DeviceError, match="no .xctestrun"):
        IOSDevice("A", Path("Demo.app"), PROGRESS)


def test_requires_xcode(env, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda n: None)
    with pytest.raises(DeviceError, match="Xcode"):
        IOSDevice("A", Path("Demo.app"), PROGRESS)


def test_close_stops_agent(env):
    IOSDevice("A", Path("Demo.app"), PROGRESS).close()
    assert env[2][-1] == ("stopped", "proc")


def test_lost_agent_is_a_driver_error(drv, env):
    env[1].replies["/tree"] = OSError("Connection refused")
    with pytest.raises(DeviceError, match="Lost the iOS agent during /tree"):
        drv.screen()


def test_agent_errors_are_driver_errors(drv, env):
    env[1].replies["/type"] = {"error": "no focus"}
    with pytest.raises(DeviceError, match="iOS agent /type: no focus"):
        drv.type_text("x")


def test_log_tail_without_log(drv):
    drv.agent_log = Path("/nonexistent/log")
    assert drv._log_tail() == "(no log)"


# --- app lifecycle ----------------------------------------------------------------------------------


def test_install(env, tmp_path):
    sim = env[0]
    d = IOSDevice("A", Path("Demo.app"), PROGRESS)
    assert d.install(make_app(tmp_path)) == "dev.demo"
    assert any("simctl install A" in c for c in sim.cmds)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"platforms": ("iPhoneOS",)}, "built for iPhoneOS, not the iOS Simulator"),
        ({"bundle_id": None}, "no CFBundleIdentifier"),
    ],
)
def test_install_rejects_bad_bundles(env, tmp_path, kwargs, message):
    with pytest.raises(DeviceError, match=message):
        IOSDevice("A", Path("Demo.app"), PROGRESS).install(make_app(tmp_path, **kwargs))


def test_install_without_plist(env, tmp_path):
    app = tmp_path / "Bad.app"
    app.mkdir()
    with pytest.raises(DeviceError, match="no readable Info.plist"):
        IOSDevice("A", Path("Demo.app"), PROGRESS).install(app)


def test_launch_waits_for_the_app_in_front(drv, env):
    drv.launch()
    assert any("simctl launch A dev.demo" in c for c in env[0].cmds)
    assert env[1].paths() == ["/wait_foreground"]


def test_launch_reports_an_app_that_never_shows(drv, env):
    env[1].replies["/wait_foreground"] = {"error": "did not come to the foreground"}
    with pytest.raises(DeviceError, match="did not come to the foreground"):
        drv.launch()


@pytest.mark.parametrize(
    ("raw", "state"), [(4, "foreground"), (3, "background"), (2, "background"), (1, "not_running"), (0, "not_running")]
)
def test_app_state(drv, env, raw, state):
    env[1].replies["/state"] = {"state": raw}
    assert drv.app_state() == state


def test_an_unknown_app_state_is_an_error_not_a_guess(drv, env):
    env[1].replies["/state"] = {"state": 9}
    with pytest.raises(DeviceError, match="app state jevtest doesn't know: 9"):
        drv.app_state()


def test_stop_clear_reinstall_resume(drv, env):
    sim = env[0]
    drv.stop()
    drv.clear_data()
    drv.resume()
    assert [c.split("simctl ")[1].split()[0] for c in sim.cmds] == ["terminate", "terminate", "uninstall", "install"]
    assert env[1].paths() == ["/activate"]


def test_screen_and_screenshot(drv, env, tmp_path):
    env[1].replies["/tree"] = json.loads((FIX / "ios_login.json").read_text())
    assert drv.screen().elements
    drv.screenshot(tmp_path / "s.png")
    assert env[0].cmds[-1].endswith(f"io A screenshot {tmp_path / 's.png'}")


# --- touch, keys, device ---------------------------------------------------------------------------


def test_agent_commands(drv, env):
    field = Element("text_field", "abc", value="abc", bounds=(0, 0, 10, 10))
    drv.tap(1, 2)
    drv.double_tap(1, 2)
    drv.long_press(1, 2, seconds=2)
    drv.drag(1, 2, 3, 4)
    drv.type_text("hi")
    drv.type_text("hi", at=(5, 6))
    drv.clear_text(field)
    drv.key("enter")
    drv.back()
    drv.home()
    drv.hide_keyboard()
    drv.rotate("landscape")
    drv.wait_idle(2)
    drv.wait_idle(2, quiet=0.5)
    drv.wait_change(1.5)
    sent = [(p, {k: v for k, v in b.items() if k != "bundle_id"}) for p, b in env[1].calls]
    assert sent == [
        ("/tap", {"x": 1, "y": 2}),
        ("/double_tap", {"x": 1, "y": 2}),
        ("/long_press", {"x": 1, "y": 2, "seconds": 2}),
        ("/drag", {"x1": 1, "y1": 2, "x2": 3, "y2": 4}),
        ("/type", {"text": "hi"}),
        ("/tap", {"x": 5, "y": 6}),
        ("/idle", {"timeout": 3.0}),
        ("/type", {"text": "hi"}),
        ("/tap", {"x": 8, "y": 5}),
        ("/idle", {"timeout": 3}),
        ("/key", {"key": "delete", "count": 3}),
        ("/key", {"key": "enter"}),
        ("/back", {}),
        ("/home", {}),
        ("/hide_keyboard", {}),
        ("/tree", {}),  # closed: the keyboard is gone
        ("/rotate", {}),
        ("/rotate", {"orientation": "landscape"}),
        ("/tree", {}),  # turned: the app is wider than tall
        ("/idle", {"timeout": 2}),
        ("/idle", {"timeout": 2, "quiet": 0.5}),
        ("/change", {"timeout": 1.5}),
    ]
    assert all(b["bundle_id"] == "dev.demo" for _, b in env[1].calls)


def test_simctl_device_commands(drv, env):
    drv.set_location(1.5, -2.5)
    drv.open_url("app://x")
    drv.grant("photos")
    tails = [c.split("simctl ", 1)[1] for c in env[0].cmds]
    assert tails == ["location A set 1.5,-2.5", "openurl A app://x", "privacy A grant photos dev.demo"]


def test_rotate_waits_until_the_app_has_turned(drv, env):
    agent = env[1]
    portrait = {"width": 402, "height": 874, "elements": [], "keyboard": False}
    agent.replies["/tree"] = [portrait, {**portrait, "width": 874, "height": 402}]  # the first read: not yet
    drv.rotate("landscape")
    paths = [p for p, _ in agent.calls]
    assert paths[-4:] == ["/rotate", "/tree", "/change", "/tree"]


def test_rotate_fails_when_the_app_never_turns(drv, env, monkeypatch):
    env[1].turns = False  # e.g. an app that allows portrait only
    ticks = iter([0, 1, 4])
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks))
    with pytest.raises(DeviceError, match="The app did not turn to landscape within 3 seconds"):
        drv.rotate("landscape")


def test_what_a_step_changed_is_put_back_on_close(drv, env):
    sim, agent = env[0], env[1]
    agent.replies["/appearance"] = {"raw": 1}  # light, before the test
    agent.replies["/rotate"] = {"raw": 1}  # portrait
    drv.dark_mode(on=True)
    drv.dark_mode(on=False)
    drv.rotate("landscape")
    drv.set_location(1.0, 2.0)
    agent.calls.clear()
    drv.close()
    sent = [(p, {k: v for k, v in b.items() if k != "bundle_id"}) for p, b in agent.calls]
    assert sent == [("/appearance", {"raw": 1}), ("/rotate", {"raw": 1})]  # remembered once, before the first change
    assert sim.cmds[-1].endswith("simctl location A clear")


def test_nothing_changed_nothing_put_back(drv, env):
    env[1].calls.clear()
    drv.close()
    assert env[1].calls == [] and not any("location" in c for c in env[0].cmds)


def test_a_lost_agent_does_not_stop_close(drv, env):
    drv.dark_mode(on=True)
    env[1].replies["/appearance"] = OSError("gone")
    env[0].rules["list devices"] = json.dumps({"devices": {}})  # the simulator went away too
    drv.close()


def test_clear_empty_field_only_focuses(drv, env):
    drv.clear_text(Element("text_field", hint="Name", bounds=(0, 0, 100, 10)))
    assert [p for p, _ in env[1].calls] == ["/tap", "/idle"]


def test_agent_waits(drv, env):
    drv.launch()
    drv.resume()
    drv.type_text("x", at=(1, 1))
    sent = {p: b.get("timeout") for p, b in env[1].calls}
    assert sent["/wait_foreground"] == 10 and sent["/activate"] == 10 and sent["/idle"] == 3


def test_network_is_not_supported(drv):
    with pytest.raises(DeviceError, match="can.t turn an iPhone.s or simulator.s network off"):
        drv.network(on=False)
