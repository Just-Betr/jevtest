"""``.env`` files: the ``${NAME}`` values for the test file next to them."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path

from jevtest.domain.failures import TestFileError

ENV_LINE = re.compile(r"(?:export )?([A-Za-z_][A-Za-z0-9_]*)=(.*)")


def read_env(folder: Path, environ: Mapping[str, str] = os.environ) -> dict[str, str]:
    """The environment plus the ``KEY=value`` lines of `folder`/.env.

    A name set in both to different values is an error: which one is meant is not guessed.

    Raises:
        TestFileError: A line isn't ``KEY=value``, a key is set twice, or the file and the environment disagree.
    """
    f = folder / ".env"
    env = dict(environ)
    if not f.is_file():
        return env
    seen: set[str] = set()
    for n, line in enumerate(f.read_text().splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = ENV_LINE.fullmatch(line.strip())
        if not m:
            raise TestFileError(f"{f}:{n} is not a KEY=value line")
        key, value = m.groups()
        if len(value) >= len("''") and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if key in seen:
            raise TestFileError(f"{f}:{n} sets {key} a second time")
        if key in environ and environ[key] != value:
            raise TestFileError(f"{key} is set in the environment and in {f} to different values: remove one of them")
        seen.add(key)
        env[key] = value
    return env
