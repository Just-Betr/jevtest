"""One jevtest run per device: a second run on it would restart the app under the first and put its settings back.

A claim is an exclusive lock on a file named after the device, in the cache folder. The operating system lets go of
it when the run's process ends, however it ends, so a crashed run never leaves a device claimed.
"""

from __future__ import annotations

import fcntl
import os
from typing import TextIO

from jevtest.domain.failures import DeviceError

from .common import cache_dir


class Claims:
    """The devices this run has claimed."""

    def __init__(self) -> None:
        self._held: dict[str, TextIO] = {}

    def claim(self, device_id: str) -> None:
        """Claim the device with this serial or UDID.

        Raises:
            DeviceError: Another run has claimed it.
        """
        if device_id in self._held:
            return
        folder = cache_dir() / "in-use"
        folder.mkdir(parents=True, exist_ok=True)
        f = (folder / f"{device_id}.lock").open("a+")
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            f.seek(0)
            holder = f.read().strip() or "another process"
            f.close()
            raise DeviceError(
                f"another jevtest run ({holder}) is testing it: wait for it to finish, or use another device"
            ) from None
        f.seek(0)
        f.truncate()
        f.write(f"pid {os.getpid()}")
        f.flush()
        self._held[device_id] = f

    def release(self) -> None:
        """Let go of every device this run claimed."""
        for f in self._held.values():
            f.close()
        self._held.clear()
