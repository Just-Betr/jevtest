"""iOS tooling: simulators and connected iPhones, Xcode signing, app bundles, and building and calling the agent.

Everything here runs on the Mac. `ios.IOSDevice` uses it.
"""

from __future__ import annotations

import json
import socket
import tempfile
import threading
import urllib.request
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

from jevtest.domain.failures import DeviceError

from .common import run, run_bytes
from .tool_output import Object, as_list, as_object, as_text, dig, parse_json, parse_plist, text_at, texts

AGENT_SRC = Path(__file__).resolve().parent / "ios_agent"
"""The XCUITest agent's Xcode project, built the first time a version is needed."""


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
    return [
        sim
        for r in runtimes
        for sim in sorted(
            (_simulator(d, r) for d in as_list(by_runtime[r], what)), key=lambda d: (d["name"], d["udid"])
        )
    ]


def _simulator(raw: object, runtime: str) -> Simulator:
    d = as_object(raw, "a simctl device")
    return {
        "udid": as_text(d.get("udid"), "a simulator's udid"),
        "name": as_text(d.get("name"), "a simulator's name"),
        "state": as_text(d.get("state"), "a simulator's state"),
        "runtime": runtime.rsplit(".", 1)[-1],
    }


def phones(*, connected: bool = True) -> list[Phone]:
    """Real iPhones paired with this Mac: those with a live connection, or (`connected` False) those without."""
    listed = as_list(devicectl("list", "devices").get("devices", []), "devicectl's device list")
    return [
        {"udid": text_at(d, "hardwareProperties", "udid"), "name": text_at(d, "deviceProperties", "name")}
        for d in listed
        if _iphone(d) and (text_at(d, "connectionProperties", "tunnelState") == "connected") == connected
    ]


def _iphone(d: object) -> bool:
    return (text_at(d, "hardwareProperties", "reality"), text_at(d, "hardwareProperties", "platform")) == (
        "physical",
        "iOS",
    )


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
        # the name is right, but the device isn't ready: say what's wrong, rather than that there's no such device
        if any(wanted in (p["udid"], p["name"]) for p in phones(connected=False)):
            raise DeviceError(
                f"The iPhone '{wanted}' is paired but not connected: plug it in with USB, unlock it and keep it awake"
            )
        if any(wanted in (s["udid"], s["name"]) for s in simulators()):
            raise DeviceError(
                f"The simulator '{wanted}' isn't booted: boot it (xcrun simctl boot \"{wanted}\"); jevtest never "
                "boots devices"
            )
        listed = ", ".join(f"{t.name} ({t.udid})" for t in running) or "none"
        raise DeviceError(
            f"No booted simulator or connected iPhone called '{wanted}' (names are exact). Running: {listed}"
        )
    if len(matches) > 1:
        raise DeviceError(
            f"Several devices are called '{wanted}' ({', '.join(t.udid for t in matches)}): name one by its UDID"
        )
    return matches[0]


def _running_targets() -> list[Target]:
    """The booted simulators, then the connected iPhones."""
    sims = [Target(d["udid"], d["name"], physical=False) for d in simulators() if d["state"] == "Booted"]
    return sims + [Target(d["udid"], d["name"], physical=True) for d in phones()]


def xcode_team(team: str) -> str:
    """Check the app's team is signed into Xcode (Settings > Accounts), so jevtest can sign its agent."""
    raw = run(["defaults", "export", "com.apple.dt.Xcode", "-"], check=False)
    what = "Xcode's signed-in teams"
    prefs: Object = parse_plist(raw.encode(), what) if raw.strip() else {}
    accounts = as_object(prefs.get("IDEProvisioningTeamByIdentifier", {}), what)
    ids = sorted({text_at(t, "teamID") for account in accounts.values() for t in as_list(account, what)} - {""})
    if team not in ids:
        signed_in = ", ".join(ids) or "none"
        raise DeviceError(
            f"The app is signed by team {team}, which is not signed into Xcode (signed in: "
            f"{signed_in}). jevtest signs its agent with the app's team: in Xcode, Settings > "
            "Accounts, add the Apple Account for that team."
        )
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
        raise DeviceError(
            f"{app.name} is not signed for a real iPhone (it has no provisioning profile). "
            "Build it for the device, signed with your team."
        )
    teams = texts(prof.get("TeamIdentifier", []), f"{app.name}'s team")
    if len(teams) != 1:
        raise DeviceError(
            f"{app.name}'s provisioning profile names {len(teams)} teams ({', '.join(teams)}); expected one"
        )
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


def http_post(url: str, body: Mapping[str, object], timeout: float, token: str) -> Object:
    """A POST to the agent, carrying the run's token (the agent refuses a request without it); its JSON reply."""
    headers = {"Content-Type": "application/json", "X-Jevtest-Token": token}
    req = urllib.request.Request(url, method="POST", data=json.dumps(body).encode(), headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw: bytes = resp.read()
    return as_object(parse_json(raw, "the iOS agent's reply"), "the iOS agent's reply")


def build_agent_with_xcodebuild(out: Path, destination: str, signing: list[str]) -> None:
    """Build the agent for `destination` into `out`, signed as `signing` says (nothing for a simulator)."""
    run(
        [
            "xcodebuild",
            "build-for-testing",
            "-project",
            str(AGENT_SRC / "JevAgent.xcodeproj"),
            "-scheme",
            "JevAgent",
            "-destination",
            destination,
            "-derivedDataPath",
            str(out),
            "-quiet",
            *signing,
        ],
        timeout=900,
    )
