"""Finding and claiming each device a test file names, before any test starts."""

import types
from pathlib import Path

import pytest

from jevtest.adapters.devices import finder
from jevtest.adapters.devices._typing import override
from jevtest.adapters.devices.claim import Claims
from jevtest.domain.failures import DeviceError
from jevtest.domain.kinds import Platform
from tests.adapters.devices.conftest import make_app


class Claimed(Claims):
    """Claims that note each device instead of locking it."""

    def __init__(self) -> None:
        super().__init__()
        self.devices: list[str] = []

    @override
    def claim(self, device_id: str) -> None:
        self.devices.append(device_id)


@pytest.fixture
def found(monkeypatch):
    monkeypatch.setattr(finder, "find_android", lambda d: f"serial-of-{d}")
    monkeypatch.setattr(finder, "check_awake", lambda serial: None)
    monkeypatch.setattr(
        finder, "find_target", lambda d: types.SimpleNamespace(udid=f"udid-of-{d}", name=d, physical=d == "BH")
    )
    return Claimed()


def test_each_platform_is_asked_and_the_device_claimed(found, tmp_path):
    sim_build = make_app(tmp_path / "sim")
    phone_build = make_app(tmp_path / "phone", platforms=("iPhoneOS",))
    assert finder.find_device(found, Platform.ANDROID, "Pixel 9", Path("a.apk")) is False
    assert finder.find_device(found, Platform.IOS, "iPhone 17", sim_build) is False
    assert finder.find_device(found, Platform.IOS, "BH", phone_build) is True
    assert found.devices == ["serial-of-Pixel 9", "udid-of-iPhone 17", "udid-of-BH"]


def test_a_build_for_the_wrong_kind_of_device_is_refused_before_claiming_it(found, tmp_path):
    with pytest.raises(DeviceError, match=r"^Demo.app is built for iPhoneSimulator, not a real iPhone \(BH\)"):
        finder.find_device(found, Platform.IOS, "BH", make_app(tmp_path))
    assert found.devices == []
