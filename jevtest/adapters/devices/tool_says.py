"""What the device tools say, recognised in one place.

jevtest drives adb, `am`, `pm`, `dumpsys`, `simctl` and XCUITest, and some of what it needs to know only shows in
their text. Every such text jevtest recognises is here, each with where it was measured, so a tool that changes its
wording is fixed in one place. (Structured output, JSON and plists, is read by `tool_output`.)
"""

from __future__ import annotations

import re

# --- Android ----------------------------------------------------------------------------------------------------

RESUMED = ("topResumedActivity=", "mResumedActivity: ")
"""How `dumpsys activity activities` names the activity on top: Android 13 and newer print `topResumedActivity=…`,
12 and older only `mResumedActivity: …` (measured on 12, 13, 15 and 17; each prints one of the two)."""

RESUMED_GREP = "|".join(RESUMED)
"""A `grep -E` pattern for the line naming the activity on top."""

_RESUMED_APP = re.compile(rf"(?:{'|'.join(map(re.escape, RESUMED))})ActivityRecord\{{\S+ \S+ ([\w.]+)/")


def resumed_app(dumpsys: str) -> str | None:
    """The package of the activity on top, from `dumpsys activity activities`; None when none is (mid-transition)."""
    found = _RESUMED_APP.search(dumpsys)
    return found[1] if found else None


PERMISSION_PROMPT = re.compile(r"com\.(google\.)?android\.permissioncontroller")
"""The package of the system's permission prompt (both names measured)."""

AWAKE_QUERY = "dumpsys power | grep -m1 mWakefulness=; dumpsys window | grep -m1 -E 'isKeyguardShowing='"
"""A shell command whose output says whether the device is awake and unlocked (`awake_and_unlocked`)."""


def awake_and_unlocked(out: str) -> bool:
    """Whether the output of `AWAKE_QUERY` says the screen is on and no lock screen shows."""
    return "mWakefulness=Awake" in out and "isKeyguardShowing=true" not in out


def am_error(out: str) -> str | None:
    """What `am start` says went wrong (its `Error: …` line), or None.

    Android 13 prints it and exits 0; 14 and newer print it and exit 1 (measured).
    """
    found = re.search(r"^Error: (.+)$", out, re.MULTILINE)
    return found[1] if found else None


NO_ACTIVITY_FOR_INTENT = "unable to resolve Intent"
"""In `am start`'s error when no app handles a link (measured on Android 13 and 17)."""


def pm_exception(out: str) -> str | None:
    """The reason in a `pm` Java exception, from its `…Exception: reason` line under "Exception occurred …"."""
    found = re.search(r"^[\w.$]+(?:Exception|Error): (.+)$", out, re.MULTILINE)
    return found[1] if found else None


def granted(permission: str, dumpsys_package: str) -> bool:
    """Whether `dumpsys package` records the runtime permission as granted."""
    return re.search(rf"^\s*{re.escape(permission)}: granted=true", dumpsys_package, re.MULTILINE) is not None


def declared(permission: str, dumpsys_package: str) -> bool:
    """Whether the app declares the permission (it's under "requested permissions:" in `dumpsys package`)."""
    return re.search(rf"^\s*{re.escape(permission)}\s*$", dumpsys_package, re.MULTILINE) is not None


# --- iOS --------------------------------------------------------------------------------------------------------


def no_app_for_url(said: str) -> bool:
    """Whether the agent's open of a URL failed because no app handles it (measured: "…error 115.")."""
    return "LSApplicationWorkspaceErrorDomain error 115" in said


PRIVACY_REFUSED = "Operation not permitted"
"""What `simctl privacy grant` says for a service it doesn't know (measured on iOS 26.5)."""

PRIVACY_KEEPS_APP_RUNNING = frozenset({"location", "location-always", "siri"})
"""The `simctl privacy grant` services that leave a running app running. Every other one ends the app within 0.11 s,
even when the grant changes nothing (measured on iOS 26.5, each service granted twice to a running app)."""


def app_ended(said: str) -> bool:
    """Whether the agent couldn't read the app because it ended meanwhile: XCTest's ui-testing error 10001.

    Measured: `Error Domain=com.apple.dt.xctest.ui-testing.error Code=10001 "Application … is not running"`, from an
    app that crashed on launch while the agent read it.
    """
    return "com.apple.dt.xctest.ui-testing.error Code=10001" in said


UI_TESTING_NOT_AUTHORIZED = ("Authentication canceled", "Not authorized for performing UI testing actions")
"""What XCUITest says when an iPhone asked to authenticate for UI testing and wasn't answered (measured on iOS 27:
the agent's log, `The test runner failed to initialize for UI testing. (Underlying Error: Authentication canceled.
Canceled by user.)`, and an agent call meanwhile, `… Not authorized for performing UI testing actions`)."""


def ui_testing_not_authorized(said: str) -> bool:
    """Whether `said` is XCUITest saying the iPhone didn't authorize UI testing (`UI_TESTING_NOT_AUTHORIZED`)."""
    return any(text in said for text in UI_TESTING_NOT_AUTHORIZED)


AUTOMATION_NOT_ALLOWED = "enabling automation mode"
"""In the agent's log when an iPhone didn't allow UI automation ("Timed out while enabling automation mode")."""

XCODE_TOOL_MISSING = "unable to find utility"
"""What xcrun says for a tool only Xcode has (simctl, devicectl) when the selected developer folder is the Command Line
Tools (measured: `xcrun: error: unable to find utility "simctl", not a developer tool or in PATH`, exit 72)."""
