"""iOS Simulator driver: simctl for the app/device, the XCUITest agent for screen and touch."""

from __future__ import annotations

import hashlib
import json
import os
import plistlib
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

from ..screen import Element, Screen
from .base import Driver, DriverError, run

AGENT_SRC = Path(__file__).resolve().parent.parent / "ios_agent"
CACHE = Path(os.environ.get("JEVTEST_CACHE", Path.home() / ".cache" / "jevtest"))

# Container types that never matter to a tester on their own.
SKIP_KINDS = {"other", "keyboard", "navigation_bar", "tab_bar", "list", "scroll_view"}
SPRINGBOARD = "com.apple.springboard"


def simctl(*args, timeout=120, check=True) -> str:
    return run(["xcrun", "simctl", *args], timeout=timeout, check=check)


def simulators() -> list[dict]:
    data = json.loads(simctl("list", "devices", "available", "-j"))
    def version(runtime: str) -> tuple:
        return tuple(int(n) for n in runtime.rsplit("iOS-", 1)[-1].split("-") if n.isdigit())
    runtimes = sorted((r for r in data["devices"] if "iOS" in r), key=version, reverse=True)
    return [dict(d, runtime=r.rsplit(".", 1)[-1]) for r in runtimes for d in data["devices"][r]]


def pick_simulator(udid: str | None) -> str:
    sims = simulators()
    if udid:
        match = [d for d in sims if udid in (d["udid"], d["name"])]
        if not match:
            raise DriverError(f"No iOS simulator '{udid}'")
        dev = match[0]
    else:
        booted = [d for d in sims if d["state"] == "Booted"]
        phones = [d for d in sims if "iPhone" in d["name"]]
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


def _app_bundle(app_path: Path, workdir: Path) -> Path:
    """Return a simulator .app directory from a .app, .zip or .ipa."""
    if app_path.is_dir() and app_path.suffix == ".app":
        return app_path
    if app_path.suffix.lower() in (".zip", ".ipa"):
        with zipfile.ZipFile(app_path) as z:
            z.extractall(workdir)
        found = sorted(workdir.rglob("*.app"), key=lambda p: len(p.parts))
        if found:
            return found[0]
    raise DriverError(f"iOS needs a simulator .app (or .zip/.ipa containing one), got {app_path.name}")


class IOSDriver(Driver):
    platform = "ios"

    def __init__(self, udid: str | None = None):
        if shutil.which("xcrun") is None:
            raise DriverError("Xcode command line tools are required for iOS")
        self.udid = pick_simulator(udid)
        self.port = free_port()
        self.agent: subprocess.Popen | None = None
        self.app_path: Path | None = None
        self._tmp = tempfile.TemporaryDirectory()
        self._start_agent()

    # --- agent ------------------------------------------------------------
    def _build_agent(self) -> Path:
        digest = hashlib.sha1()
        for f in sorted(AGENT_SRC.rglob("*")):
            if f.is_file():
                digest.update(f.read_bytes())
        out = CACHE / f"ios-agent-{digest.hexdigest()[:12]}"
        runs = list((out / "Build/Products").glob("*.xctestrun")) if out.exists() else []
        if runs:
            return runs[0]
        print("  building iOS agent (one time, ~1 min)...", flush=True)
        run(["xcodebuild", "build-for-testing", "-project", str(AGENT_SRC / "JevAgent.xcodeproj"),
             "-scheme", "JevAgent", "-destination", "generic/platform=iOS Simulator",
             "-derivedDataPath", str(out), "-quiet"], timeout=900)
        runs = list((out / "Build/Products").glob("*.xctestrun"))
        if not runs:
            raise DriverError("iOS agent build produced no .xctestrun")
        return runs[0]

    def _start_agent(self):
        xctestrun = self._build_agent()
        env = dict(os.environ, TEST_RUNNER_JEVTEST_PORT=str(self.port))
        log = open(Path(self._tmp.name) / "agent.log", "w")
        self.agent = subprocess.Popen(
            ["xcodebuild", "test-without-building", "-xctestrun", str(xctestrun),
             "-destination", f"id={self.udid}"],
            stdout=log, stderr=subprocess.STDOUT, env=env, start_new_session=True)
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            if self.agent.poll() is not None:
                raise DriverError("iOS agent exited: " + (Path(self._tmp.name) / "agent.log").read_text()[-1500:])
            try:
                if self._call("/status", timeout=2).get("ok"):
                    return
            except OSError:
                pass
            time.sleep(1)
        raise DriverError("iOS agent did not start within 180s")

    def _call(self, path: str, timeout: float = 60, **body) -> dict:
        body.setdefault("bundle_id", self.app_id)
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", method="POST",
                                     data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
        if "error" in data:
            raise DriverError(f"iOS agent {path}: {data['error']}")
        return data

    def close(self):
        if self.agent and self.agent.poll() is None:
            self.agent.terminate()
            try:
                self.agent.wait(10)
            except subprocess.TimeoutExpired:
                self.agent.kill()
        self._tmp.cleanup()

    # --- lifecycle ---------------------------------------------------------
    def install(self, app_path: Path) -> str:
        bundle = _app_bundle(app_path, Path(self._tmp.name) / "app")
        info = plistlib.loads((bundle / "Info.plist").read_bytes())
        platforms = info.get("CFBundleSupportedPlatforms", [])
        if platforms and "iPhoneSimulator" not in platforms:
            raise DriverError(f"{app_path.name} is built for {platforms}, not the iOS Simulator. "
                              "Build with `-sdk iphonesimulator` (physical devices are not supported yet).")
        self.app_path = bundle
        self.app_id = info["CFBundleIdentifier"]
        simctl("install", self.udid, str(bundle), timeout=300)
        return self.app_id

    def launch(self):
        simctl("launch", self.udid, self.app_id)
        self._call("/activate")

    def resume(self):
        self._call("/activate")

    def stop(self):
        simctl("terminate", self.udid, self.app_id, check=False)

    def clear_data(self):
        # The simulator has no "clear data"; a reinstall is the equivalent.
        self.reinstall()

    def reinstall(self):
        self.stop()
        simctl("uninstall", self.udid, self.app_id, check=False)
        simctl("install", self.udid, str(self.app_path), timeout=300)

    # --- observe -------------------------------------------------------------
    def screen(self) -> Screen:
        data = self._call("/tree")
        w, h = int(data["width"]), int(data["height"])
        elements = []
        for d in data["elements"]:
            kind = d["type"]
            x1, y1 = max(int(d["x"]), 0), max(int(d["y"]), 0)
            x2, y2 = min(int(d["x"] + d["w"]), w), min(int(d["y"] + d["h"]), h)
            if x2 - x1 < 2 or y2 - y1 < 2:
                continue
            label, value = d.get("label", ""), d.get("value", "")
            editable = kind in ("text_field", "password_field", "text_area")
            if kind == "password_field":
                value = ""  # secure fields report bullets
            text = label
            if value and value != label and kind not in ("switch",):
                text = f"{label}: {value}" if label else value
            checked = None
            if kind == "switch":
                checked = value in ("1", "true")
            if kind in SKIP_KINDS and not (text or d.get("identifier")):
                continue
            if kind == "other" and not text:
                continue
            elements.append(Element(
                kind="text" if kind == "other" else kind, text=" ".join(text.split()),
                hint=d.get("placeholder", ""), resource_id=d.get("identifier", ""),
                bounds=(x1, y1, x2, y2), enabled=d.get("enabled", True), editable=editable,
                clickable=kind in ("button", "cell", "link", "switch", "tab", "menu_item"),
                focused=d.get("focused", False), checked=checked, selected=d.get("selected", False),
            ))
        # Drop exact duplicates (XCUITest often reports a container and its child with the same label).
        seen, unique = set(), []
        for el in elements:
            key = (el.kind, el.text, el.bounds)
            if key not in seen:
                seen.add(key)
                unique.append(el)
        return Screen(width=w, height=h, elements=unique,
                      keyboard_visible=data.get("keyboard", False), app_running=data.get("running", False))

    def screenshot(self, path: Path):
        simctl("io", self.udid, "screenshot", str(path))

    # --- touch & keys ----------------------------------------------------------
    def tap(self, x, y):
        self._call("/tap", x=x, y=y)

    def double_tap(self, x, y):
        self._call("/double_tap", x=x, y=y)

    def long_press(self, x, y, seconds=1.2):
        self._call("/long_press", x=x, y=y, seconds=seconds)

    def drag(self, x1, y1, x2, y2, seconds=0.3):
        self._call("/drag", x1=x1, y1=y1, x2=x2, y2=y2)

    def type_text(self, text):
        self._call("/type", text=text)

    def clear_text(self, el):
        self.tap(*el.center)
        self._call("/key", key="delete", count=len(el.text) + 10)

    def key(self, name):
        self._call("/key", key=name)

    def back(self):
        self._call("/back")

    def home(self):
        self._call("/home")

    def hide_keyboard(self):
        self._call("/hide_keyboard")

    # --- device ----------------------------------------------------------------
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
        raise DriverError("The iOS Simulator shares the Mac's network; it cannot be turned off per device")
