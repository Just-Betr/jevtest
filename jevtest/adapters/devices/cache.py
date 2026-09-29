"""jevtest's cache folder: agent builds, agent logs, and what each run marks as in use.

Several jevtest runs can share a machine, even of different versions. What one run uses (a device, an agent
build) it marks with a lock on a file in `in_use_dir`. The operating system lets go of a lock when the process
ends, however it ends, so a crashed run never leaves anything marked.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import IO, Self


def cache_dir() -> Path:
    """Where built agents and their logs are kept: ``$JEVTEST_CACHE``, or ``~/.cache/jevtest``."""
    return Path(os.environ.get("JEVTEST_CACHE", Path.home() / ".cache" / "jevtest"))


def in_use_dir() -> Path:
    """The folder of lock files that mark what runs are using, and of what they must put back on devices."""
    folder = cache_dir() / "in-use"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def digest(src: Path) -> str:
    """Hash of a source tree, so a changed on-device agent gets rebuilt."""
    h = hashlib.sha256()
    for f in sorted(src.rglob("*")):
        if f.is_file() and "xcuserdata" not in f.parts:
            h.update(str(f.relative_to(src)).encode())
            h.update(f.read_bytes())
    return h.hexdigest()[:12]


def lock(path: Path, *, shared: bool = False) -> IO[str]:
    """Lock the file at `path` (made if missing), shared or for this process alone, waiting while another holds it.

    Closing the returned file lets go.
    """
    return _locked(path, fcntl.LOCK_SH if shared else fcntl.LOCK_EX)


def try_lock(path: Path) -> IO[str] | None:
    """Lock the file at `path` (made if missing) for this process alone; None if another holds it."""
    try:
        return _locked(path, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return None


def _locked(path: Path, mode: int) -> IO[str]:
    """`path` opened and locked with `mode`.

    A remover locks a file for itself, and removes it while it holds it. Whoever was waiting then holds a file
    that is no longer at `path`, so it opens `path` again: two runs never hold locks on two different files of
    one name.
    """
    while True:
        f = path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(f, mode)
            held = os.fstat(f.fileno())
            try:
                now = path.stat()
            except FileNotFoundError:
                now = None
        except BaseException:
            f.close()
            raise
        if now is not None and (now.st_dev, now.st_ino) == (held.st_dev, held.st_ino):
            return f
        f.close()  # removed while this run waited for it: lock the file now at `path`


@dataclass(frozen=True)
class BuildInUse:
    """A cached agent build this run uses: no other run removes it until this is closed.

    Attributes:
        path: The build.
        version: The version of the agent's source it was built from.
    """

    path: Path
    version: str
    _lock: IO[str]

    def close(self) -> None:
        """Done with the build: another run may remove it now."""
        self._lock.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self, kind: type[BaseException] | None, error: BaseException | None, traceback: TracebackType | None
    ) -> None:
        self.close()


class AgentBuilds:
    """The cached builds of one agent, one per version of its source: ``<prefix><version><suffix>``.

    Every jevtest version whose agent differs builds its own; without removing older ones the cache only grows
    (measured: 6.5 GB of iOS agent builds at about 150 MB each). A build another run is using is never removed.

    Args:
        prefix: The name before the version, e.g. ``android-agent-``.
        suffix: The name after it: ``.apk``; an iPhone build's team, ``-TEAM1``.
        beside: Files a build leaves next to it, removed with it: ``.idsig`` for ``….apk.idsig``.
    """

    def __init__(self, prefix: str, suffix: str = "", beside: tuple[str, ...] = ()) -> None:
        self.prefix, self.suffix, self.beside = prefix, suffix, beside
        extras = "".join(f"|{re.escape(b)}" for b in beside)
        self._name = re.compile(f"{re.escape(prefix)}([0-9a-f]+){re.escape(suffix)}(?:{extras})")

    def path(self, version: str) -> Path:
        """Where the build of `version` is."""
        return cache_dir() / f"{self.prefix}{version}{self.suffix}"

    def use(self, version: str) -> BuildInUse:
        """Mark the build of `version` as in use, whether or not it's built yet; wait while another run removes it.

        The caller builds it if it isn't there, then closes it when done with it.
        """
        return BuildInUse(self.path(version), version, lock(self._lock_path(version), shared=True))

    def drop_older(self, keep: str) -> None:
        """Remove every build but `keep`'s, except those another run is using.

        Removing is tidying, so a file that can't be removed stays, and the next run tries again.
        """
        versions = {found[1] for entry in cache_dir().iterdir() if (found := self._name.fullmatch(entry.name))}
        for version in sorted(versions - {keep}):
            held = try_lock(self._lock_path(version))
            if held is None:
                continue  # another run is using it
            with held:
                build = self.path(version)
                for path in (build, *(build.with_name(build.name + b) for b in self.beside)):
                    if path.is_dir():
                        shutil.rmtree(path, ignore_errors=True)
                    else:
                        with contextlib.suppress(OSError):
                            path.unlink(missing_ok=True)
                self._lock_path(version).unlink()  # while locked: see `_locked`

    def _lock_path(self, version: str) -> Path:
        return in_use_dir() / f"{self.prefix}{version}{self.suffix}.lock"
