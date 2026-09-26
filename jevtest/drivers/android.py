"""Android driver: adb for the app and input, a small on-device agent for reading the screen."""

from __future__ import annotations

import contextlib
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from ..screen import Element, Screen
from .base import Driver, DriverError, cache_dir, digest, run, start_process, stop_process

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
DOUBLE_TAP_GAP = 0.1      # Android and Flutter ignore taps < 40 ms apart and > 300 ms apart
DRAG_STEPS = 10           # finger positions along a drag
DRAG_HOLD = 0.1           # seconds the finger rests before lifting, so nothing flings
AGENT_START_TIMEOUT = 30
TOP_ACTIVITY = re.compile(r"topResumedActivity=ActivityRecord\{\S+ \S+ ([\w.]+)/")
PERMISSION_PROMPT = re.compile(r"com\.(google\.)?android\.permissioncontroller")
AGENT_SRC = Path(__file__).resolve().parent.parent / "android_agent"
AGENT_ID = "dev.jevtest.agent"
AGENT_STOP_TIMEOUT = 10  # seconds for the agent to finish after /quit
AGENT_PORT = 7912         # on the device; adb forwards a free local port to it


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


def device_names(serial: str) -> list[str]:
    """What a test file may call this device: its serial, its model ("Pixel 4a"), its emulator's AVD name."""
    names = [serial, run([adb_path(), "-s", serial, "shell", "getprop", "ro.product.model"], check=False).strip()]
    if serial.startswith("emulator-"):
        names.append(run([adb_path(), "-s", serial, "emu", "avd", "name"], check=False).split("\n")[0].strip())
    return [n for n in names if n]


def pick_device(wanted: str, serials: list[str]) -> str:
    """The one connected device with exactly this serial, model or AVD name."""
    named = {serial: device_names(serial) for serial in serials}
    matches = [serial for serial, names in named.items() if wanted in names]
    listed = "; ".join(" / ".join(names) for names in named.values()) or "none"
    if not matches:
        raise DriverError(f"No connected Android device called '{wanted}' (names are exact). Connected: {listed}")
    if len(matches) > 1:
        raise DriverError(f"Several connected Android devices are called '{wanted}' ({', '.join(matches)}): "
                          "name one by its serial")
    return matches[0]


def http_get(url: str, timeout: float) -> str:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.read().decode()


# Devices tested at the same time share the cached agent: one builds it, the others wait for it.
AGENT_LOCK = threading.Lock()


def build_agent() -> Path:
    with AGENT_LOCK:
        return _build_agent()


def _build_agent() -> Path:
    """Compile the on-device agent with the SDK's own tools (no Gradle). Cached by source hash."""
    version = digest(AGENT_SRC)
    apk = cache_dir() / f"android-agent-{version}.apk"
    if apk.exists():
        return apk
    root = sdk_root()
    tools = sorted(root.glob("build-tools/*")) if root else []
    jars = sorted(root.glob("platforms/android-*/android.jar")) if root else []
    if not tools or not jars:
        raise DriverError("Android SDK build-tools and a platform are needed to build the jevtest agent")
    bt, jar = tools[-1], str(jars[-1])
    print("  building Android agent (one time, a few seconds)...", flush=True)
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        run(["javac", "--release", "11", "-cp", jar, "-d", str(t / "classes"),
             *map(str, sorted(AGENT_SRC.rglob("*.java")))])
        run([str(bt / "d8"), "--min-api", "24", "--lib", jar, "--output", tmp,
             *map(str, sorted((t / "classes").rglob("*.class")))])
        run([str(bt / "aapt2"), "link", "--manifest", str(AGENT_SRC / "AndroidManifest.xml"), "-I", jar,
             "--version-name", version, "-o", str(t / "base.apk")])
        with zipfile.ZipFile(t / "base.apk", "a") as z:
            z.write(t / "classes.dex", "classes.dex")
        run([str(bt / "zipalign"), "-f", "4", str(t / "base.apk"), str(t / "aligned.apk")])
        apk.parent.mkdir(parents=True, exist_ok=True)
        keystore = cache_dir() / "jevtest-debug.keystore"
        if not keystore.exists():
            run(["keytool", "-genkeypair", "-keystore", str(keystore), "-storepass", "android",
                 "-alias", "jevtest", "-keypass", "android", "-keyalg", "RSA", "-validity", "10000",
                 "-dname", "CN=jevtest"])
        run([str(bt / "apksigner"), "sign", "--ks", str(keystore), "--ks-pass", "pass:android",
             "--out", str(apk), str(t / "aligned.apk")])
    return apk


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
        full_id = a.get("resource-id", "")
        rid = "" if full_id.startswith("android:id/") else full_id.split("/")[-1]  # framework ids are structure
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
        value = text if editable else ""
        elements.append(Element(
            kind=kind, text=" ".join(label.split()), hint=a.get("hint", ""), resource_id=rid,
            bounds=(x1, y1, x2, y2), enabled=a.get("enabled", "true") == "true", editable=editable,
            clickable=clickable, scrollable=scrollable, focused=a.get("focused") == "true",
            checked=(a.get("checked") == "true") if checkable else None,
            selected=a.get("selected") == "true", value=value,
        ))
    return elements


class AndroidDriver(Driver):
    platform = "android"

    def __init__(self, device: str):
        self.adb = adb_path()
        found = devices()
        if not found:
            raise DriverError("No Android device connected. Start an emulator or connect a phone (see `adb devices`).")
        self.serial = pick_device(device, found)
        self.app_path: Path | None = None
        self.activity = ""
        self._size: tuple[int, int] | None = None
        self.agent: subprocess.Popen | None = None
        self._restore: dict[str, str] = {}  # what -> shell command that puts back what a step changed
        self._start_agent()

    # --- agent -------------------------------------------------------------------
    def _start_agent(self):
        apk = build_agent()
        version = apk.stem.rsplit("-", 1)[1]
        if f"versionName={version}" not in self.sh(f"dumpsys package {AGENT_ID} | grep versionName", check=False):
            self.sh(f"pm uninstall {AGENT_ID}", check=False)  # any older copy, whatever key signed it
            run([self.adb, "-s", self.serial, "install", str(apk)], timeout=120)
        self.sh(f"am force-stop {AGENT_ID}")  # a previous run's agent would hold the port
        self.port = int(run([self.adb, "-s", self.serial, "forward", "tcp:0", f"tcp:{AGENT_PORT}"]).strip())
        self.agent = start_process(
            [self.adb, "-s", self.serial, "shell", "am", "instrument", "-r", "-w", "-e", "port", str(AGENT_PORT),
             f"{AGENT_ID}/.Agent"],
            ready="ready=1", log=cache_dir() / f"android-agent-{self.serial}.log", timeout=AGENT_START_TIMEOUT)

    def _agent(self, path: str, wait_ms: int = 0, extra: str = "") -> str:
        url = f"http://127.0.0.1:{self.port}{path}" + (f"?ms={wait_ms}{extra}" if wait_ms else "")
        try:
            return http_get(url, timeout=wait_ms / 1000 + 10)
        except OSError as e:
            raise DriverError(f"Lost the Android agent during {path} ({e})") from None

    def close(self):
        if self.agent and self.agent.poll() is None:
            with contextlib.suppress(DriverError):  # it may already be gone
                self._agent("/quit")
                # `am instrument -w` exits once the device has finished tearing down UI automation,
                # which resets rotation state; only after that can a restore stick.
                with contextlib.suppress(subprocess.TimeoutExpired):
                    self.agent.wait(AGENT_STOP_TIMEOUT)
        stop_process(self.agent)
        run([self.adb, "-s", self.serial, "forward", "--remove", f"tcp:{self.port}"], check=False)
        for command in self._restore.values():  # leave the device as the user had it
            self.sh(command, check=False)

    def wait_idle(self, timeout: float, quiet: float | None = None):
        extra = f"&quiet={int(quiet * 1000)}" if quiet is not None else ""
        self._agent("/idle", int(timeout * 1000), extra)

    def wait_change(self, timeout: float):
        self._agent("/change", int(timeout * 1000))

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
            # No -g: permissions start ungranted, like a real install. Use a `grant:` step to pre-grant.
            run([self.adb, "-s", self.serial, "install", "-r", "-t", str(app_path)], timeout=300)
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

    def check_ready(self):
        """A phone that is asleep or locked shows no app to test. Say so; never wake or unlock it."""
        out = self.sh("dumpsys power | grep -m1 mWakefulness=; dumpsys window | grep -m1 -E 'isKeyguardShowing='",
                      check=False)
        if "mWakefulness=Awake" not in out or "isKeyguardShowing=true" in out:
            raise DriverError(f"Android device {self.serial} is asleep or locked: unlock it and keep it awake "
                              "during the run")

    def app_state(self) -> str:
        out = self.sh(f"pidof {self.app_id}; dumpsys activity activities | grep -m1 topResumedActivity",
                      check=False)
        if not re.match(r"\d+", out.strip()):
            return "not_running"
        top = TOP_ACTIVITY.search(out)
        # The app has left only when another app is on top. No top activity = mid-transition;
        # a permission prompt the app asked for sits on top of it but belongs to it.
        if not top or top.group(1) == self.app_id or PERMISSION_PROMPT.fullmatch(top.group(1)):
            return "foreground"
        return "background"

    # --- observe ---------------------------------------------------------------
    def _wait_for_typing(self):
        """Keys sent before the keyboard is connected are dropped, so wait (event-driven) until a
        text field has focus and the keyboard is up. Not "the field under the tap": on a real phone
        the keyboard slides up and the app scrolls the focused field out from under it."""
        deadline = time.monotonic() + self.settle
        while True:
            xml = self._agent("/tree")
            root = ET.fromstring(xml)
            w, h = self.size(int(root.get("rotation", "0")))
            focused = any(el.editable and el.focused for el in parse_hierarchy(xml, w, h))
            if focused and root.get("ime") == "true":
                return
            left = deadline - time.monotonic()
            if left <= 0:
                raise DriverError("The text field did not get keyboard focus")
            self.wait_change(left)

    def tree(self) -> str:
        """The UI hierarchy XML. A WebView's content arrives a moment after the WebView itself,
        so while a WebView is still empty, wait for the screen to change (event-driven)."""
        xml = self._agent("/tree")
        deadline = time.monotonic() + self.settle
        while has_empty_webview(xml) and time.monotonic() < deadline:
            self.wait_change(deadline - time.monotonic())
            xml = self._agent("/tree")
        return xml

    def screen(self) -> Screen:
        xml = self.tree()
        root = ET.fromstring(xml)
        w, h = self.size(int(root.get("rotation", "0")))
        return Screen(width=w, height=h, elements=parse_hierarchy(xml, w, h),
                      keyboard_visible=root.get("ime") == "true", keyboard_top=int(root.get("ime-top", "0")))

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
        # Press, move in steps, hold still, lift: the content stops where the finger stops. A plain
        # `input swipe` lifts while moving, so the content flings on and a scroll lands anywhere.
        steps = [(x1 + (x2 - x1) * i // DRAG_STEPS, y1 + (y2 - y1) * i // DRAG_STEPS) for i in range(1, DRAG_STEPS + 1)]
        self.sh("; ".join([f"input motionevent DOWN {x1} {y1}"] + [f"input motionevent MOVE {x} {y}" for x, y in steps]
                          + [f"sleep {DRAG_HOLD}", f"input motionevent UP {x2} {y2}"]))

    def type_text(self, text, at=None):
        if not text.isascii():
            raise DriverError("Android `input text` only supports ASCII characters")
        if at:  # focus the field, then wait until it has focus and the keyboard is up
            self.tap(*at)
            self._wait_for_typing()
        # `input text` needs %s for spaces; newlines become Enter presses.
        for i, line in enumerate(text.split("\n")):
            if i:
                self.key("enter")
            if line:
                self.sh("input text " + shlex.quote(line.replace("%", r"\%").replace(" ", "%s")))

    def clear_text(self, el):
        self.tap(*el.end)  # cursor after the text
        self._wait_for_typing()
        if el.value:  # move to the end, then delete exactly what is there
            self.sh("input keyevent 123 " + " ".join(["67"] * len(el.value)))

    def key(self, name):
        code = KEYCODES.get(name)
        if code is None and not name.isdigit():
            raise DriverError(f"Unknown key '{name}'. Known: {', '.join(sorted(KEYCODES))}, or a keycode number")
        self.sh(f"input keyevent {code if code is not None else name}")

    def back(self):
        self.key("back")

    def home(self):
        self.key("home")

    def hide_keyboard(self):
        # Back closes the keyboard, but with no keyboard it leaves the screen: check right before.
        if ET.fromstring(self._agent("/tree")).get("ime") == "true":
            self.key("back")

    # --- device ------------------------------------------------------------------
    def rotate(self, orientation):
        if orientation not in ROTATIONS:
            raise DriverError(f"Unknown orientation '{orientation}' (use {', '.join(ROTATIONS)})")
        if "rotation" not in self._restore:  # rotating needs auto-rotate off; close() puts both back
            self._restore["rotation"] = (f"{self._setting('system', 'user_rotation')}; "
                                         f"{self._setting('system', 'accelerometer_rotation')}")
        self.sh("settings put system accelerometer_rotation 0")
        self.sh(f"settings put system user_rotation {ROTATIONS[orientation]}")

    def _setting(self, namespace: str, key: str) -> str:
        """The shell command that puts an Android setting back to its current value."""
        value = self.sh(f"settings get {namespace} {key}", check=False).strip()
        if value in ("", "null"):
            return f"settings delete {namespace} {key}"
        return f"settings put {namespace} {key} {value}"

    def set_location(self, lat, lon):
        if not self.serial.startswith("emulator-"):
            raise DriverError("Setting location is only supported on the Android emulator")
        run([self.adb, "-s", self.serial, "emu", "geo", "fix", str(lon), str(lat)])

    def open_url(self, url):
        self.sh(f"am start -W -a android.intent.action.VIEW -d {shlex.quote(url)}")

    def dark_mode(self, on):
        if "dark_mode" not in self._restore:
            now = self.sh("cmd uimode night", check=False).strip().removeprefix("Night mode: ")
            if now not in ("yes", "no", "auto"):
                raise DriverError(f"Can't read the device's dark mode setting to restore it later (got {now!r})")
            self._restore["dark_mode"] = f"cmd uimode night {now}"
        self.sh(f"cmd uimode night {'yes' if on else 'no'}")

    def grant(self, permission):
        if not permission.startswith("android.permission."):
            raise DriverError(f"'{permission}': give the full Android permission name, "
                              f"e.g. android.permission.{permission.upper()}")
        self.sh(f"pm grant {self.app_id} {permission}")

    def network(self, on):
        if "network" not in self._restore:
            wifi = self.sh("settings get global wifi_on", check=False).strip() not in ("0", "")
            data = self.sh("settings get global mobile_data", check=False).strip() == "1"
            self._restore["network"] = (f"svc wifi {'enable' if wifi else 'disable'}; "
                                        f"svc data {'enable' if data else 'disable'}")
        state = "enable" if on else "disable"
        self.sh(f"svc wifi {state}; svc data {state}")
