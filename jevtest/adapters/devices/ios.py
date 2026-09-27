"""iOS driver: a simulator (simctl) or a real iPhone (devicectl), with the XCUITest agent for screen and touch.

The agent is the same on both. What differs is how jevtest gets to it:
- simulator: an unsigned agent, reached at 127.0.0.1 (the simulator shares the Mac's network);
- iPhone: an agent signed with your Xcode team, reached through the USB tunnel Xcode keeps to the
  phone (its address changes when the phone relocks, so it is read fresh when needed).
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import urllib.request
import zipfile
from base64 import b64decode
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict, cast

from jevtest.domain.failures import DeviceError
from jevtest.domain.kinds import AppState, Orientation
from jevtest.domain.screen import Element, Point, Screen

from .common import FOLLOW_UP, BaseDevice, Progress, cache_dir, digest, run, run_bytes, start_process, stop_process
from .ios_screen import AgentTree, parse_tree
from .tool_output import Object, as_list, as_object, as_text, dig, parse_json, parse_plist, text_at, texts

AGENT_SRC = Path(__file__).resolve().parent / "ios_agent"
AGENT_CALL_TIMEOUT = 150
"""Seconds one agent call may take. Before touching while a system alert is up, XCUITest waits up to 60 s for
SpringBoard to settle (normally well under a second; an iPhone that needs a restart can take the full 60 s)."""
APP_WAIT = 10.0
"""Seconds to wait for the app to come to the foreground after a launch or a resume."""
AGENT_START_TIMEOUT = 300  # includes xcodebuild installing the agent on a fresh simulator or phone
# XCUIApplication.State raw values.
# XCUIApplication.State: unknown, notRunning, runningBackgroundSuspended, runningBackground, runningForeground
APP_STATES = {0: AppState.NOT_RUNNING, 1: AppState.NOT_RUNNING, 2: AppState.BACKGROUND, 3: AppState.BACKGROUND,
              4: AppState.FOREGROUND}


def simctl(*args: str, timeout: float = 120, check: bool = True) -> str:
    """Run ``xcrun simctl ...``."""
    return run(["xcrun", "simctl", *args], timeout=timeout, check=check)


def devicectl(*args: str, timeout: float = 300) -> Object:
    """Run ``xcrun devicectl ...`` and return its JSON result."""
    what = f"devicectl {' '.join(args[:3])}"
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "out.json"
        run(["xcrun", "devicectl", *args, "--json-output", str(out)], timeout=timeout)
        return as_object(dig(parse_json(out.read_text(), what), "result") or {}, what)


def _runtime_version(runtime: str) -> tuple[int, ...]:
    """``...SimRuntime.iOS-26-5`` as (26, 5), for sorting."""
    return tuple(int(n) for n in runtime.rsplit("iOS-", 1)[-1].split("-") if n.isdigit())


class Simulator(TypedDict):
    """An available simulator, as simctl lists it."""

    udid: str
    name: str
    state: str
    runtime: str


class Phone(TypedDict):
    """A connected iPhone, as devicectl lists it."""

    udid: str
    name: str


def simulators() -> list[Simulator]:
    """Available iOS simulators, newest runtime first, then by name."""
    what = "simctl list devices"
    by_runtime = as_object(dig(parse_json(simctl("list", "devices", "available", "-j"), what), "devices"), what)
    runtimes = sorted((r for r in by_runtime if ".iOS-" in r), key=_runtime_version, reverse=True)
    return [sim for r in runtimes
            for sim in sorted((_simulator(d, r) for d in as_list(by_runtime[r], what)),
                              key=lambda d: (d["name"], d["udid"]))]


def _simulator(raw: object, runtime: str) -> Simulator:
    d = as_object(raw, "a simctl device")
    return {"udid": as_text(d.get("udid"), "a simulator's udid"), "name": as_text(d.get("name"), "a simulator's name"),
            "state": as_text(d.get("state"), "a simulator's state"), "runtime": runtime.rsplit(".", 1)[-1]}


def phones() -> list[Phone]:
    """Real iPhones connected to this Mac (paired, with a live connection)."""
    listed = as_list(devicectl("list", "devices").get("devices", []), "devicectl's device list")
    return [{"udid": text_at(d, "hardwareProperties", "udid"), "name": text_at(d, "deviceProperties", "name")}
            for d in listed if _connected_iphone(d)]


def _connected_iphone(d: object) -> bool:
    return (text_at(d, "hardwareProperties", "reality"), text_at(d, "hardwareProperties", "platform"),
            text_at(d, "connectionProperties", "tunnelState")) == ("physical", "iOS", "connected")


@dataclass(frozen=True)
class Target:
    """A booted simulator or a connected iPhone."""

    udid: str
    name: str
    physical: bool


def find_target(wanted: str) -> Target:
    """The one booted simulator or connected iPhone with exactly this name or UDID.

    jevtest never boots, opens or unlocks a device.
    """
    running = _running_targets()
    matches = [t for t in running if wanted in (t.udid, t.name)]
    if not matches:
        listed = ", ".join(f"{t.name} ({t.udid})" for t in running) or "none"
        raise DeviceError(f"No booted simulator or connected iPhone called '{wanted}' (names are exact). "
                          f"Running: {listed}")
    if len(matches) > 1:
        raise DeviceError(f"Several devices are called '{wanted}' ({', '.join(t.udid for t in matches)}): "
                          "name one by its UDID")
    return matches[0]


def _running_targets() -> list[Target]:
    """The booted simulators, then the connected iPhones."""
    sims = [Target(d["udid"], d["name"], physical=False) for d in simulators() if d["state"] == "Booted"]
    return sims + [Target(d["udid"], d["name"], physical=True) for d in phones()]


def xcode_team(team: str) -> str:
    """Check the app's team is signed into Xcode (Settings > Accounts), so jevtest can sign its agent."""
    raw = run(["defaults", "export", "com.apple.dt.Xcode", "-"], check=False)
    what = "Xcode's signed-in teams"
    prefs = parse_plist(raw.encode(), what) if raw.strip() else {}
    accounts = as_object(prefs.get("IDEProvisioningTeamByIdentifier", {}), what)
    ids = sorted({text_at(t, "teamID") for account in accounts.values() for t in as_list(account, what)} - {""})
    if team not in ids:
        signed_in = ", ".join(ids) or "none"
        raise DeviceError(f"The app is signed by team {team}, which is not signed into Xcode (signed in: "
                          f"{signed_in}). jevtest signs its agent with the app's team: in Xcode, Settings > "
                          "Accounts, add the Apple Account for that team.")
    return team


def profile(app: Path) -> Object | None:
    """An app's embedded provisioning profile, decoded; None for a simulator build (it has none)."""
    path = app / "embedded.mobileprovision"
    if not path.exists():
        return None
    raw = run_bytes(["security", "cms", "-D", "-i", str(path)], check=False)
    return parse_plist(raw, f"{app.name}'s embedded.mobileprovision")


def provisioned_devices(app: Path) -> set[str]:
    """The device UDIDs an app's embedded provisioning profile allows."""
    prof = profile(app) or {}
    return set(texts(prof.get("ProvisionedDevices", []), f"{app.name}'s provisioned devices"))


def app_team(app: Path) -> str:
    """The Apple team that signed a device build: jevtest signs its agent with the same team."""
    prof = profile(app)
    if prof is None:
        raise DeviceError(f"{app.name} is not signed for a real iPhone (it has no provisioning profile). "
                          "Build it for the device, signed with your team.")
    teams = texts(prof.get("TeamIdentifier", []), f"{app.name}'s team")
    if len(teams) != 1:
        raise DeviceError(f"{app.name}'s provisioning profile names {len(teams)} teams ({', '.join(teams)}); "
                          "expected one")
    return teams[0]


# Devices tested at the same time share the agent build: one builds and starts it at a time.
AGENT_LOCK = threading.Lock()


def free_port() -> int:
    """A free local port for the agent to listen on."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
    return port


def app_bundle(app_path: Path, workdir: Path) -> Path:
    """An .app directory from a .app, or a .zip / .ipa containing one."""
    if app_path.suffix.lower() == ".app" and app_path.is_dir():
        return app_path
    if app_path.suffix.lower() in (".zip", ".ipa") and zipfile.is_zipfile(app_path):
        with zipfile.ZipFile(app_path) as z:
            z.extractall(workdir)
        found = sorted(workdir.rglob("*.app"), key=lambda p: (len(p.parts), str(p)))
        if found:
            return found[0]
    raise DeviceError(f"iOS needs an .app (or a .zip/.ipa containing one), got {app_path.name}")


def http_post(url: str, body: Mapping[str, object], timeout: float) -> Object:
    """A POST to the agent; its JSON reply."""
    req = urllib.request.Request(url, method="POST", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw: bytes = resp.read()
    return as_object(parse_json(raw, "the iOS agent's reply"), "the iOS agent's reply")


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
        self.team = xcode_team(app_team(app_bundle(app, Path(self._tmp.name) / "team"))) if self.physical else ""
        self.port = free_port()
        self.host = "127.0.0.1"
        self.agent: subprocess.Popen[str] | None = None
        self.app_id = ""
        self.agent_log = cache_dir() / f"ios-agent-{self.port}.log"
        self.app_path: Path | None = None
        self._restore: dict[str, dict[str, object]] = {}  # agent call -> body that puts back what a step changed
        self._location_set = False
        self._start_agent()

    # --- agent ------------------------------------------------------------------
    def _xcodebuild(self, out: Path, destination: str, signing: list[str]) -> None:
        """Build the agent for `destination` into `out`."""
        run(["xcodebuild", "build-for-testing", "-project", str(AGENT_SRC / "JevAgent.xcodeproj"),
             "-scheme", "JevAgent", "-destination", destination, "-derivedDataPath", str(out), "-quiet", *signing],
            timeout=900)

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
            self._xcodebuild(out, "generic/platform=iOS Simulator", [])
        else:
            # Built for this phone, so Xcode registers it with the team and puts it in the profile.
            self._progress(f"building and signing the iOS agent for {self.name} (team {self.team})")
            signing = ["-allowProvisioningUpdates", "-allowProvisioningDeviceRegistration",
                       f"DEVELOPMENT_TEAM={self.team}", "CODE_SIGN_STYLE=Automatic", "CODE_SIGNING_ALLOWED=YES",
                       "CODE_SIGNING_REQUIRED=YES", "CODE_SIGN_IDENTITY=Apple Development",
                       f"JEVTEST_TEAM_SUFFIX=.{self.team}"]
            try:
                self._xcodebuild(out, f"id={self.udid}", signing)
            except DeviceError:  # a first build can race Xcode replacing the provisioning profile
                self._xcodebuild(out, f"id={self.udid}", signing)
        runs = sorted((out / "Build/Products").glob("*.xctestrun"))
        if not runs:
            raise DeviceError("iOS agent build produced no .xctestrun")
        return runs[0]

    def _start_agent(self) -> None:
        """Build the agent if needed and start it on the device; on an iPhone, find its tunnel address."""
        env = dict(os.environ, TEST_RUNNER_JEVTEST_PORT=str(self.port))
        with AGENT_LOCK:
            xctestrun = self._build_agent()
            try:
                self.agent = start_process(
                    ["xcodebuild", "test-without-building", "-xctestrun", str(xctestrun),
                     "-destination", f"id={self.udid}"],
                    ready="JEVTEST_AGENT_READY", log=self.agent_log, timeout=AGENT_START_TIMEOUT, env=env)
            except DeviceError as e:
                if "enabling automation mode" in self.agent_log.read_text(errors="replace"):
                    raise DeviceError(
                        f"{self.name} did not allow UI automation (\"Timed out while enabling automation mode\"). "
                        "Unlock it and keep the screen on, check Settings > Developer > Enable UI Automation is on, "
                        "and answer any prompt on its screen; then run again.") from e
                raise
        if self.physical:
            self.host = self._tunnel_host()

    def _tunnel_host(self) -> str:
        """The phone's address on the USB tunnel Xcode keeps to it. Changes when the phone relocks."""
        address = text_at(devicectl("device", "info", "details", "--device", self.udid),
                          "connectionProperties", "tunnelIPAddress")
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
            data = http_post(self._url(path), body, timeout=AGENT_CALL_TIMEOUT)
        except OSError as e:
            if not self.physical:
                raise DeviceError(f"Lost the iOS agent during {path} ({e}). Agent log tail:\n{self._log_tail()}") \
                    from None
            try:  # the phone's tunnel address changes when it relocks: look it up again, once
                self.host = self._tunnel_host()
                data = http_post(self._url(path), body, timeout=AGENT_CALL_TIMEOUT)
            except (OSError, DeviceError) as again:
                raise DeviceError(f"Lost the agent on {self.name} during {path} ({again}). "
                                  f"Is it unlocked and plugged in? Agent log tail:\n{self._log_tail()}") from None
        if "error" in data:
            raise DeviceError(f"iOS agent {path}: {data['error']}")
        return data

    def close(self) -> None:
        """Put back anything a step changed, then stop the agent."""
        self.restore()
        stop_process(self.agent)
        self._tmp.cleanup()

    def restore(self) -> None:
        """Put back what steps changed (appearance, orientation, a simulator's location)."""
        for path, body in self._restore.items():
            with contextlib.suppress(DeviceError):
                self._call(path, **body)
        self._restore.clear()
        if self._location_set and not self.physical:
            simctl("location", self.udid, "clear")
        self._location_set = False

    def _remember(self, path: str) -> None:
        """Before the first change through `path`, note the current value so close() can put it back."""
        if path not in self._restore:
            self._restore[path] = {"raw": self._call(path)["raw"]}

    def check_ready(self) -> None:
        """An iPhone that is locked can't be tested: say so; never unlock it."""
        if self.physical and devicectl("device", "info", "lockState", "--device", self.udid).get("passcodeRequired"):
            raise DeviceError(f"{self.name} is locked: unlock it and keep it unlocked during the run")

    # --- lifecycle ----------------------------------------------------------------
    def install(self, app_path: Path) -> str:
        """Install a simulator or device build (an .app, or a .zip/.ipa containing one); return its bundle id."""
        bundle = app_bundle(app_path, Path(self._tmp.name) / "app")
        try:
            raw = (bundle / "Info.plist").read_bytes()
        except OSError as e:
            raise DeviceError(f"{app_path.name} has no readable Info.plist ({e})") from None
        info = parse_plist(raw, f"{app_path.name}'s Info.plist")
        platforms = texts(info.get("CFBundleSupportedPlatforms", []), f"{app_path.name}'s supported platforms")
        needed = "iPhoneOS" if self.physical else "iPhoneSimulator"
        if platforms and needed not in platforms:
            where = f"a real iPhone ({self.name})" if self.physical else "the iOS Simulator"
            how = "a device build signed with your team" if self.physical else "a build with `-sdk iphonesimulator`"
            raise DeviceError(f"{app_path.name} is built for {', '.join(platforms)}, not {where}. Use {how}.")
        if "CFBundleIdentifier" not in info:
            raise DeviceError(f"{app_path.name} Info.plist has no CFBundleIdentifier")
        self.app_path = bundle
        self.app_id = as_text(info["CFBundleIdentifier"], f"{app_path.name}'s bundle id")
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
    def screen(self) -> Screen:
        """What's on the screen now."""
        return parse_tree(cast(AgentTree, self._call("/tree")))  # the agent's own JSON

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

    def drag(self, x1: int, y1: int, x2: int, y2: int) -> None:
        """Press, move, lift."""
        self._call("/drag", x1=x1, y1=y1, x2=x2, y2=y2)

    def wait_idle(self, timeout: float, quiet: float | None = None) -> None:
        """Return once the screen has stopped changing (for `quiet` seconds), or after `timeout` seconds."""
        if quiet is None:
            self._call("/idle", timeout=timeout)
        else:
            self._call("/idle", timeout=timeout, quiet=quiet)

    def wait_change(self, timeout: float) -> None:
        """Return as soon as the screen changes, or after `timeout` seconds."""
        self._call("/change", timeout=timeout)

    def type_text(self, text: str, at: Point | None = None) -> None:
        """Type into the focused field, or first focus the field at `at`."""
        if at:  # focus the field and let the focus change finish
            self.tap(*at)
            self.wait_idle(FOLLOW_UP)
        self._call("/type", text=text)

    def clear_text(self, element: Element) -> None:
        """Erase a text field: put the cursor after its text, then delete exactly what is there."""
        self.tap(*element.end)
        self.wait_idle(FOLLOW_UP)
        if element.value:
            self._call("/key", key="delete", count=len(element.value))

    def key(self, name: str) -> None:
        """Press a named key."""
        self._call("/key", key=name)

    def back(self) -> None:
        """The "Back" button, else the navigation bar's back button, else an edge swipe."""
        self._call("/back")

    def home(self) -> None:
        """Press Home."""
        self._call("/home")

    def hide_keyboard(self) -> None:
        """Close the keyboard."""
        self._call("/hide_keyboard")

    # --- device -----------------------------------------------------------------------
    def rotate(self, orientation: Orientation) -> None:
        """Rotate the device; the orientation is put back on close."""
        self._remember("/rotate")
        self._call("/rotate", orientation=orientation)

    def set_location(self, latitude: float, longitude: float) -> None:
        """Simulate a GPS location; on a simulator it's cleared on close."""
        self._location_set = True
        if self.physical:
            self._call("/location", lat=latitude, lon=longitude)
        else:
            simctl("location", self.udid, "set", f"{latitude},{longitude}")

    def open_url(self, url: str) -> None:
        """Open a deep link or URL."""
        if self.physical:
            self._call("/open_url", url=url)
        else:
            simctl("openurl", self.udid, url)

    def dark_mode(self, on: bool) -> None:
        """Switch the appearance; the previous one is put back on close."""
        self._remember("/appearance")
        self._call("/appearance", dark=on)

    def grant(self, permission: str) -> None:
        """Grant a simulator privacy service (photos, camera, ...). A real iPhone can't."""
        if self.physical:
            raise DeviceError("A real iPhone can't pre-grant permissions: let the test tap the permission prompt")
        # simctl services: all, calendar, contacts, location, location-always, photos, microphone, ...
        simctl("privacy", self.udid, "grant", permission, self.app_id)

    def network(self, on: bool) -> None:
        """Not possible on iOS: always an error."""
        raise DeviceError("jevtest can't turn an iPhone's or simulator's network off")
