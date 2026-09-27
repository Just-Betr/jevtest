import pytest

from jevtest.adapters.devices.ios import IOSDevice


@pytest.fixture(autouse=True)
def _ios_devices_leave_nothing_behind(monkeypatch):
    """Most tests make an IOSDevice and drop it: remove each one's temporary folder afterwards."""
    made: list[IOSDevice] = []
    real_init = IOSDevice.__init__

    def tracked(self, *args, **kwargs):
        made.append(self)
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(IOSDevice, "__init__", tracked)
    yield
    for device in made:
        if hasattr(device, "_tmp"):
            device._tmp.cleanup()
