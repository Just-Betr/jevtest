from pathlib import Path

import pytest

from jevtest.drivers import android
from jevtest.drivers.android import AndroidDriver, has_empty_webview, parse_hierarchy
from jevtest.drivers.base import DriverError
from jevtest.screen import Element

FIX = Path(__file__).parent / "fixtures"
LOGIN = (FIX / "android_login.xml").read_text()
WEB = (FIX / "android_webview.xml").read_text()
EMPTY_WEB = ('<hierarchy rotation="0"><node class="android.widget.FrameLayout" bounds="[0,0][100,100]">'
             '<node class="android.webkit.WebView" bounds="[0,0][100,100]"/></node></hierarchy>')


class Adb:
    """Stands in for `run`: answers by the first matching substring, records every command."""

    def __init__(self, rules=None):
        self.rules = dict(rules or {})
        self.cmds: list[str] = []

    def __call__(self, cmd, timeout=120, check=True, binary=False):
        line = " ".join(map(str, cmd))
        self.cmds.append(line)
        for needle, reply in self.rules.items():
            if needle in line:
                if isinstance(reply, Exception):
                    raise reply
                reply = reply.pop(0) if isinstance(reply, list) else reply
                return reply.encode() if binary else reply
        return b"" if binary else ""

    def shell(self):
        return [c.split(" shell ", 1)[1] for c in self.cmds if " shell " in c]


@pytest.fixture
def adb(monkeypatch):
    fake = Adb({"adb devices": "List of devices attached\nemulator-5554\tdevice\nR58N\tunauthorized\n",
                "wm size": "Physical size: 1080x2424\n",
                "aapt2": "dev.demo\n",
                "resolve-activity": "priority=0\ndev.demo/.MainActivity\n"})
    monkeypatch.setattr(android, "run", fake)
    monkeypatch.setattr(android.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(android.time, "sleep", lambda s: None)
    return fake


@pytest.fixture
def drv(adb):
    d = AndroidDriver()
    d.install(Path("app.apk"))
    adb.cmds.clear()
    return d


# --- parsing real dumps ------------------------------------------------------------------

def test_parse_login_screen():
    els = parse_hierarchy(LOGIN, 1080, 2424)
    assert [(e.kind, e.text, e.hint) for e in els] == [
        ("text", "Sign in", ""), ("text_field", "", "Email"), ("password_field", "", "Password"),
        ("button", "Sign in", "")]
    email = els[1]
    assert email.editable and email.enabled and email.bounds == (63, 352, 1017, 499)


def test_parse_webview_content():
    els = parse_hierarchy(WEB, 1080, 2424)
    kinds = {(e.kind, e.text or e.hint) for e in els}
    assert {("text", "Web Greeter"), ("text_field", "Your name"), ("button", "Say hello"),
            ("checkbox", "I agree to the terms")} <= kinds
    assert next(e for e in els if e.kind == "checkbox").checked is False


def test_parse_rules():
    xml = """<hierarchy rotation="0">
      <node class="android.widget.TextView" package="com.android.systemui" text="12:00" bounds="[0,0][100,50]"/>
      <node class="android.widget.TextView" text="Off screen" bounds="[0,3000][100,3100]"/>
      <node class="android.widget.TextView" text="Partly off" bounds="[-50,10][60,40]"/>
      <node class="android.widget.FrameLayout" bounds="[0,0][100,100]"/>
      <node class="android.widget.Button" text="Go" content-desc="Go now" clickable="true" enabled="false"
            bounds="[0,0][100,100]"/>
      <node class="android.widget.Switch" checkable="true" checked="true" text="Wifi" bounds="[0,0][100,100]"/>
      <node class="android.view.View" clickable="true" content-desc="Card" bounds="[0,0][100,100]"/>
      <node class="android.view.View" content-desc="Label" bounds="[0,0][100,100]"/>
      <node class="androidx.recyclerview.widget.RecyclerView" scrollable="true" bounds="[0,0][100,100]"/>
      <node class="com.custom.Thing" resource-id="dev.demo:id/thing" bounds="[0,0][100,100]"/>
      <node class="android.widget.TextView" text="  lots   of
            space " bounds="[0,0][100,100]"/>
      <node class="android.widget.TextView" text="no bounds"/>
    </hierarchy>"""
    els = parse_hierarchy(xml, 1080, 2424)
    assert [(e.kind, e.text) for e in els] == [
        ("text", "Partly off"), ("button", "Go (Go now)"), ("switch", "Wifi"), ("button", "Card"), ("text", "Label"),
        ("list", ""), ("thing", ""), ("text", "lots of space")]
    assert els[0].bounds == (0, 10, 60, 40)
    assert els[1].enabled is False and els[2].checked is True and els[5].scrollable
    assert els[6].resource_id == "thing"


def test_empty_webview_detection():
    assert has_empty_webview(EMPTY_WEB)
    assert not has_empty_webview(WEB)
    assert not has_empty_webview(LOGIN)


# --- tools and devices ---------------------------------------------------------------------

def test_sdk_root(monkeypatch, tmp_path):
    monkeypatch.setenv("ANDROID_HOME", str(tmp_path))
    assert android.sdk_root() == tmp_path
    monkeypatch.delenv("ANDROID_HOME")
    monkeypatch.delenv("ANDROID_SDK_ROOT", raising=False)
    monkeypatch.setattr(android.Path, "home", lambda: tmp_path)
    assert android.sdk_root() is None
    (tmp_path / "Android/Sdk").mkdir(parents=True)
    assert android.sdk_root() == tmp_path / "Android/Sdk"


def test_tools_found_in_sdk(monkeypatch, tmp_path):
    monkeypatch.setattr(android.shutil, "which", lambda n: None)
    monkeypatch.setenv("ANDROID_HOME", str(tmp_path))
    for version in ("34.0.0", "36.1.0"):
        (tmp_path / "build-tools" / version).mkdir(parents=True)
        (tmp_path / "build-tools" / version / "aapt2").write_text("")
    (tmp_path / "platform-tools").mkdir()
    (tmp_path / "platform-tools/adb").write_text("")
    assert android.aapt2_path().endswith("36.1.0/aapt2")
    assert android.adb_path() == str(tmp_path / "platform-tools/adb")


def test_tool_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(android.shutil, "which", lambda n: None)
    monkeypatch.setattr(android, "sdk_root", lambda: None)
    with pytest.raises(DriverError, match="adb not found"):
        android.adb_path()


def test_devices_only_lists_ready_ones(adb):
    assert android.devices() == ["emulator-5554"]


def test_bootable_avds(tmp_path):
    root, home = tmp_path / "sdk", tmp_path / "avd"
    (root / "system-images/android-37/x").mkdir(parents=True)
    for name, sysdir in (("Good", "system-images/android-37/x/"), ("Missing", "system-images/nope/"),
                         ("NoConfig", None)):
        (home / f"{name}.avd").mkdir(parents=True)
        if sysdir:
            (home / f"{name}.avd/config.ini").write_text(f"hw.keyboard=yes\nimage.sysdir.1={sysdir}\n")
    assert android.bootable_avds(["Missing", "NoConfig", "Good"], root, home) == ["Good"]


def emulator_env(monkeypatch, tmp_path, avds="Good\n", boot=("0", "1")):
    root = tmp_path / "sdk"
    (root / "emulator").mkdir(parents=True)
    (root / "emulator/emulator").write_text("")
    (root / "img").mkdir()
    home = tmp_path / "avd"
    (home / "Good.avd").mkdir(parents=True)
    (home / "Good.avd/config.ini").write_text("image.sysdir.1=img\n")
    monkeypatch.setenv("ANDROID_HOME", str(root))
    monkeypatch.setenv("ANDROID_AVD_HOME", str(home))
    monkeypatch.setattr(android.shutil, "which", lambda n: "/bin/adb" if n == "adb" else None)
    started = []
    monkeypatch.setattr(android.subprocess, "Popen", lambda cmd, **kw: started.append((cmd, kw)))
    fake = Adb({"-list-avds": avds, "adb devices": "List\nemulator-5554\tdevice\n",
                "sys.boot_completed": list(boot)})
    monkeypatch.setattr(android, "run", fake)
    monkeypatch.setattr(android.time, "sleep", lambda s: None)
    return started, root


def test_start_emulator_boots_first_good_avd(monkeypatch, tmp_path):
    started, root = emulator_env(monkeypatch, tmp_path)
    assert android.start_emulator() == "emulator-5554"
    cmd, kw = started[0]
    assert cmd[1:3] == ["-avd", "Good"] and kw["env"]["ANDROID_SDK_ROOT"] == str(root)


def test_start_emulator_no_avds(monkeypatch, tmp_path):
    emulator_env(monkeypatch, tmp_path, avds="")
    with pytest.raises(DriverError, match="no bootable AVD"):
        android.start_emulator()


def test_start_emulator_timeout(monkeypatch, tmp_path):
    emulator_env(monkeypatch, tmp_path, boot=["0"] * 50)
    ticks = iter(range(0, 1000, 100))
    monkeypatch.setattr(android.time, "monotonic", lambda: next(ticks))
    with pytest.raises(DriverError, match="did not boot within 180s"):
        android.start_emulator()


def test_start_emulator_without_sdk(monkeypatch):
    monkeypatch.setattr(android, "sdk_root", lambda: None)
    monkeypatch.setattr(android.shutil, "which", lambda n: None)
    with pytest.raises(DriverError, match="no emulator found"):
        android.start_emulator()


# --- driver setup ---------------------------------------------------------------------------

def test_picks_first_device(adb):
    assert AndroidDriver().serial == "emulator-5554"


def test_named_device_must_be_connected(adb):
    with pytest.raises(DriverError, match="R58N not connected"):
        AndroidDriver("R58N")
    assert AndroidDriver("emulator-5554").serial == "emulator-5554"


def test_no_device_boots_emulator(adb, monkeypatch):
    adb.rules["adb devices"] = "List of devices attached\n"
    monkeypatch.setattr(android, "start_emulator", lambda: "emulator-5556")
    assert AndroidDriver().serial == "emulator-5556"


def test_install_apk(adb):
    d = AndroidDriver()
    assert d.install(Path("app.apk")) == "dev.demo"
    assert d.activity == "dev.demo/.MainActivity"
    assert any("install -r -g -t app.apk" in c for c in adb.cmds)


def test_install_aab(adb, monkeypatch):
    adb.rules["dump manifest"] = "dev.bundle\n"
    adb.rules["resolve-activity"] = "dev.bundle/.Main\n"
    d = AndroidDriver()
    assert d.install(Path("app.aab")) == "dev.bundle"
    assert any("build-apks" in c for c in adb.cmds) and any("install-apks" in c for c in adb.cmds)


def test_install_aab_needs_bundletool(adb, monkeypatch):
    monkeypatch.setattr(android.shutil, "which", lambda n: None)
    d = AndroidDriver.__new__(AndroidDriver)
    d.adb, d.serial = "/bin/adb", "emulator-5554"
    with pytest.raises(DriverError, match="bundletool"):
        d.install(Path("app.aab"))


def test_install_rejects_other_files(adb):
    with pytest.raises(DriverError, match="needs an .apk or .aab"):
        AndroidDriver().install(Path("app.ipa"))


@pytest.mark.parametrize("reply", ["No activity found\n", ""])
def test_install_needs_launcher_activity(adb, reply):
    adb.rules["resolve-activity"] = reply
    with pytest.raises(DriverError, match="no launcher activity"):
        AndroidDriver().install(Path("app.apk"))


# --- lifecycle ---------------------------------------------------------------------------------

def test_lifecycle_commands(drv, adb):
    drv.launch()
    drv.resume()
    drv.stop()
    drv.clear_data()
    assert adb.shell() == ["settings put system accelerometer_rotation 0", "am start -W -n dev.demo/.MainActivity",
                           "am start -W -n dev.demo/.MainActivity", "am force-stop dev.demo", "pm clear dev.demo"]


def test_reinstall(drv, adb):
    drv.reinstall()
    assert adb.shell()[0] == "pm uninstall dev.demo"
    assert any("install -r" in c for c in adb.cmds)


@pytest.mark.parametrize("reply,state", [
    ("1234\n  topResumedActivity=ActivityRecord{1 u0 dev.demo/.MainActivity t9}\n", "foreground"),
    ("1234\n  topResumedActivity=ActivityRecord{1 u0 com.launcher/.Home t1}\n", "background"),
    ("\n", "not_running")])
def test_app_state(drv, adb, reply, state):
    adb.rules["pidof"] = reply
    assert drv.app_state() == state


# --- observe ------------------------------------------------------------------------------------

def test_screen(drv, adb):
    adb.rules["uiautomator"] = "noise " + LOGIN
    adb.rules["mInputShown"] = "  mInputShown=true\n"
    adb.rules["pidof"] = "1\n dev.demo/.M\n"
    s = drv.screen()
    assert (s.width, s.height, s.keyboard_visible, s.app_running) == (1080, 2424, True, True)
    assert len(s.elements) == 4


def test_screen_in_landscape_swaps_size(drv, adb):
    adb.rules["uiautomator"] = LOGIN.replace('rotation="0"', 'rotation="1"')
    s = drv.screen()
    assert (s.width, s.height) == (2424, 1080)


def test_size_is_cached_and_validated(drv, adb):
    drv.size()
    drv.size()
    assert adb.shell().count("wm size") == 1
    drv._size = None
    adb.rules["wm size"] = "garbage"
    with pytest.raises(DriverError, match="screen size"):
        drv.size()


def test_dump_retries_until_ready(drv, adb):
    adb.rules["uiautomator"] = ["ERROR: could not get idle state", EMPTY_WEB, WEB]
    assert drv.dump() == WEB


def test_dump_accepts_a_blank_webview_eventually(drv, adb):
    adb.rules["uiautomator"] = [EMPTY_WEB] * 4
    assert drv.dump() == EMPTY_WEB


def test_dump_fails(drv, adb):
    adb.rules["uiautomator"] = "ERROR"
    with pytest.raises(DriverError, match="dump failed"):
        drv.dump()


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
    assert adb.shell() == ["input tap 1 2", "input tap 3 4; sleep 0.1; input tap 3 4", "input swipe 5 6 5 6 1500",
                           "input swipe 1 2 3 4 300"]


def test_type_text_escapes(drv, adb):
    drv.type_text("50% off & more\nline2")
    assert adb.shell() == ["input text '50\\%%soff%s&%smore'", "input keyevent 66", "input text line2"]


def test_type_text_blank_lines_are_just_enter(drv, adb):
    drv.type_text("a\n\nb")
    assert adb.shell() == ["input text a", "input keyevent 66", "input keyevent 66", "input text b"]


def test_type_text_rejects_non_ascii(drv):
    with pytest.raises(DriverError, match="ASCII"):
        drv.type_text("café")


def test_clear_text(drv, adb):
    drv.clear_text(Element("text_field", "abc", bounds=(0, 0, 10, 10)))
    assert adb.shell()[0] == "input tap 5 5"
    assert adb.shell()[1] == "input keyevent 123 " + " ".join(["67"] * 13)


def test_keys(drv, adb):
    drv.key("Enter")
    drv.key("82")
    drv.back()
    drv.home()
    assert adb.shell() == ["input keyevent 66", "input keyevent 82", "input keyevent 4", "input keyevent 3"]
    with pytest.raises(DriverError, match="Unknown key 'hyper'"):
        drv.key("hyper")


@pytest.mark.parametrize("shown,pressed", [("mInputShown=true", True), ("mInputShown=false", False)])
def test_hide_keyboard_only_presses_back_when_open(drv, adb, shown, pressed):
    adb.rules["mInputShown"] = shown
    drv.hide_keyboard()
    assert ("input keyevent 4" in adb.shell()) is pressed


# --- device ----------------------------------------------------------------------------------------

def test_device_commands(drv, adb):
    drv.rotate("landscape")
    drv.open_url("https://x.dev/a b")
    drv.dark_mode(True)
    drv.dark_mode(False)
    drv.grant("camera")
    drv.grant("com.custom.PERM")
    drv.network(False)
    assert adb.shell() == [
        "settings put system accelerometer_rotation 0", "settings put system user_rotation 1",
        "am start -W -a android.intent.action.VIEW -d 'https://x.dev/a b'",
        "cmd uimode night yes", "cmd uimode night no",
        "pm grant dev.demo android.permission.CAMERA", "pm grant dev.demo com.custom.PERM",
        "svc wifi disable; svc data disable"]


def test_rotate_rejects_unknown(drv):
    with pytest.raises(DriverError, match="Unknown orientation"):
        drv.rotate("diagonal")


def test_location_on_emulator_only(drv, adb):
    drv.set_location(37.5, -122.25)
    assert adb.cmds[-1].endswith("emu geo fix -122.25 37.5")
    drv.serial = "R58N"
    with pytest.raises(DriverError, match="only supported on the Android emulator"):
        drv.set_location(1, 2)


# --- shared helpers from the base class --------------------------------------------------------------

def test_swipe_and_scroll_geometry(drv, adb):
    adb.rules["uiautomator"] = LOGIN
    drv.swipe("left", el=Element("text", bounds=(0, 0, 100, 100)))
    drv.scroll("down")
    assert adb.shell()[0] == "input swipe 85 50 15 50 300"
    assert adb.shell()[-1] == "input swipe 540 1939 540 485 300"


def test_bad_directions(drv):
    with pytest.raises(DriverError):
        drv.swipe("diagonal", el=Element("text", bounds=(0, 0, 1, 1)))
    with pytest.raises(DriverError):
        drv.scroll("inward")
