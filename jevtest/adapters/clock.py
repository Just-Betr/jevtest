"""Real time, for the runner's `Clock` port."""

from __future__ import annotations

import time


class SystemClock:
    """The system's monotonic clock."""

    def now(self) -> float:
        """Seconds on a monotonic clock."""
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        """Wait."""
        time.sleep(seconds)
