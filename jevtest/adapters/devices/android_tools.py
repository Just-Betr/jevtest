"""Android tooling: the SDK's tools, the connected devices, and building jevtest's on-device agent.

Everything here runs on the computer; nothing talks to the app. `android.AndroidDevice` uses it.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
import urllib.request
import zipfile
from pathlib import Path

from jevtest.domain.failures import DeviceError

from .cache import AgentBuilds, BuildInUse, cache_dir, digest
from .common import Progress, run

AGENT_SRC = Path(__file__).resolve().parent / "android_agent"
"""The on-device agent's source, built into an APK the first time a version is needed."""


def sdk_root() -> Path | None:
    """The Android SDK: ``$ANDROID_HOME``, ``$ANDROID_SDK_ROOT``, or Android Studio's default place."""
    for var in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        if os.environ.get(var):
            return Path(os.environ[var])
    for p in (Path.home() / "Library/Android/sdk", Path.home() / "Android/Sdk"):
        if p.exists():
            return p
    return None


def _tool(name: str, sdk_subpath: str, hint: str) -> str:
    """An SDK tool: on the PATH, or the highest version in the SDK."""
    found = shutil.which(name)
    if found:
        return found
    root = sdk_root()
    matches = sorted(root.glob(sdk_subpath)) if root else []
    if not matches:
        raise DeviceError(f"{name} not found. {hint}")
    return str(matches[-1])  # highest version for build-tools/*


def adb_path() -> str:
    """Adb."""
    return _tool("adb", "platform-tools/adb", "Install Android platform-tools or set ANDROID_HOME.")


def aapt2_path() -> str:
    """aapt2, which reads an APK's package name."""
    return _tool("aapt2", "build-tools/*/aapt2", "Install Android SDK build-tools (needed to read the APK).")


def bundletool_path() -> str:
    """bundletool, which installs an Android App Bundle (.aab).

    Raises:
        DeviceError: It isn't on the PATH.
    """
    found = shutil.which("bundletool")
    if not found:
        raise DeviceError("bundletool is required to install .aab files (brew install bundletool)")
    return found


def devices() -> list[str]:
    """The serials of the connected devices that are ready (not offline or unauthorized)."""
    out = run([adb_path(), "devices"], timeout=20)
    return [line.split()[0] for line in out.splitlines()[1:] if line.strip().endswith("\tdevice")]


def device_names(serial: str) -> list[str]:
    """What a test file may call this device: its serial, its model ("Pixel 4a"), its emulator's AVD name."""
    names = [serial, run([adb_path(), "-s", serial, "shell", "getprop", "ro.product.model"], check=False).strip()]
    if serial.startswith("emulator-"):
        names.append(run([adb_path(), "-s", serial, "emu", "avd", "name"], check=False).split("\n")[0].strip())
    return [n for n in names if n]


def pick_device(wanted: str, serials: list[str]) -> str:
    """The one connected device with exactly this serial, model or AVD name."""
    named = {serial: device_names(serial) for serial in serials}
    matches = [serial for serial, names in named.items() if wanted in names]
    listed = "; ".join(" / ".join(names) for names in named.values()) or "none"
    if not matches:
        raise DeviceError(f"No connected Android device called '{wanted}' (names are exact). Connected: {listed}")
    if len(matches) > 1:
        raise DeviceError(
            f"Several connected Android devices are called '{wanted}' ({', '.join(matches)}): name one by its serial"
        )
    return matches[0]


def http_get(url: str, timeout: float) -> str:
    """A GET to the agent on its forwarded localhost port."""
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        body: bytes = resp.read()
    return body.decode()


# Devices tested at the same time share the cached agent: one builds it, the others wait for it.
AGENT_LOCK = threading.Lock()


AGENT_BUILDS = AgentBuilds("android-agent-", ".apk", beside=(".idsig", ".part", ".idsig.part"))
"""The agent APKs in the cache, with the ``.idsig`` apksigner writes beside each, and what a build that stopped
partway left (see `_put_in_place`)."""


def build_agent(progress: Progress) -> BuildInUse:
    """The agent APK, built once per source version and cached (older versions removed); one device at a time.

    The caller closes it once the APK is installed, so no other jevtest run removes it before then.
    """
    version = digest(AGENT_SRC)
    with AGENT_LOCK:
        apk = AGENT_BUILDS.use(version)
        try:
            if not apk.path.exists():
                _build_agent(apk.path, version, progress)
            AGENT_BUILDS.drop_older(keep=version)
        except BaseException:
            apk.close()
            raise
        return apk


def _build_agent(apk: Path, version: str, progress: Progress) -> None:
    """Compile the on-device agent with the SDK's own tools (no Gradle) into `apk`.

    `apk` appears only once it's whole: a build that stops partway leaves nothing a later run would take for built.
    """
    root = sdk_root()
    tools = sorted(root.glob("build-tools/*")) if root else []
    jars = sorted(root.glob("platforms/android-*/android.jar")) if root else []
    if not tools or not jars:
        raise DeviceError(
            "Android SDK build-tools and a platform are needed to build the jevtest agent: install them in Android "
            "Studio's SDK Manager"
        )
    missing = [tool for tool in ("javac", "keytool") if shutil.which(tool) is None]
    if missing:
        raise DeviceError(
            f"{' and '.join(missing)} not found: jevtest builds its Android agent with a JDK (11 or newer). Install "
            "one and put its bin folder on the PATH"
        )
    bt, jar = tools[-1], str(jars[-1])
    progress("building the Android agent (one time, a few seconds)")
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        run(
            [
                "javac",
                "--release",
                "11",
                "-cp",
                jar,
                "-d",
                str(t / "classes"),
                *map(str, sorted(AGENT_SRC.rglob("*.java"))),
            ]
        )
        run(
            [
                str(bt / "d8"),
                "--min-api",
                "24",
                "--lib",
                jar,
                "--output",
                tmp,
                *map(str, sorted((t / "classes").rglob("*.class"))),
            ]
        )
        run(
            [
                str(bt / "aapt2"),
                "link",
                "--manifest",
                str(AGENT_SRC / "AndroidManifest.xml"),
                "-I",
                jar,
                "--version-name",
                version,
                "-o",
                str(t / "base.apk"),
            ]
        )
        with zipfile.ZipFile(t / "base.apk", "a") as z:
            z.write(t / "classes.dex", "classes.dex")
        run([str(bt / "zipalign"), "-f", "4", str(t / "base.apk"), str(t / "aligned.apk")])
        cache_dir().mkdir(parents=True, exist_ok=True)
        keystore = cache_dir() / "jevtest-debug.keystore"
        if not keystore.exists():
            run(
                [
                    "keytool",
                    "-genkeypair",
                    "-keystore",
                    str(t / "new.keystore"),
                    "-storepass",
                    "android",
                    "-alias",
                    "jevtest",
                    "-keypass",
                    "android",
                    "-keyalg",
                    "RSA",
                    "-validity",
                    "10000",
                    "-dname",
                    "CN=jevtest",
                ]
            )
            _put_in_place(t / "new.keystore", keystore)
        run(
            [
                str(bt / "apksigner"),
                "sign",
                "--ks",
                str(keystore),
                "--ks-pass",
                "pass:android",
                "--out",
                str(t / "signed.apk"),
                str(t / "aligned.apk"),
            ]
        )
        signature, beside = t / "signed.apk.idsig", apk.with_name(apk.name + ".idsig")
        if signature.exists():  # apksigner writes one when it signs with v4 (build-tools 37 does: measured)
            _put_in_place(signature, beside)
        else:
            beside.unlink(missing_ok=True)  # an earlier build's would not match this APK
        _put_in_place(t / "signed.apk", apk)  # last: the APK is there only with all of it


def _put_in_place(made: Path, final: Path) -> None:
    """Move a file made elsewhere to `final` in one step, so `final` is never there half written.

    The temporary folder may be on another disk, where a move is a copy: the copy is made next to `final`, as
    ``….part``, and renamed, which is one step.
    """
    part = final.with_name(final.name + ".part")
    shutil.move(made, part)
    part.replace(final)
