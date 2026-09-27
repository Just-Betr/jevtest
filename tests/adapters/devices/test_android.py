import shutil
import time
from pathlib import Path

import pytest

from jevtest.adapters.devices import android
from jevtest.adapters.devices.android import AndroidDevice
from jevtest.domain.failures import DeviceError
from jevtest.domain.kinds import Direction, Orientation
from jevtest.domain.screen import Element
from tests.adapters.devices.conftest import FIX, LOGIN
from tests.conftest import PROGRESS

WEB = (FIX / "android_webview.xml").read_text()
EMPTY_WEB = (
    '<hierarchy rotation="0"><node class="android.widget.FrameLayout" bounds="[0,0][100,100]">'
    '<node class="android.webkit.WebView" bounds="[0,0][100,100]"/></node></hierarchy>'
)


@pytest.fixture
def drv(adb):
    d = AndroidDevice("emulator-5554", PROGRESS)
    d.install(Path("app.apk"))
    adb.cmds.clear()
    return d


# --- parsing real dumps ------------------------------------------------------------------


# --- driver setup ---------------------------------------------------------------------------


def test_uses_the_named_device(adb):
    assert AndroidDevice("emulator-5554", PROGRESS).serial == "emulator-5554"


def test_device_named_by_serial_model_or_avd(adb):
    adb.rules["adb devices"] = "List of devices attached\nemulator-5554\tdevice\n15241JEC\tdevice\n"
    adb.rules["-s 15241JEC shell getprop ro.product.model"] = "Pixel 4a\n"
    adb.rules["-s emulator-5554 shell getprop ro.product.model"] = "sdk_gphone64_arm64\n"
    adb.rules["emu avd name"] = "Pixel_10\nOK\n"
    assert AndroidDevice("15241JEC", PROGRESS).serial == "15241JEC"
    assert AndroidDevice("Pixel 4a", PROGRESS).serial == "15241JEC"
    assert AndroidDevice("Pixel_10", PROGRESS).serial == "emulator-5554"
    with pytest.raises(DeviceError, match=r"called 'Galaxy' \(names are exact\). Connected: .*Pixel_10.*Pixel 4a"):
        AndroidDevice("Galaxy", PROGRESS)
    with pytest.raises(DeviceError, match="called 'pixel 4a'"):
        AndroidDevice("pixel 4a", PROGRESS)


def test_a_name_two_devices_share_is_an_error(adb):
    adb.rules["adb devices"] = "List of devices attached\nA1\tdevice\nB2\tdevice\n"
    adb.rules["getprop ro.product.model"] = "Pixel 4a\n"
    with pytest.raises(DeviceError, match=r"Several connected Android devices are called 'Pixel 4a' \(A1, B2\)"):
        AndroidDevice("Pixel 4a", PROGRESS)


def test_no_device_is_an_error_not_a_boot(adb):
    adb.rules["adb devices"] = "List of devices attached\n"
    with pytest.raises(DeviceError, match="No Android device connected"):
        AndroidDevice("emulator-5554", PROGRESS)
    assert not any("emulator" in c and "-avd" in c for c in adb.cmds)


def test_install_apk(adb):
    d = AndroidDevice("emulator-5554", PROGRESS)
    assert d.install(Path("app.apk")) == "dev.demo"
    assert d.activity == "dev.demo/.MainActivity"
    assert any(c.endswith("install -r -t app.apk") for c in adb.cmds)  # no -g: permissions start ungranted


def test_install_aab(adb, monkeypatch):
    adb.rules["dump manifest"] = "dev.bundle\n"
    adb.rules["resolve-activity"] = "dev.bundle/.Main\n"
    d = AndroidDevice("emulator-5554", PROGRESS)
    assert d.install(Path("app.aab")) == "dev.bundle"
    assert any("build-apks" in c for c in adb.cmds) and any("install-apks" in c for c in adb.cmds)


def test_install_aab_needs_bundletool(adb, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda n: None)
    d = AndroidDevice.__new__(AndroidDevice)
    d.adb, d.serial = "/bin/adb", "emulator-5554"
    with pytest.raises(DeviceError, match="bundletool"):
        d.install(Path("app.aab"))


def test_install_rejects_other_files(adb):
    with pytest.raises(DeviceError, match="needs an .apk or .aab"):
        AndroidDevice("emulator-5554", PROGRESS).install(Path("app.ipa"))


@pytest.mark.parametrize("reply", ["No activity found\n", ""])
def test_install_needs_launcher_activity(adb, reply):
    adb.rules["resolve-activity"] = reply
    with pytest.raises(DeviceError, match="no launcher activity"):
        AndroidDevice("emulator-5554", PROGRESS).install(Path("app.apk"))


# --- lifecycle ---------------------------------------------------------------------------------


def test_lifecycle_commands(drv, adb):
    drv.launch()
    drv.resume()
    drv.stop()
    drv.clear_data()
    # launching never touches device settings
    assert adb.shell() == [
        "am start -W -n dev.demo/.MainActivity",
        "am start -W -n dev.demo/.MainActivity",
        "am force-stop dev.demo",
        "pm clear dev.demo",
    ]


def test_reinstall(drv, adb):
    drv.reinstall()
    assert adb.shell()[0] == "pm uninstall dev.demo"
    assert any("install -r" in c for c in adb.cmds)


@pytest.mark.parametrize(
    ("reply", "ready"),
    [
        ("  mWakefulness=Awake\n    isKeyguardShowing=false\n", True),
        ("  mWakefulness=Dozing\n    isKeyguardShowing=true\n", False),
        ("  mWakefulness=Awake\n    isKeyguardShowing=true\n", False),  # awake on the lock screen
        ("  mWakefulness=Asleep\n    isKeyguardShowing=false\n", False),
    ],
)
def test_check_ready_reports_a_locked_or_sleeping_phone(drv, adb, reply, ready):
    adb.rules["dumpsys power"] = reply
    if ready:
        drv.check_ready()
    else:
        with pytest.raises(DeviceError, match="asleep or locked: unlock it"):
            drv.check_ready()
    assert not any("keyevent" in c for c in adb.shell())  # never wakes or unlocks it


@pytest.mark.parametrize(
    ("reply", "state"),
    [
        ("1234\n  topResumedActivity=ActivityRecord{1 u0 dev.demo/.MainActivity t9}\n", "foreground"),
        ("1234\n  topResumedActivity=ActivityRecord{1 u0 com.launcher/.Home t1}\n", "background"),
        (
            (
                "1234\n  topResumedActivity=ActivityRecord{2 u0 com.google.android.permissioncontroller/"
                "com.android.permissioncontroller.permission.ui.GrantPermissionsActivity t9}\n"
            ),
            "foreground",
        ),
        ("1234\n  topResumedActivity=ActivityRecord{2 u0 com.android.permissioncontroller/.Grant t9}\n", "foreground"),
        ("1234\n", "foreground"),  # between screens: nobody on top yet, the app has not left
        ("\n", "not_running"),
    ],
)
def test_app_state(drv, adb, reply, state):
    adb.rules["pidof"] = reply
    assert drv.app_state() == state


# --- observe ------------------------------------------------------------------------------------


def test_screen_reads_the_agent_tree(drv, agent):
    agent.replies["/tree"] = LOGIN.replace('<hierarchy rotation="0"', '<hierarchy rotation="0" ime="true"')
    s = drv.screen()
    assert (s.width, s.height, s.keyboard_visible) == (1080, 2424, True)
    assert len(s.elements) == 4
    assert agent.paths() == ["/tree"]


def test_page_scrolls_stay_above_the_keyboard(drv, adb, agent):
    keyboard_up = '<hierarchy rotation="0" ime="true" ime-top="1400"'
    agent.replies["/tree"] = LOGIN.replace('<hierarchy rotation="0"', keyboard_up)
    s = drv.screen()
    assert s.keyboard_top == 1400
    drv.scroll("down", screen=s)
    drag = adb.shell()[-1]
    assert drag.startswith("input motionevent DOWN 540 1120;") and drag.endswith("UP 540 280")  # all above y=1400


def test_screen_in_landscape_swaps_size(drv, agent):
    agent.replies["/tree"] = LOGIN.replace('rotation="0"', 'rotation="1"')
    s = drv.screen()
    assert (s.width, s.height) == (2424, 1080)


def test_size_is_cached_and_validated(drv, adb):
    drv.size()
    drv.size()
    assert adb.shell().count("wm size") == 1
    drv._size = None
    adb.rules["wm size"] = "garbage"
    with pytest.raises(DeviceError, match="screen size"):
        drv.size()


def test_empty_webview_waits_for_its_content(drv, agent):
    agent.replies["/tree"] = [EMPTY_WEB, EMPTY_WEB, WEB]
    assert drv.tree() == WEB
    assert [p.split("?")[0] for p in agent.paths()] == ["/tree", "/change", "/tree", "/change", "/tree"]


def test_webview_wait_follows_the_settle_rule(drv, agent, monkeypatch):
    monkeypatch.setattr(time, "monotonic", lambda: 100.0)
    agent.replies["/tree"] = [EMPTY_WEB, WEB]
    drv.tree()
    assert agent.paths()[1] == "/change?ms=3000"


def test_really_blank_webview_is_accepted(drv, agent, monkeypatch):
    agent.replies["/tree"] = EMPTY_WEB
    ticks = iter([0, 1, 2, 5])
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks))
    assert drv.tree() == EMPTY_WEB


def test_lost_agent(drv, agent):
    agent.replies["/tree"] = OSError("refused")
    with pytest.raises(DeviceError, match="Lost the Android agent during /tree"):
        drv.screen()


def test_waits_are_forwarded_to_the_agent(drv, agent):
    drv.wait_idle(1.5)
    drv.wait_idle(1.5, quiet=0.5)
    drv.wait_change(2)
    assert agent.paths() == ["/idle?ms=1500", "/idle?ms=1500&quiet=500", "/change?ms=2000"]


def test_screenshot(drv, adb, tmp_path):
    adb.rules["screencap"] = "PNGDATA"
    drv.screenshot(tmp_path / "s.png")
    assert (tmp_path / "s.png").read_bytes() == b"PNGDATA"


# --- touch & keys -------------------------------------------------------------------------------


def test_touch_commands(drv, adb):
    drv.tap(1, 2)
    drv.double_tap(3, 4)
    drv.long_press(5, 6, seconds=1.5)
    drv.drag(1, 2, 3, 4)
    assert adb.shell()[:3] == ["input tap 1 2", "input tap 3 4; sleep 0.1; input tap 3 4", "input swipe 5 6 5 6 1500"]
    drag = adb.shell()[3].split("; ")
    assert drag[0] == "input motionevent DOWN 1 2" and len(drag) == 13
    assert drag[-2:] == ["sleep 0.1", "input motionevent UP 3 4"]  # held still before lifting: no fling


FOCUSED = LOGIN.replace('<hierarchy rotation="0"', '<hierarchy rotation="0" ime="true"').replace(
    'focused="false" scrollable="false" long-clickable="false" password="false" selected="false" '
    'bounds="[63,352][1017,499]"',
    'focused="true" scrollable="false" long-clickable="false" password="false" selected="false" '
    'bounds="[63,352][1017,499]"',
)


def test_type_into_field_waits_for_focus_and_keyboard(drv, adb, agent):
    assert FOCUSED != LOGIN
    agent.replies["/tree"] = [LOGIN, FOCUSED]  # right after the tap: not yet focused; then ready
    drv.type_text("hi", at=(540, 425))
    assert [c for c in adb.shell() if c.startswith("input")] == ["input tap 540 425", "input text hi"]
    assert [p.split("?")[0] for p in agent.paths()] == ["/tree", "/change", "/tree"]


def test_type_into_field_that_never_focuses(drv, adb, agent, monkeypatch):
    ticks = iter([0, 1, 4])
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks))
    with pytest.raises(DeviceError, match="did not get keyboard focus"):
        drv.type_text("hi", at=(540, 425))


def test_type_text_escapes(drv, adb):
    drv.type_text("50% off & more\nline2")
    assert adb.shell() == ["input text '50\\%%soff%s&%smore'", "input keyevent 66", "input text line2"]


def test_type_text_blank_lines_are_just_enter(drv, adb):
    drv.type_text("a\n\nb")
    assert adb.shell() == ["input text a", "input keyevent 66", "input keyevent 66", "input text b"]


def test_type_text_rejects_non_ascii(drv):
    with pytest.raises(DeviceError, match="ASCII"):
        drv.type_text("café")


def test_clear_text_deletes_exactly_the_value(drv, adb, agent):
    agent.replies["/tree"] = FOCUSED
    field = Element("text_field", "abc", value="abc", bounds=(63, 352, 1017, 499))
    drv.clear_text(field)
    inputs = [c for c in adb.shell() if c.startswith("input")]
    assert inputs == ["input tap 1009 425", "input keyevent 123 67 67 67"]  # tap at the end, End, 3 deletes


def test_clear_empty_field_only_focuses(drv, adb, agent):
    agent.replies["/tree"] = FOCUSED
    drv.clear_text(Element("text_field", hint="Email", bounds=(63, 352, 1017, 499)))
    assert [c for c in adb.shell() if c.startswith("input")] == ["input tap 1009 425"]


def test_keys(drv, adb):
    drv.key("enter")
    drv.key("82")
    drv.back()
    drv.home()
    assert adb.shell() == ["input keyevent 66", "input keyevent 82", "input keyevent 4", "input keyevent 3"]
    with pytest.raises(DeviceError, match="Unknown key 'hyper'"):
        drv.key("hyper")
    with pytest.raises(DeviceError, match="Unknown key 'Enter'"):  # names are exact
        drv.key("Enter")


@pytest.mark.parametrize(("ime", "pressed"), [("true", True), ("false", False)])
def test_hide_keyboard_only_presses_back_when_open(drv, adb, agent, ime, pressed):
    agent.replies["/tree"] = LOGIN.replace('<hierarchy rotation="0"', f'<hierarchy rotation="0" ime="{ime}"')
    drv.hide_keyboard()
    assert ("input keyevent 4" in adb.shell()) is pressed


# --- device ----------------------------------------------------------------------------------------


def test_device_commands(drv, adb):
    adb.rules["settings get system accelerometer_rotation"] = "1\n"
    adb.rules["settings get system user_rotation"] = "0\n"
    adb.rules["cmd uimode night"] = "Night mode: auto\n"
    adb.rules["settings get global wifi_on"] = "1\n"
    adb.rules["settings get global mobile_data"] = "0\n"
    drv.rotate("landscape")
    drv.open_url("https://x.dev/a b")
    drv.dark_mode(on=True)
    drv.dark_mode(on=False)
    drv.grant("android.permission.CAMERA")
    drv.network(on=False)
    drv.network(on=True)
    assert adb.shell() == [
        "settings get system user_rotation",
        "settings get system accelerometer_rotation",
        "settings put system accelerometer_rotation 0",
        "settings put system user_rotation 1",
        "am start -W -a android.intent.action.VIEW -d 'https://x.dev/a b'",
        "cmd uimode night",
        "cmd uimode night yes",
        "cmd uimode night no",
        "pm grant dev.demo android.permission.CAMERA",
        "settings get global wifi_on",
        "settings get global mobile_data",
        "svc wifi disable; svc data disable",
        "svc wifi enable; svc data enable",
    ]
    n = len(adb.shell())
    drv.close()  # everything the steps changed goes back to how it was
    assert adb.shell()[n:][-3:] == [
        "settings put system user_rotation 0; settings put system accelerometer_rotation 1",
        "cmd uimode night auto",
        "svc wifi enable; svc data disable",
    ]


def test_grant_needs_the_full_permission_name(drv):
    with pytest.raises(
        DeviceError, match="'camera': give the full Android permission name, e.g. android.permission.CAMERA"
    ):
        drv.grant("camera")


def test_unreadable_dark_mode_is_an_error_not_a_guess(drv, adb):
    adb.rules["cmd uimode night"] = "\n"
    with pytest.raises(DeviceError, match="Can't read the device's dark mode setting"):
        drv.dark_mode(on=True)


def test_rotation_restores_the_users_auto_rotate(drv, adb, agent):
    adb.rules["settings get system accelerometer_rotation"] = "1\n"
    drv.rotate("landscape")
    drv.rotate("portrait")
    assert adb.shell().count("settings get system accelerometer_rotation") == 1  # remembered once
    drv.close()
    assert adb.shell()[-1] == "settings delete system user_rotation; settings put system accelerometer_rotation 1"


def test_location_on_emulator_only(drv, adb):
    drv.set_location(37.5, -122.25)
    assert adb.cmds[-1].endswith("emu geo fix -122.25 37.5")
    drv.serial = "R58N"
    with pytest.raises(DeviceError, match="only supported on the Android emulator"):
        drv.set_location(1, 2)


# --- shared helpers from the base class --------------------------------------------------------------


def test_swipe_and_scroll_geometry(drv, adb):
    drv.swipe(Direction.LEFT, element=Element("text", bounds=(0, 0, 100, 100)))
    drv.scroll(Direction.DOWN)
    assert adb.shell()[0].startswith("input motionevent DOWN 85 50;") and adb.shell()[0].endswith("UP 15 50")
    assert adb.shell()[-1].startswith("input motionevent DOWN 540 1939;") and adb.shell()[-1].endswith("UP 540 485")


# --- agent -----------------------------------------------------------------------------------------


def test_agent_started_on_the_forwarded_port(adb, agent):
    d = AndroidDevice("emulator-5554", PROGRESS)
    assert d.port == 7000
    [(cmd, ready)] = agent.started
    assert ready == "ready=1" and cmd[-1] == "dev.jevtest.agent/.Agent" and "7912" in cmd
    assert "am force-stop dev.jevtest.agent" in adb.shell()
    assert not any(" install " in c for c in adb.cmds)  # the right version is already on the device


def test_agent_reinstalled_when_version_differs(adb, agent):
    adb.rules["dumpsys package dev.jevtest.agent"] = "    versionName=old\n"
    AndroidDevice("emulator-5554", PROGRESS)
    assert "pm uninstall dev.jevtest.agent" in adb.shell()
    assert any(c.endswith("install /cache/android-agent-abc123.apk") for c in adb.cmds)


def test_close_quits_agent_and_removes_forward(drv, adb, agent):
    proc = drv.agent
    drv.close()
    assert agent.paths()[-1] == "/quit"
    assert proc.waited == [android.AGENT_STOP_TIMEOUT]  # let the device finish before restoring anything
    assert any(c.endswith("forward --remove tcp:7000") for c in adb.cmds)


def test_close_stops_an_agent_that_does_not_finish(drv, adb, agent):
    drv.agent.exits_on_quit = False
    drv.close()
    assert agent.started[-1] == ("stopped",)
    assert any(c.endswith("forward --remove tcp:7000") for c in adb.cmds)


def test_close_when_agent_already_gone(drv, adb, agent):
    drv.agent.running = False
    drv.close()
    assert "/quit" not in agent.paths()


def test_close_tolerates_agent_error(drv, agent):
    agent.replies["/quit"] = OSError("gone")
    drv.close()


def test_reinstall_needs_an_installed_app(adb):
    with pytest.raises(DeviceError, match="Nothing to reinstall: no app was installed"):
        AndroidDevice("emulator-5554", PROGRESS).reinstall()


def test_restore_puts_back_once(drv, adb):
    adb.rules["settings get system accelerometer_rotation"] = "1\n"
    adb.rules["settings get system user_rotation"] = "0\n"
    drv.rotate(Orientation.LANDSCAPE)
    drv.restore()
    drv.restore()  # already put back: nothing to do
    restores = [c for c in adb.shell() if c.startswith("settings put system user_rotation 0;")]
    assert len(restores) == 1
