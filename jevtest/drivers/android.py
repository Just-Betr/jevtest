"""Android driver: plain adb. uiautomator dump for the screen, `input` for touch and keys."""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from ..screen import Element, Screen
from .base import Driver, DriverError, run

KEYCODES = {
    "enter": 66, "delete": 67, "backspace": 67, "tab": 61, "escape": 111, "space": 62,
    "back": 4, "home": 3, "menu": 82, "search": 84, "dpad_up": 19, "dpad_down": 20,
    "dpad_left": 21, "dpad_right": 22, "volume_up": 24, "volume_down": 25, "power": 26,
    "app_switch": 187, "move_end": 123, "move_home": 122,
}
ROTATIONS = {"portrait": 0, "landscape": 1, "portrait_upside_down": 2, "landscape_right": 3}
KINDS = {
    "EditText": "text_field", "AutoCompleteTextView": "text_field", "Button": "button",
    "ImageButton": "button", "CheckBox": "checkbox", "Switch": "switch", "ToggleButton": "switch",
    "RadioButton": "radio", "ImageView": "image", "TextView": "text", "SeekBar": "slider",
    "ProgressBar": "progress", "Spinner": "dropdown", "WebView": "webview",
    "RecyclerView": "list", "ListView": "list", "ScrollView": "scroll_view",
}
EDITABLE = {"EditText", "AutoCompleteTextView"}
# Always report on/off for these: WebView checkboxes come through with checkable="false".
TOGGLES = {"CheckBox", "Switch", "RadioButton", "ToggleButton", "SwitchCompat", "SwitchMaterial"}
DUMP_RETRIES = 4          # uiautomator fails while the UI animates
DOUBLE_TAP_GAP = 0.1      # Android and Flutter ignore taps < 40 ms apart and > 300 ms apart


def sdk_root() -> Path | None:
    for var in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        if os.environ.get(var):
            return Path(os.environ[var])
    for p in (Path.home() / "Library/Android/sdk", Path.home() / "Android/Sdk"):
        if p.exists():
            return p
    return None


def _tool(name: str, sdk_subpath: str, hint: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    root = sdk_root()
    matches = sorted(root.glob(sdk_subpath)) if root else []
    if not matches:
        raise DriverError(f"{name} not found. {hint}")
    return str(matches[-1])  # highest version for build-tools/*


def adb_path() -> str:
    return _tool("adb", "platform-tools/adb", "Install Android platform-tools or set ANDROID_HOME.")


def aapt2_path() -> str:
    return _tool("aapt2", "build-tools/*/aapt2", "Install Android SDK build-tools (needed to read the APK).")


def devices() -> list[str]:
    out = run([adb_path(), "devices"], timeout=20)
    return [line.split()[0] for line in out.splitlines()[1:] if line.strip().endswith("\tdevice")]


def bootable_avds(avd_names: list[str], root: Path, avd_home: Path) -> list[str]:
    """AVDs whose system image is actually installed, in name order."""
    ok = []
    for name in sorted(avd_names):
        config = avd_home / f"{name}.avd" / "config.ini"
        sysdir = ""
        if config.is_file():
            for line in config.read_text().splitlines():
                if line.startswith("image.sysdir.1="):
                    sysdir = line.split("=", 1)[1].strip()
        if sysdir and (root / sysdir).is_dir():
            ok.append(name)
    return ok


def start_emulator(timeout: float = 180) -> str:
    """Boot the first AVD with an installed system image and wait until it's ready."""
    root = sdk_root()
    emulator = shutil.which("emulator") or (str(root / "emulator/emulator") if root else "")
    if not root or not Path(emulator).exists():
        raise DriverError("No Android device connected and no emulator found. Set ANDROID_HOME.")
    avd_home = Path(os.environ.get("ANDROID_AVD_HOME", Path.home() / ".android/avd"))
    avds = bootable_avds(run([emulator, "-list-avds"], timeout=30).split(), root, avd_home)
    if not avds:
        raise DriverError("No Android device connected and no bootable AVD. Create one in Android Studio.")
    env = dict(os.environ, ANDROID_SDK_ROOT=str(root))
    subprocess.Popen([emulator, "-avd", avds[0], "-no-snapshot-save", "-no-boot-anim"], env=env,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for serial in devices():
            if run([adb_path(), "-s", serial, "shell", "getprop", "sys.boot_completed"],
                   check=False, timeout=10).strip() == "1":
                return serial
        time.sleep(2)
    raise DriverError(f"Emulator {avds[0]} did not boot within {timeout:g}s")


def has_empty_webview(xml: str) -> bool:
    for node in ET.fromstring(xml).iter("node"):
        if node.get("class") == "android.webkit.WebView" and len(node.findall(".//node")) == 0:
            return True
    return False


def parse_hierarchy(xml: str, width: int, height: int) -> list[Element]:
    """uiautomator XML -> the elements a tester cares about, in document order."""
    elements = []
    for node in ET.fromstring(xml).iter("node"):
        a = node.attrib
        if a.get("package") == "com.android.systemui":  # status bar, navigation bar
            continue
        nums = re.findall(r"-?\d+", a.get("bounds", ""))
        if len(nums) != 4:
            continue
        x1, y1, x2, y2 = map(int, nums)
        x1, y1, x2, y2 = max(x1, 0), max(y1, 0), min(x2, width), min(y2, height)
        if x2 - x1 < 2 or y2 - y1 < 2:  # off screen or invisible
            continue
        cls = a.get("class", "").split(".")[-1]
        text, desc = a.get("text", ""), a.get("content-desc", "")
        label = f"{text} ({desc})" if text and desc and text != desc else (text or desc)
        editable = cls in EDITABLE
        clickable = a.get("clickable") == "true" or a.get("long-clickable") == "true"
        checkable = a.get("checkable") == "true" or cls in TOGGLES
        scrollable = a.get("scrollable") == "true"
        rid = a.get("resource-id", "").split("/")[-1]
        if not (label or editable or clickable or checkable or scrollable or rid):
            continue
        if cls in KINDS:
            kind = KINDS[cls]
        elif cls in ("View", ""):  # Flutter and Compose render most widgets as plain Views
            kind = "button" if clickable else "text"
        else:
            kind = cls.lower()
        if editable and a.get("password") == "true":
            kind = "password_field"
        elements.append(Element(
            kind=kind, text=" ".join(label.split()), hint=a.get("hint", ""), resource_id=rid,
            bounds=(x1, y1, x2, y2), enabled=a.get("enabled", "true") == "true", editable=editable,
            clickable=clickable, scrollable=scrollable, focused=a.get("focused") == "true",
            checked=(a.get("checked") == "true") if checkable else None,
            selected=a.get("selected") == "true",
        ))
    return elements


class AndroidDriver(Driver):
    platform = "android"

    def __init__(self, serial: str | None = None):
        self.adb = adb_path()
        found = devices()
        if serial and serial not in found:
            raise DriverError(f"Android device {serial} not connected (connected: {', '.join(found) or 'none'})")
        self.serial = serial or (found[0] if found else start_emulator())
        self.app_path: Path | None = None
        self.activity = ""
        self._size: tuple[int, int] | None = None

    # --- plumbing ------------------------------------------------------------
    def sh(self, cmd: str, timeout: float = 60, check: bool = True) -> str:
        return run([self.adb, "-s", self.serial, "shell", cmd], timeout=timeout, check=check)

    def size(self, rotation: int = 0) -> tuple[int, int]:
        if self._size is None:
            found = re.findall(r"(\d+)x(\d+)", self.sh("wm size"))
            if not found:
                raise DriverError("Could not read the screen size (`wm size`)")
            self._size = tuple(map(int, found[-1]))  # an override size, if any, is listed last
        w, h = self._size
        return (h, w) if rotation in (1, 3) else (w, h)

    # --- lifecycle -------------------------------------------------------------
    def install(self, app_path: Path) -> str:
        self.app_path = app_path
        suffix = app_path.suffix.lower()
        if suffix == ".apk":
            self.app_id = run([aapt2_path(), "dump", "packagename", str(app_path)]).strip()
            run([self.adb, "-s", self.serial, "install", "-r", "-g", "-t", str(app_path)], timeout=300)
        elif suffix == ".aab":
            self._install_bundle(app_path)
        else:
            raise DriverError(f"Android needs an .apk or .aab, got {app_path.name}")
        out = self.sh(f"cmd package resolve-activity --brief -c android.intent.category.LAUNCHER {self.app_id}")
        lines = out.strip().splitlines()
        self.activity = lines[-1].strip() if lines else ""
        if "/" not in self.activity:
            raise DriverError(f"{self.app_id} has no launcher activity")
        return self.app_id

    def _install_bundle(self, app_path: Path):
        bundletool = shutil.which("bundletool")
        if not bundletool:
            raise DriverError("bundletool is required to install .aab files (brew install bundletool)")
        self.app_id = run([bundletool, "dump", "manifest", "--bundle", str(app_path),
                           "--xpath", "/manifest/@package"]).strip()
        with tempfile.TemporaryDirectory() as tmp:
            apks = Path(tmp) / "app.apks"
            run([bundletool, "build-apks", "--bundle", str(app_path), "--output", str(apks),
                 "--connected-device", "--device-id", self.serial, "--adb", self.adb], timeout=600)
            run([bundletool, "install-apks", "--apks", str(apks), "--device-id", self.serial,
                 "--adb", self.adb], timeout=300)

    def launch(self):
        self.sh("settings put system accelerometer_rotation 0", check=False)
        self.sh(f"am start -W -n {self.activity}")

    def resume(self):
        self.sh(f"am start -W -n {self.activity}")

    def stop(self):
        self.sh(f"am force-stop {self.app_id}")

    def clear_data(self):
        self.sh(f"pm clear {self.app_id}")

    def reinstall(self):
        self.sh(f"pm uninstall {self.app_id}", check=False)
        self.install(self.app_path)

    def app_state(self) -> str:
        out = self.sh(f"pidof {self.app_id}; dumpsys activity activities | grep -m1 topResumedActivity",
                      check=False)
        if not re.match(r"\d+", out.strip()):
            return "not_running"
        return "foreground" if f" {self.app_id}/" in out else "background"

    def keyboard_shown(self) -> bool:
        return "mInputShown=true" in self.sh("dumpsys input_method | grep mInputShown", check=False)

    # --- observe ---------------------------------------------------------------
    def dump(self) -> str:
        """The UI hierarchy XML, once it is complete.

        uiautomator fails while the UI animates, and a WebView's content arrives a
        moment after the WebView itself, so an empty WebView means "not loaded yet".
        """
        xml = ""
        for attempt in range(DUMP_RETRIES):
            out = self.sh("uiautomator dump --compressed /sdcard/jevtest.xml >/dev/null 2>&1; "
                          "cat /sdcard/jevtest.xml", check=False)
            if "<hierarchy" in out:
                xml = out[out.index("<hierarchy"):]
                if not has_empty_webview(xml):
                    return xml
            if attempt < DUMP_RETRIES - 1:
                time.sleep(0.5)
        if xml:
            return xml  # a WebView that really is blank
        raise DriverError("uiautomator dump failed (is the screen locked?)")

    def screen(self) -> Screen:
        xml = self.dump()
        rot = re.search(r'<hierarchy rotation="(\d)"', xml)
        w, h = self.size(int(rot.group(1)) if rot else 0)
        return Screen(width=w, height=h, elements=parse_hierarchy(xml, w, h),
                      keyboard_visible=self.keyboard_shown(),
                      app_running=self.app_state() != "not_running")

    def screenshot(self, path: Path):
        path.write_bytes(run([self.adb, "-s", self.serial, "exec-out", "screencap", "-p"], binary=True))

    # --- touch & keys ------------------------------------------------------------
    def tap(self, x, y):
        self.sh(f"input tap {x} {y}")

    def double_tap(self, x, y):
        # One shell call, so the gap between the taps is the sleep and not adb latency.
        self.sh(f"input tap {x} {y}; sleep {DOUBLE_TAP_GAP}; input tap {x} {y}")

    def long_press(self, x, y, seconds=1.2):
        self.sh(f"input swipe {x} {y} {x} {y} {int(seconds * 1000)}")

    def drag(self, x1, y1, x2, y2, seconds=0.3):
        self.sh(f"input swipe {x1} {y1} {x2} {y2} {int(seconds * 1000)}")

    def type_text(self, text, at=None):
        if not text.isascii():
            raise DriverError("Android `input text` only supports ASCII characters")
        # `input text` needs %s for spaces; newlines become Enter presses.
        for i, line in enumerate(text.split("\n")):
            if i:
                self.key("enter")
            if line:
                self.sh("input text " + shlex.quote(line.replace("%", r"\%").replace(" ", "%s")))

    def clear_text(self, el):
        self.tap(*el.center)
        self.sh("input keyevent 123 " + " ".join(["67"] * (len(el.text) + 10)))  # end, then deletes

    def key(self, name):
        code = KEYCODES.get(name.lower())
        if code is None and not name.isdigit():
            raise DriverError(f"Unknown key '{name}'. Known: {', '.join(sorted(KEYCODES))}, or a keycode number")
        self.sh(f"input keyevent {code if code is not None else name}")

    def back(self):
        self.key("back")

    def home(self):
        self.key("home")

    def hide_keyboard(self):
        # Back closes the keyboard, but with no keyboard it leaves the screen: check right before.
        if self.keyboard_shown():
            self.key("back")

    # --- device ------------------------------------------------------------------
    def rotate(self, orientation):
        if orientation not in ROTATIONS:
            raise DriverError(f"Unknown orientation '{orientation}' (use {', '.join(ROTATIONS)})")
        self.sh("settings put system accelerometer_rotation 0")
        self.sh(f"settings put system user_rotation {ROTATIONS[orientation]}")

    def set_location(self, lat, lon):
        if not self.serial.startswith("emulator-"):
            raise DriverError("Setting location is only supported on the Android emulator")
        run([self.adb, "-s", self.serial, "emu", "geo", "fix", str(lon), str(lat)])

    def open_url(self, url):
        self.sh(f"am start -W -a android.intent.action.VIEW -d {shlex.quote(url)}")

    def dark_mode(self, on):
        self.sh(f"cmd uimode night {'yes' if on else 'no'}")

    def grant(self, permission):
        if "." not in permission:
            permission = "android.permission." + permission.upper()
        self.sh(f"pm grant {self.app_id} {permission}")

    def network(self, on):
        state = "enable" if on else "disable"
        self.sh(f"svc wifi {state}; svc data {state}")
