import json
import plistlib
import zipfile
from pathlib import Path

import pytest

from jevtest.drivers import ios
from jevtest.drivers.base import DriverError
from jevtest.drivers.ios import IOSDriver, app_bundle, parse_tree
from jevtest.screen import Element

from .test_android import Adb

FIX = Path(__file__).parent / "fixtures"
SIMS = {"devices": {
    "com.apple.CoreSimulator.SimRuntime.iOS-26-5": [
        {"name": "iPhone 17", "udid": "B", "state": "Shutdown"},
        {"name": "iPad Air", "udid": "C", "state": "Shutdown"}],
    "com.apple.CoreSimulator.SimRuntime.iOS-18-0": [{"name": "iPhone 16", "udid": "A", "state": "Booted"}],
    "com.apple.CoreSimulator.SimRuntime.watchOS-11-0": [{"name": "Watch", "udid": "W", "state": "Booted"}],
}}


class Agent:
    """Stands in for the XCUITest agent's HTTP API."""

    def __init__(self):
        self.replies: dict[str, object] = {"/status": {"ok": True}, "/state": {"state": 4}}
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url, body, timeout):
        path = url.split("8123", 1)[1]
        self.calls.append((path, body))
        reply = self.replies.get(path, {"ok": True})
        if isinstance(reply, list):
            reply = reply.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def paths(self):
        return [p for p, _ in self.calls]


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("JEVTEST_CACHE", str(tmp_path / "cache"))
    xctestrun = tmp_path / "cache" / f"ios-agent-{ios.digest(ios.AGENT_SRC)}" / "Build/Products/a.xctestrun"
    xctestrun.parent.mkdir(parents=True)
    xctestrun.write_text("")
    sim = Adb({"list devices": json.dumps(SIMS)})
    agent, procs = Agent(), []
    monkeypatch.setattr(ios, "run", sim)
    monkeypatch.setattr(ios, "http_post", agent)
    monkeypatch.setattr(ios, "free_port", lambda: 8123)
    monkeypatch.setattr(ios.shutil, "which", lambda n: "/usr/bin/xcrun")

    def start_process(cmd, ready, log, timeout, env):
        procs.append((cmd, ready, env))
        return "proc"
    monkeypatch.setattr(ios, "start_process", start_process)
    monkeypatch.setattr(ios, "stop_process", lambda proc: procs.append(("stopped", proc)))
    return sim, agent, procs


def make_app(tmp_path, bundle_id="dev.demo", platforms=("iPhoneSimulator",), name="Demo.app") -> Path:
    app = tmp_path / name
    app.mkdir(parents=True)
    info = {"CFBundleSupportedPlatforms": list(platforms)}
    if bundle_id:
        info["CFBundleIdentifier"] = bundle_id
    (app / "Info.plist").write_bytes(plistlib.dumps(info))
    return app


@pytest.fixture
def drv(env, tmp_path):
    d = IOSDriver()
    d.install(make_app(tmp_path))
    env[0].cmds.clear()
    env[1].calls.clear()
    return d


# --- parsing real agent trees -------------------------------------------------------------------

def test_parse_login_tree():
    s = parse_tree(json.loads((FIX / "ios_login.json").read_text()))
    assert (s.width, s.height) == (402, 874)
    # Flutter reports its obscured password field as a plain text field on iOS.
    assert [(e.kind, e.text) for e in s.elements] == [
        ("text", "Sign in"), ("text_field", "Email"), ("text_field", "Password"), ("button", "Sign in")]


def test_parse_webview_tree():
    s = parse_tree(json.loads((FIX / "ios_webview.json").read_text()))
    texts = [e.text for e in s.elements]
    assert {"Web Greeter", "Say hello", "Nobody greeted yet", "I agree to the terms", "Show more"} <= set(texts)
    assert not any("scroll bar" in t for t in texts)
    assert next(e for e in s.elements if e.kind == "switch").checked is False
    # WebKit marks every element "focused"; only a text field's focus means anything
    assert [e.kind for e in s.elements if e.focused] == ["text_field"]
    assert next(e for e in s.elements if e.kind == "text_field").editable


def test_parse_rules():
    data = {"width": 100, "height": 200, "keyboard": True, "running": True, "elements": [
        {"type": "application", "label": "App", "x": 0, "y": 0, "w": 100, "h": 200},
        {"type": "other", "label": "", "x": 0, "y": 0, "w": 100, "h": 200},
        {"type": "other", "label": "Card", "x": 0, "y": 0, "w": 50, "h": 50},
        {"type": "text", "label": "Hi", "value": "Hi", "x": 0, "y": 0, "w": 50, "h": 50},
        {"type": "text", "label": "Hi", "value": "Hi", "x": 0, "y": 0, "w": 50, "h": 50},
        {"type": "text", "label": "Tiny", "x": 0, "y": 0, "w": 1, "h": 1},
        {"type": "text", "label": "Vertical scroll bar, 2 pages", "x": 0, "y": 0, "w": 5, "h": 50},
        {"type": "text_field", "label": "Email", "value": "a@b.c", "placeholder": "Email", "x": 0, "y": 0,
         "w": 50, "h": 20, "focused": True},
        {"type": "text_field", "label": "", "value": "typed", "x": 0, "y": 30, "w": 50, "h": 20},
        {"type": "password_field", "label": "Password", "value": "•••", "x": 0, "y": 60, "w": 50, "h": 20},
        {"type": "switch", "label": "Wifi", "value": "1", "x": 0, "y": 90, "w": 50, "h": 20},
        {"type": "button", "label": "Go", "identifier": "go", "x": -10, "y": 190, "w": 50, "h": 50,
         "enabled": False},
        {"type": "list", "label": "", "identifier": "feed", "x": 0, "y": 0, "w": 100, "h": 100},
    ]}
    s = parse_tree(data)
    assert [(e.kind, e.text) for e in s.elements] == [
        ("text", "Card"), ("text", "Hi"), ("text_field", "Email: a@b.c"), ("text_field", "typed"),
        ("password_field", "Password"), ("switch", "Wifi"), ("button", "Go"), ("list", "")]
    go = s.elements[6]
    assert go.bounds == (0, 190, 40, 200) and go.enabled is False and go.clickable and go.resource_id == "go"
    assert s.elements[2].focused and s.elements[5].checked is True
    assert s.keyboard_visible


# --- simulators -------------------------------------------------------------------------------------

def test_simulators_sorted_newest_first(env):
    sims = ios.simulators()
    assert [(d["name"], d["runtime"]) for d in sims] == [
        ("iPad Air", "iOS-26-5"), ("iPhone 17", "iOS-26-5"), ("iPhone 16", "iOS-18-0")]


def test_pick_prefers_booted(env):
    sim = env[0]
    assert ios.pick_simulator(None) == "A"
    assert not any(" boot A" in c for c in sim.cmds)


def test_pick_boots_newest_iphone_when_none_booted(env):
    sim = env[0]
    data = json.loads(json.dumps(SIMS))
    data["devices"]["com.apple.CoreSimulator.SimRuntime.iOS-18-0"][0]["state"] = "Shutdown"
    sim.rules["list devices"] = json.dumps(data)
    assert ios.pick_simulator(None) == "B"
    assert any("simctl boot B" in c for c in sim.cmds)


def test_pick_by_name_or_udid(env):
    assert ios.pick_simulator("iPad Air") == "C"
    assert ios.pick_simulator("B") == "B"
    with pytest.raises(DriverError, match="No iOS simulator named"):
        ios.pick_simulator("Nokia")


def test_pick_with_no_simulators(env):
    env[0].rules["list devices"] = json.dumps({"devices": {}})
    with pytest.raises(DriverError, match="No iOS simulators available"):
        ios.pick_simulator(None)


def test_free_port_is_usable():
    assert 0 < ios.free_port() < 65536


# --- app bundles ----------------------------------------------------------------------------------

def test_app_bundle_from_dir_zip_and_ipa(tmp_path):
    app = make_app(tmp_path / "src")
    assert app_bundle(app, tmp_path / "w") == app
    for name in ("app.zip", "app.ipa"):
        archive = tmp_path / name
        with zipfile.ZipFile(archive, "w") as z:
            z.write(app / "Info.plist", "Payload/Demo.app/Info.plist")
        found = app_bundle(archive, tmp_path / f"w-{name}")
        assert found.name == "Demo.app"


def test_app_bundle_rejects_bad_input(tmp_path):
    (tmp_path / "empty.zip").write_bytes(b"not a zip")
    empty = tmp_path / "noapp.zip"
    with zipfile.ZipFile(empty, "w") as z:
        z.writestr("readme.txt", "x")
    for path in (tmp_path / "empty.zip", empty, tmp_path / "missing.app", tmp_path / "x.apk"):
        with pytest.raises(DriverError, match="simulator .app"):
            app_bundle(path, tmp_path / "w")




# --- agent lifecycle --------------------------------------------------------------------------------

def test_starts_agent_on_simulator(env):
    _, agent, procs = env
    d = IOSDriver()
    cmd, ready, run_env = procs[0]
    assert cmd[:2] == ["xcodebuild", "test-without-building"] and cmd[-1] == "id=A"
    assert ready == "JEVTEST_AGENT_READY"  # returns the moment the agent says so, no polling
    assert run_env["TEST_RUNNER_JEVTEST_PORT"] == "8123"
    assert d.agent_log.name == "ios-agent-8123.log"


def test_agent_is_built_when_missing(env, monkeypatch, tmp_path):
    sim = env[0]
    monkeypatch.setenv("JEVTEST_CACHE", str(tmp_path / "fresh"))

    def build(cmd, **kw):
        if "build-for-testing" in cmd:
            out = Path(cmd[cmd.index("-derivedDataPath") + 1]) / "Build/Products"
            out.mkdir(parents=True)
            (out / "x.xctestrun").write_text("")
        return sim(cmd, **kw)
    monkeypatch.setattr(ios, "run", build)
    IOSDriver()
    assert any("build-for-testing" in c for c in sim.cmds)


def test_agent_build_without_output_fails(env, monkeypatch, tmp_path):
    monkeypatch.setenv("JEVTEST_CACHE", str(tmp_path / "fresh"))
    with pytest.raises(DriverError, match="no .xctestrun"):
        IOSDriver()








def test_requires_xcode(env, monkeypatch):
    monkeypatch.setattr(ios.shutil, "which", lambda n: None)
    with pytest.raises(DriverError, match="Xcode"):
        IOSDriver()


def test_close_stops_agent(env):
    IOSDriver().close()
    assert env[2][-1] == ("stopped", "proc")




def test_lost_agent_is_a_driver_error(drv, env):
    env[1].replies["/tree"] = OSError("Connection refused")
    with pytest.raises(DriverError, match="Lost the iOS agent during /tree"):
        drv.screen()


def test_agent_errors_are_driver_errors(drv, env):
    env[1].replies["/type"] = {"error": "no focus"}
    with pytest.raises(DriverError, match="iOS agent /type: no focus"):
        drv.type_text("x")


def test_log_tail_without_log(drv):
    drv.agent_log = Path("/nonexistent/log")
    assert drv._log_tail() == "(no log)"


def test_http_post_roundtrip(monkeypatch):
    class R:
        def read(self):
            return b'{"ok": true}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    seen = []
    monkeypatch.setattr(ios.urllib.request, "urlopen", lambda req, timeout: seen.append((req, timeout)) or R())
    assert ios.http_post("http://127.0.0.1:1/x", {"a": 1}, timeout=3) == {"ok": True}
    assert json.loads(seen[0][0].data) == {"a": 1} and seen[0][1] == 3


# --- app lifecycle ----------------------------------------------------------------------------------

def test_install(env, tmp_path):
    sim = env[0]
    d = IOSDriver()
    assert d.install(make_app(tmp_path)) == "dev.demo"
    assert any("simctl install A" in c for c in sim.cmds)


@pytest.mark.parametrize("kwargs,message", [
    ({"platforms": ("iPhoneOS",)}, "built for iPhoneOS, not the iOS Simulator"),
    ({"bundle_id": None}, "no CFBundleIdentifier")])
def test_install_rejects_bad_bundles(env, tmp_path, kwargs, message):
    with pytest.raises(DriverError, match=message):
        IOSDriver().install(make_app(tmp_path, **kwargs))


def test_install_without_plist(env, tmp_path):
    app = tmp_path / "Bad.app"
    app.mkdir()
    with pytest.raises(DriverError, match="no readable Info.plist"):
        IOSDriver().install(app)


def test_launch_waits_for_the_app_in_front(drv, env):
    drv.launch()
    assert any("simctl launch A dev.demo" in c for c in env[0].cmds)
    assert env[1].paths() == ["/wait_foreground"]


def test_launch_reports_an_app_that_never_shows(drv, env):
    env[1].replies["/wait_foreground"] = {"error": "did not come to the foreground"}
    with pytest.raises(DriverError, match="did not come to the foreground"):
        drv.launch()


@pytest.mark.parametrize("raw,state", [(4, "foreground"), (3, "background"), (2, "background"),
                                       (1, "not_running"), (0, "not_running"), (9, "background")])
def test_app_state(drv, env, raw, state):
    env[1].replies["/state"] = {"state": raw}
    assert drv.app_state() == state


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
    field = Element("text_field", "abc", bounds=(0, 0, 10, 10))
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
    sent = [(p, {k: v for k, v in b.items() if k != "bundle_id"}) for p, b in env[1].calls]
    assert sent == [
        ("/tap", {"x": 1, "y": 2}), ("/double_tap", {"x": 1, "y": 2}), ("/long_press", {"x": 1, "y": 2, "seconds": 2}),
        ("/drag", {"x1": 1, "y1": 2, "x2": 3, "y2": 4}), ("/type", {"text": "hi"}),
        ("/tap", {"x": 5, "y": 6}), ("/idle", {"timeout": 3}), ("/type", {"text": "hi"}),
        ("/tap", {"x": 5, "y": 5}), ("/idle", {"timeout": 3}), ("/key", {"key": "delete", "count": 13}),
        ("/key", {"key": "enter"}), ("/back", {}), ("/home", {}), ("/hide_keyboard", {}),
        ("/rotate", {"orientation": "landscape"}), ("/idle", {"timeout": 2})]
    assert all(b["bundle_id"] == "dev.demo" for _, b in env[1].calls)


def test_simctl_device_commands(drv, env):
    drv.set_location(1.5, -2.5)
    drv.open_url("app://x")
    drv.dark_mode(True)
    drv.dark_mode(False)
    drv.grant("photos")
    tails = [c.split("simctl ", 1)[1] for c in env[0].cmds]
    assert tails == ["location A set 1.5,-2.5", "openurl A app://x", "ui A appearance dark",
                     "ui A appearance light", "privacy A grant photos dev.demo"]


def test_network_is_not_supported(drv):
    with pytest.raises(DriverError, match="shares the Mac's network"):
        drv.network(False)
