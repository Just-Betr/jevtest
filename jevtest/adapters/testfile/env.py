"""``.env`` files: the ``${NAME}`` values for the test file next to them."""

from __future__ import annotations

import os
import re
from collections.abc import Iterator, Mapping
from pathlib import Path

from jevtest.adapters.shapes import read_user_text
from jevtest.domain.failures import TestFileError
from jevtest.domain.variables import VARIABLE

ENV_LINE = re.compile(r"(?:export )?([A-Za-z_][A-Za-z0-9_]*)=(.*)")
QUOTES = "'\""


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
    for n, key, value in _entries(f):
        if key in seen:
            raise TestFileError(f"{f}:{n} sets {key} a second time")
        inner = VARIABLE.search(value)
        if inner:
            raise TestFileError(
                f"{f}:{n}: {key} uses {inner[0]}, but .env values aren't filled in from other values: write the "
                "whole value"
            )
        if key in environ and environ[key] != value:
            raise TestFileError(f"{key} is set in the environment and in {f} to different values: remove one of them")
        seen.add(key)
        env[key] = value
    return env


def _entries(f: Path) -> Iterator[tuple[int, str, str]]:
    """Each ``KEY=value`` line's number, key and value (quotes around the value removed)."""
    text = read_user_text(f, TestFileError, str(f), "save it as UTF-8")
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = ENV_LINE.fullmatch(line.strip())
        if not m:
            raise TestFileError(f"{f}:{n} is not a KEY=value line")
        key, value = m.groups()
        if " #" in value and not _quoted(value):
            raise TestFileError(
                f"{f}:{n}: is ' #…' a comment or part of {key}? Put the comment on its own line, or quote the value"
            )
        yield n, key, _unquoted(value)


def _quoted(value: str) -> bool:
    return len(value) >= len("''") and value[0] == value[-1] and value[0] in QUOTES


def _unquoted(value: str) -> str:
    return value[1:-1] if _quoted(value) else value
