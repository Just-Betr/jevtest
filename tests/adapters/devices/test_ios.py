import json
import re
import shutil
import time
from pathlib import Path

import pytest

from jevtest.adapters.devices.common import AgentRefused, ToolFailed, Undo
from jevtest.adapters.devices.ios import IOSDevice
from jevtest.adapters.devices.ios_tools import AGENT_SRC
from jevtest.domain.failures import DeviceError
from jevtest.domain.screen import Element
from jevtest.domain.steps import KEYS
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
    assert d.agent_log.name == "ios-agent-A.log"  # one per device, replaced each run: the cache never grows


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
    d = IOSDevice("A", Path("Demo.app"), PROGRESS)
    d.close()
    assert env[2][-1] == ("stopped", d.agent)


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
    assert [c.split("simctl ")[1].split()[0] for c in sim.cmds] == [
        "terminate",
        "terminate",
        "uninstall",
        "install",
        "privacy",  # no permission decided, as a new install has
    ]
    assert sim.cmds[-1].endswith("simctl privacy A reset all dev.demo")
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
    env[1].replies["/state"] = [{"state": 4}, {"state": 3}]  # Home returns before the app has left
    drv.home()
    drv.hide_keyboard()
    drv.rotate("landscape")
    sent = [(p, {k: v for k, v in b.items() if k != "bundle_id"}) for p, b in env[1].calls]
    assert sent == [
        ("/tap", {"x": 1, "y": 2}),
        ("/double_tap", {"x": 1, "y": 2}),
        ("/long_press", {"x": 1, "y": 2, "seconds": 2}),
        ("/drag", {"x1": 1, "y1": 2, "x2": 3, "y2": 4, "velocity": 1500, "hold": 0.05}),
        ("/type", {"text": "hi"}),
        ("/tap", {"x": 5, "y": 6}),
        ("/tree", {}),  # focused, with the keyboard up
        ("/type", {"text": "hi"}),
        ("/tap", {"x": 8, "y": 5}),
        ("/tree", {}),
        ("/key", {"key": "delete", "count": 3}),
        ("/tree", {}),  # read again: the field is empty
        ("/tree", {}),  # a key goes into a field: the keyboard is up
        ("/key", {"key": "enter"}),
        ("/back", {}),
        ("/home", {}),
        ("/state", {}),
        ("/state", {}),  # background now
        ("/hide_keyboard", {}),
        ("/tree", {}),  # closed: the keyboard is gone
        ("/rotate", {}),
        ("/rotate", {"orientation": "landscape"}),
        ("/tree", {}),  # turned: the app is wider than tall
    ]
    assert all(b["bundle_id"] == "dev.demo" for _, b in env[1].calls)


def test_a_scroll_drags_slowly_so_nothing_flings_on_and_a_swipe_flicks(drv, env):
    screen = {"width": 402, "height": 874, "elements": [], "keyboard": False}
    env[1].replies["/tree"] = screen
    drv.scroll("down")
    drv.swipe("left")
    drags = [b for p, b in env[1].calls if p == "/drag"]
    assert [(b["y1"], b["y2"], b["velocity"], b["hold"]) for b in drags] == [
        (699, 175, 300, 0.1),
        (437, 437, 1500, 0.05),
    ]


def test_a_swipe_on_a_slider_drags_its_thumb_slowly_on_to_the_screen_edge(drv, env):
    """The finger starts on the thumb's middle (its thumb is about as wide as the slider is tall) and goes on
    past the slider's end, since the thumb trails it; slowly, so the thumb keeps up (measured on iOS 26.5)."""
    env[1].replies["/tree"] = {"width": 402, "height": 874, "elements": [], "keyboard": False}
    half = Element(kind="slider", text="Volume: 50%", bounds=(32, 287, 370, 318), position=0.5)
    full = Element(kind="slider", text="Bass: Loud", bounds=(32, 400, 370, 431), position=1.0)
    drv.swipe("right", half)
    drv.swipe("left", full)
    drv.swipe("up", half)  # across it, as on any element: a slider moves only sideways
    drags = [b for p, b in env[1].calls if p == "/drag"]
    assert [(b["x1"], b["y1"], b["x2"], b["y2"], b["velocity"]) for b in drags] == [
        (201, 302, 401, 302, 300),
        (354, 415, 0, 415, 300),
        (201, 311, 201, 293, 1500),
    ]


def test_hide_keyboard_in_a_field_of_several_lines_says_return_adds_a_line(drv, env, monkeypatch):
    env[1].replies["/hide_keyboard"] = {"ok": True, "multiline": True}  # the agent took the new line back out
    env[1].replies["/tree"] = {"width": 402, "height": 874, "elements": [], "keyboard": True}
    ticks = iter([0, 1, 4])
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(time, "sleep", lambda _: None)
    with pytest.raises(
        DeviceError,
        match=r"^The keyboard did not close \(the field takes several lines, so Return adds one, which jevtest "
        r"took back out, and there's no Done; on iOS only the app can close it then, e\.g\. on a tap outside the "
        r"field\) within 3 seconds$",
    ):
        drv.hide_keyboard()


def test_simctl_device_commands(drv, env):
    env[1].replies["/state"] = {"state": 1}  # not running: nothing to start again
    drv.set_location(1.5, -2.5)
    drv.grant(["photos"])
    tails = [c.split("simctl ", 1)[1] for c in env[0].cmds]
    assert tails == ["location A set 1.5,-2.5", "privacy A grant photos dev.demo"]


def test_a_grant_starts_the_app_again_once_the_simulator_ended_it(drv, env, slept):
    env[1].replies["/state"] = [{"state": 4}, {"state": 4}, {"state": 1}]  # running; still; gone
    drv.grant(["camera"])
    tails = [c.split("simctl ", 1)[1] for c in env[0].cmds]
    assert tails == ["privacy A grant camera dev.demo", "launch A dev.demo"]
    assert env[1].paths()[-1] == "/wait_foreground" and slept == [0.25]


def test_a_grant_that_leaves_the_app_running_doesnt_wait_for_it_to_end(drv, env, slept):
    """Location and Siri leave a running app running (measured); every other service ends it."""
    drv.grant(["location", "location-always"])
    tails = [c.split("simctl ", 1)[1] for c in env[0].cmds]
    assert tails == ["privacy A grant location dev.demo", "privacy A grant location-always dev.demo"]
    assert "/state" not in env[1].paths() and slept == []


def test_a_grant_with_one_service_that_ends_the_app_starts_it_again(drv, env, slept):
    env[1].replies["/state"] = [{"state": 4}, {"state": 1}]  # running; gone
    drv.grant(["location", "camera"])
    tails = [c.split("simctl ", 1)[1] for c in env[0].cmds]
    assert tails[-1] == "launch A dev.demo"


def test_a_grant_fails_if_the_simulator_never_ends_the_app(drv, env, monkeypatch):
    ticks = iter([0, 1, 4])
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(time, "sleep", lambda _: None)
    with pytest.raises(DeviceError, match="The simulator did not end the app after the permission changed within 3"):
        drv.grant(["camera"])


def test_rotate_waits_until_the_app_has_turned(drv, env, slept):
    agent = env[1]
    portrait = {"width": 402, "height": 874, "elements": [], "keyboard": False}
    agent.replies["/tree"] = [portrait, {**portrait, "width": 874, "height": 402}]  # the first read: not yet
    drv.rotate("landscape")
    paths = [p for p, _ in agent.calls]
    assert paths[-3:] == ["/rotate", "/tree", "/tree"] and slept == [0.25]


def test_ios_has_only_the_keys_every_platform_has(drv, env):
    with pytest.raises(DeviceError, match="iOS has no key 'menu': it presses only backspace, delete, enter"):
        drv.key("menu")
    assert all(p != "/key" for p, _ in env[1].calls)


def test_rotate_fails_when_the_app_never_turns(drv, env, monkeypatch):
    env[1].turns = False  # e.g. an app that allows portrait only
    ticks = iter([0, 1, 4])
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks))
    with pytest.raises(DeviceError, match="The app did not turn to landscape within 3 seconds: does the app allow"):
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


def test_a_put_back_that_fails_is_said_and_the_rest_still_go_back(env):
    told: list[str] = []
    d = IOSDevice("A", Path("Demo.app"), told.append)
    env[1].replies["/appearance"] = [{"raw": 1}, {}, OSError("gone")]  # read, set, then lost when putting back
    d.dark_mode(on=True)
    d.set_location(1.0, 2.0)
    d.close()
    assert any(c.endswith("simctl location A clear") for c in env[0].cmds)  # still put back
    assert (
        told[-1]
        == "couldn't put back dark mode (Lost the iOS agent during /appearance (gone); the next test starts it again.): set it by hand"
    )


def test_clear_empty_field_only_focuses(drv, env):
    drv.clear_text(Element("text_field", hint="Name", bounds=(0, 0, 100, 10)))
    assert [p for p, _ in env[1].calls] == ["/tap", "/tree"]  # focused, with the keyboard up: nothing to delete


def test_agent_waits(drv, env):
    drv.launch()
    drv.resume()
    sent = {p: b.get("timeout") for p, b in env[1].calls}
    assert sent["/wait_foreground"] == 10 and sent["/activate"] == 10


def test_typing_waits_until_the_keyboard_is_up(drv, env, slept):
    """On iOS the keyboard is the sign a field takes keys: XCUITest reports no field as focused (measured)."""
    agent = env[1]
    plain = {"width": 402, "height": 874, "elements": [], "keyboard": False}
    focused = {
        **plain,
        "keyboard": True,
        "elements": [{"type": "text_field", "x": 0, "y": 0, "w": 9, "h": 9, "focused": False}],
    }
    agent.replies["/tree"] = [plain, focused]  # right after the tap: not yet
    drv.type_text("x", at=(1, 1))
    assert [p for p, _ in agent.calls][-4:] == ["/tap", "/tree", "/tree", "/type"] and slept == [0.25]


def field_tree(value: str) -> dict[str, object]:
    field = {"type": "text_field", "value": value, "x": 0, "y": 100, "w": 300, "h": 40, "focused": True}
    return {"width": 402, "height": 874, "elements": [field], "keyboard": True, "keyboard_top": 500}


def test_clear_deletes_again_what_xcuitest_dropped(drv, env):
    """XCUITest drops some of many deletes typed at once (measured: 7 of 11 landed): read, delete again."""
    field = Element("text_field", "hello world", value="hello world", editable=True, bounds=(0, 100, 300, 140))
    env[1].replies["/tree"] = [field_tree("hello world"), field_tree("hell"), field_tree("")]
    drv.clear_text(field)
    assert [b["count"] for p, b in env[1].calls if p == "/key"] == [11, 4]


def test_clear_fails_when_deleting_removes_nothing(drv, env):
    field = Element("text_field", "stuck", value="stuck", editable=True, bounds=(0, 100, 300, 140))
    env[1].replies["/tree"] = [field_tree("stuck"), field_tree("stuck")]
    with pytest.raises(DeviceError, match="^Couldn't clear text_field 'stuck': deleting left 'stuck' in it$"):
        drv.clear_text(field)


def test_a_picker_wheel_is_turned_by_xctest(drv, env):
    wheel = Element(kind="picker", text="Red", bounds=(25, 213, 377, 504))
    drv.choose(wheel, "Blue")
    assert env[1].calls[-1] == ("/adjust", {"x": 201, "y": 358, "value": "Blue", "bundle_id": "dev.demo"})
    env[1].replies["/adjust"] = {"error": "the wheel shows 'Red', not 'Brown'"}
    with pytest.raises(DeviceError, match=r"^Can't turn picker 'Red' to 'Brown': .*the wheel shows 'Red', not 'Brown'"):
        drv.choose(wheel, "Brown")


def test_autofill_off_is_not_supported(drv):
    with pytest.raises(DeviceError, match="^jevtest can.t turn an iPhone.s or simulator.s autofill off$"):
        drv.autofill_off()


def test_network_is_not_supported(drv):
    with pytest.raises(DeviceError, match="can.t turn an iPhone.s or simulator.s network off"):
        drv.network(on=False)


def test_looks_says_nothing_on_ios_where_frames_move_with_their_animation(drv, env):
    assert drv.looks([Element("button", "OK", bounds=(0, 0, 9, 9))]) == ""
    assert not any(p == "/pixels" for p, _ in env[1].calls)


@pytest.mark.parametrize("physical", [False, True])
def test_a_link_no_app_opens_says_so(drv, env, physical):
    """Simulator or iPhone, the agent opens it, and says why it couldn't in its own words (measured: error 115)."""
    drv.physical = physical
    env[1].replies["/open_url"] = {
        "error": "The operation could not be completed. (LSApplicationWorkspaceErrorDomain error 115.)"
    }
    with pytest.raises(DeviceError, match="^No app on the device opens x://y"):
        drv.open_url("x://y")
    env[1].replies["/open_url"] = {"error": "Not a URL"}
    with pytest.raises(AgentRefused, match="^iOS agent /open_url: Not a URL$"):
        drv.open_url("x://y")


def test_a_simulator_opens_links_through_the_agent_so_ios_doesnt_ask(drv, env):
    """`simctl openurl` makes iOS ask "Open in “App”?" first (measured); the agent's open doesn't."""
    drv.open_url("jevtestdemo://open")
    assert ("/open_url", {"url": "jevtestdemo://open"}) in [
        (p, {k: v for k, v in b.items() if k != "bundle_id"}) for p, b in env[1].calls
    ]
    assert not any("openurl" in c for c in env[0].cmds)


def test_a_key_with_no_keyboard_up_says_ios_needs_a_field(drv, env, monkeypatch):
    env[1].replies["/tree"] = {"width": 402, "height": 874, "elements": [], "keyboard": False}
    ticks = iter([0, 1, 4])
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(time, "sleep", lambda _: None)
    with pytest.raises(
        DeviceError,
        match="^No keyboard came up within 3 seconds: iOS presses keys only into a field, so tap one first$",
    ):
        drv.key("enter")
    assert "/key" not in env[1].paths()


def test_what_a_killed_run_left_changed_is_put_back_first_on_ios(env):
    left = Undo("A")
    left.remember("dark mode", lambda: {"call": "/appearance", "body": {"raw": 1}})
    left.remember("location", lambda: {"simctl": ["location", "A", "clear"]})
    told: list[str] = []
    d = IOSDevice("A", Path("Demo.app"), told.append)
    assert ("/appearance", {"raw": 1}) in [
        (p, {k: v for k, v in b.items() if k != "bundle_id"}) for p, b in env[1].calls
    ]
    assert any(c.endswith("simctl location A clear") for c in env[0].cmds)
    assert told[-1] == "putting back what a run that was stopped left changed: dark mode, location"
    assert d._undo.left_by_a_stopped_run() == {}


@pytest.mark.parametrize(
    "entry",
    [
        {"raw": 1},  # 0.9.7 and older: the body alone, under the agent path
        {"call": "/rotate", "body": 5},
        {"simctl": ["location", 1]},
        5,
    ],
)
def test_an_entry_another_version_wrote_is_named_not_sent(env, entry):
    Undo("A").remember("rotation", lambda: entry)
    told: list[str] = []
    IOSDevice("A", Path("Demo.app"), told.append)
    assert "/rotate" not in env[1].paths() and not any("simctl location" in c for c in env[0].cmds)
    assert told[-1] == "can't put back rotation (another jevtest version changed it): set it by hand"


def test_several_grants_start_the_app_again_once(drv, env, slept):
    env[1].replies["/state"] = [{"state": 4}, {"state": 1}]  # running; gone after the grants
    drv.grant(["camera", "photos"])
    tails = [c.split("simctl ", 1)[1] for c in env[0].cmds]
    assert tails == ["privacy A grant camera dev.demo", "privacy A grant photos dev.demo", "launch A dev.demo"]


def test_a_service_the_simulator_wont_grant_says_what_names_it_takes(drv, env):
    env[1].replies["/state"] = {"state": 1}
    env[0].rules["privacy"] = ToolFailed(["xcrun", "simctl"], 1, "Failed to set access\nOperation not permitted\n")
    with pytest.raises(DeviceError, match="^The simulator didn't grant 'bogus': use a service name such as camera"):
        drv.grant(["bogus"])
    env[0].rules["privacy"] = ToolFailed(["xcrun", "simctl"], 164, "Invalid device: A")  # another failure: as it came
    with pytest.raises(ToolFailed, match="Invalid device: A$"):
        drv.grant(["camera"])


def test_a_key_passes_on_an_agent_failure_as_it_came(drv, env):
    """Only a keyboard that never came up gets the hint about fields: an agent failure is reported as it is."""
    env[1].replies["/tree"] = {"error": "Application is not running"}
    with pytest.raises(AgentRefused, match="^iOS agent /tree: Application is not running$"):
        drv.key("enter")


def test_the_agent_presses_exactly_the_keys_a_test_file_can_name_on_ios():
    """The Swift agent has its own table of key names: it must match `KEYS`, which the loader checks files against."""
    swift = (AGENT_SRC / "AgentUITests" / "JevAgentUITests.swift").read_text()
    table = re.search(r"let keys: \[String: String\] = \[(.*?)\]\n", swift, re.DOTALL)
    assert table is not None
    assert sorted(re.findall(r'"(\w+)":', table[1])) == sorted(KEYS)


def test_an_agent_that_stopped_is_started_again_before_the_next_test(drv, env, monkeypatch):
    """One test's lost agent isn't every test's: on a new port, as the old one may still be held."""
    told: list[str] = []
    drv._progress = told.append
    drv.prepare_for_test()
    assert len([p for p in env[2] if p[0] != "stopped"]) == 1  # running: nothing to do
    drv.agent.returncode = 1  # something stopped it
    swap(monkeypatch, "free_port", lambda: 8124)
    drv.prepare_for_test()
    started = [p for p in env[2] if p[0] != "stopped"]
    assert len(started) == 2 and started[-1][2]["TEST_RUNNER_JEVTEST_PORT"] == "8124"
    assert told == ["the iOS agent had stopped: starting it again"]
    assert drv.port == 8124
