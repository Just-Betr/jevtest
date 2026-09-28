"""Finding test files: as given, or every test file in a folder."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from jevtest.domain.failures import TestFileError

from .loader import is_test_file

YAML = frozenset({".yaml", ".yml"})


def find_test_files(paths: Sequence[str]) -> tuple[list[Path], list[Path]]:
    """The test files to run, and the other YAML files found in folders.

    Files are taken as given; a folder contributes every YAML file with `app:` in it and its subfolders, in name
    order. The other YAML files in folders must turn out to be libraries a test file includes.

    Raises:
        TestFileError: A path doesn't exist, or no test file was found.
    """
    files: list[Path] = []
    others: list[Path] = []
    for p in map(Path, paths):
        if p.is_dir():
            for f in _yaml_files(p):
                if is_test_file(f):
                    files.append(f)
                elif not _hidden(f, p):  # a hidden test file runs; hidden other YAML (.github/) is none of ours
                    others.append(f)
        elif p.exists():
            if p.suffix not in YAML:
                raise TestFileError(f"{p} is not a test file: test files are .yaml or .yml")
            files.append(p)
        else:
            raise TestFileError(f"Test file not found: {p}")
    unique = list(dict.fromkeys(f.resolve() for f in files))
    if not unique:
        raise TestFileError(f"No test files (YAML with `app:`) in {', '.join(paths)}")
    return unique, [f.resolve() for f in others]


def _hidden(f: Path, folder: Path) -> bool:
    return any(part.startswith(".") for part in f.relative_to(folder).parts)


def _yaml_files(folder: Path) -> list[Path]:
    """Every YAML file in `folder` and its subfolders, in name order."""
    return sorted(f for f in folder.rglob("*") if f.suffix in YAML and f.is_file())
