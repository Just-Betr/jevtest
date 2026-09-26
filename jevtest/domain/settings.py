"""Settings: what a test file may tune, the defaults that suit most apps, and the limits that keep a test honest.

A test file needs no settings at all. Its `settings:` block changes a default for every step in the file, and a
step can change `timeout`, `settle`, `max_actions`, `max_scrolls` or `confidence` for itself. Every value must be
within `LIMITS`: a limit is where a setting stops tuning a test and starts hiding a problem in the app.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

JEV_MODELS = "typesafe/jev-"
"""jevtest uses Jev only: a model name must start with this."""


@dataclass(frozen=True)
class Settings:
    """How steps wait, how far they go, and how sure Jev must be.

    Attributes:
        model: The Jev version that decides and judges. Part of every lockfile key, so changing it re-asks Jev.
        timeout: Seconds a step waits for what it looks for (an element, a check).
        settle: Most seconds to wait for the screen to stop changing after an action. The wait ends as soon as the
            screen is still, so a higher value only costs time on screens that never stop moving.
        max_actions: Actions a `do:` goal may take.
        max_scrolls: Scrolls a `scroll_to:` may make. It also stops at the end of the content.
        confidence: An `expect:` passes when Jev's probability that the statement is true is above this.
    """

    model: str = "typesafe/jev-1.13"
    timeout: float = 10.0
    settle: float = 3.0
    max_actions: int = 10
    max_scrolls: int = 50
    confidence: float = 0.5


LIMITS: Mapping[str, tuple[float, float]] = {
    "timeout": (1, 300),
    "settle": (1, 30),
    "max_actions": (1, 50),
    "max_scrolls": (1, 500),
    "confidence": (0.5, 0.99),
}
"""The lowest and highest value of each numeric setting, inclusive."""

WHOLE = frozenset({"max_actions", "max_scrolls"})
"""Settings that count something, so take whole numbers."""

STEP_SETTINGS = frozenset(LIMITS)
"""Settings a step can change for itself. `model` is for the whole file: one file, one Jev."""

DEFAULTS = Settings()
"""The settings of a file without a `settings:` block."""
