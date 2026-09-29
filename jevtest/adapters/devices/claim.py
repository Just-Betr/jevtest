"""One jevtest run per device: a second run on it would restart the app under the first and put its settings back.

A claim is an exclusive lock on a file named after the device, in the cache folder. The operating system lets go of
it when the run's process ends, however it ends, so a crashed run never leaves a device claimed.
"""

from __future__ import annotations

import os
from typing import IO

from jevtest.domain.failures import DeviceError

from .cache import cache_dir, in_use_dir, try_lock


class Claims:
    """The devices this run has claimed."""

    def __init__(self) -> None:
        self._held: dict[str, IO[str]] = {}

    def claim(self, device_id: str) -> None:
        """Claim the device with this serial or UDID.

        Raises:
            DeviceError: Another run has claimed it, or the cache folder can't hold the claim.
        """
        if device_id in self._held:
            return
        try:
            self._held[device_id] = self._lock(device_id)
        except OSError as e:
            raise DeviceError(
                f"can't mark it as in use in {cache_dir()} ({e.strerror or e}): make that folder writable, or set "
                "JEVTEST_CACHE to one that is"
            ) from None

    @staticmethod
    def _lock(device_id: str) -> IO[str]:
        """The device's lock file, locked for this run and saying so.

        Raises:
            DeviceError: Another run holds it.
            OSError: The file can't be made, locked or written.
        """
        path = in_use_dir() / f"{device_id}.lock"
        f = try_lock(path)
        if f is None:
            holder = path.read_text(encoding="utf-8").strip() or "another process"
            raise DeviceError(
                f"another jevtest run ({holder}) is testing it: wait for it to finish, or use another device"
            )
        try:
            f.seek(0)
            f.truncate()
            f.write(f"pid {os.getpid()}")
            f.flush()
        except BaseException:
            f.close()
            raise
        return f

    def release(self) -> None:
        """Let go of every device this run claimed."""
        for f in self._held.values():
            f.close()
        self._held.clear()
