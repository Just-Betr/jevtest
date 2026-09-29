"""iOS driver: a simulator (simctl) or a real iPhone (devicectl), with the XCUITest agent for screen and touch.

Finding devices, signing, and building the agent are in `ios_tools`; reading the screen is in `ios_screen`. This
module is the `Device` itself.

The agent is the same on both. What differs is how jevtest gets to it:
- simulator: an unsigned agent, reached at 127.0.0.1 (the simulator shares the Mac's network);
- iPhone: an agent signed with your Xcode team, reached through the USB tunnel Xcode keeps to the
  phone (its address changes when the phone relocks, so it is read fresh when needed).
"""

from __future__ import annotations

import contextlib
import os
import re
import secrets
import shutil
import subprocess
import tempfile
from base64 import b64decode
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

from jevtest.adapters.shapes import is_json_object
from jevtest.domain.failures import DeviceError
from jevtest.domain.kinds import AppState, Orientation
from jevtest.domain.screen import Element, Point, Screen
from jevtest.domain.steps import KEYS

from ._typing import override
from .common import (
    BaseDevice,
    Progress,
    Undo,
    cache_dir,
    digest,
    drop_older,
    no_app_opens,
    start_process,
    stop_process,
    wait_until,
)
from .ios_screen import AgentTree, parse_tree
from .ios_tools import (
    AGENT_LOCK,
    AGENT_SRC,
    app_bundle,
    app_team,
    build_agent_with_xcodebuild,
    build_info,
    check_build,
    devicectl,
    find_target,
    free_port,
    http_post,
    provisioned_devices,
    simctl,
    xcode_team,
)
from .tool_output import Object, as_text, text_at

AGENT_CALL_TIMEOUT = 150
"""Seconds one agent call may take. Before touching while a system alert is up, XCUITest waits up to 60 s for
SpringBoard to settle (normally well under a second; an iPhone that needs a restart can take the full 60 s)."""
APP_WAIT = 10.0
"""Seconds to wait for the app to come to the foreground after a launch or a resume."""
AGENT_START_TIMEOUT = 300  # includes xcodebuild installing the agent on a fresh simulator or phone
# XCUIApplication.State raw values.
# XCUIApplication.State: unknown, notRunning, runningBackgroundSuspended, runningBackground, runningForeground
SWIPE_SPEED = 1500
"""Points per second a swipe's finger moves: a flick, as swipe-to-dismiss and carousels expect."""
SWIPE_HOLD = 0.05
SCROLL_SPEED = 300
"""Points per second a scroll's finger moves. Faster, a web page flings on after the finger lifts, however long
it rests first (measured in a WKWebView: at 500 a 100 pt drag moved the page 320 pt, at 1500 470 pt; at 250-350
a 524 pt drag moved it 514 pt, the 10 pt being touch slop), and `scroll_to` could jump past its target."""
SCROLL_HOLD = 0.1

APP_STATES = {
    0: AppState.NOT_RUNNING,
    1: AppState.NOT_RUNNING,
    2: AppState.BACKGROUND,
    3: AppState.BACKGROUND,
    4: AppState.FOREGROUND,
}


class IOSDevice(BaseDevice):
    """A booted iOS simulator or a connected iPhone, driven through jevtest's XCUITest agent.

    Args:
        device: The device's exact name or UDID.
        app: The build under test. On an iPhone, jevtest signs its agent with the team that signed this build.
        progress: Told about slow one-time work (building and signing the agent).
    """

    def __init__(self, device: str, app: Path, progress: Progress) -> None:
        self._progress = progress
        if shutil.which("xcrun") is None:
            raise DeviceError("Xcode command line tools are required for iOS")
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
        # agent call -> body that puts back what a step changed; "location" for a simulator's
        self._restore: Undo[dict[str, object]] = Undo(target.udid)
        try:
            self.team = xcode_team(app_team(app_bundle(app, Path(self._tmp.name) / "team"))) if self.physical else ""
            self._start_agent()
            left = self._restore.left_by_a_stopped_run()
            if left:
                self._progress(f"putting back what a run that was stopped left changed: {Undo.described(left)}")
                self._put_back(left)
                self._restore.clear()
        except BaseException:
            self._tmp.cleanup()  # the caller never gets a device to close
            raise

    # --- agent ------------------------------------------------------------------
    def _build_agent(self) -> Path:
        """The agent's .xctestrun: built once per source version (and team, and phone, for iPhones)."""
        if not self.physical:
            out = cache_dir() / f"ios-agent-{digest(AGENT_SRC)}"
        else:
            out = cache_dir() / f"ios-agent-{digest(AGENT_SRC)}-{self.team}"
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
            xctestrun = self._build_agent()
            # older agent builds, of this kind, go (about 150 MB each); so do 0.9.1's per-run logs, named by port
            suffix = f"-{re.escape(self.team)}" if self.physical else ""
            drop_older(rf"ios-agent-([0-9a-f]+){suffix}", digest(AGENT_SRC))
            drop_older(r"ios-agent-(\d+)\.log", "")
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
                if "enabling automation mode" in self.agent_log.read_text(errors="replace"):
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
                    f"Lost the iOS agent during {path} ({e}). Agent log tail:\n{self._log_tail()}"
                ) from None
            try:  # the phone's tunnel address changes when it relocks: look it up again, once
                self.host = self._tunnel_host()
                data = http_post(self._url(path), body, timeout=AGENT_CALL_TIMEOUT, token=self.token)
            except (OSError, DeviceError) as again:
                raise DeviceError(
                    f"Lost the agent on {self.name} during {path} ({again}). "
                    f"Is it unlocked and plugged in? Agent log tail:\n{self._log_tail()}"
                ) from None
        if "error" in data:
            raise DeviceError(f"iOS agent {path}: {data['error']}")
        return data

    @override
    def close(self) -> None:
        """Put back anything a step changed, then stop the agent."""
        self.restore()
        stop_process(self.agent)
        self._tmp.cleanup()

    @override
    def restore(self) -> None:
        """Put back what steps changed (appearance, orientation, a simulator's location)."""
        self._put_back(self._restore)
        self._restore.clear()

    def _put_back(self, changed: Mapping[str, object]) -> None:
        for path, body in changed.items():
            with contextlib.suppress(DeviceError):
                if path == "location":
                    simctl("location", self.udid, "clear")
                elif is_json_object(body):
                    self._call(path, **body)

    def _remember(self, path: str) -> None:
        """Before the first change through `path`, note the current value so close() can put it back."""
        if path not in self._restore:
            self._restore[path] = {"raw": self._call(path)["raw"]}

    @override
    def check_ready(self) -> None:
        """An iPhone that is locked can't be tested: say so; never unlock it."""
        if self.physical and devicectl("device", "info", "lockState", "--device", self.udid).get("passcodeRequired"):
            raise DeviceError(f"{self.name} is locked: unlock it and keep it unlocked during the run")

    # --- lifecycle ----------------------------------------------------------------
    def install(self, app: Path) -> str:
        """Install a simulator or device build (an .app, or a .zip/.ipa containing one); return its bundle id."""
        bundle, info = build_info(app, Path(self._tmp.name) / "app")
        check_build(app, info, physical=self.physical, device=self.name)
        if "CFBundleIdentifier" not in info:
            raise DeviceError(f"{app.name} Info.plist has no CFBundleIdentifier")
        self.app_path = bundle
        self.app_id = as_text(info["CFBundleIdentifier"], f"{app.name}'s bundle id")
        self._install_bundle()
        return self.app_id

    def _install_bundle(self) -> None:
        """Install the unpacked build."""
        if self.physical:
            devicectl("device", "install", "app", "--device", self.udid, str(self.app_path))
        else:
            simctl("install", self.udid, str(self.app_path), timeout=300)

    def launch(self) -> None:
        """Launch the app and wait until it's in the foreground."""
        if self.physical:
            devicectl("device", "process", "launch", "--device", self.udid, "--terminate-existing", self.app_id)
        else:
            simctl("launch", self.udid, self.app_id)
        self._call("/wait_foreground", timeout=APP_WAIT)

    def resume(self) -> None:
        """Bring the app back to the foreground without restarting it."""
        self._call("/activate", timeout=APP_WAIT)

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
        """Uninstall and install the build again."""
        self.stop()
        if self.physical:
            devicectl("device", "uninstall", "app", "--device", self.udid, self.app_id)
        else:
            simctl("uninstall", self.udid, self.app_id, check=False)
        self._install_bundle()

    # --- observe --------------------------------------------------------------------
    @override
    def screen(self) -> Screen:
        """What's on the screen now."""
        return parse_tree(cast("AgentTree", self._call("/tree")))  # the agent's own JSON

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
    def drag(self, x1: int, y1: int, x2: int, y2: int, *, scroll: bool = False) -> None:
        """Press, move, lift: a swipe at a flick's speed, a scroll slowly enough that nothing flings on."""
        speed, hold = (SCROLL_SPEED, SCROLL_HOLD) if scroll else (SWIPE_SPEED, SWIPE_HOLD)
        self._call("/drag", x1=x1, y1=y1, x2=x2, y2=y2, velocity=speed, hold=hold)

    def type_text(self, text: str, at: Point | None = None) -> None:
        """Type into the focused field, or first focus the field at `at`."""
        if at:  # focus the field; keys sent before the keyboard is up are lost
            self.tap(*at)
            self._wait_for_keyboard()
        self._call("/type", text=text)

    def clear_text(self, element: Element) -> None:
        """Erase a text field: put the cursor after its text, then delete exactly what is there."""
        self.tap(*element.end)
        self._wait_for_keyboard()
        if element.value:
            self._call("/key", key="delete", count=len(element.value))

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
        except DeviceError as e:
            raise DeviceError(f"{e}: iOS presses keys only into a field, so tap one first") from None
        self._call("/key", key=name)

    def back(self) -> None:
        """The "Back" button, else the navigation bar's back button, else an edge swipe."""
        self._call("/back")

    def home(self) -> None:
        """Press Home, and wait until the app has left the foreground (the press returns before it has)."""
        self._call("/home")
        wait_until(
            lambda: self.app_state() is not AppState.FOREGROUND, "The app was still in the foreground after Home"
        )

    def looks(self, elements: Sequence[Element]) -> str:  # noqa: ARG002 - the Device port; iOS needs no pixels
        """Nothing: on iOS an element's frame moves with its animation (measured), so its bounds say it all."""
        return ""

    def hide_keyboard(self) -> None:
        """Close the keyboard, and wait until it's gone."""
        self._call("/hide_keyboard")
        self.wait_until(
            lambda s: not s.keyboard_visible,
            "The keyboard did not close (jevtest tapped Done, or pressed Return where there's no Done; "
            "on iOS only the app can close it then, e.g. on Return)",
        )

    # --- device -----------------------------------------------------------------------
    def rotate(self, orientation: Orientation) -> None:
        """Rotate the device; the orientation is put back on close."""
        self._remember("/rotate")
        self._call("/rotate", orientation=orientation)
        wide = orientation in (Orientation.LANDSCAPE, Orientation.LANDSCAPE_RIGHT)
        try:
            self.wait_until(lambda s: (s.width > s.height) == wide, f"The app did not turn to {orientation}")
        except DeviceError as e:
            raise DeviceError(
                f"{e}: does the app allow it? (UISupportedInterfaceOrientations in its Info.plist; iPhone apps "
                "usually leave out portrait_upside_down)"
            ) from None

    def set_location(self, latitude: float, longitude: float) -> None:
        """Simulate a GPS location; on a simulator it's cleared on close."""
        if not self.physical:
            self._restore["location"] = {}
        if self.physical:
            self._call("/location", lat=latitude, lon=longitude)
        else:
            simctl("location", self.udid, "set", f"{latitude},{longitude}")

    def open_url(self, url: str) -> None:
        """Open a deep link or URL."""
        try:
            if self.physical:
                self._call("/open_url", url=url)
            else:
                simctl("openurl", self.udid, url)
        except DeviceError as e:
            # measured: simctl says "LSApplicationWorkspaceErrorDomain, code=115", an iPhone "...error 115."
            if re.search(r"LSApplicationWorkspaceErrorDomain(, code=| error )115", str(e)):
                raise no_app_opens(url) from None
            raise

    def dark_mode(self, *, on: bool) -> None:
        """Switch the appearance; the previous one is put back on close."""
        self._remember("/appearance")
        self._call("/appearance", dark=on)

    def grant(self, permissions: Sequence[str]) -> None:
        """Grant simulator privacy services (photos, camera, ...). A real iPhone can't.

        The simulator ends an app whose permissions change, even to what they were (measured: gone within
        0.25 s), so a running app is started again once it's gone: once, after every grant.
        """
        if self.physical:
            raise DeviceError("A real iPhone can't pre-grant permissions: let the test tap the permission prompt")
        running = self.app_state() is not AppState.NOT_RUNNING
        # simctl services: all, calendar, contacts, location, location-always, photos, microphone, ...
        for permission in permissions:
            try:
                simctl("privacy", self.udid, "grant", permission, self.app_id)
            except DeviceError:  # measured: an unknown service fails with "Operation not permitted"
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
