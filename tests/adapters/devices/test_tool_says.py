"""The tool texts jevtest recognises, each from real output (the source says where it was measured)."""

import pytest

from jevtest.adapters.devices import tool_says as says


@pytest.mark.parametrize(
    ("dumpsys", "app"),
    [
        ("    topResumedActivity=ActivityRecord{97583c0 u0 dev.demo/.MainActivity t33}\n", "dev.demo"),  # 13+
        (
            "    mResumedActivity: ActivityRecord{2b5d5e4 u0 com.google.android.apps.nexuslauncher/.Nexus t11}\n",
            "com.google.android.apps.nexuslauncher",
        ),  # 12 and older
        ("", None),  # none on top: mid-transition
    ],
)
def test_the_resumed_app_on_every_android(dumpsys, app):
    assert says.resumed_app(dumpsys) == app
    assert all(marker in says.RESUMED_GREP for marker in says.RESUMED)


def test_awake_and_unlocked():
    assert says.awake_and_unlocked("  mWakefulness=Awake\n    isKeyguardShowing=false\n")
    assert not says.awake_and_unlocked("  mWakefulness=Dozing\n    isKeyguardShowing=true\n")
    assert not says.awake_and_unlocked("  mWakefulness=Awake\n    isKeyguardShowing=true\n")


def test_what_am_start_says():
    out = "Starting: Intent { dat=x:// }\nError: Activity not started, unable to resolve Intent { dat=x:// }\n"
    said = says.am_error(out)
    assert said is not None and says.NO_ACTIVITY_FOR_INTENT in said
    assert says.am_error("Starting: Intent\nStatus: ok\n") is None


def test_the_reason_in_a_pm_exception():
    out = (
        "\nException occurred while executing 'grant':\n"
        "java.lang.SecurityException: Package dev.demo has not requested permission android.permission.X\n"
        "\tat com.android.server.pm..."
    )
    assert says.pm_exception(out) == "Package dev.demo has not requested permission android.permission.X"
    assert says.pm_exception("adb: device offline") is None


def test_granted_and_declared_from_dumpsys_package():
    record = (
        "    requested permissions:\n      android.permission.CAMERA\n      android.permission.RECORD_AUDIO\n"
        "    runtime permissions:\n      android.permission.CAMERA: granted=true, flags=[ USER_SET ]\n"
        "      android.permission.RECORD_AUDIO: granted=false, flags=[ ]\n"
    )
    assert says.granted("android.permission.CAMERA", record)
    assert not says.granted("android.permission.RECORD_AUDIO", record)
    assert says.declared("android.permission.RECORD_AUDIO", record)
    assert not says.declared("android.permission.READ_CONTACTS", record)


def test_no_app_for_a_url_on_ios():
    assert says.no_app_for_url("(LSApplicationWorkspaceErrorDomain error 115.)")
    assert not says.no_app_for_url("Not a URL")
