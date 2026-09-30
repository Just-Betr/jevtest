"""Android driver: adb for the app and input, a small on-device agent for reading the screen.

Finding the SDK and the device, and building the agent, are in `android_tools`; reading the screen is in
`android_screen`. This module is the `Device` itself.
"""

from __future__ import annotations

import contextlib
import re
import shlex
import subprocess
import tempfile
import urllib.parse
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from pathlib import Path

from jevtest.domain.failures import DeviceError
from jevtest.domain.kinds import AppState, Orientation
from jevtest.domain.screen import Element, Point, Screen

from . import tool_says as says
from ._typing import override
from .android_screen import EDITABLE, has_empty_webview, keyboard_up, parse_screen, typing_ready
from .android_tools import aapt2_path, adb_path, build_agent, bundletool_path, devices, http_get, pick_device
from .cache import cache_dir
from .common import (
    AgentRefused,
    BaseDevice,
    Progress,
    ToolFailed,
    Undo,
    no_app_opens,
    run,
    run_bytes,
    start_process,
    stop_process,
    wait_until,
)

KEYCODES = {
    "enter": 66,
    "return": 66,
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
AGENT_ID = "dev.jevtest.agent"
AGENT_STOP_TIMEOUT = 10  # seconds for the agent to finish after /quit
AGENT_PORT = 7912  # on the device; adb forwards a free local port to it
AGENT_ERROR = "error: "
"""How the agent starts an answer to a request it couldn't do."""
AUTOFILL_SERVICE = "autofill_service"  # the secure setting naming the autofill service; unset: none
AGENT_CALL_TIMEOUT = 10  # seconds for one agent call to answer (it answers at once: this catches a lost agent)


class AmStartFailed(DeviceError):
    """`am start` said why it couldn't start something. `said` is its own words."""

    def __init__(self, message: str, said: str) -> None:
        super().__init__(message)
        self.said = said


def check_awake(serial: str) -> None:
    """Raise `DeviceError` if the device is asleep or locked: it shows no app to test. Never wakes or unlocks it."""
    out = run([adb_path(), "-s", serial, "shell", says.AWAKE_QUERY], check=False)
    if not says.awake_and_unlocked(out):
        raise DeviceError(f"Android device {serial} is asleep or locked: unlock it and keep it awake during the run")


def find_android(device: str) -> str:
    """The serial of the one connected Android device with exactly this serial, model or AVD name."""
    found = devices()
    if not found:
        raise DeviceError("No Android device connected. Start an emulator or connect a phone (see `adb devices`).")
    return pick_device(device, found)


class AndroidDevice(BaseDevice):
    """An Android phone or emulator, driven through adb and jevtest's on-device agent.

    Args:
        device: The device's exact serial, model or emulator AVD name.
        progress: Told about slow one-time work (building the agent).
    """

    def __init__(self, device: str, progress: Progress) -> None:
        self._progress = progress
        self.adb = adb_path()
        self.serial = find_android(device)
        self.app_path: Path | None = None
        self.activity = ""
        self._size: tuple[int, int] | None = None
        self.port = 0
        self.agent: subprocess.Popen[str] | None = None
        self._undo = Undo(self.serial)  # each entry: the shell command that puts it back
        self._install_agent()
        self._stop_old_agent()
        # after the old agent stops, as stopping one resets the rotation state and would undo a rotation put back
        self._put_back_left_by_a_stopped_run()
        self._launch_agent()

    # --- agent -------------------------------------------------------------------
    def _install_agent(self) -> None:
        """Install the agent, unless this version of it is installed."""
        with build_agent(self._progress) as apk:
            installed = self.sh(f"dumpsys package {AGENT_ID} | grep versionName", check=False)
            if f"versionName={apk.version}" not in installed:
                self.sh(f"pm uninstall {AGENT_ID}", check=False)  # any older copy, whatever key signed it
                run([self.adb, "-s", self.serial, "install", str(apk.path)], timeout=120)

    def _stop_old_agent(self) -> None:
        """Stop an agent a previous run left running: it would hold the port."""
        self.sh(f"am force-stop {AGENT_ID}")

    def _launch_agent(self) -> None:
        """Start the agent, and forward a free local port to it."""
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

    def _agent(self, path: str) -> str:
        """Call the agent: it answers at once, it never waits for the screen.

        Raises:
            AgentRefused: It answered that it couldn't (``error: …``).
            DeviceError: It didn't answer: something stopped it.
        """
        url = f"http://127.0.0.1:{self.port}{path}"
        try:
            answer = http_get(url, timeout=AGENT_CALL_TIMEOUT)
        except OSError as e:
            raise DeviceError(
                f"Lost the Android agent during {path} ({e}): something stopped it, such as another tool using UI "
                "Automation (only one can at a time); the next test starts it again"
            ) from None
        if answer.startswith(AGENT_ERROR):
            raise AgentRefused("Android agent", path.split("?", 1)[0], answer.removeprefix(AGENT_ERROR))
        return answer

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
    def _put_back(self, entry: object) -> bool:
        """Run the entry's shell command (rotation, dark mode, network, autofill).

        Its exit code isn't checked: the rotation's ends with `wm user-rotation`, which older Android lacks (measured),
        and the settings before it still go back. Not reaching the device at all raises.
        """
        if not isinstance(entry, str):
            return False
        self.sh(entry, check=False)
        return True

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
        bundletool = bundletool_path()
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
        self._am_start(f"-n {self.activity}", f"start {self.activity}")

    def resume(self) -> None:
        """Bring the app back to the foreground without restarting it.

        Starting a running app's launcher activity brings its task forward as it was.
        """
        self.launch()

    def _am_start(self, args: str, what: str) -> None:
        """`am start -W`, failing with what it says went wrong: Android 13 says it and exits 0 (measured).

        Raises:
            AmStartFailed: It said why it couldn't start.
            ToolFailed: It exited with an error and said nothing `am_error` recognises.
        """
        try:
            said = says.am_error(self.sh(f"am start -W {args}"))
        except ToolFailed as e:  # Android 14 and newer exit 1 after saying why (measured)
            said = says.am_error(e.output)
            if said is None:
                raise
        if said is not None:
            raise AmStartFailed(f"Could not {what}: {said}", said)

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
    def prepare_for_test(self) -> None:
        """Fail if the phone is asleep or locked (never wake or unlock it); start the agent again if it stopped.

        Something may have stopped the agent (the test it was in has failed): one test's loss isn't every test's.
        """
        check_awake(self.serial)
        if self.agent is not None and self.agent.poll() is not None:
            self._progress("the Android agent had stopped: starting it again")
            run([self.adb, "-s", self.serial, "forward", "--remove", f"tcp:{self.port}"], check=False)
            self._stop_old_agent()
            self._launch_agent()

    @override
    def app_state(self) -> AppState:
        """Where the app is: running at all, and whether it or its own permission prompt is on top."""
        out = self.sh(
            f"pidof {self.app_id}; dumpsys activity activities | grep -m1 -E '{says.RESUMED_GREP}'", check=False
        )
        if not re.match(r"\d+", out.strip()):
            return AppState.NOT_RUNNING
        top = says.resumed_app(out)
        # The app has left only when another app is on top. No top activity = mid-transition;
        # a permission prompt the app asked for sits on top of it but belongs to it.
        if top is None or top == self.app_id or says.PERMISSION_PROMPT.fullmatch(top):
            return AppState.FOREGROUND
        return AppState.BACKGROUND

    # --- observe ---------------------------------------------------------------
    def _wait_for_typing(self) -> None:
        """Wait until a text field has focus and the keyboard is up.

        Keys sent before the keyboard is connected are dropped. Not "the field under the tap": on a real phone
        the keyboard slides up and the app scrolls the focused field out from under it.
        """
        wait_until(lambda: typing_ready(self._agent("/tree")), "The text field did not get keyboard focus")

    def tree(self) -> str:
        """The UI hierarchy XML.

        A WebView's content arrives a moment after the WebView itself: while a WebView is empty, it's read
        again every `CHECK_INTERVAL`, for up to `FOLLOW_UP` seconds (then as it is: some web views are empty).
        """
        xml = [self._agent("/tree")]

        def filled() -> bool:
            if has_empty_webview(xml[0]):
                xml[0] = self._agent("/tree")
            return not has_empty_webview(xml[0])

        with contextlib.suppress(DeviceError):
            wait_until(filled, "A web view stayed empty")
        return xml[0]

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
        """Press, move, hold still, lift: every drag moves the content as far as the finger, so a scroll too."""
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
        """Type into the focused field, or first focus the field at `at`.

        ASCII is typed key by key (`adb shell input text`). A line with any other letter (`José`, `日本`) is put in at
        the cursor by the agent: `input text` types only the keys of a US keyboard.
        """
        if at:  # focus the field, then wait until it has focus and the keyboard is up
            self.tap(*at)
            self._wait_for_typing()
        for i, line in enumerate(text.split("\n")):
            if i:
                self.key("enter")  # newlines become Enter presses
            if not line.isascii():
                answer = self._agent(
                    f"/insert?text={urllib.parse.quote(line, safe='')}&fields={','.join(sorted(EDITABLE))}"
                )
                if answer != "inserted":
                    raise DeviceError(f"Couldn't type {line!r}: {answer}")
                continue
            # `input text` types %s as a space and has no escape for it, so each piece ends right after a % and no
            # piece holds a %s of the text's own; spaces are then written as %s (measured).
            pieces = re.findall(r"[^%]*%|[^%]+", line)
            if pieces:
                self.sh("; ".join("input text " + shlex.quote(p.replace(" ", "%s")) for p in pieces))

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
            raise DeviceError(f"Unknown key '{name}'. Known: {', '.join(sorted(KEYCODES))}, or a key code number")
        self.sh(f"input keyevent {code if code is not None else name}")

    def back(self) -> None:
        """Press Back."""
        self.key("back")

    @override
    def _press_home(self) -> None:
        self.key("home")

    def looks(self, elements: Sequence[Element]) -> str:
        """A fingerprint of how the elements are drawn now, from one screenshot.

        The tree can't show a system dialog moving: fading in, it reports its final bounds at once; sliding up,
        its first bounds for about half a second, then its final ones (both measured).
        """
        if not elements:
            return ""
        rects = ";".join(",".join(str(v) for v in e.bounds) for e in elements)
        return self._agent(f"/pixels?rects={rects}")

    def hide_keyboard(self) -> None:
        """Close the keyboard with Back, if it's up, and wait until it's gone."""
        # Back closes the keyboard, but with no keyboard it leaves the screen: check right before.
        if keyboard_up(ET.fromstring(self._agent("/tree"))):
            self.key("back")
            self.wait_until(lambda s: not s.keyboard_visible, "The keyboard did not close")

    # --- device ------------------------------------------------------------------
    def rotate(self, orientation: Orientation) -> None:
        """Rotate the screen; auto-rotate and the orientation are put back on close."""
        # Locking the rotation turns auto-rotate off. The agent puts the device's rotation state back when it stops;
        # close() also puts back both settings, as they were before the first rotate.
        self._undo.remember("rotation", self._rotation_put_back)
        if self._agent(f"/rotate?to={ROTATIONS[orientation]}") != "rotated":
            raise DeviceError(f"The device refused to turn the screen to {orientation}")
        self._wait_for_rotation(orientation)

    def _rotation_put_back(self) -> str:
        """The shell command that puts back the rotation settings as they are now.

        That's the settings, and the window manager's own lock, which a stopped agent's rotation lock leaves set and
        which turns auto-rotate off again over the setting (measured); older Android lacks the command.
        """
        user = self.sh("settings get system user_rotation", check=False).strip()
        auto = self.sh("settings get system accelerometer_rotation", check=False).strip()
        lock = "wm user-rotation free" if auto == "1" else f"wm user-rotation lock {user if user.isdigit() else 0}"
        return "; ".join(
            (_setting_command("user_rotation", user), _setting_command("accelerometer_rotation", auto), lock)
        )

    def _wait_for_rotation(self, orientation: Orientation) -> None:
        """Wait until the screen has turned.

        The setting takes effect a moment later, and the screen can be still before it does: waiting for a still
        screen alone could read the old layout, or one halfway through turning.

        Raises:
            DeviceError: The screen didn't turn within `FOLLOW_UP` seconds.
        """
        wait_until(
            lambda: int(ET.fromstring(self._agent("/tree")).get("rotation", "0")) == ROTATIONS[orientation],
            f"The screen did not turn to {orientation}",
        )

    def set_location(self, latitude: float, longitude: float) -> None:
        """Set the emulator's GPS location."""
        if not self.serial.startswith("emulator-"):
            raise DeviceError("Setting location is only supported on the Android emulator")
        run([self.adb, "-s", self.serial, "emu", "geo", "fix", str(longitude), str(latitude)])

    def open_url(self, url: str) -> None:
        """Open a deep link or URL."""
        try:
            self._am_start(f"-a android.intent.action.VIEW -d {shlex.quote(url)}", f"open {url}")
        except AmStartFailed as e:
            if says.NO_ACTIVITY_FOR_INTENT in e.said:
                raise no_app_opens(url) from None
            raise

    def dark_mode(self, *, on: bool) -> None:
        """Switch dark mode; the previous setting is put back on close."""
        self._undo.remember("dark mode", self._dark_mode_put_back)
        self.sh(f"cmd uimode night {'yes' if on else 'no'}")

    def _dark_mode_put_back(self) -> str:
        """The shell command that puts back the dark mode setting as it is now.

        Raises:
            DeviceError: The setting isn't one Android lists (yes, no, auto), so it couldn't be put back.
        """
        now = self.sh("cmd uimode night", check=False).strip().removeprefix("Night mode: ")
        if now not in ("yes", "no", "auto"):
            raise DeviceError(f"Can't read the device's dark mode setting to restore it later (got {now!r})")
        return f"cmd uimode night {now}"

    def grant(self, permissions: Sequence[str]) -> None:
        """Grant the app runtime permissions, by their full names (the test file loader checks they're full)."""
        for permission in permissions:
            try:
                self.sh(f"pm grant {self.app_id} {permission}")
            except ToolFailed as e:
                raise DeviceError(f"Can't grant {permission}: {says.pm_exception(e.output) or e}") from None
            # Android 15 and 17 answer a permission the app doesn't declare with no error, and grant nothing
            # (measured; 12 and 13 refuse it): what Android recorded is what counts.
            record = self.sh(f"dumpsys package {self.app_id}", check=False)
            if not says.granted(permission, record):
                if not says.declared(permission, record):
                    raise DeviceError(f"Can't grant {permission}: the app doesn't declare it in its manifest")
                raise DeviceError(f"Can't grant {permission}: Android didn't record it as granted")

    def network(self, *, on: bool) -> None:
        """Switch Wi-Fi and mobile data; their previous state is put back on close."""
        self._undo.remember("network", self._network_put_back)
        state = "enable" if on else "disable"
        self.sh(f"svc wifi {state}; svc data {state}")

    def autofill_off(self) -> None:
        """Turn off the autofill service; the device's own is put back on close.

        A password manager (Google's, on a phone with a Google account) offers to save what a sign-in typed in a
        sheet over the app, which stays up into the next test (measured on a Pixel 4a, Android 13).
        """
        self._undo.remember("autofill", self._autofill_put_back)
        self.sh(f"settings delete secure {AUTOFILL_SERVICE}")

    def _autofill_put_back(self) -> str:
        """The shell command that puts the autofill service back as it is now."""
        return _setting_command(AUTOFILL_SERVICE, self.sh(f"settings get secure {AUTOFILL_SERVICE}").strip(), "secure")

    def _network_put_back(self) -> str:
        """The shell command that puts Wi-Fi and mobile data back as they are now."""
        wifi = self.sh("settings get global wifi_on", check=False).strip() not in ("0", "")
        data = self.sh("settings get global mobile_data", check=False).strip() == "1"
        return f"svc wifi {'enable' if wifi else 'disable'}; svc data {'enable' if data else 'disable'}"


def _setting_command(key: str, value: str, table: str = "system") -> str:
    """The shell command that sets `key` in the settings `table` back to `value`, as read (``null``: it wasn't set)."""
    if value in ("", "null"):
        return f"settings delete {table} {key}"
    return f"settings put {table} {key} {value}"
