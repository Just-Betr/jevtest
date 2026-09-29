"""iOS tooling: simulators and connected iPhones, Xcode signing, app bundles, and building and calling the agent.

Everything here runs on the Mac. `ios.IOSDevice` uses it.
"""

from __future__ import annotations

import json
import re
import socket
import tempfile
import threading
import urllib.request
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from jevtest.domain.failures import DeviceError

from .cache import AgentBuilds, cache_dir
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


_IOS_RUNTIME = re.compile(r"com\.apple\.CoreSimulator\.SimRuntime\.iOS-(\d+(?:-\d+)*)")
"""A simctl runtime identifier for iOS, and its version: ``com.apple.CoreSimulator.SimRuntime.iOS-26-5``."""


def _ios_version(runtime: str) -> tuple[int, ...] | None:
    """The iOS version a simctl runtime identifier names, e.g. (26, 5); None for another OS (watchOS, ...)."""
    found = _IOS_RUNTIME.fullmatch(runtime)
    return tuple(int(n) for n in found[1].split("-")) if found else None


@dataclass(frozen=True)
class Simulator:
    """An available iOS simulator, as simctl lists it."""

    udid: str
    name: str
    booted: bool
    version: tuple[int, ...]
    """The iOS version it runs, e.g. (26, 5)."""

    @property
    def runs(self) -> str:
        """What it runs, in words: ``iOS 26.5``."""
        return "iOS " + ".".join(map(str, self.version))


@dataclass(frozen=True)
class Phone:
    """A real iPhone paired with this Mac, as devicectl lists it."""

    udid: str
    name: str
    connected: bool
    """It has a live connection: plugged in (or on the network), unlocked since it was last locked."""


def simulators() -> list[Simulator]:
    """Available iOS simulators (not watchOS, tvOS, ...), newest iOS first, then by name."""
    what = "simctl list devices"
    by_runtime = as_object(dig(parse_json(simctl("list", "devices", "available", "-j"), what), "devices"), what)
    sims = [
        _simulator(raw, version)
        for runtime, listed in by_runtime.items()
        if (version := _ios_version(runtime)) is not None
        for raw in as_list(listed, what)
    ]
    by_name = sorted(sims, key=lambda s: (s.name, s.udid))
    return sorted(by_name, key=lambda s: s.version, reverse=True)  # a stable sort: by name within a version


def _simulator(raw: object, version: tuple[int, ...]) -> Simulator:
    d = as_object(raw, "a simctl device")
    return Simulator(
        udid=as_text(d.get("udid"), "a simulator's udid"),
        name=as_text(d.get("name"), "a simulator's name"),
        booted=as_text(d.get("state"), "a simulator's state") == "Booted",
        version=version,
    )


def phones() -> list[Phone]:
    """Real iPhones paired with this Mac, connected or not."""
    listed = as_list(devicectl("list", "devices").get("devices", []), "devicectl's device list")
    return [
        Phone(
            udid=text_at(d, "hardwareProperties", "udid"),
            name=text_at(d, "deviceProperties", "name"),
            connected=text_at(d, "connectionProperties", "tunnelState") == "connected",
        )
        for d in listed
        if _iphone(d)
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
    runs: str = ""
    """What it runs, to tell devices with the same name apart: ``iOS 26.5`` for a simulator."""


def find_target(wanted: str) -> Target:
    """The one booted simulator or connected iPhone with exactly this name or UDID.

    jevtest never boots, opens or unlocks a device.
    """
    sims, iphones = simulators(), phones()
    running = [Target(s.udid, s.name, physical=False, runs=s.runs) for s in sims if s.booted] + [
        Target(p.udid, p.name, physical=True) for p in iphones if p.connected
    ]
    matches = [t for t in running if wanted in (t.udid, t.name)]
    if not matches:
        # the name is right, but the device isn't ready: say what's wrong, rather than that there's no such device
        if any(wanted in (p.udid, p.name) for p in iphones):
            raise DeviceError(
                f"The iPhone '{wanted}' is paired but not connected: plug it in with USB, unlock it and keep it awake"
            )
        if any(wanted in (s.udid, s.name) for s in sims):
            raise DeviceError(
                f"The simulator '{wanted}' isn't booted: boot it (xcrun simctl boot \"{wanted}\"); jevtest never "
                "boots devices"
            )
        listed = ", ".join(f"{t.name} ({t.udid})" for t in running) or "none"
        raise DeviceError(
            f"No booted simulator or connected iPhone called '{wanted}' (names are exact). Running: {listed}"
        )
    if len(matches) > 1:
        listed = ", ".join(f"{t.udid} ({t.runs or 'an iPhone'})" for t in matches)
        raise DeviceError(f"Several devices are called '{wanted}' ({listed}): name one by its UDID")
    return matches[0]


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


def agent_builds(team: str) -> AgentBuilds:
    """The agent's cached builds: for simulators (`team` empty), or signed by `team` for its iPhones."""
    return AgentBuilds("ios-agent-", f"-{team}" if team else "")


def remove_port_logs() -> None:
    """Remove the agent logs jevtest 0.9.1 left, one per run, named by port (``ios-agent-8123.log``).

    Since 0.9.2 there is one log per device. Remove this in 1.0, when no 0.9.1 cache is left.
    """
    for log in cache_dir().glob("ios-agent-*.log"):
        if re.fullmatch(r"ios-agent-\d+\.log", log.name):
            log.unlink(missing_ok=True)


def free_port() -> int:
    """A free local port for the agent to listen on."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
    return port


def check_build(app: Path, info: Object, *, physical: bool, device: str) -> None:
    """Raise `DeviceError` unless the build (its Info.plist `info`) runs on this kind of device."""
    platforms = texts(info.get("CFBundleSupportedPlatforms", []), f"{app.name}'s supported platforms")
    needed = "iPhoneOS" if physical else "iPhoneSimulator"
    if platforms and needed not in platforms:
        where = f"a real iPhone ({device})" if physical else "the iOS Simulator"
        how = "a device build signed with your team" if physical else "a build with `-sdk iphonesimulator`"
        raise DeviceError(f"{app.name} is built for {', '.join(platforms)}, not {where}. Use {how}.")


def info_plist(app: Path) -> Object:
    """A build's Info.plist: from an .app, or read from inside a .zip / .ipa without unpacking it.

    Raises:
        DeviceError: It isn't an iOS build, or has no readable Info.plist.
    """
    try:
        if _is_archive(app):
            with zipfile.ZipFile(app) as z:
                raw = z.read(f"{_app_in_archive(app, z.namelist())}/Info.plist")
        else:
            raw = (_app_dir(app) / "Info.plist").read_bytes()
    except (OSError, KeyError) as e:
        raise DeviceError(f"{app.name} has no readable Info.plist ({e})") from None
    return parse_plist(raw, f"{app.name}'s Info.plist")


def app_bundle(app: Path, workdir: Path) -> Path:
    """The build's .app: itself, or unpacked into `workdir` from a .zip / .ipa.

    Raises:
        DeviceError: It isn't an iOS build.
    """
    if not _is_archive(app):
        return _app_dir(app)
    with zipfile.ZipFile(app) as z:
        inside = _app_in_archive(app, z.namelist())
        z.extractall(workdir)
    return workdir / inside


NOT_A_BUILD = "iOS needs an .app (or a .zip/.ipa containing one), got {name}"


def _is_archive(app: Path) -> bool:
    return app.suffix.lower() in (".zip", ".ipa") and zipfile.is_zipfile(app)


def _app_dir(app: Path) -> Path:
    if app.suffix.lower() != ".app" or not app.is_dir():
        raise DeviceError(NOT_A_BUILD.format(name=app.name))
    return app


def _app_in_archive(app: Path, names: list[str]) -> PurePosixPath:
    """The .app a .zip / .ipa holds: the outermost (an .ipa's `Payload/X.app`, not an extension's .app inside it)."""
    apps = {
        PurePosixPath(*parts[: i + 1])
        for parts in (PurePosixPath(n).parts for n in names)
        for i, part in enumerate(parts)
        if part.endswith(".app")
    }
    if not apps:
        raise DeviceError(NOT_A_BUILD.format(name=app.name))
    return min(apps, key=lambda p: (len(p.parts), str(p)))


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
