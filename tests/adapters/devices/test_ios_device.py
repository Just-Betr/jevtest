"""Real-iPhone paths of the iOS driver (the simulator paths are in test_ios.py)."""

import json
import plistlib
import zipfile
from base64 import b64encode
from pathlib import Path

import pytest

from jevtest.adapters.devices.ios import IOSDevice
from jevtest.domain.failures import DeviceError
from tests.adapters.devices.conftest import make_app, swap
from tests.conftest import PROGRESS


def signed_app(tmp_path) -> Path:
    """A device build of the app, signed by team TEAM1 (its profile is decoded by the fake `security cms`)."""
    app = make_app(tmp_path / "signed", platforms=("iPhoneOS",))
    (app / "embedded.mobileprovision").write_bytes(b"signed")
    return app


@pytest.fixture
def dev(phone, tmp_path):
    d = IOSDevice("BH", signed_app(tmp_path), PROGRESS)
    d.install(signed_app(tmp_path / "again"))
    phone.ctl.calls.clear()
    phone[1].calls.clear()
    return d


# --- finding phones and teams ----------------------------------------------------------------------


def teams(*entries):
    return plistlib.dumps({"IDEProvisioningTeamByIdentifier": {"a": list(entries)}}).decode()


def test_the_agent_is_signed_by_the_apps_team(phone, tmp_path):
    assert IOSDevice("BH", signed_app(tmp_path), PROGRESS).team == "TEAM1"


@pytest.mark.parametrize(
    ("prefs", "message"),
    [
        ("", r"signed by team TEAM1, which is not signed into Xcode \(signed in: none\).*Settings > Accounts"),
        (teams({"teamID": "T2"}), r"signed by team TEAM1, which is not signed into Xcode \(signed in: T2\)"),
    ],
)
def test_the_apps_team_must_be_signed_into_xcode(phone, tmp_path, prefs, message):
    phone[0].rules["defaults export"] = prefs
    with pytest.raises(DeviceError, match=message):
        IOSDevice("BH", signed_app(tmp_path), PROGRESS)


def test_a_simulator_build_on_a_phone_is_an_error(phone, tmp_path):
    with pytest.raises(DeviceError, match=r"Demo.app is not signed for a real iPhone \(it has no provisioning"):
        IOSDevice("BH", make_app(tmp_path), PROGRESS)


@pytest.mark.parametrize(
    ("profile", "message"),
    [
        ({"TeamIdentifier": []}, "names 0 teams"),
        ({"TeamIdentifier": ["A", "B"]}, r"names 2 teams \(A, B\); expected one"),
    ],
)
def test_the_profile_must_name_one_team(phone, tmp_path, profile, message):
    phone[0].rules["security cms"] = plistlib.dumps(profile).decode()
    with pytest.raises(DeviceError, match=message):
        IOSDevice("BH", signed_app(tmp_path), PROGRESS)


# --- the signed agent ----------------------------------------------------------------------------


def test_agent_on_a_phone_is_reached_through_the_tunnel(tmp_path, phone):
    d = IOSDevice("BH", signed_app(tmp_path), PROGRESS)
    assert (d.physical, d.team, d.host) == (True, "TEAM1", "[fd00::1]")
    assert d._url("/tree") == "http://[fd00::1]:8123/tree"
    cmd, _, run_env = phone.procs[0]
    assert cmd[-1] == "id=00008150-X"
    assert run_env["TEST_RUNNER_JEVTEST_LOCAL_ONLY"] == "0"  # reached over the USB tunnel: the token guards it


def test_ipv4_tunnel_address_has_no_brackets(tmp_path, phone):
    phone.ctl.replies["info details"] = {"connectionProperties": {"tunnelIPAddress": "10.0.0.2"}}
    assert IOSDevice("BH", signed_app(tmp_path), PROGRESS).host == "10.0.0.2"


def test_no_tunnel_is_a_clear_error(tmp_path, phone):
    phone.ctl.replies["info details"] = {"connectionProperties": {}}
    with pytest.raises(DeviceError, match="No connection to BH: unlock it and keep it plugged in"):
        IOSDevice("BH", signed_app(tmp_path), PROGRESS)


def test_agent_is_signed_for_the_phone_when_not_provisioned(phone, monkeypatch, tmp_path):
    swap(monkeypatch, "provisioned_devices", lambda app: set())  # a new phone for this build
    builds = []

    def fake_build(out, destination, signing):
        builds.append((destination, signing))

    swap(monkeypatch, "build_agent_with_xcodebuild", fake_build)
    IOSDevice("BH", signed_app(tmp_path), PROGRESS)
    [(destination, signing)] = builds
    assert destination == "id=00008150-X"
    wanted = {"DEVELOPMENT_TEAM=TEAM1", "JEVTEST_TEAM_SUFFIX=.TEAM1", "-allowProvisioningDeviceRegistration"}
    assert wanted <= set(signing)


def test_agent_build_retries_once_when_xcode_swaps_the_profile(tmp_path, phone, monkeypatch):
    swap(monkeypatch, "provisioned_devices", lambda app: set())
    attempts = []

    def flaky(out, destination, signing):
        attempts.append(destination)
        if len(attempts) == 1:
            raise DeviceError("Build input file cannot be found: ...mobileprovision")

    swap(monkeypatch, "build_agent_with_xcodebuild", flaky)
    IOSDevice("BH", signed_app(tmp_path), PROGRESS)
    assert len(attempts) == 2


# --- talking to it ---------------------------------------------------------------------------------


def test_relocked_phone_gets_a_new_tunnel_address(dev, phone):
    agent = phone[1]
    agent.replies["/tree"] = [
        OSError("no route"),
        json.loads((Path(__file__).parent / "fixtures/ios_login.json").read_text()),
    ]
    phone.ctl.replies["info details"] = {"connectionProperties": {"tunnelIPAddress": "fd00::2"}}
    assert dev.screen().elements
    assert dev.host == "[fd00::2]"


def test_lost_phone_is_a_clear_error(dev, phone):
    phone[1].replies["/tree"] = OSError("no route")
    with pytest.raises(DeviceError, match="Lost the agent on BH during /tree .* unlocked and plugged in"):
        dev.screen()


def test_locked_phone(dev, phone):
    dev.prepare_for_test()
    phone.ctl.replies["info lockState"] = {"passcodeRequired": True}
    with pytest.raises(DeviceError, match="BH is locked: unlock it"):
        dev.prepare_for_test()


def test_simulator_is_always_ready(drv):
    drv.prepare_for_test()


# --- the app on a phone ------------------------------------------------------------------------------


def test_install_needs_a_device_build(phone, tmp_path):
    with pytest.raises(DeviceError, match="built for iPhoneSimulator, not a real iPhone .BH.*signed with your team"):
        IOSDevice("BH", signed_app(tmp_path), PROGRESS).install(make_app(tmp_path))


def test_app_lifecycle_uses_devicectl_and_the_agent(dev, phone):
    dev.launch()
    dev.stop()
    dev.clear_data()
    calls = [c[:3] for c in phone.ctl.calls]
    assert calls == [("device", "process", "launch"), ("device", "uninstall", "app"), ("device", "install", "app")]
    assert "--terminate-existing" in phone.ctl.calls[0]
    assert [p for p, _ in phone[1].calls] == ["/wait_foreground", "/terminate", "/terminate"]


def test_device_commands_go_through_the_agent(dev, phone, tmp_path):
    phone[0].cmds.clear()
    phone[1].replies["/screenshot"] = {"png": b64encode(b"PNG").decode()}
    dev.screenshot(tmp_path / "s.png")
    dev.set_location(1.5, -2.5)
    dev.open_url("app://x")
    dev.dark_mode(on=True)
    dev.close()
    assert (tmp_path / "s.png").read_bytes() == b"PNG"
    sent = [(p, {k: v for k, v in b.items() if k != "bundle_id"}) for p, b in phone[1].calls]
    assert sent == [
        ("/screenshot", {}),
        ("/location", {"lat": 1.5, "lon": -2.5}),
        ("/open_url", {"url": "app://x"}),
        ("/appearance", {}),
        ("/appearance", {"dark": True}),
        ("/appearance", {"raw": 1}),
    ]
    assert not any("simctl" in c for c in phone[0].cmds)


def test_permissions_cannot_be_pregranted_on_a_phone(dev):
    with pytest.raises(DeviceError, match="can't pre-grant permissions"):
        dev.grant("camera")


def test_a_phone_that_refuses_ui_automation_says_what_to_do(tmp_path, phone, monkeypatch):
    def refuse(cmd, ready, log, timeout, env):
        log.write_text("Failed to initialize for UI testing: Timed out while enabling automation mode.\n")
        raise DeviceError("xcodebuild exited before it was ready.")

    swap(monkeypatch, "start_process", refuse)
    with pytest.raises(DeviceError, match=r"BH did not allow UI automation .*Enable UI Automation is on"):
        IOSDevice("BH", signed_app(tmp_path), PROGRESS)


def test_other_agent_start_errors_pass_through(tmp_path, phone, monkeypatch):
    def fail(cmd, ready, log, timeout, env):
        log.write_text("error: something else\n")
        raise DeviceError("xcodebuild exited before it was ready.")

    swap(monkeypatch, "start_process", fail)
    with pytest.raises(DeviceError, match="^xcodebuild exited before it was ready.$"):
        IOSDevice("BH", signed_app(tmp_path), PROGRESS)


def test_an_ipa_is_unpacked_once_for_its_team_and_its_install(phone, tmp_path):
    app = signed_app(tmp_path)
    ipa = tmp_path / "Demo.ipa"
    with zipfile.ZipFile(ipa, "w") as z:
        for f in app.iterdir():
            z.write(f, f"Payload/Demo.app/{f.name}")
    d = IOSDevice("BH", ipa, PROGRESS)
    assert d.install(ipa) == "dev.demo"
    unpacked = list(Path(d._tmp.name).rglob("*.app"))
    assert [p.relative_to(d._tmp.name).as_posix() for p in unpacked] == ["build-0/Payload/Demo.app"]
    assert d.app_path == unpacked[0]
