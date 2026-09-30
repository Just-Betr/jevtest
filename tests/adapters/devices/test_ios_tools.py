"""The iOS tools: simulators and phones, signing and provisioning, app bundles, building and calling the agent."""

import json
import plistlib
import shutil
import urllib.request
import zipfile
from pathlib import Path

import pytest

from jevtest.adapters.devices import ios_tools
from jevtest.adapters.devices.common import ToolFailed
from jevtest.adapters.devices.ios_tools import app_bundle, info_plist, orphaned_agents
from jevtest.domain.failures import DeviceError
from tests.adapters.devices.conftest import PHONE, SIMS, make_app, swap


def test_simulators_sorted_newest_first(env):
    sims = ios_tools.simulators()
    assert [(d.name, d.runs) for d in sims] == [
        ("iPad Air", "iOS 26.5"),
        ("iPhone 17", "iOS 26.5"),
        ("iPhone 16", "iOS 18.0"),
    ]
    assert [d.version for d in sims] == [(26, 5), (26, 5), (18, 0)]  # numbers: iOS 9 would sort below 18


def test_a_simulator_list_is_read_once_per_lookup_even_when_nothing_matches(env):
    with pytest.raises(DeviceError, match="Running: "):
        ios_tools.find_target("nothing")
    assert sum("simctl list devices" in c for c in env[0].cmds) == 1
    assert sum(c[:2] == ("list", "devices") for c in env.ctl.calls) == 1


def test_uses_the_named_booted_simulator_and_never_boots_or_opens_one(env):
    sim = env[0]
    assert ios_tools.find_target("iPhone 16") == ios_tools.Target("A", "iPhone 16", False, runs="iOS 18.0")
    assert not any(" boot " in c or "open -a" in c for c in sim.cmds)


def test_find_by_exact_name_or_udid_and_it_must_be_booted(env):
    assert ios_tools.find_target("A").udid == "A"
    with pytest.raises(DeviceError, match=r"called 'iphone 16' \(names are exact\). Running: iPhone 16 \(A\)"):
        ios_tools.find_target("iphone 16")
    with pytest.raises(DeviceError, match=r"The simulator 'iPad Air' isn't booted: boot it \(xcrun simctl boot"):
        ios_tools.find_target("iPad Air")  # exists, but is not booted


def test_a_name_two_devices_share_is_an_error(env):
    env[0].rules["list devices"] = json.dumps(
        {
            "devices": {
                "com.apple.CoreSimulator.SimRuntime.iOS-26-5": [
                    {"name": "iPhone 17 Pro", "udid": "X", "state": "Booted"}
                ],
                "com.apple.CoreSimulator.SimRuntime.iOS-26-3": [
                    {"name": "iPhone 17 Pro", "udid": "Y", "state": "Booted"}
                ],
            }
        }
    )
    env.ctl.phones = [dict(PHONE, deviceProperties={"name": "iPhone 17 Pro"})]
    with pytest.raises(
        DeviceError,
        match=r"Several devices are called 'iPhone 17 Pro' \(X \(iOS 26\.5\), Y \(iOS 26\.3\), 00008150-X \(an iPhone\)\): "
        "name one by its UDID",
    ):
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
            z.writestr("Payload/Demo.app/PlugIns/Share.appex/Watch.app/Info.plist", "an app inside the app")
        found = app_bundle(archive, tmp_path / f"w-{name}")
        assert found == tmp_path / f"w-{name}" / "Payload" / "Demo.app"
        assert (found / "Info.plist").is_file()


def test_the_info_plist_is_read_from_inside_an_archive_without_unpacking_it(tmp_path):
    app = make_app(tmp_path / "src", bundle_id="dev.zipped")
    archive = tmp_path / "app.ipa"
    with zipfile.ZipFile(archive, "w") as z:
        z.write(app / "Info.plist", "Payload/Demo.app/Info.plist")
        z.writestr("Payload/Demo.app/Watch/W.app/Info.plist", plistlib.dumps({"CFBundleIdentifier": "dev.watch"}))
    before = set(tmp_path.rglob("*"))
    assert info_plist(archive)["CFBundleIdentifier"] == "dev.zipped"  # the outer app's, not the watch app's
    assert info_plist(app)["CFBundleIdentifier"] == "dev.zipped"
    assert set(tmp_path.rglob("*")) == before


def test_a_build_with_no_info_plist_says_so(tmp_path):
    (tmp_path / "Bare.app").mkdir()
    archive = tmp_path / "bare.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("Bare.app/binary", "x")
    for build in (tmp_path / "Bare.app", archive):
        with pytest.raises(DeviceError, match=f"^{build.name} has no readable Info.plist"):
            info_plist(build)


def test_app_bundle_rejects_bad_input(tmp_path):
    (tmp_path / "empty.zip").write_bytes(b"not a zip")
    empty = tmp_path / "noapp.zip"
    with zipfile.ZipFile(empty, "w") as z:
        z.writestr("readme.txt", "x")
    for path in (tmp_path / "empty.zip", empty, tmp_path / "missing.app", tmp_path / "x.apk"):
        with pytest.raises(DeviceError, match="needs an .app"):
            app_bundle(path, tmp_path / "w")
        with pytest.raises(DeviceError, match="needs an .app"):
            info_plist(path)


def test_http_post_roundtrip(monkeypatch):
    class R:
        def read(self):
            return b'{"ok": true}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    seen = []

    def urlopen(req, timeout):
        seen.append((req, timeout))
        return R()

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    assert ios_tools.http_post("http://127.0.0.1:1/x", {"a": 1}, timeout=3, token="t0k") == {"ok": True}
    req, timeout = seen[0]
    assert json.loads(req.data) == {"a": 1} and timeout == 3
    assert req.get_header("X-jevtest-token") == "t0k"  # the agent refuses a request without the run's token


def test_phones_lists_paired_real_iphones_and_whether_each_is_connected(env):
    env.ctl.phones = [
        PHONE,
        dict(PHONE, connectionProperties={"tunnelState": "disconnected"}),  # not reachable now
        dict(PHONE, hardwareProperties={"reality": "simulated", "platform": "iOS", "udid": "S"}),
        dict(PHONE, hardwareProperties={"reality": "physical", "platform": "watchOS", "udid": "W"}),
    ]
    assert ios_tools.phones() == [
        ios_tools.Phone("00008150-X", "BH", connected=True),
        ios_tools.Phone("00008150-X", "BH", connected=False),
    ]


def test_a_paired_iphone_that_isnt_connected_says_so(env):
    env.ctl.phones = [dict(PHONE, connectionProperties={"tunnelState": "disconnected"})]  # asleep, locked, unplugged
    with pytest.raises(DeviceError, match="^The iPhone 'BH' is paired but not connected: plug it in with USB, unlock"):
        ios_tools.find_target("BH")


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
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")  # a Mac: CI runs on Linux

    def fake_run(cmd, timeout, check=True):
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


def test_only_the_per_run_logs_of_0_9_1_are_removed(tmp_path, monkeypatch):
    monkeypatch.setenv("JEVTEST_CACHE", str(tmp_path))
    names = ("ios-agent-8123.log", "ios-agent-D9656DC8-8F3B-4E0A-9C1D-1234567890AB.log", "ios-agent-00008150-X.log")
    for name in names:
        (tmp_path / name).write_text("")
    ios_tools.remove_port_logs()
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(names[1:])  # each device's own log stays


def test_ios_on_a_machine_without_xcrun_says_it_needs_a_mac_with_xcode(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(DeviceError, match="^iOS needs a Mac with Xcode: xcrun isn't on the PATH$"):
        ios_tools.simulators()


def test_ios_with_only_the_command_line_tools_says_to_install_and_select_xcode(monkeypatch):
    """Measured with DEVELOPER_DIR=/Library/Developer/CommandLineTools: xcrun exits 72 with this."""
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    said = 'xcrun: error: unable to find utility "simctl", not a developer tool or in PATH'

    def no_simctl(cmd, timeout, check=True):
        raise ToolFailed(cmd, 72, said)

    swap(monkeypatch, "run", no_simctl)
    with pytest.raises(
        DeviceError, match=r"^iOS needs Xcode, not only its Command Line Tools: xcrun can't find simctl\."
    ):
        ios_tools.simulators()
    swap(monkeypatch, "run", lambda cmd, timeout, check=True: (_ for _ in ()).throw(ToolFailed(cmd, 1, "other")))
    with pytest.raises(ToolFailed):  # any other failure is passed on as it was
        ios_tools.simulators()


def test_orphaned_agents_are_the_jevtest_agents_no_run_owns_on_that_device():
    """Measured: `kill -9` on a run left its xcodebuild running, its parent then launchd (pid 1)."""
    agent = (
        "xcodebuild test-without-building -xctestrun /Users/me/.cache/jevtest/ios-agent-fb19/Build/Products/J.xctestrun"
    )
    ps = "\n".join(
        [
            f"60689     1 /Applications/Xcode.app/Contents/Developer/usr/bin/{agent} -destination id=SIM-A",
            f"60700 60600 /Applications/Xcode.app/Contents/Developer/usr/bin/{agent} -destination id=SIM-A",  # a live run's
            f"60701     1 /Applications/Xcode.app/Contents/Developer/usr/bin/{agent} -destination id=SIM-B",  # another device
            "60702     1 xcodebuild test-without-building -xctestrun /tmp/theirs.xctestrun -destination id=SIM-A",
            "  1     0 /sbin/launchd",
            "",
        ]
    )
    assert orphaned_agents(ps, "SIM-A") == [60689]
    assert orphaned_agents("", "SIM-A") == []
