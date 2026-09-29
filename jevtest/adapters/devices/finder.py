"""Finding the device a test file names, before any test starts, and claiming it for this run."""

from __future__ import annotations

from pathlib import Path

from jevtest.domain.kinds import Platform

from .android import check_awake, find_android
from .claim import Claims
from .ios_tools import check_build, find_target, info_plist


def find_device(claims: Claims, platform: Platform, device: str, app: Path) -> bool:
    """Claim the one running device of `platform` called `device`; whether it's a real iPhone.

    Raises:
        DeviceError: No running device, or several, are called that; it's asleep or locked (Android); another
            jevtest run is using it; or `app` is the wrong kind of build for it (a simulator build for an iPhone).
    """
    if platform is Platform.ANDROID:
        serial = find_android(device)
        check_awake(serial)  # asleep before the run: one clear error, not one failure per test
        claims.claim(serial)
        return False
    target = find_target(device)
    check_build(app, info_plist(app), physical=target.physical, device=target.name)
    claims.claim(target.udid)
    return target.physical
