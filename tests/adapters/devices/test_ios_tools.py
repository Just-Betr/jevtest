"""The iOS tools: simulators and phones, signing and provisioning, app bundles, building and calling the agent."""

import json
import plistlib
import zipfile
from pathlib import Path

import pytest

from jevtest.adapters.devices import ios_tools
from jevtest.adapters.devices.ios_tools import app_bundle
from jevtest.domain.failures import DeviceError
from tests.adapters.devices.conftest import PHONE, SIMS, make_app, swap


def test_simulators_sorted_newest_first(env):
    sims = ios_tools.simulators()
    assert [(d["name"], d["runtime"]) for d in sims] == [
        ("iPad Air", "iOS-26-5"),
        ("iPhone 17", "iOS-26-5"),
        ("iPhone 16", "iOS-18-0"),
    ]


def test_uses_the_named_booted_simulator_and_never_boots_or_opens_one(env):
    sim = env[0]
    assert ios_tools.find_target("iPhone 16") == ios_tools.Target("A", "iPhone 16", False)
    assert not any(" boot " in c or "open -a" in c for c in sim.cmds)


def test_find_by_exact_name_or_udid_and_it_must_be_booted(env):
    assert ios_tools.find_target("A").udid == "A"
    with pytest.raises(DeviceError, match=r"called 'iphone 16' \(names are exact\). Running: iPhone 16 \(A\)"):
        ios_tools.find_target("iphone 16")
    with pytest.raises(DeviceError, match="No booted simulator or connected iPhone called 'iPad Air'"):
        ios_tools.find_target("iPad Air")  # exists, but is not booted


def test_a_name_two_devices_share_is_an_error(env):
    env[0].rules["list devices"] = json.dumps(
        {
            "devices": {
                "com.apple.CoreSimulator.SimRuntime.iOS-26-5": [
                    {"name": "iPhone 17 Pro", "udid": "X", "state": "Booted"},
                    {"name": "iPhone 17 Pro", "udid": "Y", "state": "Booted"},
                ]
            }
        }
    )
    with pytest.raises(DeviceError, match=r"Several devices are called 'iPhone 17 Pro' \(X, Y\): name one by its UDID"):
        ios_tools.find_target("iPhone 17 Pro")


def test_find_with_nothing_running(env):
    env[0].rules["list devices"] = json.dumps({"devices": {}})
    with pytest.raises(DeviceError, match="called 'A' .*Running: none"):
        ios_tools.find_target("A")


def test_free_port_is_usable():
    assert 0 < ios_tools.free_port() < 65536


def test_app_bundle_from_dir_zip_and_ipa(tmp_path):
    app = make_app(tmp_path / "src")
    assert app_bundle(app, tmp_path / "w") == app
    for name in ("app.zip", "app.ipa"):
        archive = tmp_path / name
        with zipfile.ZipFile(archive, "w") as z:
            z.write(app / "Info.plist", "Payload/Demo.app/Info.plist")
        found = app_bundle(archive, tmp_path / f"w-{name}")
        assert found.name == "Demo.app"


def test_app_bundle_rejects_bad_input(tmp_path):
    (tmp_path / "empty.zip").write_bytes(b"not a zip")
    empty = tmp_path / "noapp.zip"
    with zipfile.ZipFile(empty, "w") as z:
        z.writestr("readme.txt", "x")
    for path in (tmp_path / "empty.zip", empty, tmp_path / "missing.app", tmp_path / "x.apk"):
        with pytest.raises(DeviceError, match="needs an .app"):
            app_bundle(path, tmp_path / "w")


def test_http_post_roundtrip(monkeypatch):
    class R:
        def read(self):
            return b'{"ok": true}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    seen = []
    monkeypatch.setattr(ios_tools.urllib.request, "urlopen", lambda req, timeout: seen.append((req, timeout)) or R())
    assert ios_tools.http_post("http://127.0.0.1:1/x", {"a": 1}, timeout=3) == {"ok": True}
    assert json.loads(seen[0][0].data) == {"a": 1} and seen[0][1] == 3


def test_phones_lists_connected_real_iphones(env):
    ios_tools.devicectl.phones = [
        PHONE,
        dict(PHONE, connectionProperties={"tunnelState": "disconnected"}),  # not reachable now
        dict(PHONE, hardwareProperties={"reality": "simulated", "platform": "iOS", "udid": "S"}),
        dict(PHONE, hardwareProperties={"reality": "physical", "platform": "watchOS", "udid": "W"}),
    ]
    assert ios_tools.phones() == [{"udid": "00008150-X", "name": "BH"}]


def test_find_target_by_phone_name(phone):
    assert ios_tools.find_target("BH") == ios_tools.Target("00008150-X", "BH", True)
    assert ios_tools.find_target("00008150-X").physical is True


def test_provisioned_devices(env, tmp_path):
    app = tmp_path / "R.app"
    app.mkdir()
    assert ios_tools.provisioned_devices(app) == set()  # no profile: a simulator build
    (app / "embedded.mobileprovision").write_bytes(b"signed")
    env[0].rules["security cms"] = plistlib.dumps({"ProvisionedDevices": ["U1", "U2"]}).decode()
    assert ios_tools.provisioned_devices(app) == {"U1", "U2"}
    env[0].rules["security cms"] = "not a plist"
    with pytest.raises(DeviceError, match="R.app's embedded.mobileprovision can't be read"):
        ios_tools.provisioned_devices(app)


def test_devicectl_returns_the_json_result(monkeypatch):
    seen = []

    def fake_run(cmd, timeout):
        seen.append(cmd)
        Path(cmd[cmd.index("--json-output") + 1]).write_text(json.dumps({"result": {"devices": [1]}}))

    swap(monkeypatch, "run", fake_run)
    assert ios_tools.devicectl("list", "devices") == {"devices": [1]}
    assert seen[0][:4] == ["xcrun", "devicectl", "list", "devices"]


def test_agent_build_command(monkeypatch, tmp_path):
    seen = []
    swap(monkeypatch, "run", lambda cmd, timeout: seen.append(cmd))
    ios_tools.build_agent_with_xcodebuild(tmp_path / "out", "id=U", ["X=1"])
    assert seen[0][:2] == ["xcodebuild", "build-for-testing"] and seen[0][-1] == "X=1"


def test_sims_constant_still_has_a_booted_simulator():
    assert any(d["state"] == "Booted" for v in SIMS["devices"].values() for d in v)
