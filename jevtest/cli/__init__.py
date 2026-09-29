"""The command line: parsing arguments, wiring real implementations to ports, printing."""

from __future__ import annotations

import sys

SYSTEMS = ("darwin", "linux")
"""The systems jevtest runs on (`sys.platform`): macOS and Linux. It locks files with `fcntl`, which Windows lacks."""


def command() -> int:
    """The ``jevtest`` command: on a system jevtest doesn't run on, says so before importing what needs one."""
    if not sys.platform.startswith(SYSTEMS):
        print(f"error: jevtest runs on macOS and Linux, not {sys.platform}", file=sys.stderr)
        return 2
    from .main import main  # noqa: PLC0415 - only after the check: it needs macOS or Linux

    return main()
