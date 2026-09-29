"""The ``jevtest`` command, and the composition root: the one place that picks real implementations.

    jevtest run PATH... --lock MODE --out DIR [--test NAME] [--prune-lock] [-v]

A test file says which app to test on which device(s); jevtest runs its tests on each platform it lists. A
folder runs every test file in it; any other YAML in it must be a library one of those files includes. Several
devices for a platform share its tests and run at the same time, as do the platforms. jevtest uses devices that
are already running; it never starts, stops or manages them. Nothing is assumed: anything missing or wrong is an
error that says what to fix.

Exit codes: 0 all tests passed, 1 a test failed, 2 something needs fixing first, 128 + the signal when stopped
(130 Ctrl-C, 143 SIGTERM as CI sends on cancel, 129 SIGHUP when the terminal closes).
"""

from __future__ import annotations

import argparse
import functools
import signal
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from jevtest import __version__
from jevtest.adapters.clock import SystemClock
from jevtest.adapters.devices.android import AndroidDevice
from jevtest.adapters.devices.claim import Claims
from jevtest.adapters.devices.finder import find_device
from jevtest.adapters.devices.ios import IOSDevice
from jevtest.adapters.jev.client import JevClient
from jevtest.adapters.jev.lockfile import LockMode
from jevtest.domain.failures import JevtestError
from jevtest.domain.kinds import Platform
from jevtest.domain.ports import Clock, Device

from .run import FindDevice, MakeClient, MakeDevice, RunOptions, run_command


def make_device(platform: Platform, device: str, app: Path, progress: Callable[[str], None]) -> Device:
    """The real device for a platform."""
    if platform is Platform.ANDROID:
        return AndroidDevice(device, progress)
    return IOSDevice(device, app, progress)


CLAIMS = Claims()
"""The devices this process is testing: a second jevtest run can't use them at the same time."""

FIND_DEVICE: FindDevice = functools.partial(find_device, CLAIMS)
"""Finds each device a file names, and claims it for this process."""


def make_client(model: str, api_key: str | None) -> JevClient:
    """The real Jev client for `model`; retries are reported on stderr."""
    return JevClient(model, api_key, log=lambda message: print(message, file=sys.stderr, flush=True))


def parser() -> argparse.ArgumentParser:
    """The command line."""
    p = argparse.ArgumentParser(
        prog="jevtest", description="Plain-English end-to-end tests for mobile apps, driven by Jev."
    )
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True, metavar="COMMAND")
    r = sub.add_parser("run", help="run test files, or every test file in a folder")
    r.add_argument("paths", nargs="+", metavar="PATH", help="test files and/or folders of them")
    r.add_argument("--test", action="append", default=[], metavar="NAME", help="only run this test (repeatable)")
    r.add_argument(
        "--lock",
        required=True,
        choices=[m.value for m in LockMode],
        help="record: repeat the saved do: steps and recorded Jev answers, ask Jev about anything new and save it; "
        "frozen: only saved steps and recorded answers, anything new fails the run (no key or network needed); "
        "refresh: ask Jev again about everything and save it anew; off: no lockfile, Jev is asked every time",
    )
    r.add_argument(
        "--out", required=True, metavar="DIR", help="results folder (each run adds a timestamped folder in it)"
    )
    r.add_argument(
        "--prune-lock",
        action="store_true",
        help="after a run of every test where every test passed, drop the saved steps and answers it didn't use",
    )
    r.add_argument("-v", "--verbose", action="store_true", help="print every Jev question and answer")
    return p


STOP_SIGNALS = (signal.SIGTERM, signal.SIGHUP)
"""Signals that stop a run like Ctrl-C. One that is already ignored (``nohup`` ignores SIGHUP) stays ignored."""


class Stopped(KeyboardInterrupt):
    """A stop signal, raised like Ctrl-C so every device is put back and its agent stopped before jevtest exits."""

    def __init__(self, signum: int) -> None:
        super().__init__(signum)
        self.signum = signum


def _stop(signum: int, _frame: object) -> None:
    raise Stopped(signum)


def main(
    argv: Sequence[str] | None = None,
    *,
    devices: MakeDevice = make_device,
    find: FindDevice = FIND_DEVICE,
    client: MakeClient = make_client,
    clock: Clock | None = None,
) -> int:
    """Run the command line; return the exit code.

    The keyword arguments are the implementations to use; tests pass fakes.
    """
    args = parser().parse_args(argv)
    options = RunOptions(
        tuple(args.paths),
        LockMode(args.lock),
        Path(args.out),
        tuple(args.test),
        prune_lock=args.prune_lock,
        verbose=args.verbose,
    )
    previous = {s: signal.signal(s, _stop) for s in STOP_SIGNALS if signal.getsignal(s) is signal.SIG_DFL}
    try:
        return run_command(options, devices, client, clock or SystemClock(), find)
    except JevtestError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt as e:
        signum = e.signum if isinstance(e, Stopped) else signal.SIGINT
        print(f"\nstopped ({signal.Signals(signum).name}): devices put back; this run wrote no report", file=sys.stderr)
        return 128 + signum
    finally:
        CLAIMS.release()
        for s, handler in previous.items():
            signal.signal(s, handler)
