"""Android driver: plain adb (uiautomator dump for the screen, `input` for touch)."""

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
    "back": 4, "home": 3, "menu": 82, "search": 84, "up": 19, "down": 20, "left": 21,
    "right": 22, "volume_up": 24, "volume_down": 25, "power": 26, "app_switch": 187,
    "move_end": 123, "move_home": 122,
}

KINDS = {
    "EditText": "text_field", "AutoCompleteTextView": "text_field", "Button": "button",
    "ImageButton": "button", "CheckBox": "checkbox", "Switch": "switch", "ToggleButton": "switch",
    "RadioButton": "radio", "ImageView": "image", "TextView": "text", "SeekBar": "slider",
    "ProgressBar": "progress", "Spinner": "dropdown", "WebView": "webview",
    "RecyclerView": "list", "ListView": "list", "ScrollView": "scroll_view",
}


def sdk_root() -> Path | None:
    for var in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        if os.environ.get(var):
            return Path(os.environ[var])
    for p in (Path.home() / "Library/Android/sdk", Path.home() / "Android/Sdk"):
        if p.exists():
            return p
    return None


def adb_path() -> str:
    found = shutil.which("adb")
    if found:
        return found
    root = sdk_root()
    if root and (root / "platform-tools/adb").exists():
        return str(root / "platform-tools/adb")
    raise DriverError("adb not found. Install Android platform-tools or set ANDROID_HOME.")


def aapt2_path() -> str:
    if shutil.which("aapt2"):
        return shutil.which("aapt2")
    root = sdk_root()
    tools = sorted((root / "build-tools").glob("*/aapt2")) if root else []
    if not tools:
        raise DriverError("aapt2 not found (Android SDK build-tools) — needed to read the APK's package name.")
    return str(tools[-1])


def devices() -> list[str]:
    out = run([adb_path(), "devices"], timeout=20)
    return [line.split()[0] for line in out.splitlines()[1:] if line.strip().endswith("device")]


def start_emulator(timeout: float = 180) -> str:
    """Boot the first available AVD and wait for it."""
    root = sdk_root()
    emulator = shutil.which("emulator") or (str(root / "emulator/emulator") if root else None)
    if not emulator or not Path(emulator).exists():
        raise DriverError("No Android device connected and no emulator binary found.")
    avds = run([emulator, "-list-avds"], timeout=30).split()
    if not avds:
        raise DriverError("No Android device connected and no AVDs exist. Create one in Android Studio.")
    subprocess.Popen([emulator, "-avd", avds[0], "-no-snapshot-save", "-no-boot-anim"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for serial in devices():
            boot = run([adb_path(), "-s", serial, "shell", "getprop", "sys.boot_completed"],
                       check=False, timeout=10).strip()
            if boot == "1":
                return serial
        time.sleep(2)
    raise DriverError(f"Emulator {avds[0]} did not boot within {timeout}s")


class AndroidDriver(Driver):
    platform = "android"

    def __init__(self, serial: str | None = None):
        self.adb = adb_path()
        found = devices()
        if serial:
            if serial not in found:
                raise DriverError(f"Android device {serial} not connected (connected: {found or 'none'})")
            self.serial = serial
        else:
            self.serial = found[0] if found else start_emulator()
        self.app_path: Path | None = None
        self.activity = ""
        self._size: tuple[int, int] | None = None

    # --- plumbing --------------------------------------------------------
    def sh(self, cmd: str, timeout: float = 60, check: bool = True) -> str:
        return run([self.adb, "-s", self.serial, "shell", cmd], timeout=timeout, check=check)

    def size(self, rotation: int = 0) -> tuple[int, int]:
        if self._size is None:
            m = re.findall(r"(\d+)x(\d+)", self.sh("wm size"))
            self._size = tuple(map(int, m[-1]))  # override size (if any) is listed last
        w, h = self._size
        return (h, w) if rotation in (1, 3) else (w, h)

    # --- lifecycle -------------------------------------------------------
    def install(self, app_path: Path) -> str:
        self.app_path = app_path
        suffix = app_path.suffix.lower()
        if suffix == ".apk":
            self.app_id = run([aapt2_path(), "dump", "packagename", str(app_path)]).strip()
            run([self.adb, "-s", self.serial, "install", "-r", "-g", "-t", str(app_path)], timeout=300)
        elif suffix == ".aab":
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
        else:
            raise DriverError(f"Android needs an .apk or .aab, got {app_path.name}")
        out = self.sh(f"cmd package resolve-activity --brief -c android.intent.category.LAUNCHER {self.app_id}")
        self.activity = out.strip().splitlines()[-1]
        if "/" not in self.activity:
            raise DriverError(f"{self.app_id} has no launcher activity")
        return self.app_id

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

    def running(self) -> bool:
        return bool(self.sh(f"pidof {self.app_id}", check=False).strip())

    # --- observe ---------------------------------------------------------
    def screen(self) -> Screen:
        xml = ""
        for _ in range(4):  # uiautomator fails while the UI is animating; retry
            out = self.sh("uiautomator dump --compressed /sdcard/jevtest.xml >/dev/null 2>&1; "
                          "cat /sdcard/jevtest.xml", check=False)
            if "<hierarchy" in out:
                xml = out[out.index("<hierarchy"):]
                break
            time.sleep(0.5)
        if not xml:
            raise DriverError("uiautomator dump failed (is the screen locked?)")
        rot = re.search(r'<hierarchy rotation="(\d)"', xml)
        w, h = self.size(int(rot.group(1)) if rot else 0)
        elements = []
        for node in ET.fromstring(xml).iter("node"):
            a = node.attrib
            if a.get("package") == "com.android.systemui":
                continue
            m = re.findall(r"\d+", a.get("bounds", ""))
            if len(m) != 4:
                continue
            x1, y1, x2, y2 = map(int, m)
            x1, y1, x2, y2 = max(x1, 0), max(y1, 0), min(x2, w), min(y2, h)
            if x2 - x1 < 2 or y2 - y1 < 2:
                continue
            cls = a.get("class", "").split(".")[-1]
            kind = KINDS.get(cls, cls.lower() or "view")
            text = a.get("text") or a.get("content-desc") or ""
            if a.get("text") and a.get("content-desc") and a["content-desc"] != a["text"]:
                text = f"{a['text']} ({a['content-desc']})"
            editable = cls in ("EditText", "AutoCompleteTextView")
            clickable = a.get("clickable") == "true" or a.get("long-clickable") == "true"
            checkable = a.get("checkable") == "true"
            scrollable = a.get("scrollable") == "true"
            rid = a.get("resource-id", "").split("/")[-1]
            if not (text or editable or clickable or checkable or scrollable or rid):
                continue
            if kind == "view":
                kind = "button" if clickable else "text"
            if a.get("password") == "true" and editable:
                kind = "password_field"
            elements.append(Element(
                kind=kind, text=" ".join(text.split()), hint=a.get("hint", ""), resource_id=rid,
                bounds=(x1, y1, x2, y2), enabled=a.get("enabled") == "true", editable=editable,
                clickable=clickable, scrollable=scrollable, focused=a.get("focused") == "true",
                checked=(a.get("checked") == "true") if checkable else None,
                selected=a.get("selected") == "true",
            ))
        ime = self.sh("dumpsys input_method | grep -E 'mInputShown|mIsInputViewShown'", check=False)
        return Screen(width=w, height=h, elements=elements,
                      keyboard_visible="=true" in ime, app_running=self.running())

    def screenshot(self, path: Path):
        path.write_bytes(run([self.adb, "-s", self.serial, "exec-out", "screencap", "-p"], binary=True))

    # --- touch & keys ------------------------------------------------------
    def tap(self, x, y):
        self.sh(f"input tap {x} {y}")

    def double_tap(self, x, y):
        # One shell invocation so the two taps land inside the double-tap window.
        self.sh(f"input tap {x} {y} & sleep 0.08; input tap {x} {y}; wait")

    def long_press(self, x, y, seconds=1.2):
        self.sh(f"input swipe {x} {y} {x} {y} {int(seconds * 1000)}")

    def drag(self, x1, y1, x2, y2, seconds=0.3):
        self.sh(f"input swipe {x1} {y1} {x2} {y2} {int(seconds * 1000)}")

    def type_text(self, text):
        # `input text` uses %s for spaces; send line by line with enter between.
        for i, line in enumerate(text.split("\n")):
            if i:
                self.key("enter")
            if line:
                self.sh("input text " + shlex.quote(line.replace("%", r"\%").replace(" ", "%s")))

    def clear_text(self, el):
        self.tap(*el.center)
        n = len(el.text) + 10
        self.sh("input keyevent 123 " + " ".join(["67"] * n))

    def key(self, name):
        code = KEYCODES.get(name.lower())
        if code is None and not name.isdigit():
            raise DriverError(f"Unknown key '{name}'. Known: {', '.join(sorted(KEYCODES))}")
        self.sh(f"input keyevent {code if code is not None else name}")

    def back(self):
        self.key("back")

    def home(self):
        self.key("home")

    def hide_keyboard(self):
        if self.screen().keyboard_visible:
            self.key("back")

    # --- device --------------------------------------------------------------
    def rotate(self, orientation):
        rot = {"portrait": 0, "landscape": 1, "portrait_upside_down": 2, "landscape_right": 3}
        if orientation not in rot:
            raise DriverError(f"Unknown orientation '{orientation}' (use {', '.join(rot)})")
        self.sh("settings put system accelerometer_rotation 0")
        self.sh(f"settings put system user_rotation {rot[orientation]}")

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
