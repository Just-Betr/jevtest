"""Real-iPhone paths of the iOS driver (the simulator paths are in test_ios.py)."""

import json
import plistlib
from base64 import b64encode
from pathlib import Path

import pytest

from jevtest.drivers import ios
from jevtest.drivers.base import DriverError
from jevtest.drivers.ios import IOSDriver

from .test_ios import SIMS, make_app

PHONE = {"hardwareProperties": {"reality": "physical", "platform": "iOS", "udid": "00008150-X"},
         "deviceProperties": {"name": "BH"}, "connectionProperties": {"tunnelState": "connected"}}
TEAMS = {"IDEProvisioningTeamByIdentifier": {"acct": [{"teamID": "TEAM1", "isFreeProvisioningTeam": False}]}}


@pytest.fixture
def phone(env, monkeypatch, tmp_path):
    """The simulator env, plus a connected iPhone 'BH', an Xcode team, and a provisioned agent build."""
    sim, agent, procs = env
    ios.devicectl.phones = [PHONE]
    sim.rules["defaults export"] = plistlib.dumps(TEAMS).decode()
    out = tmp_path / "cache" / f"ios-agent-{ios.digest(ios.AGENT_SRC)}-TEAM1" / "Build/Products"
    (out / "Debug-iphoneos/JevAgentUITests-Runner.app").mkdir(parents=True)
    (out / "a.xctestrun").write_text("")
    monkeypatch.setattr(ios, "provisioned_devices", lambda app: {"00008150-X"})
    sim.rules["security cms"] = plistlib.dumps({"TeamIdentifier": ["TEAM1"]}).decode()
    return sim, agent, procs


def signed_app(tmp_path) -> Path:
    """A device build of the app, signed by team TEAM1 (its profile is decoded by the fake `security cms`)."""
    app = make_app(tmp_path / "signed", platforms=("iPhoneOS",))
    (app / "embedded.mobileprovision").write_bytes(b"signed")
    return app


@pytest.fixture
def dev(phone, tmp_path):
    d = IOSDriver("BH", signed_app(tmp_path))
    d.install(signed_app(tmp_path / "again"))
    ios.devicectl.calls.clear()
    phone[1].calls.clear()
    return d


# --- finding phones and teams ----------------------------------------------------------------------

def test_phones_lists_connected_real_iphones(env):
    ios.devicectl.phones = [
        PHONE,
        dict(PHONE, connectionProperties={"tunnelState": "disconnected"}),       # not reachable now
        dict(PHONE, hardwareProperties={"reality": "simulated", "platform": "iOS", "udid": "S"}),
        dict(PHONE, hardwareProperties={"reality": "physical", "platform": "watchOS", "udid": "W"}),
    ]
    assert ios.phones() == [{"udid": "00008150-X", "name": "BH"}]


def test_find_target_by_phone_name(phone):
    assert ios.find_target("BH") == ios.Target("00008150-X", "BH", True)
    assert ios.find_target("00008150-X").physical is True


def teams(*entries):
    return plistlib.dumps({"IDEProvisioningTeamByIdentifier": {"a": list(entries)}}).decode()


def test_the_agent_is_signed_by_the_apps_team(phone, tmp_path):
    assert IOSDriver("BH", signed_app(tmp_path)).team == "TEAM1"


@pytest.mark.parametrize("prefs,message", [
    ("", r"signed by team TEAM1, which is not signed into Xcode \(signed in: none\).*Settings > Accounts"),
    (teams({"teamID": "T2"}), r"signed by team TEAM1, which is not signed into Xcode \(signed in: T2\)"),
])
def test_the_apps_team_must_be_signed_into_xcode(phone, tmp_path, prefs, message):
    phone[0].rules["defaults export"] = prefs
    with pytest.raises(DriverError, match=message):
        IOSDriver("BH", signed_app(tmp_path))


def test_a_simulator_build_on_a_phone_is_an_error(phone, tmp_path):
    with pytest.raises(DriverError, match=r"Demo.app is not signed for a real iPhone \(it has no provisioning"):
        IOSDriver("BH", make_app(tmp_path))


@pytest.mark.parametrize("profile,message", [
    ({"TeamIdentifier": []}, "names 0 teams"),
    ({"TeamIdentifier": ["A", "B"]}, r"names 2 teams \(A, B\); expected one"),
])
def test_the_profile_must_name_one_team(phone, tmp_path, profile, message):
    phone[0].rules["security cms"] = plistlib.dumps(profile).decode()
    with pytest.raises(DriverError, match=message):
        IOSDriver("BH", signed_app(tmp_path))


def test_provisioned_devices(env, tmp_path):
    app = tmp_path / "R.app"
    app.mkdir()
    assert ios.provisioned_devices(app) == set()  # no profile: a simulator build
    (app / "embedded.mobileprovision").write_bytes(b"signed")
    env[0].rules["security cms"] = plistlib.dumps({"ProvisionedDevices": ["U1", "U2"]}).decode()
    assert ios.provisioned_devices(app) == {"U1", "U2"}
    env[0].rules["security cms"] = "not a plist"
    with pytest.raises(DriverError, match="R.app's embedded.mobileprovision can't be read"):
        ios.provisioned_devices(app)


def test_devicectl_returns_the_json_result(monkeypatch):
    seen = []

    def fake_run(cmd, timeout):
        seen.append(cmd)
        Path(cmd[cmd.index("--json-output") + 1]).write_text(json.dumps({"result": {"devices": [1]}}))
    monkeypatch.setattr(ios, "run", fake_run)
    assert ios.devicectl("list", "devices") == {"devices": [1]}
    assert seen[0][:4] == ["xcrun", "devicectl", "list", "devices"]


# --- the signed agent ----------------------------------------------------------------------------

def test_agent_on_a_phone_is_reached_through_the_tunnel(tmp_path, phone):
    d = IOSDriver("BH", signed_app(tmp_path))
    assert (d.physical, d.team, d.host) == (True, "TEAM1", "[fd00::1]")
    assert d._url("/tree") == "http://[fd00::1]:8123/tree"
    cmd = phone[2][0][0]
    assert cmd[-1] == "id=00008150-X"


def test_ipv4_tunnel_address_has_no_brackets(tmp_path, phone):
    ios.devicectl.replies["info details"] = {"connectionProperties": {"tunnelIPAddress": "10.0.0.2"}}
    assert IOSDriver("BH", signed_app(tmp_path)).host == "10.0.0.2"


def test_no_tunnel_is_a_clear_error(tmp_path, phone):
    ios.devicectl.replies["info details"] = {"connectionProperties": {}}
    with pytest.raises(DriverError, match="No connection to BH: unlock it and keep it plugged in"):
        IOSDriver("BH", signed_app(tmp_path))


def test_agent_is_signed_for_the_phone_when_not_provisioned(phone, monkeypatch, tmp_path):
    monkeypatch.setattr(ios, "provisioned_devices", lambda app: set())  # a new phone for this build
    builds = []

    def fake_build(self, out, destination, signing):
        builds.append((destination, signing))
    monkeypatch.setattr(IOSDriver, "_xcodebuild", fake_build)
    IOSDriver("BH", signed_app(tmp_path))
    [(destination, signing)] = builds
    assert destination == "id=00008150-X"
    wanted = {"DEVELOPMENT_TEAM=TEAM1", "JEVTEST_TEAM_SUFFIX=.TEAM1", "-allowProvisioningDeviceRegistration"}
    assert wanted <= set(signing)


def test_agent_build_retries_once_when_xcode_swaps_the_profile(tmp_path, phone, monkeypatch):
    monkeypatch.setattr(ios, "provisioned_devices", lambda app: set())
    attempts = []

    def flaky(self, out, destination, signing):
        attempts.append(destination)
        if len(attempts) == 1:
            raise DriverError("Build input file cannot be found: ...mobileprovision")
    monkeypatch.setattr(IOSDriver, "_xcodebuild", flaky)
    IOSDriver("BH", signed_app(tmp_path))
    assert len(attempts) == 2


def test_agent_build_command(phone, monkeypatch, tmp_path):
    d = IOSDriver("BH", signed_app(tmp_path))
    seen = []
    monkeypatch.setattr(ios, "run", lambda cmd, timeout: seen.append(cmd))
    d._xcodebuild(tmp_path / "out", "id=U", ["X=1"])
    assert seen[0][:2] == ["xcodebuild", "build-for-testing"] and seen[0][-1] == "X=1"


# --- talking to it ---------------------------------------------------------------------------------

def test_relocked_phone_gets_a_new_tunnel_address(dev, phone):
    agent = phone[1]
    agent.replies["/tree"] = [OSError("no route"), json.loads((Path(__file__).parent / "fixtures/ios_login.json")
                                                             .read_text())]
    ios.devicectl.replies["info details"] = {"connectionProperties": {"tunnelIPAddress": "fd00::2"}}
    assert dev.screen().elements
    assert dev.host == "[fd00::2]"


def test_lost_phone_is_a_clear_error(dev, phone):
    phone[1].replies["/tree"] = OSError("no route")
    with pytest.raises(DriverError, match="Lost the agent on BH during /tree .* unlocked and plugged in"):
        dev.screen()


def test_locked_phone(dev):
    dev.check_ready()
    ios.devicectl.replies["info lockState"] = {"passcodeRequired": True}
    with pytest.raises(DriverError, match="BH is locked: unlock it"):
        dev.check_ready()


def test_simulator_is_always_ready(drv):
    drv.check_ready()


# --- the app on a phone ------------------------------------------------------------------------------

def test_install_needs_a_device_build(phone, tmp_path):
    with pytest.raises(DriverError, match="built for iPhoneSimulator, not a real iPhone .BH.*signed with your team"):
        IOSDriver("BH", signed_app(tmp_path)).install(make_app(tmp_path))


def test_app_lifecycle_uses_devicectl_and_the_agent(dev, phone):
    dev.launch()
    dev.stop()
    dev.clear_data()
    calls = [c[:3] for c in ios.devicectl.calls]
    assert calls == [("device", "process", "launch"), ("device", "uninstall", "app"), ("device", "install", "app")]
    assert "--terminate-existing" in ios.devicectl.calls[0]
    assert [p for p, _ in phone[1].calls] == ["/wait_foreground", "/terminate", "/terminate"]


def test_device_commands_go_through_the_agent(dev, phone, tmp_path):
    phone[0].cmds.clear()
    phone[1].replies["/screenshot"] = {"png": b64encode(b"PNG").decode()}
    dev.screenshot(tmp_path / "s.png")
    dev.set_location(1.5, -2.5)
    dev.open_url("app://x")
    dev.dark_mode(True)
    dev.close()
    assert (tmp_path / "s.png").read_bytes() == b"PNG"
    sent = [(p, {k: v for k, v in b.items() if k != "bundle_id"}) for p, b in phone[1].calls]
    assert sent == [("/screenshot", {}), ("/location", {"lat": 1.5, "lon": -2.5}), ("/open_url", {"url": "app://x"}),
                    ("/appearance", {}), ("/appearance", {"dark": True}), ("/appearance", {"raw": 1})]
    assert not any("simctl" in c for c in phone[0].cmds)


def test_permissions_cannot_be_pregranted_on_a_phone(dev):
    with pytest.raises(DriverError, match="can't pre-grant permissions"):
        dev.grant("camera")


def test_sims_constant_still_has_a_booted_simulator():
    assert any(d["state"] == "Booted" for v in SIMS["devices"].values() for d in v)


def test_a_phone_that_refuses_ui_automation_says_what_to_do(tmp_path, phone, monkeypatch):
    def refuse(cmd, ready, log, timeout, env):
        log.write_text("Failed to initialize for UI testing: Timed out while enabling automation mode.\n")
        raise DriverError("xcodebuild exited before it was ready.")
    monkeypatch.setattr(ios, "start_process", refuse)
    with pytest.raises(DriverError, match=r"BH did not allow UI automation .*Enable UI Automation is on"):
        IOSDriver("BH", signed_app(tmp_path))


def test_other_agent_start_errors_pass_through(tmp_path, phone, monkeypatch):
    def fail(cmd, ready, log, timeout, env):
        log.write_text("error: something else\n")
        raise DriverError("xcodebuild exited before it was ready.")
    monkeypatch.setattr(ios, "start_process", fail)
    with pytest.raises(DriverError, match="^xcodebuild exited before it was ready.$"):
        IOSDriver("BH", signed_app(tmp_path))
