"""iOS driver: a simulator (simctl) or a real iPhone (devicectl), with the XCUITest agent for screen and touch.

Finding devices, signing, and building the agent are in `ios_tools`; reading the screen is in `ios_screen`. This
module is the `Device` itself.

The agent is the same on both. What differs is how jevtest gets to it:
- simulator: an unsigned agent, reached at 127.0.0.1 (the simulator shares the Mac's network);
- iPhone: an agent signed with your Xcode team, reached through the USB tunnel Xcode keeps to the
  phone (its address changes when the phone relocks, so it is read fresh when needed).
"""

from __future__ import annotations

import os
import secrets
import subprocess
import tempfile
from base64 import b64decode
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import cast

from jevtest.adapters.shapes import is_json_object, is_list
from jevtest.domain.failures import DeviceError
from jevtest.domain.kinds import AppState, Orientation
from jevtest.domain.screen import Element, Point, Screen
from jevtest.domain.steps import KEYS

from . import tool_says as says
from ._typing import override
from .cache import BuildInUse, cache_dir, digest
from .common import (
    AgentRefused,
    BaseDevice,
    Progress,
    TimedOut,
    ToolFailed,
    Undo,
    no_app_opens,
    start_process,
    stop_process,
    wait_until,
)
from .ios_screen import AgentTree, parse_tree
from .ios_tools import (
    AGENT_LOCK,
    AGENT_SRC,
    agent_builds,
    app_bundle,
    app_team,
    build_agent_with_xcodebuild,
    check_build,
    devicectl,
    find_target,
    free_port,
    http_post,
    info_plist,
    provisioned_devices,
    remove_port_logs,
    simctl,
    xcode_team,
)
from .tool_output import Object, as_text, text_at

AGENT_CALL_TIMEOUT = 150
"""Seconds one agent call may take. Before touching while a system alert is up, XCUITest waits up to 60 s for
SpringBoard to settle (normally well under a second; an iPhone that needs a restart can take the full 60 s)."""
APP_WAIT = 10.0
"""Seconds to wait for the app to come to the foreground after a launch or a resume."""
AGENT_START_TIMEOUT = 300
"""Seconds the agent may take to start, including xcodebuild installing it on a fresh simulator or phone."""
SWIPE_SPEED = 1500
"""Points per second a swipe's finger moves: a flick, as swipe-to-dismiss and carousels expect."""
SWIPE_HOLD = 0.05
"""Seconds a swipe's finger rests at its end before it lifts."""
SCROLL_SPEED = 300
"""Points per second a scroll's finger moves. Faster, a web page flings on after the finger lifts, however long
it rests first (measured in a WKWebView: at 500 a 100 pt drag moved the page 320 pt, at 1500 470 pt; at 250-350
a 524 pt drag moved it 514 pt, the 10 pt being touch slop), and `scroll_to` could jump past its target."""
SCROLL_HOLD = 0.1
"""Seconds a scroll's finger rests at its end before it lifts."""

APP_STATES = {
    0: AppState.NOT_RUNNING,
    1: AppState.NOT_RUNNING,
    2: AppState.BACKGROUND,
    3: AppState.BACKGROUND,
    4: AppState.FOREGROUND,
}
"""XCUIApplication.State's raw values (unknown, notRunning, runningBackgroundSuspended, runningBackground,
runningForeground), as jevtest's app states."""


PORTRAIT, LANDSCAPE_LEFT = 1, 3
"""UIDeviceOrientation's raw values for upright and for `rotate: landscape` (landscapeLeft)."""

SCREEN_ORIENTATIONS = frozenset({1, 2, 3, 4})
"""The UIDeviceOrientation raw values that turn the screen: portrait, upside down, landscape left and right. The
others (unknown, face up, face down) say nothing about which way the screen is turned."""


class IOSDevice(BaseDevice):
    """A booted iOS simulator or a connected iPhone, driven through jevtest's XCUITest agent.

    Args:
        device: The device's exact name or UDID.
        app: The build under test. On an iPhone, jevtest signs its agent with the team that signed this build.
        progress: Told about slow one-time work (building and signing the agent).
    """

    def __init__(self, device: str, app: Path, progress: Progress) -> None:
        self._progress = progress
        target = find_target(device)
        self.udid, self.name, self.physical = target.udid, target.name, target.physical
        self._tmp = tempfile.TemporaryDirectory()
        self.port = free_port()
        self.token = secrets.token_urlsafe(32)  # the agent serves only requests that carry this run's token
        self.host = "127.0.0.1"
        self.agent: subprocess.Popen[str] | None = None
        self.app_id = ""
        self.agent_log = cache_dir() / f"ios-agent-{target.udid}.log"
        self.app_path: Path | None = None
        self._unpacked: dict[Path, Path] = {}  # build -> its .app, unpacked once
        self._agent_build: BuildInUse | None = None
        self._undo = Undo(target.udid)  # each entry: an agent call ({"call", "body"}) or {"simctl": args}
        try:
            self.team = xcode_team(app_team(self._bundle(app))) if self.physical else ""
            self._start_agent()
            self._put_back_left_by_a_stopped_run()
        except BaseException:
            self._release()  # the caller never gets a device to close
            raise

    # --- agent ------------------------------------------------------------------
    def _build_agent(self, out: Path) -> Path:
        """The agent's .xctestrun, built into `out` once per source version (and team, and phone, for iPhones)."""
        runner = out / "Build/Products/Debug-iphoneos/JevAgentUITests-Runner.app"
        runs = sorted((out / "Build/Products").glob("*.xctestrun"))
        if runs and (not self.physical or self.udid in provisioned_devices(runner)):
            return runs[0]
        if not self.physical:
            self._progress("building the iOS agent (one time, about a minute)")
            build_agent_with_xcodebuild(out, "generic/platform=iOS Simulator", [])
        else:
            # Built for this phone, so Xcode registers it with the team and puts it in the profile.
            self._progress(f"building and signing the iOS agent for {self.name} (team {self.team})")
            signing = [
                "-allowProvisioningUpdates",
                "-allowProvisioningDeviceRegistration",
                f"DEVELOPMENT_TEAM={self.team}",
                "CODE_SIGN_STYLE=Automatic",
                "CODE_SIGNING_ALLOWED=YES",
                "CODE_SIGNING_REQUIRED=YES",
                "CODE_SIGN_IDENTITY=Apple Development",
                f"JEVTEST_TEAM_SUFFIX=.{self.team}",
            ]
            try:
                build_agent_with_xcodebuild(out, f"id={self.udid}", signing)
            except DeviceError:  # a first build can race Xcode replacing the provisioning profile
                build_agent_with_xcodebuild(out, f"id={self.udid}", signing)
        runs = sorted((out / "Build/Products").glob("*.xctestrun"))
        if not runs:
            raise DeviceError("iOS agent build produced no .xctestrun")
        return runs[0]

    def _start_agent(self) -> None:
        """Build the agent if needed and start it on the device; on an iPhone, find its tunnel address."""
        env = dict(
            os.environ,
            TEST_RUNNER_JEVTEST_PORT=str(self.port),
            TEST_RUNNER_JEVTEST_TOKEN=self.token,
            TEST_RUNNER_JEVTEST_LOCAL_ONLY="0" if self.physical else "1",  # an iPhone is reached over USB
        )
        with AGENT_LOCK:
            builds, version = agent_builds(self.team), digest(AGENT_SRC)
            if self._agent_build is None:  # marked once, until close(): xcodebuild runs the agent from it
                self._agent_build = builds.use(version)
            xctestrun = self._build_agent(self._agent_build.path)
            builds.drop_older(keep=version)  # about 150 MB each
            remove_port_logs()
            try:
                self.agent = start_process(
                    [
                        "xcodebuild",
                        "test-without-building",
                        "-xctestrun",
                        str(xctestrun),
                        "-destination",
                        f"id={self.udid}",
                    ],
                    ready="JEVTEST_AGENT_READY",
                    log=self.agent_log,
                    timeout=AGENT_START_TIMEOUT,
                    env=env,
                )
            except DeviceError as e:
                log = self.agent_log.read_text(errors="replace")
                if says.ui_testing_not_authorized(log):
                    raise DeviceError(self._not_authorized()) from e
                if says.AUTOMATION_NOT_ALLOWED in log:
                    raise DeviceError(
                        f'{self.name} did not allow UI automation ("Timed out while enabling automation mode"). '
                        "Unlock it and keep the screen on, check Settings > Developer > Enable UI Automation is on, "
                        "and answer any prompt on its screen; then run again."
                    ) from e
                raise
        if self.physical:
            self.host = self._tunnel_host()

    def _tunnel_host(self) -> str:
        """The phone's address on the USB tunnel Xcode keeps to it. Changes when the phone relocks."""
        address = text_at(
            devicectl("device", "info", "details", "--device", self.udid), "connectionProperties", "tunnelIPAddress"
        )
        if not address:
            raise DeviceError(f"No connection to {self.name}: unlock it and keep it plugged in")
        return f"[{address}]" if ":" in address else address

    def _url(self, path: str) -> str:
        """The agent's URL for `path`."""
        return f"http://{self.host}:{self.port}{path}"

    def _log_tail(self) -> str:
        """The end of the agent's log, for error messages."""
        return self.agent_log.read_text()[-1500:] if self.agent_log.exists() else "(no log)"

    def _call(self, path: str, **body: object) -> Object:
        """Call the agent; on an iPhone, find the tunnel again once if the call fails (it moves on relock).

        Raises:
            DeviceError: The agent is unreachable or reported an error.
        """
        body.setdefault("bundle_id", self.app_id)
        try:
            data = http_post(self._url(path), body, timeout=AGENT_CALL_TIMEOUT, token=self.token)
        except OSError as e:
            if not self.physical:
                raise DeviceError(
                    f"Lost the iOS agent during {path} ({e}); the next test starts it again.\nAgent log tail:\n"
                    f"{self._log_tail()}"
                ) from None
            try:  # the phone's tunnel address changes when it relocks: look it up again, once
                self.host = self._tunnel_host()
                data = http_post(self._url(path), body, timeout=AGENT_CALL_TIMEOUT, token=self.token)
            except (OSError, DeviceError) as again:
                raise DeviceError(
                    f"Lost the agent on {self.name} during {path} ({again}). "
                    "Is it unlocked and plugged in? The next test starts the agent again.\nAgent log tail:\n"
                    f"{self._log_tail()}"
                ) from None
        if "error" in data:
            if self.physical and says.ui_testing_not_authorized(str(data["error"])):
                raise DeviceError(self._not_authorized())
            raise AgentRefused("iOS agent", path, str(data["error"]))
        return data

    def _not_authorized(self) -> str:
        """What to do when the iPhone asked to authenticate for UI testing and wasn't answered."""
        return (
            f"{self.name} asked for your passcode to allow UI testing, and it wasn't given: unlock it, enter the "
            "passcode when it asks, keep it unlocked, and run again"
        )

    @override
    def close(self) -> None:
        """Put back anything a step changed, then stop the agent."""
        self.restore()
        self._release()

    def _release(self) -> None:
        """Stop the agent, let go of its build, and remove the unpacked app."""
        stop_process(self.agent)
        if self._agent_build is not None:
            self._agent_build.close()
        self._tmp.cleanup()

    @override
    def _put_back(self, entry: object) -> bool:
        """Make the entry's agent call or tool command (appearance, orientation, location).

        An entry is ``{"call": path, "body": {...}}``, ``{"simctl": [args]}`` or ``{"devicectl": [args]}``.
        """
        if not is_json_object(entry):
            return False
        call, body = entry.get("call"), entry.get("body")
        if isinstance(call, str) and is_json_object(body):
            self._call(call, **body)
            return True
        tools: tuple[tuple[str, Callable[..., object]], ...] = (("simctl", simctl), ("devicectl", devicectl))
        for name, tool in tools:
            args = entry.get(name)
            if is_list(args) and all(isinstance(a, str) for a in args):
                tool(*map(str, args))
                return True
        return False

    def _remember_agent_setting(self, what: str, path: str) -> None:
        """Before the first change to `what`, read its current value through the agent's `path`, to put it back."""
        self._undo.remember(what, lambda: {"call": path, "body": {"raw": self._call(path)["raw"]}})

    @override
    def prepare_for_test(self) -> None:
        """Fail if an iPhone is locked (never unlock it); start the agent again if it stopped.

        Something may have stopped the agent (the test it was in has failed): one test's loss isn't every test's.
        """
        if self.physical and devicectl("device", "info", "lockState", "--device", self.udid).get("passcodeRequired"):
            raise DeviceError(f"{self.name} is locked: unlock it and keep it unlocked during the run")
        if self.agent is not None and self.agent.poll() is not None:
            self._progress("the iOS agent had stopped: starting it again")
            self.port = free_port()  # the one before may still be held by what's left of the agent
            self._start_agent()

    # --- lifecycle ----------------------------------------------------------------
    def install(self, app: Path) -> str:
        """Install a simulator or device build (an .app, or a .zip/.ipa containing one); return its bundle id."""
        bundle = self._bundle(app)
        info = info_plist(bundle)
        check_build(app, info, physical=self.physical, device=self.name)
        if "CFBundleIdentifier" not in info:
            raise DeviceError(f"{app.name} Info.plist has no CFBundleIdentifier")
        self.app_path = bundle
        self.app_id = as_text(info["CFBundleIdentifier"], f"{app.name}'s bundle id")
        self._install_bundle()
        return self.app_id

    def _bundle(self, app: Path) -> Path:
        """The build's .app, unpacked from a .zip / .ipa the first time it's needed."""
        if app not in self._unpacked:
            self._unpacked[app] = app_bundle(app, Path(self._tmp.name) / f"build-{len(self._unpacked)}")
        return self._unpacked[app]

    def _install_bundle(self) -> None:
        """Install the unpacked build."""
        if self.physical:
            devicectl("device", "install", "app", "--device", self.udid, str(self.app_path))
        else:
            simctl("install", self.udid, str(self.app_path), timeout=300)

    def launch(self) -> None:
        """Launch the app and wait until it's in the foreground; one that's running comes back as it was.

        That's what `simctl launch` and Android's `am start` do; devicectl did too once `--terminate-existing` was
        dropped (measured: the same process). A test that wants a new start stops the app first (`restart:`).
        """
        if self.physical:
            devicectl("device", "process", "launch", "--device", self.udid, self.app_id)
        else:
            simctl("launch", self.udid, self.app_id)
        self._call("/wait_foreground", timeout=APP_WAIT)

    def resume(self) -> None:
        """Bring the app back to the foreground without restarting it."""
        self._call("/activate", timeout=APP_WAIT)

    @override
    def app_state(self) -> AppState:
        """Where the app is, from XCUITest's state."""
        state = self._call("/state").get("state")
        if not isinstance(state, int) or state not in APP_STATES:
            raise DeviceError(f"The iOS agent reported an app state jevtest doesn't know: {state!r}")
        return APP_STATES[state]

    def stop(self) -> None:
        """Stop the app."""
        if self.physical:
            self._call("/terminate")
        else:
            simctl("terminate", self.udid, self.app_id, check=False)

    def clear_data(self) -> None:
        """Reinstall the app: iOS has no "clear data", and a reinstall is Apple's supported equivalent."""
        self.reinstall()

    def reinstall(self) -> None:
        """Uninstall and install the build again, with no permission decided, as a new install has.

        On an iPhone the uninstall doesn't always clear the app's permissions: a camera denial outlived one
        reinstall in three (measured on iOS 27), and the next test got no prompt. So they're reset after it.
        """
        self.stop()
        if self.physical:
            devicectl("device", "uninstall", "app", "--device", self.udid, self.app_id)
            self._install_bundle()
            self._call("/reset_permissions")
        else:
            simctl("uninstall", self.udid, self.app_id, check=False)
            self._install_bundle()
            simctl("privacy", self.udid, "reset", "all", self.app_id)

    # --- observe --------------------------------------------------------------------
    @override
    def screen(self) -> Screen:
        """What's on the screen now; nothing of the app when it isn't running.

        The agent answers so for an app that isn't in the foreground, and XCTest fails the read of one that ends while
        it reads it: that too is nothing of the app.
        """
        try:
            return parse_tree(cast("AgentTree", self._call("/tree")))  # the agent's own JSON
        except DeviceError as e:
            if not says.app_ended(str(e)):
                raise
            return parse_tree({"width": 0, "height": 0, "elements": []})

    def screenshot(self, path: Path) -> None:
        """Save a PNG of the screen."""
        if self.physical:
            path.write_bytes(b64decode(as_text(self._call("/screenshot").get("png"), "the agent's screenshot")))
        else:
            simctl("io", self.udid, "screenshot", str(path))

    # --- touch & keys -----------------------------------------------------------------
    def tap(self, x: int, y: int) -> None:
        """Tap a point."""
        self._call("/tap", x=x, y=y)

    def double_tap(self, x: int, y: int) -> None:
        """Double-tap a point."""
        self._call("/double_tap", x=x, y=y)

    def long_press(self, x: int, y: int, seconds: float = 1.2) -> None:
        """Press and hold a point."""
        self._call("/long_press", x=x, y=y, seconds=seconds)

    @override
    def drag(self, x1: int, y1: int, x2: int, y2: int) -> None:
        """Press, move, lift, at a flick's speed."""
        self._call("/drag", x1=x1, y1=y1, x2=x2, y2=y2, velocity=SWIPE_SPEED, hold=SWIPE_HOLD)

    @override
    def _scroll_drag(self, x1: int, y1: int, x2: int, y2: int) -> None:
        """Press, move slowly enough that nothing flings on, hold still, lift."""
        self._call("/drag", x1=x1, y1=y1, x2=x2, y2=y2, velocity=SCROLL_SPEED, hold=SCROLL_HOLD)

    def type_text(self, text: str, at: Point | None = None) -> None:
        """Type into the focused field, or first focus the field at `at`."""
        if at:  # focus the field; keys sent before the keyboard is up are lost
            self.tap(*at)
            self._wait_for_keyboard()
        self._call("/type", text=text)

    def choose(self, picker: Element, value: str) -> None:
        """Turn the picker wheel at `picker` to `value` (XCTest's own way: the wheel shows only its selected row).

        Raises:
            DeviceError: No wheel is there, or it has no such value.
        """
        try:
            self._call("/adjust", x=picker.center[0], y=picker.center[1], value=value)
        except DeviceError as e:
            raise DeviceError(f"Can't turn {picker.label()} to '{value}': {e}") from None

    def clear_text(self, element: Element) -> None:
        """Erase a text field: put the cursor after its text, then delete what is there until nothing is.

        XCUITest drops some of many deletes typed at once (measured: 7 of 11 landed in a UIKit field, 6 of 11 on
        another run), so the field is read again after each round, and a round that deletes nothing is an error.

        Raises:
            DeviceError: The field keeps text that deleting doesn't remove.
        """
        self.tap(*element.end)
        self._wait_for_keyboard()
        left = element.value
        while left:
            self._call("/key", key="delete", count=len(left))
            now = self._value_at(element)
            if len(now) >= len(left):
                raise DeviceError(f"Couldn't clear {element.label()}: deleting left '{now}' in it")
            left = now

    def _value_at(self, field: Element) -> str:
        """The text now in the field where `field` was (a field doesn't move while the keyboard is up)."""
        return next((e.value for e in self.screen().elements if e.editable and e.bounds == field.bounds), "")

    def _wait_for_keyboard(self) -> None:
        """`wait_until` the keyboard is up after tapping a field.

        On iOS the keyboard is the sign a field takes keys: XCUITest doesn't report which text field has focus
        (measured: every field reads unfocused while one is being typed into).
        """
        self.wait_until(lambda s: s.keyboard_visible, "The keyboard did not come up for the text field")

    def key(self, name: str) -> None:
        """Press a named key: iOS has only the keys every platform has, and takes them only into a field.

        XCUITest types keys into the field with keyboard focus; with none, it fails (measured). So the keyboard
        must be up.
        """
        if name not in KEYS:
            raise DeviceError(f"iOS has no key '{name}': it presses only {', '.join(KEYS)}")
        try:
            self.wait_until(lambda s: s.keyboard_visible, "No keyboard came up")
        except TimedOut as e:
            raise DeviceError(f"{e}: iOS presses keys only into a field, so tap one first") from None
        self._call("/key", key=name)

    def back(self) -> None:
        """The "Back" button, else the navigation bar's back button, else an edge swipe."""
        self._call("/back")

    @override
    def _press_home(self) -> None:
        self._call("/home")

    def looks(self, elements: Sequence[Element]) -> str:  # noqa: ARG002 - the Device port; iOS needs no pixels
        """Nothing: on iOS an element's frame moves with its animation (measured), so its bounds say it all."""
        return ""

    def hide_keyboard(self) -> None:
        """Close the keyboard, and wait until it's gone."""
        multiline = self._call("/hide_keyboard").get("multiline") is True
        self.wait_until(
            lambda s: not s.keyboard_visible,
            "The keyboard did not close (the field takes several lines, so Return adds one, which jevtest took "
            "back out, and there's no Done; on iOS only the app can close it then, e.g. on a tap outside the field)"
            if multiline
            else "The keyboard did not close (jevtest tapped Done, or pressed Return where there's no Done; "
            "on iOS only the app can close it then, e.g. on Return)",
        )

    # --- device -----------------------------------------------------------------------
    def rotate(self, orientation: Orientation) -> None:
        """Rotate the device; the orientation is put back on close."""
        self._undo.remember("rotation", lambda: {"call": "/rotate", "body": {"raw": self._orientation_now()}})
        self._call("/rotate", orientation=orientation)
        wide = orientation in (Orientation.LANDSCAPE, Orientation.LANDSCAPE_RIGHT)
        try:
            self.wait_until(lambda s: (s.width > s.height) == wide, f"The app did not turn to {orientation}")
        except TimedOut as e:
            raise DeviceError(
                f"{e}: does the app allow it? (UISupportedInterfaceOrientations in its Info.plist; iPhone apps "
                "usually leave out portrait_upside_down)"
            ) from None

    def _orientation_now(self) -> int:
        """The device's orientation, to put back: as XCUITest reports it, or as the screen shows it, if it says none.

        A simulator never turned reports `unknown` (measured: raw 0 on iOS 26.5), and putting back `unknown` left it
        turned (measured: in landscape; seen upside down). The screen then says which way it is, and a simulator starts
        upright: portrait when it's taller than wide, else landscape as `rotate: landscape` turns it.
        """
        raw = self._call("/rotate")["raw"]
        if isinstance(raw, int) and raw in SCREEN_ORIENTATIONS:
            return raw
        s = self.screen()
        return LANDSCAPE_LEFT if s.width > s.height else PORTRAIT

    def set_location(self, latitude: float, longitude: float) -> None:
        """Simulate a GPS location; it's cleared on close, so the device uses its actual location again.

        An iPhone's is set with devicectl, whose simulation lasts until it's cleared (its help says so), and
        `--latitude=` takes a negative number where `--latitude -33.8` is refused (measured).
        """
        if self.physical:
            self._undo.remember(
                "location", lambda: {"devicectl": ["device", "simulate", "location", "clear", "--device", self.udid]}
            )
            devicectl(
                "device",
                "simulate",
                "location",
                "coordinate",
                "--device",
                self.udid,
                f"--latitude={latitude}",
                f"--longitude={longitude}",
            )
            return
        self._undo.remember("location", lambda: {"simctl": ["location", self.udid, "clear"]})
        simctl("location", self.udid, "set", f"{latitude},{longitude}")

    def open_url(self, url: str) -> None:
        """Open a deep link or URL, through the agent on a simulator too.

        `simctl openurl` opens a link as if from outside the app, and iOS then asks "Open in “App”?" before a
        custom scheme opens (measured); the agent's open doesn't ask, on a simulator or an iPhone.
        """
        try:
            self._call("/open_url", url=url)
        except AgentRefused as e:
            if says.no_app_for_url(e.said):
                raise no_app_opens(url) from None
            raise

    def dark_mode(self, *, on: bool) -> None:
        """Switch the appearance; the previous one is put back on close."""
        self._remember_agent_setting("dark mode", "/appearance")
        self._call("/appearance", dark=on)

    def grant(self, permissions: Sequence[str]) -> None:
        """Grant simulator privacy services (photos, camera, ...). A real iPhone can't.

        The simulator ends a running app when most services are granted, even unchanged, and leaves it running for
        a few (`PRIVACY_KEEPS_APP_RUNNING`): an app that was ended is started again once it's gone, after every grant.
        """
        if self.physical:
            raise DeviceError("A real iPhone can't pre-grant permissions: let the test tap the permission prompt")
        ends = not set(permissions) <= says.PRIVACY_KEEPS_APP_RUNNING
        running = ends and self.app_state() is not AppState.NOT_RUNNING
        # simctl services: all, calendar, contacts, location, location-always, photos, microphone, ...
        for permission in permissions:
            try:
                simctl("privacy", self.udid, "grant", permission, self.app_id)
            except ToolFailed as e:
                if says.PRIVACY_REFUSED not in e.output:
                    raise
                raise DeviceError(
                    f"The simulator didn't grant '{permission}': use a service name such as camera, photos, "
                    "microphone, location or contacts (`xcrun simctl privacy` lists them)"
                ) from None
        if running:
            wait_until(
                lambda: self.app_state() is AppState.NOT_RUNNING,
                "The simulator did not end the app after the permission changed",
            )
            self.launch()

    def network(self, *, on: bool) -> None:
        """Not possible on iOS: always an error."""
        raise DeviceError(f"jevtest can't turn an iPhone's or simulator's network {'on' if on else 'off'}")

    def autofill_off(self) -> None:
        """Not possible on iOS: always an error."""
        raise DeviceError("jevtest can't turn an iPhone's or simulator's autofill off")
