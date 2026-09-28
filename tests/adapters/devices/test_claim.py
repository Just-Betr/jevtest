import os
import subprocess
import sys

import pytest

from jevtest.adapters.devices.claim import Claims
from jevtest.domain.failures import DeviceError


@pytest.fixture(autouse=True)
def cache(tmp_path, monkeypatch):
    monkeypatch.setenv("JEVTEST_CACHE", str(tmp_path))
    return tmp_path


def hold_in_another_process(cache, device_id):
    """Another jevtest run: a process that claims the device and keeps it until its stdin closes."""
    code = (
        "import sys; from jevtest.adapters.devices.claim import Claims; "
        f"c = Claims(); c.claim({device_id!r}); print('held', flush=True); sys.stdin.read()"
    )
    p = subprocess.Popen(
        [sys.executable, "-c", code],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        env={**os.environ, "JEVTEST_CACHE": str(cache)},
    )
    assert p.stdout is not None and p.stdout.readline().strip() == "held"
    return p


def test_a_device_another_run_claimed_is_refused_until_that_run_ends(cache):
    other = hold_in_another_process(cache, "emulator-5554")
    try:
        with pytest.raises(DeviceError, match=rf"^another jevtest run \(pid {other.pid}\) is testing it: wait"):
            Claims().claim("emulator-5554")
    finally:
        other.communicate("")  # the other run ends: the operating system lets go of its claim
    mine = Claims()
    mine.claim("emulator-5554")
    mine.release()


def test_a_run_claims_a_device_once_however_often_it_is_named(cache):
    claims = Claims()
    claims.claim("A")
    claims.claim("A")  # the same device by another name, or in another file
    assert (cache / "in-use" / "A.lock").read_text() == f"pid {os.getpid()}"
    claims.release()
    later = Claims()
    later.claim("A")  # released: free for the next run
    later.release()
