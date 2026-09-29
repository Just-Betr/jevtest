"""The Android tools: the SDK, connected devices, building the agent, and calling it."""

import shutil
import urllib.request
import zipfile
from pathlib import Path

import pytest

from jevtest.adapters.devices import android_tools
from jevtest.adapters.devices.cache import digest
from jevtest.domain.failures import DeviceError
from tests.adapters.devices.conftest import swap
from tests.conftest import PROGRESS, PROGRESS_MESSAGES


def test_sdk_root(monkeypatch, tmp_path):
    monkeypatch.setenv("ANDROID_HOME", str(tmp_path))
    assert android_tools.sdk_root() == tmp_path
    monkeypatch.delenv("ANDROID_HOME")
    monkeypatch.delenv("ANDROID_SDK_ROOT", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert android_tools.sdk_root() is None
    (tmp_path / "Android/Sdk").mkdir(parents=True)
    assert android_tools.sdk_root() == tmp_path / "Android/Sdk"


def test_tools_found_in_sdk(monkeypatch, tmp_path):
    monkeypatch.setattr(shutil, "which", lambda n: None)
    monkeypatch.setenv("ANDROID_HOME", str(tmp_path))
    for version in ("34.0.0", "36.1.0"):
        (tmp_path / "build-tools" / version).mkdir(parents=True)
        (tmp_path / "build-tools" / version / "aapt2").write_text("")
    (tmp_path / "platform-tools").mkdir()
    (tmp_path / "platform-tools/adb").write_text("")
    assert android_tools.aapt2_path().endswith("36.1.0/aapt2")
    assert android_tools.adb_path() == str(tmp_path / "platform-tools/adb")


def test_tool_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(shutil, "which", lambda n: None)
    swap(monkeypatch, "sdk_root", lambda: None)
    with pytest.raises(DeviceError, match="adb not found"):
        android_tools.adb_path()


def test_devices_only_lists_ready_ones(adb):
    assert android_tools.devices() == ["emulator-5554"]


def test_build_agent_is_cached(monkeypatch, tmp_path):
    monkeypatch.setenv("JEVTEST_CACHE", str(tmp_path))
    apk = tmp_path / f"android-agent-{digest(android_tools.AGENT_SRC)}.apk"
    apk.write_text("")
    with android_tools.build_agent(PROGRESS) as built:
        assert built.path == apk


def test_build_agent_with_sdk_tools(monkeypatch, tmp_path):

    monkeypatch.setenv("JEVTEST_CACHE", str(tmp_path / "cache"))
    sdk = tmp_path / "sdk"
    (sdk / "build-tools/37.0.0").mkdir(parents=True)
    (sdk / "platforms/android-37").mkdir(parents=True)
    (sdk / "platforms/android-37/android.jar").write_text("")
    swap(monkeypatch, "sdk_root", lambda: sdk)
    seen = []

    def fake_run(cmd, **kw):
        seen.append(Path(cmd[0]).name)
        if cmd[0] == "javac":
            out = Path(cmd[cmd.index("-d") + 1])
            out.mkdir(parents=True)
            (out / "Agent.class").write_text("")
        elif cmd[0].endswith("d8"):
            (Path(cmd[cmd.index("--output") + 1]) / "classes.dex").write_text("dex")
        elif cmd[0].endswith("aapt2"):
            with zipfile.ZipFile(cmd[cmd.index("-o") + 1], "w") as z:
                z.writestr("AndroidManifest.xml", "m")
        elif cmd[0].endswith("zipalign"):
            Path(cmd[-1]).write_bytes(Path(cmd[-2]).read_bytes())
        elif cmd[0] == "keytool":
            Path(cmd[cmd.index("-keystore") + 1]).write_text("ks")
        elif cmd[0].endswith("apksigner"):
            if signer_fails:
                raise DeviceError("apksigner stopped")  # e.g. Ctrl-C partway
            out = Path(cmd[cmd.index("--out") + 1])
            out.write_bytes(Path(cmd[-1]).read_bytes())
            if signs_v4:
                out.with_name(out.name + ".idsig").write_text("v4")
        return ""

    swap(monkeypatch, "run", fake_run)
    signer_fails, signs_v4 = False, True
    with android_tools.build_agent(PROGRESS) as built:
        apk = built.path
    assert seen == ["javac", "d8", "aapt2", "zipalign", "keytool", "apksigner"]
    assert "classes.dex" in zipfile.ZipFile(apk).namelist()
    assert apk.with_name(apk.name + ".idsig").read_text() == "v4"
    assert (tmp_path / "cache" / "jevtest-debug.keystore").read_text() == "ks"
    assert sorted(p.name for p in (tmp_path / "cache").iterdir() if p.name.endswith(".part")) == []
    seen.clear()
    apk.unlink()
    signer_fails = True
    with pytest.raises(DeviceError, match="apksigner stopped"):
        android_tools.build_agent(PROGRESS)
    assert not apk.exists()  # nothing a later run would take for a built agent
    signer_fails, signs_v4 = False, False  # older build-tools sign without v4
    seen.clear()
    with android_tools.build_agent(PROGRESS) as built:
        assert "classes.dex" in zipfile.ZipFile(built.path).namelist()
    assert "keytool" not in seen and "apksigner" in seen  # built again, with the same key
    assert not apk.with_name(apk.name + ".idsig").exists()  # the first build's would not match
    assert "building the Android agent (one time, a few seconds)" in PROGRESS_MESSAGES


def test_build_agent_needs_sdk(monkeypatch, tmp_path):
    monkeypatch.setenv("JEVTEST_CACHE", str(tmp_path))
    swap(monkeypatch, "sdk_root", lambda: None)
    with pytest.raises(DeviceError, match="build-tools and a platform"):
        android_tools.build_agent(PROGRESS)


def test_http_get(monkeypatch):
    class R:
        def read(self):
            return b"ok"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout: R())
    assert android_tools.http_get("http://x", timeout=1) == "ok"
