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
import plistlib
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import urllib.request
import zipfile
from base64 import b64decode
from dataclasses import dataclass
from pathlib import Path

from ..screen import Element, Screen
from .base import Driver, DriverError, cache_dir, digest, run, start_process, stop_process

AGENT_SRC = Path(__file__).resolve().parent.parent / "ios_agent"
AGENT_START_TIMEOUT = 300  # includes xcodebuild installing the agent on a fresh simulator or phone
# Container types that only matter when they carry a label or identifier.
CONTAINERS = {"other", "navigation_bar", "tab_bar", "list", "scroll_view", "webview"}
# Kinds whose value is shown some other way: a switch's as on/off, a secure field's is bullets.
HIDDEN_VALUE = {"switch", "password_field"}
SCROLL_INDICATOR = re.compile(r"^(Vertical|Horizontal) scroll bar\b")
TOUCHABLE = {"button", "cell", "link", "switch", "tab", "menu_item", "segmented_control", "dropdown"}
EDITABLE = {"text_field", "password_field", "text_area"}
# XCUIApplication.State raw values.
APP_STATES = {0: "not_running", 1: "not_running", 2: "background", 3: "background", 4: "foreground"}


def simctl(*args, timeout=120, check=True) -> str:
    return run(["xcrun", "simctl", *args], timeout=timeout, check=check)


def devicectl(*args, timeout=300) -> dict:
    """Run `xcrun devicectl ...` and return its JSON result."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "out.json"
        run(["xcrun", "devicectl", *args, "--json-output", str(out)], timeout=timeout)
        return json.loads(out.read_text()).get("result", {})


def _runtime_version(runtime: str) -> tuple[int, ...]:
    return tuple(int(n) for n in runtime.rsplit("iOS-", 1)[-1].split("-") if n.isdigit())


def simulators() -> list[dict]:
    """Available iOS simulators, newest runtime first, then by name."""
    data = json.loads(simctl("list", "devices", "available", "-j"))
    runtimes = sorted((r for r in data["devices"] if ".iOS-" in r), key=_runtime_version, reverse=True)
    return [dict(d, runtime=r.rsplit(".", 1)[-1])
            for r in runtimes for d in sorted(data["devices"][r], key=lambda d: (d["name"], d["udid"]))]


def phones() -> list[dict]:
    """Real iPhones connected to this Mac (paired, with a live connection)."""
    found = []
    for d in devicectl("list", "devices").get("devices", []):
        hw, props = d.get("hardwareProperties", {}), d.get("deviceProperties", {})
        conn = d.get("connectionProperties", {})
        if hw.get("reality") == "physical" and hw.get("platform") == "iOS" and conn.get("tunnelState") == "connected":
            found.append({"udid": hw.get("udid", ""), "name": props.get("name", "")})
    return found


@dataclass
class Target:
    udid: str
    name: str
    physical: bool


def find_target(wanted: str) -> Target:
    """The one booted simulator or connected iPhone with exactly this name or UDID.
    jevtest never boots, opens or unlocks a device."""
    sims = [Target(d["udid"], d["name"], False) for d in simulators() if d["state"] == "Booted"]
    real = [Target(d["udid"], d["name"], True) for d in phones()]
    matches = [t for t in sims + real if wanted in (t.udid, t.name)]
    if not matches:
        running = ", ".join(f"{t.name} ({t.udid})" for t in sims + real) or "none"
        raise DriverError(f"No booted simulator or connected iPhone called '{wanted}' (names are exact). "
                          f"Running: {running}")
    if len(matches) > 1:
        raise DriverError(f"Several devices are called '{wanted}' ({', '.join(t.udid for t in matches)}): "
                          "name one by its UDID")
    return matches[0]


def xcode_team(team: str) -> str:
    """Check the app's team is signed into Xcode (Settings > Accounts), so jevtest can sign its agent."""
    raw = run(["defaults", "export", "com.apple.dt.Xcode", "-"], check=False)
    prefs = plistlib.loads(raw.encode()) if raw.strip() else {}
    teams = [t for account in prefs.get("IDEProvisioningTeamByIdentifier", {}).values() for t in account]
    ids = sorted({t["teamID"] for t in teams if "teamID" in t})
    if team not in ids:
        signed_in = ", ".join(ids) or "none"
        raise DriverError(f"The app is signed by team {team}, which is not signed into Xcode (signed in: "
                          f"{signed_in}). jevtest signs its agent with the app's team: in Xcode, Settings > "
                          "Accounts, add the Apple Account for that team.")
    return team


def profile(app: Path) -> dict | None:
    """An app's embedded provisioning profile, decoded; None for a simulator build (it has none)."""
    path = app / "embedded.mobileprovision"
    if not path.exists():
        return None
    raw = run(["security", "cms", "-D", "-i", str(path)], binary=True, check=False)
    try:
        return plistlib.loads(raw)
    except plistlib.InvalidFileException:
        raise DriverError(f"{app.name}'s embedded.mobileprovision can't be read") from None


def provisioned_devices(app: Path) -> set[str]:
    """The device UDIDs an app's embedded provisioning profile allows."""
    return set((profile(app) or {}).get("ProvisionedDevices", []))


def app_team(app: Path) -> str:
    """The Apple team that signed a device build: jevtest signs its agent with the same team."""
    prof = profile(app)
    if prof is None:
        raise DriverError(f"{app.name} is not signed for a real iPhone (it has no provisioning profile). "
                          "Build it for the device, signed with your team.")
    teams = prof.get("TeamIdentifier", [])
    if len(teams) != 1:
        raise DriverError(f"{app.name}'s provisioning profile names {len(teams)} teams ({', '.join(teams)}); "
                          "expected one")
    return teams[0]


# Devices tested at the same time share the agent build: one builds and starts it at a time.
AGENT_LOCK = threading.Lock()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


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
    raise DriverError(f"iOS needs an .app (or a .zip/.ipa containing one), got {app_path.name}")


def parse_tree(data: dict) -> Screen:
    """The agent's /tree reply -> the elements a tester cares about, duplicates removed."""
    w, h = int(data["width"]), int(data["height"])
    elements, seen = [], set()
    for d in data["elements"]:
        kind = d["type"]
        label, value, placeholder = d.get("label", ""), d.get("value") or "", d.get("placeholder", "")
        if kind == "application" or SCROLL_INDICATOR.match(label):
            continue
        x1, y1 = max(int(d["x"]), 0), max(int(d["y"]), 0)
        x2, y2 = min(int(d["x"] + d["w"]), w), min(int(d["y"] + d["h"]), h)
        if x2 - x1 < 2 or y2 - y1 < 2:
            continue
        if kind in EDITABLE and value == placeholder:
            value = ""  # an empty field reports its placeholder as its value
        text = label
        # Any other value that says something the label doesn't (a web <select>'s choice, a field's text).
        if kind not in HIDDEN_VALUE and value.strip() and value.strip() != label.strip():
            text = f"{label}: {value}" if label else value
        text = " ".join(text.split())
        if kind in CONTAINERS and not (text or d.get("identifier")):
            continue
        el = Element(
            kind="text" if kind == "other" else kind, text=text, hint=placeholder,
            value=value if kind in EDITABLE else "", resource_id=d.get("identifier", ""), bounds=(x1, y1, x2, y2),
            enabled=d.get("enabled", True), editable=kind in EDITABLE, clickable=kind in TOUCHABLE,
            focused=kind in EDITABLE and d.get("focused", False),  # web views mark everything focused
            selected=d.get("selected", False),
            checked=value in ("1", "true") if kind == "switch" else None,
        )
        key = (el.kind, el.text, el.bounds)  # XCUITest often reports a wrapper and its child
        if key not in seen:
            seen.add(key)
            elements.append(el)
    return Screen(width=w, height=h, elements=elements, keyboard_visible=data.get("keyboard", False),
                  keyboard_top=int(data.get("keyboard_top", 0)))


def http_post(url: str, body: dict, timeout: float) -> dict:
    req = urllib.request.Request(url, method="POST", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


class IOSDriver(Driver):
    platform = "ios"

    def __init__(self, device: str, app: Path):
        if shutil.which("xcrun") is None:
            raise DriverError("Xcode command line tools are required for iOS")
        target = find_target(device)
        self.udid, self.name, self.physical = target.udid, target.name, target.physical
        self._tmp = tempfile.TemporaryDirectory()
        self.team = xcode_team(app_team(app_bundle(app, Path(self._tmp.name) / "team"))) if self.physical else ""
        self.port = free_port()
        self.host = "127.0.0.1"
        self.agent: subprocess.Popen | None = None
        self.agent_log = cache_dir() / f"ios-agent-{self.port}.log"
        self.app_path: Path | None = None
        self._restore: dict[str, dict] = {}  # agent call -> body that puts back what a step changed
        self._location_set = False
        self._start_agent()

    # --- agent ------------------------------------------------------------------
    def _xcodebuild(self, out: Path, destination: str, signing: list[str]):
        run(["xcodebuild", "build-for-testing", "-project", str(AGENT_SRC / "JevAgent.xcodeproj"),
             "-scheme", "JevAgent", "-destination", destination, "-derivedDataPath", str(out), "-quiet", *signing],
            timeout=900)

    def _build_agent(self) -> Path:
        if not self.physical:
            out = cache_dir() / f"ios-agent-{digest(AGENT_SRC)}"
        else:
            out = cache_dir() / f"ios-agent-{digest(AGENT_SRC)}-{self.team}"
        runner = out / "Build/Products/Debug-iphoneos/JevAgentUITests-Runner.app"
        runs = sorted((out / "Build/Products").glob("*.xctestrun"))
        if runs and (not self.physical or self.udid in provisioned_devices(runner)):
            return runs[0]
        if not self.physical:
            print("  building iOS agent (one time, ~1 min)...", flush=True)
            self._xcodebuild(out, "generic/platform=iOS Simulator", [])
        else:
            # Built for this phone, so Xcode registers it with the team and puts it in the profile.
            print(f"  building and signing the iOS agent for {self.name} (team {self.team})...", flush=True)
            signing = ["-allowProvisioningUpdates", "-allowProvisioningDeviceRegistration",
                       f"DEVELOPMENT_TEAM={self.team}", "CODE_SIGN_STYLE=Automatic", "CODE_SIGNING_ALLOWED=YES",
                       "CODE_SIGNING_REQUIRED=YES", "CODE_SIGN_IDENTITY=Apple Development",
                       f"JEVTEST_TEAM_SUFFIX=.{self.team}"]
            try:
                self._xcodebuild(out, f"id={self.udid}", signing)
            except DriverError:  # a first build can race Xcode replacing the provisioning profile
                self._xcodebuild(out, f"id={self.udid}", signing)
        runs = sorted((out / "Build/Products").glob("*.xctestrun"))
        if not runs:
            raise DriverError("iOS agent build produced no .xctestrun")
        return runs[0]

    def _start_agent(self):
        env = dict(os.environ, TEST_RUNNER_JEVTEST_PORT=str(self.port))
        with AGENT_LOCK:
            xctestrun = self._build_agent()
            try:
                self.agent = start_process(
                    ["xcodebuild", "test-without-building", "-xctestrun", str(xctestrun),
                     "-destination", f"id={self.udid}"],
                    ready="JEVTEST_AGENT_READY", log=self.agent_log, timeout=AGENT_START_TIMEOUT, env=env)
            except DriverError as e:
                if "enabling automation mode" in self.agent_log.read_text(errors="replace"):
                    raise DriverError(
                        f"{self.name} did not allow UI automation (\"Timed out while enabling automation mode\"). "
                        "Unlock it and keep the screen on, check Settings > Developer > Enable UI Automation is on, "
                        "and answer any prompt on its screen; then run again.") from e
                raise
        if self.physical:
            self.host = self._tunnel_host()

    def _tunnel_host(self) -> str:
        """The phone's address on the USB tunnel Xcode keeps to it. Changes when the phone relocks."""
        conn = devicectl("device", "info", "details", "--device", self.udid).get("connectionProperties", {})
        address = conn.get("tunnelIPAddress")
        if not address:
            raise DriverError(f"No connection to {self.name}: unlock it and keep it plugged in")
        return f"[{address}]" if ":" in address else address

    def _url(self, path: str) -> str:
        return f"http://{self.host}:{self.port}{path}"

    def _log_tail(self) -> str:
        return self.agent_log.read_text()[-1500:] if self.agent_log.exists() else "(no log)"

    def _call(self, path: str, **body) -> dict:
        body.setdefault("bundle_id", self.app_id)
        try:
            data = http_post(self._url(path), body, timeout=60)
        except OSError as e:
            if not self.physical:
                raise DriverError(f"Lost the iOS agent during {path} ({e}). Agent log tail:\n{self._log_tail()}") \
                    from None
            try:  # the phone's tunnel address changes when it relocks: look it up again, once
                self.host = self._tunnel_host()
                data = http_post(self._url(path), body, timeout=60)
            except (OSError, DriverError) as again:
                raise DriverError(f"Lost the agent on {self.name} during {path} ({again}). "
                                  f"Is it unlocked and plugged in? Agent log tail:\n{self._log_tail()}") from None
        if "error" in data:
            raise DriverError(f"iOS agent {path}: {data['error']}")
        return data

    def close(self):
        """Put back anything a step changed (appearance, orientation, simulated location), then stop."""
        for path, body in self._restore.items():
            with contextlib.suppress(DriverError):
                self._call(path, **body)
        if self._location_set and not self.physical:
            simctl("location", self.udid, "clear")
        stop_process(self.agent)
        self._tmp.cleanup()

    def _remember(self, path: str):
        """Before the first change through `path`, note the current value so close() can put it back."""
        if path not in self._restore:
            self._restore[path] = {"raw": self._call(path)["raw"]}

    def check_ready(self):
        if self.physical and devicectl("device", "info", "lockState", "--device", self.udid).get("passcodeRequired"):
            raise DriverError(f"{self.name} is locked: unlock it and keep it unlocked during the run")

    # --- lifecycle ----------------------------------------------------------------
    def install(self, app_path: Path) -> str:
        bundle = app_bundle(app_path, Path(self._tmp.name) / "app")
        try:
            info = plistlib.loads((bundle / "Info.plist").read_bytes())
        except (OSError, plistlib.InvalidFileException) as e:
            raise DriverError(f"{app_path.name} has no readable Info.plist ({e})") from None
        platforms = info.get("CFBundleSupportedPlatforms", [])
        needed = "iPhoneOS" if self.physical else "iPhoneSimulator"
        if platforms and needed not in platforms:
            where = f"a real iPhone ({self.name})" if self.physical else "the iOS Simulator"
            how = "a device build signed with your team" if self.physical else "a build with `-sdk iphonesimulator`"
            raise DriverError(f"{app_path.name} is built for {', '.join(platforms)}, not {where}. Use {how}.")
        if "CFBundleIdentifier" not in info:
            raise DriverError(f"{app_path.name} Info.plist has no CFBundleIdentifier")
        self.app_path = bundle
        self.app_id = info["CFBundleIdentifier"]
        self._install_bundle()
        return self.app_id

    def _install_bundle(self):
        if self.physical:
            devicectl("device", "install", "app", "--device", self.udid, str(self.app_path))
        else:
            simctl("install", self.udid, str(self.app_path), timeout=300)

    def launch(self):
        if self.physical:
            devicectl("device", "process", "launch", "--device", self.udid, "--terminate-existing", self.app_id)
        else:
            simctl("launch", self.udid, self.app_id)
        self._call("/wait_foreground", timeout=self.timeout)

    def resume(self):
        self._call("/activate", timeout=self.timeout)

    def app_state(self) -> str:
        return APP_STATES.get(self._call("/state")["state"], "background")

    def stop(self):
        if self.physical:
            self._call("/terminate")
        else:
            simctl("terminate", self.udid, self.app_id, check=False)

    def clear_data(self):
        self.reinstall()  # iOS has no "clear data"; Apple's supported equivalent is a reinstall

    def reinstall(self):
        self.stop()
        if self.physical:
            devicectl("device", "uninstall", "app", "--device", self.udid, self.app_id)
        else:
            simctl("uninstall", self.udid, self.app_id, check=False)
        self._install_bundle()

    # --- observe --------------------------------------------------------------------
    def screen(self) -> Screen:
        return parse_tree(self._call("/tree"))

    def screenshot(self, path: Path):
        if self.physical:
            path.write_bytes(b64decode(self._call("/screenshot")["png"]))
        else:
            simctl("io", self.udid, "screenshot", str(path))

    # --- touch & keys -----------------------------------------------------------------
    def tap(self, x, y):
        self._call("/tap", x=x, y=y)

    def double_tap(self, x, y):
        self._call("/double_tap", x=x, y=y)

    def long_press(self, x, y, seconds=1.2):
        self._call("/long_press", x=x, y=y, seconds=seconds)

    def drag(self, x1, y1, x2, y2, seconds=0.3):
        self._call("/drag", x1=x1, y1=y1, x2=x2, y2=y2)

    def wait_idle(self, timeout, quiet=None):
        if quiet is None:
            self._call("/idle", timeout=timeout)
        else:
            self._call("/idle", timeout=timeout, quiet=quiet)

    def wait_change(self, timeout):
        self._call("/change", timeout=timeout)

    def type_text(self, text, at=None):
        if at:  # focus the field and let the focus change finish
            self.tap(*at)
            self.wait_idle(self.settle)
        self._call("/type", text=text)

    def clear_text(self, el):
        self.tap(*el.end)  # cursor after the text
        self.wait_idle(self.settle)
        if el.value:  # delete exactly what is there
            self._call("/key", key="delete", count=len(el.value))

    def key(self, name):
        self._call("/key", key=name)

    def back(self):
        self._call("/back")

    def home(self):
        self._call("/home")

    def hide_keyboard(self):
        self._call("/hide_keyboard")

    # --- device -----------------------------------------------------------------------
    def rotate(self, orientation):
        self._remember("/rotate")
        self._call("/rotate", orientation=orientation)

    def set_location(self, lat, lon):
        self._location_set = True
        if self.physical:
            self._call("/location", lat=lat, lon=lon)
        else:
            simctl("location", self.udid, "set", f"{lat},{lon}")

    def open_url(self, url):
        if self.physical:
            self._call("/open_url", url=url)
        else:
            simctl("openurl", self.udid, url)

    def dark_mode(self, on):
        self._remember("/appearance")
        self._call("/appearance", dark=on)

    def grant(self, permission):
        if self.physical:
            raise DriverError("A real iPhone can't pre-grant permissions: let the test tap the permission prompt")
        # simctl services: all, calendar, contacts, location, location-always, photos, microphone, ...
        simctl("privacy", self.udid, "grant", permission, self.app_id)

    def network(self, on):
        raise DriverError("jevtest can't turn an iPhone's or simulator's network off")
