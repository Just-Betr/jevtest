"""jevtest's fixed rules.

They are part of what jevtest is, not settings: the same for everyone, listed in the docs, and changed only by a
new release. A step can wait longer than `TIMEOUT` with its own `timeout:`.
"""

from __future__ import annotations

MODEL = "typesafe/jev-1.13"
"""The Jev version this release is built and tested against. Printed at the start of every run."""

TIMEOUT = 10.0
"""Seconds a step waits for what it looks for (a check, an element) unless it sets `timeout:`."""

MAX_ACTIONS = 10
"""Actions a `do:` goal may take. A bigger goal is split into steps."""

MAX_SCROLLS = 50
"""Scrolls a `scroll_to:` may make. It also stops at the end of the content."""

THRESHOLD = 0.5
"""An `expect:` passes when Jev finds the statement more likely true than false."""

SETTLE = 3.0
"""Most seconds to wait for the screen to stop changing after an action."""

LAUNCH_QUIET = 0.5
"""Seconds without a change that count as "the app has finished starting" (apps pause longer while starting)."""
