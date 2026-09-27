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

from jevtest.domain.failures import DeviceError
from jevtest.domain.kinds import AppState, Orientation
from jevtest.domain.screen import Element, Point, Screen

from ._typing import override
from .android_screen import has_empty_webview, keyboard_up, parse_screen
from .common import FOLLOW_UP, BaseDevice, Progress, cache_dir, digest, run, run_bytes, start_process, stop_process

KEYCODES = {
    "enter": 66,
    "delete": 67,
    "backspace": 67,
    "tab": 61,
    "escape": 111,
    "space": 62,
    "back": 4,
    "home": 3,
    "menu": 82,
    "search": 84,
    "dpad_up": 19,
    "dpad_down": 20,
    "dpad_left": 21,
    "dpad_right": 22,
    "volume_up": 24,
    "volume_down": 25,
    "power": 26,
    "app_switch": 187,
    "move_end": 123,
    "move_home": 122,
}
ROTATIONS = {
    Orientation.PORTRAIT: 0,
    Orientation.LANDSCAPE: 1,
    Orientation.PORTRAIT_UPSIDE_DOWN: 2,
    Orientation.LANDSCAPE_RIGHT: 3,
}
DOUBLE_TAP_GAP = 0.1  # Android and Flutter ignore taps < 40 ms apart and > 300 ms apart
DRAG_STEPS = 10  # finger positions along a drag
DRAG_HOLD = 0.1  # seconds the finger rests before lifting, so nothing flings
AGENT_START_TIMEOUT = 30
TOP_ACTIVITY = re.compile(r"topResumedActivity=ActivityRecord\{\S+ \S+ ([\w.]+)/")
PERMISSION_PROMPT = re.compile(r"com\.(google\.)?android\.permissioncontroller")
AGENT_SRC = Path(__file__).resolve().parent / "android_agent"
AGENT_ID = "dev.jevtest.agent"
AGENT_STOP_TIMEOUT = 10  # seconds for the agent to finish after /quit
AGENT_PORT = 7912  # on the device; adb forwards a free local port to it


def sdk_root() -> Path | None:
    """The Android SDK: ``$ANDROID_HOME``, ``$ANDROID_SDK_ROOT``, or Android Studio's default place."""
    for var in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        if os.environ.get(var):
            return Path(os.environ[var])
    for p in (Path.home() / "Library/Android/sdk", Path.home() / "Android/Sdk"):
        if p.exists():
            return p
    return None


def _tool(name: str, sdk_subpath: str, hint: str) -> str:
    """An SDK tool: on the PATH, or the highest version in the SDK."""
    found = shutil.which(name)
    if found:
        return found
    root = sdk_root()
    matches = sorted(root.glob(sdk_subpath)) if root else []
    if not matches:
        raise DeviceError(f"{name} not found. {hint}")
    return str(matches[-1])  # highest version for build-tools/*


def adb_path() -> str:
    """Adb."""
    return _tool("adb", "platform-tools/adb", "Install Android platform-tools or set ANDROID_HOME.")


def aapt2_path() -> str:
    """aapt2, which reads an APK's package name."""
    return _tool("aapt2", "build-tools/*/aapt2", "Install Android SDK build-tools (needed to read the APK).")


def devices() -> list[str]:
    """The serials of the connected devices that are ready (not offline or unauthorized)."""
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
        raise DeviceError(f"No connected Android device called '{wanted}' (names are exact). Connected: {listed}")
    if len(matches) > 1:
        raise DeviceError(
            f"Several connected Android devices are called '{wanted}' ({', '.join(matches)}): name one by its serial"
        )
    return matches[0]


def http_get(url: str, timeout: float) -> str:
    """A GET to the agent on its forwarded localhost port."""
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        body: bytes = resp.read()
    return body.decode()


# Devices tested at the same time share the cached agent: one builds it, the others wait for it.
AGENT_LOCK = threading.Lock()


def build_agent(progress: Progress) -> Path:
    """The agent APK, built once per source version and cached; one device builds it at a time."""
    with AGENT_LOCK:
        return _build_agent(progress)


def _build_agent(progress: Progress) -> Path:
    """Compile the on-device agent with the SDK's own tools (no Gradle). Cached by source hash."""
    version = digest(AGENT_SRC)
    apk = cache_dir() / f"android-agent-{version}.apk"
    if apk.exists():
        return apk
    root = sdk_root()
    tools = sorted(root.glob("build-tools/*")) if root else []
    jars = sorted(root.glob("platforms/android-*/android.jar")) if root else []
    if not tools or not jars:
        raise DeviceError("Android SDK build-tools and a platform are needed to build the jevtest agent")
    bt, jar = tools[-1], str(jars[-1])
    progress("building the Android agent (one time, a few seconds)")
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        run(
            [
                "javac",
                "--release",
                "11",
                "-cp",
                jar,
                "-d",
                str(t / "classes"),
                *map(str, sorted(AGENT_SRC.rglob("*.java"))),
            ]
        )
        run(
            [
                str(bt / "d8"),
                "--min-api",
                "24",
                "--lib",
                jar,
                "--output",
                tmp,
                *map(str, sorted((t / "classes").rglob("*.class"))),
            ]
        )
        run(
            [
                str(bt / "aapt2"),
                "link",
                "--manifest",
                str(AGENT_SRC / "AndroidManifest.xml"),
                "-I",
                jar,
                "--version-name",
                version,
                "-o",
                str(t / "base.apk"),
            ]
        )
        with zipfile.ZipFile(t / "base.apk", "a") as z:
            z.write(t / "classes.dex", "classes.dex")
        run([str(bt / "zipalign"), "-f", "4", str(t / "base.apk"), str(t / "aligned.apk")])
        apk.parent.mkdir(parents=True, exist_ok=True)
        keystore = cache_dir() / "jevtest-debug.keystore"
        if not keystore.exists():
            run(
                [
                    "keytool",
                    "-genkeypair",
                    "-keystore",
                    str(keystore),
                    "-storepass",
                    "android",
                    "-alias",
                    "jevtest",
                    "-keypass",
                    "android",
                    "-keyalg",
                    "RSA",
                    "-validity",
                    "10000",
                    "-dname",
                    "CN=jevtest",
                ]
            )
        run(
            [
                str(bt / "apksigner"),
                "sign",
                "--ks",
                str(keystore),
                "--ks-pass",
                "pass:android",
                "--out",
                str(apk),
                str(t / "aligned.apk"),
            ]
        )
    return apk


class AndroidDevice(BaseDevice):
    """An Android phone or emulator, driven through adb and jevtest's on-device agent.

    Args:
        device: The device's exact serial, model or emulator AVD name.
        progress: Told about slow one-time work (building the agent).
    """

    def __init__(self, device: str, progress: Progress) -> None:
        self._progress = progress
        self.adb = adb_path()
        found = devices()
        if not found:
            raise DeviceError("No Android device connected. Start an emulator or connect a phone (see `adb devices`).")
        self.serial = pick_device(device, found)
        self.app_path: Path | None = None
        self.activity = ""
        self._size: tuple[int, int] | None = None
        self.port = 0
        self.agent: subprocess.Popen[str] | None = None
        self._restore: dict[str, str] = {}  # what -> shell command that puts back what a step changed
        self._start_agent()

    # --- agent -------------------------------------------------------------------
    def _start_agent(self) -> None:
        """Install the agent if it changed, then start it and forward a free local port to it."""
        apk = build_agent(self._progress)
        version = apk.stem.rsplit("-", 1)[1]
        if f"versionName={version}" not in self.sh(f"dumpsys package {AGENT_ID} | grep versionName", check=False):
            self.sh(f"pm uninstall {AGENT_ID}", check=False)  # any older copy, whatever key signed it
            run([self.adb, "-s", self.serial, "install", str(apk)], timeout=120)
        self.sh(f"am force-stop {AGENT_ID}")  # a previous run's agent would hold the port
        self.port = int(run([self.adb, "-s", self.serial, "forward", "tcp:0", f"tcp:{AGENT_PORT}"]).strip())
        self.agent = start_process(
            [
                self.adb,
                "-s",
                self.serial,
                "shell",
                "am",
                "instrument",
                "-r",
                "-w",
                "-e",
                "port",
                str(AGENT_PORT),
                f"{AGENT_ID}/.Agent",
            ],
            ready="ready=1",
            log=cache_dir() / f"android-agent-{self.serial}.log",
            timeout=AGENT_START_TIMEOUT,
        )

    def _agent(self, path: str, wait_ms: int = 0, extra: str = "") -> str:
        """Call the agent. `wait_ms` is how long it may wait for the screen before answering."""
        url = f"http://127.0.0.1:{self.port}{path}" + (f"?ms={wait_ms}{extra}" if wait_ms else "")
        try:
            return http_get(url, timeout=wait_ms / 1000 + 10)
        except OSError as e:
            raise DeviceError(f"Lost the Android agent during {path} ({e})") from None

    @override
    def close(self) -> None:
        """Stop the agent, remove the port forward, and put back what steps changed."""
        if self.agent and self.agent.poll() is None:
            with contextlib.suppress(DeviceError):  # it may already be gone
                self._agent("/quit")
                # `am instrument -w` exits once the device has finished tearing down UI automation,
                # which resets rotation state; only after that can a restore stick.
                with contextlib.suppress(subprocess.TimeoutExpired):
                    self.agent.wait(AGENT_STOP_TIMEOUT)
        stop_process(self.agent)
        run([self.adb, "-s", self.serial, "forward", "--remove", f"tcp:{self.port}"], check=False)
        self.restore()

    @override
    def restore(self) -> None:
        """Put back what steps changed (rotation, dark mode, network), as the device was before them."""
        for command in self._restore.values():
            self.sh(command, check=False)
        self._restore.clear()

    @override
    def wait_idle(self, timeout: float, quiet: float | None = None) -> None:
        """Return once the screen has stopped changing (for `quiet` seconds), or after `timeout` seconds."""
        extra = f"&quiet={int(quiet * 1000)}" if quiet is not None else ""
        self._agent("/idle", int(timeout * 1000), extra)

    @override
    def wait_change(self, timeout: float) -> None:
        """Return as soon as the screen changes, or after `timeout` seconds."""
        self._agent("/change", int(timeout * 1000))

    # --- plumbing ------------------------------------------------------------
    def sh(self, cmd: str, timeout: float = 60, *, check: bool = True) -> str:
        """Run a shell command on the device."""
        return run([self.adb, "-s", self.serial, "shell", cmd], timeout=timeout, check=check)

    def size(self, rotation: int = 0) -> tuple[int, int]:
        """The screen size in pixels, for the current rotation (1 and 3 are landscape)."""
        if self._size is None:
            found = re.findall(r"(\d+)x(\d+)", self.sh("wm size"))
            if not found:
                raise DeviceError("Could not read the screen size (`wm size`)")
            w, h = found[-1]  # an override size, if any, is listed last
            self._size = (int(w), int(h))
        w, h = self._size
        return (h, w) if rotation in (1, 3) else (w, h)

    # --- lifecycle -------------------------------------------------------------
    def install(self, app: Path) -> str:
        """Install an .apk or .aab and return its package name."""
        self.app_path = app
        suffix = app.suffix.lower()
        if suffix == ".apk":
            self.app_id = run([aapt2_path(), "dump", "packagename", str(app)]).strip()
            # No -g: permissions start ungranted, like a real install. Use a `grant:` step to pre-grant.
            run([self.adb, "-s", self.serial, "install", "-r", "-t", str(app)], timeout=300)
        elif suffix == ".aab":
            self._install_bundle(app)
        else:
            raise DeviceError(f"Android needs an .apk or .aab, got {app.name}")
        out = self.sh(f"cmd package resolve-activity --brief -c android.intent.category.LAUNCHER {self.app_id}")
        lines = out.strip().splitlines()
        self.activity = lines[-1].strip() if lines else ""
        if "/" not in self.activity:
            raise DeviceError(f"{self.app_id} has no launcher activity")
        return self.app_id

    def _install_bundle(self, app_path: Path) -> None:
        """Install an .aab through bundletool, which builds the APKs for this device."""
        bundletool = shutil.which("bundletool")
        if not bundletool:
            raise DeviceError("bundletool is required to install .aab files (brew install bundletool)")
        self.app_id = run(
            [bundletool, "dump", "manifest", "--bundle", str(app_path), "--xpath", "/manifest/@package"]
        ).strip()
        with tempfile.TemporaryDirectory() as tmp:
            apks = Path(tmp) / "app.apks"
            run(
                [
                    bundletool,
                    "build-apks",
                    "--bundle",
                    str(app_path),
                    "--output",
                    str(apks),
                    "--connected-device",
                    "--device-id",
                    self.serial,
                    "--adb",
                    self.adb,
                ],
                timeout=600,
            )
            run(
                [bundletool, "install-apks", "--apks", str(apks), "--device-id", self.serial, "--adb", self.adb],
                timeout=300,
            )

    def launch(self) -> None:
        """Start the app's launcher activity and wait until it's shown."""
        self.sh(f"am start -W -n {self.activity}")

    def resume(self) -> None:
        """Bring the app back to the foreground without restarting it."""
        self.sh(f"am start -W -n {self.activity}")

    def stop(self) -> None:
        """Force-stop the app."""
        self.sh(f"am force-stop {self.app_id}")

    def clear_data(self) -> None:
        """Clear the app's data."""
        self.sh(f"pm clear {self.app_id}")

    def reinstall(self) -> None:
        """Uninstall and install the build again."""
        if self.app_path is None:
            raise DeviceError("Nothing to reinstall: no app was installed")
        self.sh(f"pm uninstall {self.app_id}", check=False)
        self.install(self.app_path)

    @override
    def check_ready(self) -> None:
        """A phone that is asleep or locked shows no app to test. Say so; never wake or unlock it."""
        out = self.sh(
            "dumpsys power | grep -m1 mWakefulness=; dumpsys window | grep -m1 -E 'isKeyguardShowing='", check=False
        )
        if "mWakefulness=Awake" not in out or "isKeyguardShowing=true" in out:
            raise DeviceError(
                f"Android device {self.serial} is asleep or locked: unlock it and keep it awake during the run"
            )

    def app_state(self) -> AppState:
        """Where the app is: running at all, and whether it or its own permission prompt is on top."""
        out = self.sh(f"pidof {self.app_id}; dumpsys activity activities | grep -m1 topResumedActivity", check=False)
        if not re.match(r"\d+", out.strip()):
            return AppState.NOT_RUNNING
        top = TOP_ACTIVITY.search(out)
        # The app has left only when another app is on top. No top activity = mid-transition;
        # a permission prompt the app asked for sits on top of it but belongs to it.
        if not top or top.group(1) == self.app_id or PERMISSION_PROMPT.fullmatch(top.group(1)):
            return AppState.FOREGROUND
        return AppState.BACKGROUND

    # --- observe ---------------------------------------------------------------
    def _wait_for_typing(self) -> None:
        """Wait until a text field has focus and the keyboard is up.

        Keys sent before the keyboard is connected are dropped. Not "the field under the tap": on a real phone
        the keyboard slides up and the app scrolls the focused field out from under it.
        """
        deadline = time.monotonic() + FOLLOW_UP
        while True:
            screen = parse_screen(self._agent("/tree"), self.size)
            if screen.keyboard_visible and any(el.editable and el.focused for el in screen.elements):
                return
            left = deadline - time.monotonic()
            if left <= 0:
                raise DeviceError("The text field did not get keyboard focus")
            self.wait_change(left)

    def tree(self) -> str:
        """The UI hierarchy XML.

        A WebView's content arrives a moment after the WebView itself, so while a WebView is still empty,
        wait for the screen to change.
        """
        xml = self._agent("/tree")
        deadline = time.monotonic() + FOLLOW_UP
        while has_empty_webview(xml) and time.monotonic() < deadline:
            self.wait_change(deadline - time.monotonic())
            xml = self._agent("/tree")
        return xml

    @override
    def screen(self) -> Screen:
        """What's on the screen now."""
        return parse_screen(self.tree(), self.size)

    def screenshot(self, path: Path) -> None:
        """Save a PNG of the screen."""
        path.write_bytes(run_bytes([self.adb, "-s", self.serial, "exec-out", "screencap", "-p"]))

    # --- touch & keys ------------------------------------------------------------
    def tap(self, x: int, y: int) -> None:
        """Tap a point."""
        self.sh(f"input tap {x} {y}")

    def double_tap(self, x: int, y: int) -> None:
        """Double-tap a point."""
        # One shell call, so the gap between the taps is the sleep and not adb latency.
        self.sh(f"input tap {x} {y}; sleep {DOUBLE_TAP_GAP}; input tap {x} {y}")

    def long_press(self, x: int, y: int, seconds: float = 1.2) -> None:
        """Press and hold a point."""
        self.sh(f"input swipe {x} {y} {x} {y} {int(seconds * 1000)}")

    @override
    def drag(self, x1: int, y1: int, x2: int, y2: int) -> None:
        """Press, move, hold still, lift."""
        # Press, move in steps, hold still, lift: the content stops where the finger stops. A plain
        # `input swipe` lifts while moving, so the content flings on and a scroll lands anywhere.
        steps = [(x1 + (x2 - x1) * i // DRAG_STEPS, y1 + (y2 - y1) * i // DRAG_STEPS) for i in range(1, DRAG_STEPS + 1)]
        moves = [f"input motionevent MOVE {x} {y}" for x, y in steps]
        self.sh(
            "; ".join(
                [f"input motionevent DOWN {x1} {y1}", *moves, f"sleep {DRAG_HOLD}", f"input motionevent UP {x2} {y2}"]
            )
        )

    def type_text(self, text: str, at: Point | None = None) -> None:
        """Type into the focused field, or first focus the field at `at`."""
        if not text.isascii():
            raise DeviceError("Android `input text` only supports ASCII characters")
        if at:  # focus the field, then wait until it has focus and the keyboard is up
            self.tap(*at)
            self._wait_for_typing()
        # `input text` needs %s for spaces; newlines become Enter presses.
        for i, line in enumerate(text.split("\n")):
            if i:
                self.key("enter")
            if line:
                self.sh("input text " + shlex.quote(line.replace("%", r"\%").replace(" ", "%s")))

    def clear_text(self, element: Element) -> None:
        """Erase a text field: put the cursor after its text, then delete exactly what is there."""
        self.tap(*element.end)
        self._wait_for_typing()
        if element.value:
            self.sh("input keyevent 123 " + " ".join(["67"] * len(element.value)))

    def key(self, name: str) -> None:
        """Press a named key, or an Android key code given as a number."""
        code = KEYCODES.get(name)
        if code is None and not name.isdigit():
            raise DeviceError(f"Unknown key '{name}'. Known: {', '.join(sorted(KEYCODES))}, or a keycode number")
        self.sh(f"input keyevent {code if code is not None else name}")

    def back(self) -> None:
        """Press Back."""
        self.key("back")

    def home(self) -> None:
        """Press Home."""
        self.key("home")

    def hide_keyboard(self) -> None:
        """Close the keyboard with Back, if it's up."""
        # Back closes the keyboard, but with no keyboard it leaves the screen: check right before.
        if keyboard_up(ET.fromstring(self._agent("/tree"))):
            self.key("back")

    # --- device ------------------------------------------------------------------
    def rotate(self, orientation: Orientation) -> None:
        """Rotate the screen; auto-rotate and the orientation are put back on close."""
        if "rotation" not in self._restore:  # rotating needs auto-rotate off; close() puts both back
            self._restore["rotation"] = (
                f"{self._setting('system', 'user_rotation')}; {self._setting('system', 'accelerometer_rotation')}"
            )
        self.sh("settings put system accelerometer_rotation 0")
        self.sh(f"settings put system user_rotation {ROTATIONS[orientation]}")

    def _setting(self, namespace: str, key: str) -> str:
        """The shell command that puts an Android setting back to its current value."""
        value = self.sh(f"settings get {namespace} {key}", check=False).strip()
        if value in ("", "null"):
            return f"settings delete {namespace} {key}"
        return f"settings put {namespace} {key} {value}"

    def set_location(self, latitude: float, longitude: float) -> None:
        """Set the emulator's GPS location."""
        if not self.serial.startswith("emulator-"):
            raise DeviceError("Setting location is only supported on the Android emulator")
        run([self.adb, "-s", self.serial, "emu", "geo", "fix", str(longitude), str(latitude)])

    def open_url(self, url: str) -> None:
        """Open a deep link or URL."""
        self.sh(f"am start -W -a android.intent.action.VIEW -d {shlex.quote(url)}")

    def dark_mode(self, *, on: bool) -> None:
        """Switch dark mode; the previous setting is put back on close."""
        if "dark_mode" not in self._restore:
            now = self.sh("cmd uimode night", check=False).strip().removeprefix("Night mode: ")
            if now not in ("yes", "no", "auto"):
                raise DeviceError(f"Can't read the device's dark mode setting to restore it later (got {now!r})")
            self._restore["dark_mode"] = f"cmd uimode night {now}"
        self.sh(f"cmd uimode night {'yes' if on else 'no'}")

    def grant(self, permission: str) -> None:
        """Grant the app a runtime permission, by its full name."""
        if not permission.startswith("android.permission."):
            raise DeviceError(
                f"'{permission}': give the full Android permission name, e.g. android.permission.{permission.upper()}"
            )
        self.sh(f"pm grant {self.app_id} {permission}")

    def network(self, *, on: bool) -> None:
        """Switch Wi-Fi and mobile data; their previous state is put back on close."""
        if "network" not in self._restore:
            wifi = self.sh("settings get global wifi_on", check=False).strip() not in ("0", "")
            data = self.sh("settings get global mobile_data", check=False).strip() == "1"
            self._restore["network"] = (
                f"svc wifi {'enable' if wifi else 'disable'}; svc data {'enable' if data else 'disable'}"
            )
        state = "enable" if on else "disable"
        self.sh(f"svc wifi {state}; svc data {state}")
