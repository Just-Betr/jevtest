import shutil
import time
from pathlib import Path

import pytest

from jevtest.adapters.devices import android
from jevtest.adapters.devices.android import AGENT_ID, KEYCODES, AndroidDevice
from jevtest.adapters.devices.common import AgentRefused, ToolFailed, Undo
from jevtest.domain.failures import DeviceError
from jevtest.domain.kinds import Direction, Orientation
from jevtest.domain.screen import Element
from jevtest.domain.steps import ANDROID_KEYS, KEYS
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
def test_prepare_for_test_reports_a_locked_or_sleeping_phone(drv, adb, reply, ready):
    adb.rules["dumpsys power"] = reply
    if ready:
        drv.prepare_for_test()
    else:
        with pytest.raises(DeviceError, match="asleep or locked: unlock it"):
            drv.prepare_for_test()
    assert not any("keyevent" in c for c in adb.shell())  # never wakes or unlocks it


@pytest.mark.parametrize(
    ("reply", "state"),
    [
        ("1234\n  topResumedActivity=ActivityRecord{1 u0 dev.demo/.MainActivity t9}\n", "foreground"),
        ("1234\n  topResumedActivity=ActivityRecord{1 u0 com.launcher/.Home t1}\n", "background"),
        # Android 12 and older print only mResumedActivity (measured on 12)
        (
            "1234\n    mResumedActivity: ActivityRecord{2 u0 com.google.android.apps.nexuslauncher/.Nexus t11}\n",
            "background",
        ),
        ("1234\n    mResumedActivity: ActivityRecord{2 u0 dev.demo/.MainActivity t34}\n", "foreground"),
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


def test_empty_webview_waits_for_its_content(drv, agent, slept):
    agent.replies["/tree"] = [EMPTY_WEB, EMPTY_WEB, WEB]
    assert drv.tree() == WEB
    assert agent.paths() == ["/tree", "/tree", "/tree"] and slept == [0.25]  # read again every interval


def test_really_blank_webview_is_accepted(drv, agent, monkeypatch):
    agent.replies["/tree"] = EMPTY_WEB
    ticks = iter([0, 1, 2, 5])
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks))
    assert drv.tree() == EMPTY_WEB


def test_lost_agent(drv, agent):
    agent.replies["/tree"] = OSError("refused")
    with pytest.raises(DeviceError, match="Lost the Android agent during /tree"):
        drv.screen()


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
    drv._scroll_drag(1, 2, 3, 4)
    assert adb.shell()[:3] == ["input tap 1 2", "input tap 3 4; sleep 0.1; input tap 3 4", "input swipe 5 6 5 6 1500"]
    swipe, scroll = adb.shell()[3].split("; "), adb.shell()[4].split("; ")
    assert swipe[0] == scroll[0] == "input motionevent DOWN 1 2" and len(swipe) == 12
    assert swipe[-2:] == ["input motionevent MOVE 3 4", "input motionevent UP 3 4"]  # lifted moving: a flick
    assert scroll[-2:] == ["sleep 0.1", "input motionevent UP 3 4"]  # held still before lifting: no fling


FOCUSED = LOGIN.replace('<hierarchy rotation="0"', '<hierarchy rotation="0" ime="true"').replace(
    'focused="false" scrollable="false" long-clickable="false" password="false" selected="false" '
    'bounds="[63,352][1017,499]"',
    'focused="true" scrollable="false" long-clickable="false" password="false" selected="false" '
    'bounds="[63,352][1017,499]"',
)


def test_type_into_field_waits_for_focus_and_keyboard(drv, adb, agent, slept):
    assert FOCUSED != LOGIN
    agent.replies["/tree"] = [LOGIN, FOCUSED]  # right after the tap: not yet focused; then ready
    drv.type_text("hi", at=(540, 425))
    assert [c for c in adb.shell() if c.startswith("input")] == ["input tap 540 425", "input text hi"]
    assert agent.paths() == ["/tree", "/tree"] and slept == [0.25]


def test_a_focused_field_scrolled_to_nothing_by_the_keyboard_still_takes_keys(drv, adb, agent):
    """Measured on a Pixel 4a on its side: the web page put the focused field at the keyboard's edge, 0 px tall."""
    squeezed = FOCUSED.replace('bounds="[63,352][1017,499]"', 'bounds="[66,345][2139,345]"')
    assert squeezed != FOCUSED
    agent.replies["/tree"] = squeezed
    drv.type_text("hi", at=(540, 425))
    assert [c for c in adb.shell() if c.startswith("input")] == ["input tap 540 425", "input text hi"]


def test_type_into_field_that_never_focuses(drv, adb, agent, monkeypatch):
    ticks = iter([0, 1, 4])
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks))
    with pytest.raises(DeviceError, match="did not get keyboard focus"):
        drv.type_text("hi", at=(540, 425))


def test_type_text_escapes(drv, adb):
    """`input text` types %s as a space, with no escape: a piece ends after each %, so the text's own %s survives."""
    drv.type_text("50% off & more\nline2 %s")
    assert adb.shell() == [
        "input text 50%; input text '%soff%s&%smore'",
        "input keyevent 66",
        "input text line2%s%; input text s",
    ]


def test_type_text_blank_lines_are_just_enter(drv, adb):
    drv.type_text("a\n\nb")
    assert adb.shell() == ["input text a", "input keyevent 66", "input keyevent 66", "input text b"]


def test_letters_adb_cant_type_are_put_in_by_the_agent(drv, adb, agent):
    """`input text` types only a US keyboard's keys: a line with any other letter goes to the agent, whole."""
    agent.replies["/insert"] = "inserted"
    drv.type_text("José + Zoë 日本\nplain 50%")
    # the agent decides what's a text field by jevtest's own list, so both agree
    assert agent.paths()[-1] == (
        "/insert?text=Jos%C3%A9%20%2B%20Zo%C3%AB%20%E6%97%A5%E6%9C%AC&fields=AutoCompleteTextView,EditText"
    )
    assert adb.shell() == ["input keyevent 66", "input text plain%s50%"]  # ASCII lines still key by key


def test_text_the_agent_couldnt_put_in_says_why(drv, agent):
    agent.replies["/insert"] = "no text field has input focus"
    with pytest.raises(DeviceError, match="^Couldn't type 'café': no text field has input focus$"):
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
    assert adb.shell() == [
        "input keyevent 66",
        "input keyevent 82",
        "input keyevent 4",
        "input keyevent 3",
        "pidof dev.demo; dumpsys activity activities | grep -m1 -E 'topResumedActivity=|mResumedActivity: '",
    ]
    with pytest.raises(DeviceError, match="Unknown key 'hyper'"):
        drv.key("hyper")
    with pytest.raises(DeviceError, match="Unknown key 'Enter'"):  # names are exact
        drv.key("Enter")


def test_android_presses_every_key_a_test_file_may_name():
    assert set(KEYCODES) == {*KEYS, *ANDROID_KEYS}


@pytest.mark.parametrize(("ime", "pressed"), [("true", True), ("false", False)])
def test_hide_keyboard_only_presses_back_when_open(drv, adb, agent, ime, pressed):
    agent.replies["/tree"] = [LOGIN.replace('<hierarchy rotation="0"', f'<hierarchy rotation="0" ime="{ime}"'), LOGIN]
    drv.hide_keyboard()
    assert ("input keyevent 4" in adb.shell()) is pressed


def test_hide_keyboard_waits_until_the_keyboard_is_gone(drv, adb, agent, slept):
    """Back only starts closing it: a screen read right after can still show the keyboard, or half of it."""
    up = LOGIN.replace('<hierarchy rotation="0"', '<hierarchy rotation="0" ime="true"')
    agent.replies["/tree"] = [up, up, up, LOGIN]
    drv.hide_keyboard()
    assert agent.paths() == ["/tree"] * 4 and slept == [0.25, 0.25]


def test_hide_keyboard_fails_when_the_keyboard_stays(drv, adb, agent, monkeypatch):
    agent.replies["/tree"] = LOGIN.replace('<hierarchy rotation="0"', '<hierarchy rotation="0" ime="true"')
    ticks = iter([0, 1, 4])
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks))
    with pytest.raises(DeviceError, match="The keyboard did not close within 3 seconds"):
        drv.hide_keyboard()


# --- device ----------------------------------------------------------------------------------------


def test_device_commands(drv, adb, agent):
    agent.replies["/tree"] = LOGIN.replace('rotation="0"', 'rotation="1"')  # the screen turns at once
    adb.rules["settings get system accelerometer_rotation"] = "1\n"
    adb.rules["settings get system user_rotation"] = "0\n"
    adb.rules["cmd uimode night"] = "Night mode: auto\n"
    adb.rules["settings get global wifi_on"] = "1\n"
    adb.rules["settings get global mobile_data"] = "0\n"
    adb.rules["settings get secure autofill_service"] = "com.google.android.gms/.autofill.service.AutofillService\n"
    drv.rotate("landscape")
    drv.open_url("https://x.dev/a b")
    drv.dark_mode(on=True)
    drv.dark_mode(on=False)
    drv.grant(["android.permission.CAMERA"])
    drv.network(on=False)
    drv.network(on=True)
    drv.autofill_off()
    assert adb.shell() == [
        "settings get system user_rotation",
        "settings get system accelerometer_rotation",
        "am start -W -a android.intent.action.VIEW -d 'https://x.dev/a b'",
        "cmd uimode night",
        "cmd uimode night yes",
        "cmd uimode night no",
        "pm grant dev.demo android.permission.CAMERA",
        "dumpsys package dev.demo",  # Android recorded it as granted
        "settings get global wifi_on",
        "settings get global mobile_data",
        "svc wifi disable; svc data disable",
        "svc wifi enable; svc data enable",
        "settings get secure autofill_service",
        "settings delete secure autofill_service",
    ]
    n = len(adb.shell())
    drv.close()  # everything the steps changed goes back to how it was
    assert adb.shell()[n:][-4:] == [
        "settings put system user_rotation 0; settings put system accelerometer_rotation 1; wm user-rotation free",
        "cmd uimode night auto",
        "svc wifi enable; svc data disable",
        "settings put secure autofill_service com.google.android.gms/.autofill.service.AutofillService",
    ]


def test_autofill_off_on_a_device_with_none_puts_back_none(drv, adb):
    adb.rules["settings get secure autofill_service"] = "null\n"
    drv.autofill_off()
    n = len(adb.shell())
    drv.close()
    assert "settings delete secure autofill_service" in adb.shell()[n:]


def test_unreadable_dark_mode_is_an_error_not_a_guess(drv, adb):
    adb.rules["cmd uimode night"] = "\n"
    with pytest.raises(DeviceError, match="Can't read the device's dark mode setting"):
        drv.dark_mode(on=True)


def test_rotation_restores_the_users_auto_rotate(drv, adb, agent):
    adb.rules["settings get system accelerometer_rotation"] = "1\n"
    agent.replies["/tree"] = [LOGIN.replace('rotation="0"', 'rotation="1"'), LOGIN]
    drv.rotate("landscape")
    drv.rotate("portrait")
    assert adb.shell().count("settings get system accelerometer_rotation") == 1  # remembered once
    drv.close()
    assert (
        adb.shell()[-1]
        == "settings delete system user_rotation; settings put system accelerometer_rotation 1; wm user-rotation free"
    )


def test_rotate_returns_once_the_screen_has_turned(drv, adb, agent, slept):
    """The setting takes effect a moment later; a screen read before then would show the old layout."""
    turned = LOGIN.replace('rotation="0"', 'rotation="1"')
    agent.replies["/tree"] = [LOGIN, LOGIN, turned]
    drv.rotate("landscape")
    assert agent.paths() == ["/rotate?to=1", "/tree", "/tree", "/tree"] and slept == [0.25, 0.25]


def test_rotate_fails_when_the_device_refuses(drv, agent):
    agent.replies["/rotate"] = "refused"
    with pytest.raises(DeviceError, match="refused to turn the screen to landscape"):
        drv.rotate("landscape")


def test_rotate_fails_when_the_screen_never_turns(drv, adb, agent, monkeypatch):
    ticks = iter([0, 1, 4])
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks))
    with pytest.raises(DeviceError, match="did not turn to landscape within 3 seconds"):
        drv.rotate("landscape")


def test_location_on_emulator_only(drv, adb):
    drv.set_location(37.5, -122.25)
    assert adb.cmds[-1].endswith("emu geo fix -122.25 37.5")
    drv.serial = "R58N"
    with pytest.raises(DeviceError, match="only supported on the Android emulator"):
        drv.set_location(1, 2)


# --- shared helpers from the base class --------------------------------------------------------------


def test_android_has_no_picker_wheels(drv):
    with pytest.raises(DeviceError, match=r"^Android has no picker wheels to turn to 'Blue': tap picker 'Size'"):
        drv.choose(Element("picker", "Size"), "Blue")


def test_a_swipe_inward_from_a_side_starts_clear_of_the_back_gesture(drv, adb):
    """Android takes a swipe inward from either side as back (measured: from 78 of 1080 pixels): it starts 15% in."""
    drv.swipe(Direction.RIGHT, element=Element("text", bounds=(0, 0, 100, 100)))
    drv.swipe(Direction.LEFT, element=Element("text", bounds=(980, 0, 1080, 100)))
    drv.swipe(Direction.UP, element=Element("text", bounds=(0, 2300, 100, 2424)))  # home is up from the bottom
    drv.swipe(Direction.DOWN, element=Element("text", bounds=(0, 0, 100, 100)))  # the notifications, down from the top
    right, left, up, down = [c for c in adb.shell() if c.startswith("input motionevent")]
    assert right.startswith("input motionevent DOWN 162 50;") and right.endswith("UP 232 50")
    assert left.startswith("input motionevent DOWN 918 50;") and left.endswith("UP 848 50")
    assert up.startswith("input motionevent DOWN 50 2230;") and up.endswith("UP 50 2156")
    assert down.startswith("input motionevent DOWN 50 194;") and down.endswith("UP 50 254")


def test_swipe_and_scroll_geometry(drv, adb):
    drv.swipe(Direction.LEFT, element=Element("text", bounds=(0, 0, 100, 100)))
    drv.scroll(Direction.DOWN)
    swipe, scroll = [c for c in adb.shell() if c.startswith("input motionevent")]
    assert swipe.startswith("input motionevent DOWN 85 50;") and swipe.endswith("UP 15 50")  # outward: the app's
    assert scroll.startswith("input motionevent DOWN 540 1939;") and scroll.endswith("UP 540 485")


def test_a_swipe_on_a_slider_drags_its_thumb_slowly_on_to_the_screen_edge(drv, adb):
    """As on iOS: from the thumb's middle (0.5 of a Compose slider 116 pixels tall), on past the slider's end."""
    drv.swipe(Direction.RIGHT, element=Element("slider", bounds=(42, 903, 1038, 1019), position=0.5))
    drv.swipe(Direction.LEFT, element=Element("slider", bounds=(42, 903, 1038, 1019), position=0.0))
    assert adb.shell()[-2].startswith("input motionevent DOWN 540 961;") and adb.shell()[-2].endswith("UP 1079 961")
    assert adb.shell()[-1].startswith("input motionevent DOWN 100 961;") and adb.shell()[-1].endswith("UP 0 961")


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


def test_restore_puts_back_once(drv, adb, agent):
    agent.replies["/tree"] = LOGIN.replace('rotation="0"', 'rotation="1"')
    adb.rules["settings get system accelerometer_rotation"] = "1\n"
    adb.rules["settings get system user_rotation"] = "0\n"
    drv.rotate(Orientation.LANDSCAPE)
    drv.restore()
    drv.restore()  # already put back: nothing to do
    restores = [c for c in adb.shell() if c.startswith("settings put system user_rotation 0;")]
    assert len(restores) == 1


def test_looks_is_a_fingerprint_of_the_elements_pixels(drv, agent):
    agent.replies["/pixels"] = "3fa9"
    ok, title = Element("button", "OK", bounds=(10, 20, 110, 70)), Element("text", "Hi", bounds=(0, 0, 5, 6))
    assert drv.looks([ok, title]) == "3fa9"
    assert agent.paths()[-1] == "/pixels?rects=10,20,110,70;0,0,5,6"  # one screenshot for all of them
    calls = len(agent.paths())
    assert drv.looks([]) == ""  # nothing to look at: no screenshot
    assert len(agent.paths()) == calls


def test_a_link_no_app_opens_says_so(drv, adb):
    # Android 13 prints this and still exits 0 (measured), so the output is what counts
    adb.rules["am start -W -a android.intent.action.VIEW"] = (
        "Starting: Intent { dat=x:// }\nError: Activity not started, unable to resolve Intent { dat=x:// }\n"
    )
    with pytest.raises(DeviceError, match="^No app on the device opens x://y: check the link"):
        drv.open_url("x://y")
    adb.rules["am start -W -a android.intent.action.VIEW"] = "Starting: Intent\nError: Activity class does not exist\n"
    with pytest.raises(DeviceError, match="^Could not open x://y: Activity class does not exist$"):
        drv.open_url("x://y")
    # Android 14+ exits 1 with the same words (measured on the emulator)
    adb.rules["am start -W -a android.intent.action.VIEW"] = ToolFailed(
        ["adb", "shell", "am start"], 1, "Starting: Intent\nError: Activity not started, unable to resolve Intent\n"
    )
    with pytest.raises(DeviceError, match="^No app on the device opens x://y"):
        drv.open_url("x://y")
    adb.rules["am start -W"] = ToolFailed(["adb", "shell", "am start"], 1, "adb: device offline")
    with pytest.raises(ToolFailed, match="device offline"):  # not something am said: as it came
        drv.launch()


def test_a_permission_that_cant_be_granted_says_why(drv, adb):
    adb.rules["pm grant"] = ToolFailed(
        ["adb", "shell", "pm grant"],
        255,
        "\nException occurred while executing 'grant':\n"
        "java.lang.SecurityException: Permission android.permission.INTERNET requested by package dev.demo is not a "
        "changeable permission type\n\tat com.android.server...",
    )
    with pytest.raises(
        DeviceError,
        match="^Can't grant android.permission.INTERNET: Permission .* is not a changeable permission type$",
    ):
        drv.grant(["android.permission.INTERNET"])
    adb.rules["pm grant"] = ToolFailed(["adb", "shell", "pm grant"], 1, "adb: device offline")
    with pytest.raises(
        DeviceError, match="^Can't grant android.permission.CAMERA: adb shell pm grant failed .*offline$"
    ):
        drv.grant(["android.permission.CAMERA"])


def test_an_agent_that_stopped_is_started_again_before_the_next_test(drv, adb):
    adb.rules["dumpsys power"] = "  mWakefulness=Awake\n    isKeyguardShowing=false\n"
    first = drv.agent
    drv.prepare_for_test()  # running: nothing to do
    assert drv.agent is first
    first.running = False  # something stopped it, such as another UI Automation tool
    drv.prepare_for_test()
    assert drv.agent is not first and drv.agent.poll() is None
    assert any("forward --remove tcp:" in c for c in adb.cmds)  # the old port forward goes


def test_what_a_killed_run_left_changed_is_put_back_first(adb, agent):
    Undo("emulator-5554").remember("dark mode", lambda: "cmd uimode night no")  # then the run was killed (kill -9)
    told: list[str] = []
    d = AndroidDevice("emulator-5554", told.append)
    shell = adb.shell()
    # after the old agent stops (which resets the rotation state), before the new one starts
    assert shell.index(f"am force-stop {AGENT_ID}") < shell.index("cmd uimode night no")
    assert told[-1] == "putting back what a run that was stopped left changed: dark mode"
    assert d._undo.left_by_a_stopped_run() == {}  # put back, and forgotten


def test_an_entry_another_version_wrote_is_named_not_run(adb, agent):
    undo = Undo("emulator-5554")
    undo.remember("network", lambda: {"wifi": True})  # not a shell command
    undo.remember("dark mode", lambda: "cmd uimode night no")
    told: list[str] = []
    AndroidDevice("emulator-5554", told.append)
    assert "cmd uimode night no" in adb.shell()
    assert told[-1] == "can't put back network (another jevtest version changed it): set it by hand"


def test_several_permissions_are_granted_in_turn(drv, adb):
    drv.grant(["android.permission.CAMERA", "android.permission.RECORD_AUDIO"])
    assert [c for c in adb.shell() if c.startswith("pm grant")] == [
        "pm grant dev.demo android.permission.CAMERA",
        "pm grant dev.demo android.permission.RECORD_AUDIO",
    ]


@pytest.mark.parametrize(
    ("record", "why"),
    [
        (
            "    requested permissions:\n      android.permission.INTERNET\n",
            "the app doesn't declare it in its manifest",
        ),
        (
            (
                "    requested permissions:\n      android.permission.CAMERA\n    runtime permissions:\n"
                "      android.permission.CAMERA: granted=false, flags=[ USER_FIXED ]\n"
            ),
            "Android didn't record it as granted",
        ),
    ],
)
def test_a_grant_android_didnt_record_fails(drv, adb, record, why):
    """Android 17 answers `pm grant` of an undeclared permission with no error, and grants nothing (measured)."""
    adb.rules["dumpsys package dev.demo"] = record
    with pytest.raises(DeviceError, match=f"^Can't grant android.permission.CAMERA: {why}$"):
        drv.grant(["android.permission.CAMERA"])


def test_a_request_the_agent_couldnt_do_says_why_rather_than_lost(drv, agent):
    """The agent answers what went wrong; only a request that gets no answer means something stopped it."""
    agent.replies["/pixels"] = "error: java.lang.IllegalArgumentException: not x1,y1,x2,y2: 1,2"
    with pytest.raises(AgentRefused, match=r"^Android agent /pixels: java.lang.IllegalArgumentException: not x1"):
        drv.looks([Element("button", "OK", bounds=(10, 20, 110, 70))])
