"""iOS Simulator driver: simctl for the app and device, the XCUITest agent for screen and touch."""

from __future__ import annotations

import json
import os
import plistlib
import re
import shutil
import socket
import subprocess
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from ..screen import Element, Screen
from .base import Driver, DriverError, cache_dir, digest, run, start_process, stop_process

AGENT_SRC = Path(__file__).resolve().parent.parent / "ios_agent"
AGENT_START_TIMEOUT = 300  # includes xcodebuild installing the agent on a fresh simulator
# Container types that only matter when they carry a label or identifier.
CONTAINERS = {"other", "navigation_bar", "tab_bar", "list", "scroll_view", "webview"}
# Kinds whose accessibility value means something (for plain text it repeats the label or is a heading level).
VALUE_KINDS = {"text_field", "text_area", "slider", "picker", "segmented_control", "progress"}
SCROLL_INDICATOR = re.compile(r"^(Vertical|Horizontal) scroll bar\b")
TOUCHABLE = {"button", "cell", "link", "switch", "tab", "menu_item", "segmented_control"}
EDITABLE = {"text_field", "password_field", "text_area"}
# XCUIApplication.State raw values.
APP_STATES = {0: "not_running", 1: "not_running", 2: "background", 3: "background", 4: "foreground"}


def simctl(*args, timeout=120, check=True) -> str:
    return run(["xcrun", "simctl", *args], timeout=timeout, check=check)


def _runtime_version(runtime: str) -> tuple[int, ...]:
    return tuple(int(n) for n in runtime.rsplit("iOS-", 1)[-1].split("-") if n.isdigit())


def simulators() -> list[dict]:
    """Available iOS simulators, newest runtime first, then by name."""
    data = json.loads(simctl("list", "devices", "available", "-j"))
    runtimes = sorted((r for r in data["devices"] if ".iOS-" in r), key=_runtime_version, reverse=True)
    return [dict(d, runtime=r.rsplit(".", 1)[-1])
            for r in runtimes for d in sorted(data["devices"][r], key=lambda d: (d["name"], d["udid"]))]


def pick_simulator(wanted: str | None) -> str:
    sims = simulators()
    if wanted:
        match = [d for d in sims if wanted in (d["udid"], d["name"])]
        if not match:
            raise DriverError(f"No iOS simulator named or with UDID '{wanted}'")
        dev = match[0]
    else:
        booted = [d for d in sims if d["state"] == "Booted"]
        phones = [d for d in sims if d["name"].startswith("iPhone")]
        if not (booted or phones):
            raise DriverError("No iOS simulators available. Install one in Xcode > Settings > Components.")
        dev = (booted or phones)[0]
    if dev["state"] != "Booted":
        simctl("boot", dev["udid"])
    simctl("bootstatus", dev["udid"], "-b", timeout=300)
    run(["open", "-a", "Simulator"], check=False)
    return dev["udid"]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def app_bundle(app_path: Path, workdir: Path) -> Path:
    """A simulator .app directory from a .app, or a .zip / .ipa containing one."""
    if app_path.suffix.lower() == ".app" and app_path.is_dir():
        return app_path
    if app_path.suffix.lower() in (".zip", ".ipa") and zipfile.is_zipfile(app_path):
        with zipfile.ZipFile(app_path) as z:
            z.extractall(workdir)
        found = sorted(workdir.rglob("*.app"), key=lambda p: (len(p.parts), str(p)))
        if found:
            return found[0]
    raise DriverError(f"iOS needs a simulator .app (or a .zip/.ipa containing one), got {app_path.name}")


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
        if kind in VALUE_KINDS and value and value != label:  # secure fields are not in VALUE_KINDS: bullets
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
    return Screen(width=w, height=h, elements=elements, keyboard_visible=data.get("keyboard", False))


def http_post(url: str, body: dict, timeout: float) -> dict:
    req = urllib.request.Request(url, method="POST", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


class IOSDriver(Driver):
    platform = "ios"

    def __init__(self, udid: str | None = None):
        if shutil.which("xcrun") is None:
            raise DriverError("Xcode command line tools are required for iOS")
        self.udid = pick_simulator(udid)
        self.port = free_port()
        self.agent: subprocess.Popen | None = None
        self.agent_log = cache_dir() / f"ios-agent-{self.port}.log"
        self.app_path: Path | None = None
        self._tmp = tempfile.TemporaryDirectory()
        self._start_agent()

    # --- agent ------------------------------------------------------------------
    def _build_agent(self) -> Path:
        out = cache_dir() / f"ios-agent-{digest(AGENT_SRC)}"
        runs = sorted((out / "Build/Products").glob("*.xctestrun"))
        if not runs:
            print("  building iOS agent (one time, ~1 min)...", flush=True)
            run(["xcodebuild", "build-for-testing", "-project", str(AGENT_SRC / "JevAgent.xcodeproj"),
                 "-scheme", "JevAgent", "-destination", "generic/platform=iOS Simulator",
                 "-derivedDataPath", str(out), "-quiet"], timeout=900)
            runs = sorted((out / "Build/Products").glob("*.xctestrun"))
            if not runs:
                raise DriverError("iOS agent build produced no .xctestrun")
        return runs[0]

    def _start_agent(self):
        env = dict(os.environ, TEST_RUNNER_JEVTEST_PORT=str(self.port))
        self.agent = start_process(
            ["xcodebuild", "test-without-building", "-xctestrun", str(self._build_agent()),
             "-destination", f"id={self.udid}"],
            ready="JEVTEST_AGENT_READY", log=self.agent_log, timeout=AGENT_START_TIMEOUT, env=env)

    def _url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def _log_tail(self) -> str:
        return self.agent_log.read_text()[-1500:] if self.agent_log.exists() else "(no log)"

    def _call(self, path: str, **body) -> dict:
        body.setdefault("bundle_id", self.app_id)
        try:
            data = http_post(self._url(path), body, timeout=60)
        except OSError as e:
            raise DriverError(f"Lost the iOS agent during {path} ({e}). Agent log tail:\n{self._log_tail()}") \
                from None
        if "error" in data:
            raise DriverError(f"iOS agent {path}: {data['error']}")
        return data

    def close(self):
        stop_process(self.agent)
        self._tmp.cleanup()

    # --- lifecycle ----------------------------------------------------------------
    def install(self, app_path: Path) -> str:
        bundle = app_bundle(app_path, Path(self._tmp.name) / "app")
        try:
            info = plistlib.loads((bundle / "Info.plist").read_bytes())
        except (OSError, plistlib.InvalidFileException) as e:
            raise DriverError(f"{app_path.name} has no readable Info.plist ({e})") from None
        platforms = info.get("CFBundleSupportedPlatforms", [])
        if platforms and "iPhoneSimulator" not in platforms:
            raise DriverError(f"{app_path.name} is built for {', '.join(platforms)}, not the iOS Simulator. "
                              "Build with `-sdk iphonesimulator` (physical devices are not supported yet).")
        if "CFBundleIdentifier" not in info:
            raise DriverError(f"{app_path.name} Info.plist has no CFBundleIdentifier")
        self.app_path = bundle
        self.app_id = info["CFBundleIdentifier"]
        simctl("install", self.udid, str(bundle), timeout=300)
        return self.app_id

    def launch(self):
        simctl("launch", self.udid, self.app_id)
        self._call("/wait_foreground", timeout=self.timeout)

    def resume(self):
        self._call("/activate", timeout=self.timeout)

    def app_state(self) -> str:
        return APP_STATES.get(self._call("/state")["state"], "background")

    def stop(self):
        simctl("terminate", self.udid, self.app_id, check=False)

    def clear_data(self):
        self.reinstall()  # the simulator has no "clear data"; a reinstall is the equivalent

    def reinstall(self):
        self.stop()
        simctl("uninstall", self.udid, self.app_id, check=False)
        simctl("install", self.udid, str(self.app_path), timeout=300)

    # --- observe --------------------------------------------------------------------
    def screen(self) -> Screen:
        return parse_tree(self._call("/tree"))

    def screenshot(self, path: Path):
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
        self._call("/rotate", orientation=orientation)

    def set_location(self, lat, lon):
        simctl("location", self.udid, "set", f"{lat},{lon}")

    def open_url(self, url):
        simctl("openurl", self.udid, url)

    def dark_mode(self, on):
        simctl("ui", self.udid, "appearance", "dark" if on else "light")

    def grant(self, permission):
        # simctl services: all, calendar, contacts, location, location-always, photos, microphone, ...
        simctl("privacy", self.udid, "grant", permission, self.app_id)

    def network(self, on):
        raise DriverError("The iOS Simulator shares the Mac's network; it can't be turned off per device")
