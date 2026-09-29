import contextlib
import io
import json
import plistlib
import shutil
import subprocess
import time
from pathlib import Path
from typing import NamedTuple

import pytest

from jevtest.adapters.devices import android, android_tools, ios, ios_tools
from jevtest.adapters.devices.cache import BuildInUse, digest
from jevtest.adapters.devices.ios import IOSDevice
from jevtest.domain.failures import DeviceError

FIX = Path(__file__).parent / "fixtures"
LOGIN = (FIX / "android_login.xml").read_text()

DEVICE_MODULES = (android, android_tools, ios, ios_tools)


def swap(monkeypatch, name, value):
    """Replace `name` in every device module that has it.

    A driver and its tools module both use tools such as `run`; a test swaps the one tool, wherever it's used.
    """
    holders = [m for m in DEVICE_MODULES if hasattr(m, name)]
    assert holders, f"no device module has {name}"
    for module in holders:
        monkeypatch.setattr(module, name, value)


@pytest.fixture(autouse=True)
def _ios_devices_leave_nothing_behind(monkeypatch):
    """Most tests make an IOSDevice and drop it: remove each one's temporary folder afterwards."""
    made: list[IOSDevice] = []
    real_init = IOSDevice.__init__

    def tracked(self, *args, **kwargs):
        made.append(self)
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(IOSDevice, "__init__", tracked)
    yield
    for device in made:
        if hasattr(device, "_tmp"):
            device._tmp.cleanup()


class Adb:
    """Stands in for `run`: answers by the first matching substring, records every command."""

    def __init__(self, rules=None):
        self.rules = dict(rules or {})
        self.cmds: list[str] = []

    def __call__(self, cmd, timeout=120, check=True, binary=False):
        line = " ".join(map(str, cmd))
        self.cmds.append(line)
        for needle, reply in self.rules.items():
            if needle in line:
                if isinstance(reply, Exception):
                    raise reply
                answer = reply.pop(0) if isinstance(reply, list) else reply
                return answer.encode() if binary else answer
        return b"" if binary else ""

    def run_bytes(self, cmd, timeout=120, check=True):
        return self(cmd, timeout, check, binary=True)

    def shell(self):
        return [c.split(" shell ", 1)[1] for c in self.cmds if " shell " in c]


@pytest.fixture(autouse=True)
def slept(monkeypatch):
    """A device's own waits check again after `CHECK_INTERVAL`: here the sleeps are recorded, not slept."""
    seconds: list[float] = []
    monkeypatch.setattr(time, "sleep", seconds.append)
    return seconds


class AgentHttp:
    """Stands in for the on-device agent: replies by path, records every request."""

    def __init__(self):
        self.replies: dict[str, object] = {
            "/tree": LOGIN,
            "/idle": "idle",
            "/change": "changed",
            "/rotate": "rotated",
            "/quit": "bye",
        }
        self.urls: list[str] = []
        self.started: list[tuple[object, ...]] = []  # helper processes the driver started or stopped

    def __call__(self, url, timeout):
        self.urls.append(url)
        reply = self.replies[url.split("7000", 1)[1].split("?")[0]]
        reply = reply.pop(0) if isinstance(reply, list) else reply
        if isinstance(reply, Exception):
            raise reply
        return reply

    def paths(self):
        return [u.split("7000", 1)[1] for u in self.urls]


class Proc:
    def __init__(self):
        self.running = True
        self.exits_on_quit = True
        self.waited = []

    def poll(self):
        return None if self.running else 0

    def wait(self, timeout):
        self.waited.append(timeout)
        if not self.exits_on_quit:
            raise subprocess.TimeoutExpired("am instrument", timeout)
        self.running = False


@pytest.fixture
def agent(monkeypatch):
    fake = AgentHttp()
    swap(monkeypatch, "http_get", fake)
    swap(
        monkeypatch,
        "build_agent",
        lambda progress: BuildInUse(Path("/cache/android-agent-abc123.apk"), "abc123", io.StringIO()),
    )

    def start_process(cmd, ready, log, timeout):
        fake.started.append((cmd, ready))
        return Proc()

    swap(monkeypatch, "start_process", start_process)
    swap(monkeypatch, "stop_process", lambda proc: fake.started.append(("stopped",)))
    return fake


@pytest.fixture
def adb(monkeypatch, agent):
    fake = Adb(
        {
            "adb devices": "List of devices attached\nemulator-5554\tdevice\nR58N\tunauthorized\n",
            "wm size": "Physical size: 1080x2424\n",
            "aapt2": "dev.demo\n",
            "resolve-activity": "priority=0\ndev.demo/.MainActivity\n",
            "forward tcp:0": "7000\n",
            "dumpsys package dev.jevtest.agent": "    versionName=abc123\n",
            "dumpsys package dev.demo": (
                "    requested permissions:\n      android.permission.CAMERA\n      android.permission.RECORD_AUDIO\n"
                "    runtime permissions:\n      android.permission.CAMERA: granted=true, flags=[ USER_SET ]\n"
                "      android.permission.RECORD_AUDIO: granted=true, flags=[ USER_SET ]\n"
            ),
        }
    )
    swap(monkeypatch, "run", fake)
    swap(monkeypatch, "run_bytes", fake.run_bytes)
    monkeypatch.setattr(shutil, "which", lambda name: f"/bin/{name}")
    return fake


SIMS = {
    "devices": {
        "com.apple.CoreSimulator.SimRuntime.iOS-26-5": [
            {"name": "iPhone 17", "udid": "B", "state": "Shutdown"},
            {"name": "iPad Air", "udid": "C", "state": "Shutdown"},
        ],
        "com.apple.CoreSimulator.SimRuntime.iOS-18-0": [{"name": "iPhone 16", "udid": "A", "state": "Booted"}],
        "com.apple.CoreSimulator.SimRuntime.watchOS-11-0": [{"name": "Watch", "udid": "W", "state": "Booted"}],
    }
}


class Agent:
    """Stands in for the XCUITest agent's HTTP API. Like the real one, it refuses a call without the run's token."""

    token = ""  # set from the device's environment when the fake start_process starts the agent

    def __init__(self):
        self.replies: dict[str, object] = {
            "/status": {"ok": True},
            "/state": {"state": 4},
            "/rotate": {"raw": 1},
            "/appearance": {"raw": 1},
        }
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.wide = False  # like the real app: it turns when told to (`turns = False` for one that doesn't)
        self.turns = True
        self.keyboard = False  # like the real app: a tap focuses a field and raises the keyboard

    def __call__(self, url, body, timeout, token):
        assert token == self.token, "every agent call must carry the device's token"
        path = url.split("8123", 1)[1]
        self.calls.append((path, body))
        if path == "/rotate" and "orientation" in body and self.turns:
            self.wide = body["orientation"] in ("landscape", "landscape_right")
        self.keyboard = {"/tap": True, "/hide_keyboard": False}.get(path, self.keyboard)
        if path == "/tree" and path not in self.replies:
            width, height = (874, 402) if self.wide else (402, 874)
            field = {"type": "text_field", "x": 0, "y": 0, "w": 100, "h": 40, "focused": False}  # iOS never says
            return {
                "width": width,
                "height": height,
                "elements": [field] if self.keyboard else [],
                "keyboard": self.keyboard,
            }
        reply = self.replies.get(path, {"ok": True})
        if isinstance(reply, list):
            reply = reply.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def paths(self):
        return [p for p, _ in self.calls]


class DeviceCtl:
    """Stands in for `xcrun devicectl`: replies by subcommand, records every call."""

    def __init__(self):
        self.phones: list[dict[str, object]] = []
        self.replies: dict[str, object] = {
            "info details": {"connectionProperties": {"tunnelIPAddress": "fd00::1"}},
            "info lockState": {"passcodeRequired": False, "unlockedSinceBoot": True},
        }
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, *args, timeout=300):
        self.calls.append(args)
        line = " ".join(args)
        if line.startswith("list devices"):
            return {"devices": self.phones}
        for key, reply in self.replies.items():
            if key in line:
                answer = reply.pop(0) if isinstance(reply, list) else reply
                if isinstance(answer, Exception):
                    raise answer
                return answer
        return {}


class IOSEnv(NamedTuple):
    """The fakes behind an iOS driver: the command line, the agent, started helpers, and devicectl."""

    sim: Adb
    agent: Agent
    procs: list[tuple[object, ...]]
    ctl: DeviceCtl


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("JEVTEST_CACHE", str(tmp_path / "cache"))
    xctestrun = tmp_path / "cache" / f"ios-agent-{digest(ios_tools.AGENT_SRC)}" / "Build/Products/a.xctestrun"
    xctestrun.parent.mkdir(parents=True)
    xctestrun.write_text("")
    sim = Adb({"list devices": json.dumps(SIMS)})
    agent, procs = Agent(), list[tuple[object, ...]]()
    swap(monkeypatch, "run", sim)
    swap(monkeypatch, "run_bytes", sim.run_bytes)
    swap(monkeypatch, "http_post", agent)
    swap(monkeypatch, "free_port", lambda: 8123)
    monkeypatch.setattr(shutil, "which", lambda n: "/usr/bin/xcrun")

    def start_process(cmd, ready, log, timeout, env):
        procs.append((cmd, ready, env))
        agent.token = env["TEST_RUNNER_JEVTEST_TOKEN"]
        return "proc"

    swap(monkeypatch, "start_process", start_process)
    swap(monkeypatch, "stop_process", lambda proc: procs.append(("stopped", proc)))
    ctl = DeviceCtl()
    swap(monkeypatch, "devicectl", ctl)
    return IOSEnv(sim, agent, procs, ctl)


def make_app(tmp_path, bundle_id="dev.demo", platforms=("iPhoneSimulator",), name="Demo.app") -> Path:
    app: Path = tmp_path / name
    app.mkdir(parents=True)
    info: dict[str, object] = {"CFBundleSupportedPlatforms": list(platforms)}
    if bundle_id:
        info["CFBundleIdentifier"] = bundle_id
    (app / "Info.plist").write_bytes(plistlib.dumps(info))
    return app


PHONE = {
    "hardwareProperties": {"reality": "physical", "platform": "iOS", "udid": "00008150-X"},
    "deviceProperties": {"name": "BH"},
    "connectionProperties": {"tunnelState": "connected"},
}


TEAMS = {"IDEProvisioningTeamByIdentifier": {"acct": [{"teamID": "TEAM1", "isFreeProvisioningTeam": False}]}}


@pytest.fixture
def phone(env, monkeypatch, tmp_path):
    """The simulator env, plus a connected iPhone 'BH', an Xcode team, and a provisioned agent build."""
    env.ctl.phones = [PHONE]
    sim = env.sim
    sim.rules["defaults export"] = plistlib.dumps(TEAMS).decode()
    out = tmp_path / "cache" / f"ios-agent-{digest(ios_tools.AGENT_SRC)}-TEAM1" / "Build/Products"
    (out / "Debug-iphoneos/JevAgentUITests-Runner.app").mkdir(parents=True)
    (out / "a.xctestrun").write_text("")
    swap(monkeypatch, "provisioned_devices", lambda app: {"00008150-X"})
    sim.rules["security cms"] = plistlib.dumps({"TeamIdentifier": ["TEAM1"]}).decode()
    return env


@pytest.fixture
def ios_device():
    """Makes iOS devices like `IOSDevice`, and closes each when the test ends, as a run does."""
    made: list[IOSDevice] = []

    def make(device, app, progress):
        made.append(IOSDevice(device, app, progress))
        return made[-1]

    yield make
    for d in made:
        with contextlib.suppress(DeviceError):  # a test may have left its fake agent unreachable
            d.close()
