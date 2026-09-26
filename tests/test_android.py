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


class AgentHttp:
    """Stands in for the on-device agent: replies by path, records every request."""

    def __init__(self):
        self.replies: dict[str, object] = {"/tree": LOGIN, "/idle": "idle", "/change": "changed", "/quit": "bye"}
        self.urls: list[str] = []

    def __call__(self, url, timeout):
        self.urls.append(url)
        reply = self.replies[url.split("7000", 1)[1].split("?")[0]]
        reply = reply.pop(0) if isinstance(reply, list) else reply
        if isinstance(reply, Exception):
            raise reply
        return reply

    def paths(self):
        return [u.split("7000", 1)[1] for u in self.urls]


class Proc:
    def __init__(self):
        self.running = True

    def poll(self):
        return None if self.running else 0


@pytest.fixture
def agent(monkeypatch):
    fake = AgentHttp()
    fake.started = []
    monkeypatch.setattr(android, "http_get", fake)
    monkeypatch.setattr(android, "build_agent", lambda: Path("/cache/android-agent-abc123.apk"))

    def start_process(cmd, ready, log, timeout):
        fake.started.append((cmd, ready))
        return Proc()
    monkeypatch.setattr(android, "start_process", start_process)
    monkeypatch.setattr(android, "stop_process", lambda proc: fake.started.append(("stopped",)))
    return fake


@pytest.fixture
def adb(monkeypatch, agent):
    fake = Adb({"adb devices": "List of devices attached\nemulator-5554\tdevice\nR58N\tunauthorized\n",
                "wm size": "Physical size: 1080x2424\n",
                "aapt2": "dev.demo\n",
                "resolve-activity": "priority=0\ndev.demo/.MainActivity\n",
                "forward tcp:0": "7000\n",
                "dumpsys package dev.jevtest.agent": "    versionName=abc123\n"})
    monkeypatch.setattr(android, "run", fake)
    monkeypatch.setattr(android.shutil, "which", lambda name: f"/bin/{name}")
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
    assert email.value == ""


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














# --- driver setup ---------------------------------------------------------------------------

def test_picks_first_device(adb):
    assert AndroidDriver().serial == "emulator-5554"


def test_device_named_by_serial_model_or_avd(adb):
    adb.rules["adb devices"] = "List of devices attached\nemulator-5554\tdevice\n15241JEC\tdevice\n"
    adb.rules["-s 15241JEC shell getprop ro.product.model"] = "Pixel 4a\n"
    adb.rules["-s emulator-5554 shell getprop ro.product.model"] = "sdk_gphone64_arm64\n"
    adb.rules["emu avd name"] = "Pixel_10\nOK\n"
    assert AndroidDriver("15241JEC").serial == "15241JEC"
    assert AndroidDriver("pixel 4a").serial == "15241JEC"
    assert AndroidDriver("Pixel_10").serial == "emulator-5554"
    with pytest.raises(DriverError, match="called 'Galaxy'. Connected: .*Pixel_10.*Pixel 4a"):
        AndroidDriver("Galaxy")


def test_no_device_is_an_error_not_a_boot(adb):
    adb.rules["adb devices"] = "List of devices attached\n"
    with pytest.raises(DriverError, match="No Android device connected"):
        AndroidDriver()
    assert not any("emulator" in c and "-avd" in c for c in adb.cmds)


def test_install_apk(adb):
    d = AndroidDriver()
    assert d.install(Path("app.apk")) == "dev.demo"
    assert d.activity == "dev.demo/.MainActivity"
    assert any(c.endswith("install -r -t app.apk") for c in adb.cmds)  # no -g: permissions start ungranted


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
    # launching never touches device settings
    assert adb.shell() == ["am start -W -n dev.demo/.MainActivity",
                           "am start -W -n dev.demo/.MainActivity", "am force-stop dev.demo", "pm clear dev.demo"]


def test_reinstall(drv, adb):
    drv.reinstall()
    assert adb.shell()[0] == "pm uninstall dev.demo"
    assert any("install -r" in c for c in adb.cmds)


@pytest.mark.parametrize("reply,ready", [
    ("  mWakefulness=Awake\n    isKeyguardShowing=false\n", True),
    ("  mWakefulness=Dozing\n    isKeyguardShowing=true\n", False),
    ("  mWakefulness=Awake\n    isKeyguardShowing=true\n", False),   # awake on the lock screen
    ("  mWakefulness=Asleep\n    isKeyguardShowing=false\n", False)])
def test_check_ready_reports_a_locked_or_sleeping_phone(drv, adb, reply, ready):
    adb.rules["dumpsys power"] = reply
    if ready:
        drv.check_ready()
    else:
        with pytest.raises(DriverError, match="asleep or locked: unlock it"):
            drv.check_ready()
    assert not any("keyevent" in c for c in adb.shell())  # never wakes or unlocks it


@pytest.mark.parametrize("reply,state", [
    ("1234\n  topResumedActivity=ActivityRecord{1 u0 dev.demo/.MainActivity t9}\n", "foreground"),
    ("1234\n  topResumedActivity=ActivityRecord{1 u0 com.launcher/.Home t1}\n", "background"),
    ("1234\n  topResumedActivity=ActivityRecord{2 u0 com.google.android.permissioncontroller/"
     "com.android.permissioncontroller.permission.ui.GrantPermissionsActivity t9}\n", "foreground"),
    ("1234\n  topResumedActivity=ActivityRecord{2 u0 com.android.permissioncontroller/.Grant t9}\n", "foreground"),
    ("1234\n", "foreground"),  # between screens: nobody on top yet, the app has not left
    ("\n", "not_running")])
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
    with pytest.raises(DriverError, match="screen size"):
        drv.size()


def test_empty_webview_waits_for_its_content(drv, agent):
    agent.replies["/tree"] = [EMPTY_WEB, EMPTY_WEB, WEB]
    assert drv.tree() == WEB
    assert [p.split("?")[0] for p in agent.paths()] == ["/tree", "/change", "/tree", "/change", "/tree"]


def test_webview_wait_follows_the_settle_setting(drv, agent, monkeypatch):
    drv.settle = 0.5
    monkeypatch.setattr(android.time, "monotonic", lambda: 100.0)
    agent.replies["/tree"] = [EMPTY_WEB, WEB]
    drv.tree()
    assert agent.paths()[1] == "/change?ms=500"


def test_really_blank_webview_is_accepted(drv, agent, monkeypatch):
    agent.replies["/tree"] = EMPTY_WEB
    ticks = iter([0, 1, 2, 5])
    monkeypatch.setattr(android.time, "monotonic", lambda: next(ticks))
    assert drv.tree() == EMPTY_WEB


def test_lost_agent(drv, agent):
    agent.replies["/tree"] = OSError("refused")
    with pytest.raises(DriverError, match="Lost the Android agent during /tree"):
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
    'bounds="[63,352][1017,499]"')


def test_type_into_field_waits_for_focus_and_keyboard(drv, adb, agent):
    assert FOCUSED != LOGIN
    agent.replies["/tree"] = [LOGIN, FOCUSED]  # right after the tap: not yet focused; then ready
    drv.type_text("hi", at=(540, 425))
    assert [c for c in adb.shell() if c.startswith("input")] == ["input tap 540 425", "input text hi"]
    assert [p.split("?")[0] for p in agent.paths()] == ["/tree", "/change", "/tree"]


def test_type_into_field_that_never_focuses(drv, adb, agent, monkeypatch):
    ticks = iter([0, 1, 4])
    monkeypatch.setattr(android.time, "monotonic", lambda: next(ticks))
    with pytest.raises(DriverError, match="did not get keyboard focus"):
        drv.type_text("hi", at=(540, 425))


def test_type_text_escapes(drv, adb):
    drv.type_text("50% off & more\nline2")
    assert adb.shell() == ["input text '50\\%%soff%s&%smore'", "input keyevent 66", "input text line2"]


def test_type_text_blank_lines_are_just_enter(drv, adb):
    drv.type_text("a\n\nb")
    assert adb.shell() == ["input text a", "input keyevent 66", "input keyevent 66", "input text b"]


def test_type_text_rejects_non_ascii(drv):
    with pytest.raises(DriverError, match="ASCII"):
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
    drv.key("Enter")
    drv.key("82")
    drv.back()
    drv.home()
    assert adb.shell() == ["input keyevent 66", "input keyevent 82", "input keyevent 4", "input keyevent 3"]
    with pytest.raises(DriverError, match="Unknown key 'hyper'"):
        drv.key("hyper")


@pytest.mark.parametrize("ime,pressed", [("true", True), ("false", False)])
def test_hide_keyboard_only_presses_back_when_open(drv, adb, agent, ime, pressed):
    agent.replies["/tree"] = LOGIN.replace('<hierarchy rotation="0"', f'<hierarchy rotation="0" ime="{ime}"')
    drv.hide_keyboard()
    assert ("input keyevent 4" in adb.shell()) is pressed


# --- device ----------------------------------------------------------------------------------------

def test_device_commands(drv, adb):
    adb.rules["settings get system accelerometer_rotation"] = "1\n"
    drv.rotate("landscape")
    drv.open_url("https://x.dev/a b")
    drv.dark_mode(True)
    drv.dark_mode(False)
    drv.grant("camera")
    drv.grant("com.custom.PERM")
    drv.network(False)
    assert adb.shell() == [
        "settings get system accelerometer_rotation",
        "settings put system accelerometer_rotation 0", "settings put system user_rotation 1",
        "am start -W -a android.intent.action.VIEW -d 'https://x.dev/a b'",
        "cmd uimode night yes", "cmd uimode night no",
        "pm grant dev.demo android.permission.CAMERA", "pm grant dev.demo com.custom.PERM",
        "svc wifi disable; svc data disable"]


def test_rotation_restores_the_users_auto_rotate(drv, adb, agent):
    adb.rules["settings get system accelerometer_rotation"] = "1\n"
    drv.rotate("landscape")
    drv.rotate("portrait")
    assert adb.shell().count("settings get system accelerometer_rotation") == 1  # remembered once
    drv.close()
    assert adb.shell()[-1] == "settings put system accelerometer_rotation 1"


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
    drv.swipe("left", el=Element("text", bounds=(0, 0, 100, 100)))
    drv.scroll("down")
    assert adb.shell()[0].startswith("input motionevent DOWN 85 50;") and adb.shell()[0].endswith("UP 15 50")
    assert adb.shell()[-1].startswith("input motionevent DOWN 540 1939;") and adb.shell()[-1].endswith("UP 540 485")


def test_bad_directions(drv):
    with pytest.raises(DriverError):
        drv.swipe("diagonal", el=Element("text", bounds=(0, 0, 1, 1)))
    with pytest.raises(DriverError):
        drv.scroll("inward")


# --- agent -----------------------------------------------------------------------------------------

def test_agent_started_on_the_forwarded_port(adb, agent):
    d = AndroidDriver()
    assert d.port == 7000
    [(cmd, ready)] = agent.started
    assert ready == "ready=1" and cmd[-1] == "dev.jevtest.agent/.Agent" and "7912" in cmd
    assert "am force-stop dev.jevtest.agent" in adb.shell()
    assert not any(" install " in c for c in adb.cmds)  # the right version is already on the device


def test_agent_reinstalled_when_version_differs(adb, agent):
    adb.rules["dumpsys package dev.jevtest.agent"] = "    versionName=old\n"
    AndroidDriver()
    assert "pm uninstall dev.jevtest.agent" in adb.shell()
    assert any(c.endswith("install /cache/android-agent-abc123.apk") for c in adb.cmds)


def test_close_quits_agent_and_removes_forward(drv, adb, agent):
    drv.close()
    assert agent.paths()[-1] == "/quit"
    assert agent.started[-1] == ("stopped",)
    assert any(c.endswith("forward --remove tcp:7000") for c in adb.cmds)


def test_close_when_agent_already_gone(drv, adb, agent):
    drv.agent.running = False
    drv.close()
    assert "/quit" not in agent.paths()


def test_close_tolerates_agent_error(drv, agent):
    agent.replies["/quit"] = OSError("gone")
    drv.close()


def test_build_agent_is_cached(monkeypatch, tmp_path):
    monkeypatch.setenv("JEVTEST_CACHE", str(tmp_path))
    apk = tmp_path / f"android-agent-{android.digest(android.AGENT_SRC)}.apk"
    apk.write_text("")
    assert android.build_agent() == apk


def test_build_agent_with_sdk_tools(monkeypatch, tmp_path):
    import zipfile
    monkeypatch.setenv("JEVTEST_CACHE", str(tmp_path / "cache"))
    sdk = tmp_path / "sdk"
    (sdk / "build-tools/37.0.0").mkdir(parents=True)
    (sdk / "platforms/android-37").mkdir(parents=True)
    (sdk / "platforms/android-37/android.jar").write_text("")
    monkeypatch.setattr(android, "sdk_root", lambda: sdk)
    seen = []

    def fake_run(cmd, **kw):
        seen.append(Path(cmd[0]).name)
        if cmd[0] == "javac":
            out = Path(cmd[cmd.index("-d") + 1])
            out.mkdir(parents=True)
            (out / "Agent.class").write_text("")
        elif cmd[0].endswith("d8"):
            (Path(cmd[cmd.index("--output") + 1]) / "classes.dex").write_text("dex")
        elif cmd[0].endswith("aapt2"):
            with zipfile.ZipFile(cmd[cmd.index("-o") + 1], "w") as z:
                z.writestr("AndroidManifest.xml", "m")
        elif cmd[0].endswith("zipalign"):
            Path(cmd[-1]).write_bytes(Path(cmd[-2]).read_bytes())
        elif cmd[0] == "keytool":
            Path(cmd[cmd.index("-keystore") + 1]).write_text("ks")
        elif cmd[0].endswith("apksigner"):
            Path(cmd[cmd.index("--out") + 1]).write_bytes(Path(cmd[-1]).read_bytes())
        return ""
    monkeypatch.setattr(android, "run", fake_run)
    apk = android.build_agent()
    assert seen == ["javac", "d8", "aapt2", "zipalign", "keytool", "apksigner"]
    assert "classes.dex" in zipfile.ZipFile(apk).namelist()
    seen.clear()
    apk.unlink()
    android.build_agent()
    assert "keytool" not in seen  # the keystore is reused


def test_build_agent_needs_sdk(monkeypatch, tmp_path):
    monkeypatch.setenv("JEVTEST_CACHE", str(tmp_path))
    monkeypatch.setattr(android, "sdk_root", lambda: None)
    with pytest.raises(DriverError, match="build-tools and a platform"):
        android.build_agent()


def test_http_get(monkeypatch):
    class R:
        def read(self):
            return b"ok"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    monkeypatch.setattr(android.urllib.request, "urlopen", lambda url, timeout: R())
    assert android.http_get("http://x", timeout=1) == "ok"
