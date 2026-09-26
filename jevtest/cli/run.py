"""The ``jevtest run`` command: find and load test files, run them on their devices, write the results.

Files run one after another; a file's devices run at the same time, one thread each.
"""

from __future__ import annotations

import os
import re
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from jevtest import __version__
from jevtest.adapters.jev.client import JevClient
from jevtest.adapters.jev.lockfile import LockedModel, LockMode
from jevtest.adapters.reports.json_report import write_report
from jevtest.adapters.reports.junit import Suite as JunitSuite
from jevtest.adapters.reports.junit import write_junit
from jevtest.adapters.testfile.discovery import find_test_files
from jevtest.adapters.testfile.env import read_env
from jevtest.adapters.testfile.loader import load
from jevtest.application.brain import Brain
from jevtest.application.planning import shard
from jevtest.application.runner import TestRunner
from jevtest.domain.failures import DeviceError, ModelError, TestFileError
from jevtest.domain.kinds import Platform
from jevtest.domain.ports import Clock, Device
from jevtest.domain.results import RunResult
from jevtest.domain.steps import Suite

from .console import ConsoleListener, Printer, summary

API_KEY = "OPENROUTER_API_KEY"

MakeDevice = Callable[[Platform, str, Path, Callable[[str], None]], Device]
"""Makes the device for a platform: (platform, device name, app build, progress) -> device."""

MakeClient = Callable[[str, str | None], JevClient]
"""Makes the Jev client from the model and the API key (None when it isn't set)."""


@dataclass(frozen=True)
class RunOptions:
    """What the command line asked for.

    Attributes:
        paths: Test files and folders.
        lock: How the lockfile is used.
        out: The results folder.
        tests: Only these tests (empty: all of them).
        prune_lock: After a fully passing run, drop recorded decisions it didn't use.
        verbose: Print every model question and answer.
    """

    paths: tuple[str, ...]
    lock: LockMode
    out: Path
    tests: tuple[str, ...] = ()
    prune_lock: bool = False
    verbose: bool = False


@dataclass(frozen=True)
class Job:
    """Some of a file's tests, on one device.

    Attributes:
        suite: The file, with only this device's tests.
        platform: The device's platform.
        device: The device's name.
        out: Where this device's results go.
        tag: How output names the device, e.g. ``checkout · android · Pixel 8``.
    """

    suite: Suite
    platform: Platform
    device: str
    out: Path
    tag: str

    @property
    def app(self) -> Path:
        """The build this job installs."""
        return self.suite.apps[self.platform]


@dataclass(frozen=True)
class JobResult:
    """What one device's run produced, for the reports."""

    job: Job
    result: RunResult
    logs: dict[str, list[str]]


def slug(text: str) -> str:
    """`text` as a safe folder name."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("_") or "device"


def plan(suite: Suite, out: Path, label: str, printer: Printer) -> list[Job]:
    """One job per device, each with its share of the file's tests."""
    jobs = []
    for platform in suite.apps:
        devices = suite.devices[platform]
        for device, tests in zip(devices, shard(suite.tests, len(devices)), strict=True):
            tag = " · ".join(filter(None, (label, platform.value, device)))
            if not tests:
                printer.block(tag, [f"no tests left for this device ({len(devices)} devices, fewer groups of tests)"])
                continue
            jobs.append(Job(replace(suite, tests=tests), platform, device, out / platform.value / slug(device), tag))
    return jobs


class Runs:
    """Runs jobs on their devices.

    Args:
        make_device: Makes each job's device.
        clock: Time, for the runner.
        verbose: Print every model question and answer.
    """

    def __init__(self, make_device: MakeDevice, clock: Clock, *, verbose: bool) -> None:
        self.make_device = make_device
        self.clock = clock
        self.verbose = verbose

    def one(self, job: Job, model: LockedModel, printer: Printer) -> JobResult:
        """Run a job's tests on its device and write its report."""
        job.out.mkdir(parents=True, exist_ok=True)
        device = self.make_device(job.platform, job.device, job.app,
                                  lambda message: printer.block(job.tag, [f"  {message}..."]))
        listener = ConsoleListener(printer, job.tag, verbose=self.verbose)
        try:
            app_id = device.install(job.app)
            printer.block(job.tag, [f"jevtest {__version__} · {job.tag} · {app_id} · {job.suite.settings.model} · "
                                    f"lockfile: {model.mode.value}"])
            result = TestRunner(job.suite, device, Brain(model), job.out, clock=self.clock, listener=listener).run()
        finally:
            device.close()
        write_report(job.out / "report.json", {"file": str(job.suite.path), "platform": job.platform.value,
                                                "device": job.device, "app": str(job.app), "app_id": app_id,
                                                "model": job.suite.settings.model}, result, listener.logs, model.calls)
        printer.block(job.tag, summary(result, model.calls, job.out))
        return JobResult(job, result, listener.logs)

    def all(self, jobs: Sequence[Job], model: LockedModel, printer: Printer) -> list[JobResult]:
        """Run the jobs at the same time, one thread per device.

        A device that fails to start doesn't stop the others; its error is raised once they have all finished.
        """
        if len(jobs) == 1:
            return [self.one(jobs[0], model.fork(), printer)]
        outcomes: list[JobResult | BaseException | None] = [None] * len(jobs)

        def work(i: int, job: Job, fork: LockedModel) -> None:
            try:
                outcomes[i] = self.one(job, fork, printer)
            except Exception as e:
                outcomes[i] = e

        threads = [threading.Thread(target=work, args=(i, job, model.fork()), daemon=True)
                   for i, job in enumerate(jobs)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        results: list[JobResult] = []
        for job, outcome in zip(jobs, outcomes, strict=True):
            if isinstance(outcome, TestFileError | DeviceError | ModelError):
                raise type(outcome)(f"{job.tag}: {outcome}")
            if isinstance(outcome, BaseException):
                raise outcome
            assert outcome is not None
            results.append(outcome)
        return results


def run_command(options: RunOptions, make_device: MakeDevice, make_client: MakeClient, clock: Clock) -> int:
    """``jevtest run``: 0 if every test passed, 1 if any failed.

    Raises:
        JevtestError: Something needs fixing before tests can run (the message says what).
    """
    files, others = find_test_files(options.paths)
    loaded = _load(files, others)
    if options.tests:
        loaded = _only(loaded, options.tests)
    for suite, _ in loaded:
        for app in suite.apps.values():
            if not app.exists():
                raise TestFileError(f"App not found: {app}")
    if options.prune_lock and (options.tests or options.lock is LockMode.OFF):
        raise TestFileError("--prune-lock needs every test to run (no --test) and a lockfile (not --lock off)")

    root = Path(os.path.commonpath([f.parent for f in files]))
    out = options.out / time.strftime("%Y%m%d-%H%M%S")
    runs = Runs(make_device, clock, verbose=options.verbose)
    suites: list[JunitSuite] = []
    for suite, env in loaded:  # one file at a time; its devices at the same time
        label = suite.path.relative_to(root).with_suffix("").as_posix() if len(loaded) > 1 else ""
        done = _run_file(suite, env, label=label, out=out / label if label else out, runs=runs, options=options,
                         make_client=make_client)
        suites += [JunitSuite("jevtest." + r.job.tag.replace(" · ", "."), r.result, r.logs) for r in done]
    write_junit(out / "junit.xml", suites)
    passed = sum(s.result.passed for s in suites)
    failed = sum(s.result.failed for s in suites)
    if len(suites) > 1:
        print(f"\nAll: {passed}/{passed + failed} passed ({len(loaded)} file(s), {len(suites)} device run(s)). "
              f"JUnit: {out / 'junit.xml'}")
    return 0 if failed == 0 else 1


Loaded = list[tuple[Suite, dict[str, str]]]


def _load(files: Sequence[Path], others: Sequence[Path]) -> Loaded:
    """Each test file, with the environment its ``${NAME}`` values came from.

    Every other YAML file found in a folder must be a library one of them includes.
    """
    loaded: Loaded = []
    for f in files:
        env = read_env(f.parent)
        loaded.append((load(f, env), env))
    included = {lib for suite, _ in loaded for lib in suite.includes}
    stray = [f for f in others if f not in included]
    if stray:
        raise TestFileError(f"{', '.join(str(f) for f in stray)}: no `app:` and not included by any test file. "
                            "Add `app:` to run it, include it from a test file, or move it out of the folder")
    return loaded


def _only(loaded: Loaded, names: Sequence[str]) -> Loaded:
    """Only the named tests, from whichever files have them."""
    known = {t.name for suite, _ in loaded for t in suite.tests}
    missing = [n for n in names if n not in known]
    if missing:
        raise TestFileError(f"No test named: {', '.join(missing)}")
    kept = [(replace(s, tests=tuple(t for t in s.tests if t.name in names)), env) for s, env in loaded]
    return [(s, env) for s, env in kept if s.tests]


def _run_file(suite: Suite, env: dict[str, str], *, label: str, out: Path, runs: Runs, options: RunOptions,
              make_client: MakeClient) -> list[JobResult]:
    """Run one file on its devices, with its lockfile; prune the lockfile if asked."""
    printer = Printer(parallel=sum(len(suite.devices[p]) for p in suite.apps) > 1)
    if label:
        print(f"\n=== {suite.path.name} ===", flush=True)
    jobs = plan(suite, out, label, printer)

    def connect() -> JevClient:
        return make_client(suite.settings.model, env.get(API_KEY))

    model = LockedModel(suite.settings.model, suite.path.with_suffix(".lock.json"), options.lock, connect)
    try:
        done = runs.all(jobs, model, printer)
        if options.prune_lock:
            if any(r.result.failed for r in done):
                print(f"{model.path.name}: not pruned, because a test failed", flush=True)
            else:
                print(f"{model.path.name}: pruned {model.prune()} unused decision(s)", flush=True)
    finally:
        model.save()
    return done
