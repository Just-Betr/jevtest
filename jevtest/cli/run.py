"""The ``jevtest run`` command: find and load test files, run them on their devices, write the results.

Files run one after another; a file's devices run at the same time, one thread each.
"""

from __future__ import annotations

import contextlib
import itertools
import os
import re
import shutil
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from jevtest import __version__
from jevtest.adapters.jev.client import KEY_HELP
from jevtest.adapters.jev.lockfile import JevAsker, LockedModel, LockMode
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
from jevtest.domain.steps import Do, Expect, Grant, Network, Step, Suite, Test, Use

from .console import ConsoleListener, Printer, summary

API_KEY = "TYPESAFE_API_KEY"

MakeDevice = Callable[[Platform, str, Path, Callable[[str], None]], Device]
FindDevice = Callable[[Platform, str, Path], bool]
"""Claims the named device, and says whether it's a real iPhone. Raises `DeviceError` unless it's running and the
build (the path) suits it."""
"""Makes the device for a platform: (platform, device name, app build, progress) -> device."""

MakeClient = Callable[[str, str | None], JevAsker]
"""Makes the Jev client from the model and the API key (None when it isn't set)."""


@dataclass(frozen=True)
class RunOptions:
    """What the command line asked for.

    Attributes:
        paths: Test files and folders.
        lock: How the lockfile is used.
        out: The results folder.
        tests: Only these tests (empty: all of them).
        prune_lock: After a fully passing run, drop saved steps and recorded answers it didn't use.
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
    jobs: list[Job] = []
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
        self._open: list[Device] = []  # devices not closed yet: closed on Ctrl-C too
        self._open_lock = threading.Lock()

    def _close(self, device: Device) -> None:
        """Close a device once: whichever comes first, its own job ending or an interrupted run."""
        with self._open_lock:
            if device not in self._open:
                return
            self._open.remove(device)
        device.close()

    def close_all(self) -> None:
        """Close every device still open: put back what steps changed and stop the agents (on Ctrl-C)."""
        with self._open_lock:
            left = list(self._open)
        for device in left:
            with contextlib.suppress(DeviceError):
                self._close(device)

    def one(self, job: Job, model: LockedModel, printer: Printer) -> JobResult:
        """Run a job's tests on its device and write its report."""
        job.out.mkdir(parents=True, exist_ok=True)
        device = self.make_device(
            job.platform, job.device, job.app, lambda message: printer.block(job.tag, [f"  {message}..."])
        )
        with self._open_lock:
            self._open.append(device)
        listener = ConsoleListener(printer, job.tag, verbose=self.verbose)
        try:
            app_id = device.install(job.app)
            header = (
                f"jevtest {__version__} · {job.tag} · {app_id} · {job.suite.settings.model} · "
                f"lockfile: {model.mode.value}"
            )
            printer.block(job.tag, [header])
            result = TestRunner(
                job.suite,
                device,
                Brain(model, job.suite.variables),
                job.out,
                platform=job.platform.value,
                clock=self.clock,
                listener=listener,
            ).run()
        finally:
            self._close(device)
        write_report(
            job.out / "report.json",
            {
                "file": str(job.suite.path),
                "platform": job.platform.value,
                "device": job.device,
                "app": str(job.app),
                "app_id": app_id,
                "model": job.suite.settings.model,
            },
            result,
            listener.logs,
            model.calls,
        )
        printer.block(job.tag, summary(result, model.calls, job.out))
        return JobResult(job, result, listener.logs)

    def all(self, jobs: Sequence[Job], model: LockedModel, printer: Printer) -> list[JobResult]:
        """Run the jobs at the same time, one thread per device.

        A device that fails to start doesn't stop the others; its error is raised once they have all finished.
        """
        if len(jobs) == 1:
            return [self.one(jobs[0], model.fork(), printer)]
        outcomes: dict[int, JobResult | Exception] = {}

        def work(i: int, job: Job, fork: LockedModel) -> None:
            try:
                outcomes[i] = self.one(job, fork, printer)
            except Exception as e:  # noqa: BLE001 - handed to the main thread, which raises it
                outcomes[i] = e

        # Daemon threads, so Ctrl-C ends the run at once instead of waiting for every device. A daemon thread
        # never runs its cleanup, so on Ctrl-C this thread closes the devices before the run ends.
        threads = [
            threading.Thread(target=work, args=(i, job, model.fork()), daemon=True) for i, job in enumerate(jobs)
        ]
        for t in threads:
            t.start()
        try:
            for t in threads:
                t.join()
        except KeyboardInterrupt:
            self.close_all()
            raise
        return [_result(job, outcomes[i]) for i, job in enumerate(jobs)]


def _result(job: Job, outcome: JobResult | Exception) -> JobResult:
    """A job's result, or its error raised, naming the device (a jevtest error) or as it was (a bug)."""
    if isinstance(outcome, TestFileError | DeviceError | ModelError):
        raise type(outcome)(f"{job.tag}: {outcome}")
    if isinstance(outcome, Exception):
        raise outcome
    return outcome


def run_command(
    options: RunOptions, make_device: MakeDevice, make_client: MakeClient, clock: Clock, find_device: FindDevice
) -> int:
    """``jevtest run``: 0 if every test passed, 1 if any failed.

    Raises:
        JevtestError: Something needs fixing before tests can run (the message says what).
    """
    files, others = find_test_files(options.paths)
    loaded = _load(files, others)
    if options.tests:
        loaded = _only(loaded, options.tests)
    _check_runnable(loaded, options)
    _find_devices(loaded, find_device)
    root = Path(os.path.commonpath([f.parent for f in files]))
    out = _results_folder(options.out, time.strftime("%Y%m%d-%H%M%S"))
    runs = Runs(make_device, clock, verbose=options.verbose)
    suites: list[JunitSuite] = []
    for suite, env in loaded:  # one file at a time; its devices at the same time
        label = suite.path.relative_to(root).with_suffix("").as_posix() if len(loaded) > 1 else ""
        done = _run_file(
            suite,
            env,
            label=label,
            out=out / label if label else out,
            runs=runs,
            options=options,
            make_client=make_client,
        )
        suites += [JunitSuite("jevtest." + r.job.tag.replace(" · ", "."), r.result, r.logs) for r in done]
    write_junit(out / "junit.xml", suites)
    return _report_all(suites, files=len(loaded), junit=out / "junit.xml")


Loaded = list[tuple[Suite, dict[str, str]]]


def _check_runnable(loaded: Loaded, options: RunOptions) -> None:
    """Every app build exists, and --prune-lock has a whole run and a lockfile to prune."""
    missing = [app for suite, _ in loaded for app in suite.apps.values() if not app.exists()]
    if missing:
        raise TestFileError(f"App not found: {missing[0]}")
    bundles = [app for suite, _ in loaded for app in suite.apps.values() if app.suffix.lower() == ".aab"]
    if bundles and not shutil.which("bundletool"):
        raise TestFileError(
            f"{bundles[0].name}: bundletool is required to install .aab files (brew install bundletool)"
        )
    if options.prune_lock and (options.tests or options.lock is LockMode.OFF):
        raise TestFileError("--prune-lock needs every test to run (no --test) and a lockfile (not --lock off)")
    for suite, _ in loaded:
        network = next((name for name, step in _steps(suite) if isinstance(step.action, Network)), None)
        if Platform.IOS in suite.apps and network is not None:
            raise TestFileError(
                f"{suite.path.name} runs on iOS, where jevtest can't turn the network on or off, and test "
                f"'{network}' has a network: step. Put Android-only tests in a file whose app: is Android only"
            )
    for suite, _ in loaded:
        _check_grants(suite)
    if options.lock in (LockMode.REFRESH, LockMode.OFF):
        keyless = [suite.path.name for suite, env in loaded if not env.get(API_KEY) and _asks_jev(suite)]
        if keyless:
            raise TestFileError(
                f"--lock {options.lock} asks Jev about every do: and expect:, and {keyless[0]} has them, but "
                f"{API_KEY} is not set: put it in the .env next to the test file, or in the environment. {KEY_HELP}"
            )


def _results_folder(parent: Path, stamp: str) -> Path:
    """A new folder for this run's results, made before any device is touched.

    One that can't be made would lose the whole run. Another run that started in the same second has `stamp`, so
    this one gets `stamp-2`, and so on.

    Raises:
        TestFileError: It can't be made.
    """
    for n in itertools.count(1):
        out = parent / (stamp if n == 1 else f"{stamp}-{n}")
        try:
            out.mkdir(parents=True)
        except FileExistsError:
            continue
        except OSError as e:
            raise TestFileError(f"--out {parent}: can't make the results folder {out} ({e.strerror})") from None
        return out
    raise AssertionError  # pragma: no cover - itertools.count never ends


def _find_devices(loaded: Loaded, find_device: FindDevice) -> None:
    """Every device every file names is running, before any test starts: not found only once others finish.

    A real iPhone can't be granted permissions, so a file that runs on one has no `grant:` in its tests.
    """
    named = dict.fromkeys((p, d, suite.apps[p]) for suite, _ in loaded for p in suite.apps for d in suite.devices[p])
    iphones: set[str] = set()
    for platform, device, app in named:
        try:
            if find_device(platform, device, app):
                iphones.add(device)
        except DeviceError as e:
            raise DeviceError(f"{platform} · {device}: {e}") from None
    for suite, _ in loaded:
        iphone = next((d for d in suite.devices.get(Platform.IOS, ()) if d in iphones), None)
        granting = next((name for name, step in _steps(suite) if isinstance(step.action, Grant)), None)
        if iphone is not None and granting is not None:
            raise TestFileError(
                f"{suite.path.name} runs on the iPhone {iphone}, where jevtest can't pre-grant permissions (Apple "
                f"doesn't allow it), and test '{granting}' has a grant: step. Run it on a simulator, or tap the "
                "permission prompt instead"
            )


def _check_grants(suite: Suite) -> None:
    """Each `grant:` names its permission for every platform the file runs on, as that platform names it."""
    for name, step in _steps(suite):
        if not isinstance(step.action, Grant):
            continue
        for platform in suite.apps:
            permissions = step.action.names_on(platform)
            if permissions is None:
                raise TestFileError(
                    f"{suite.path.name} runs on {platform}, and test '{name}' has a grant: with no {platform} "
                    f"permission. Add it: grant: {{android: android.permission.CAMERA, ios: camera}}"
                )
            for permission in permissions:
                if platform is Platform.ANDROID and not permission.startswith("android.permission."):
                    raise TestFileError(
                        f"{suite.path.name} runs on Android, where test '{name}' grants '{permission}': Android "
                        f"needs the full name, e.g. android.permission.{permission.upper()}. For both platforms: "
                        f"grant: {{android: android.permission.{permission.upper()}, ios: {permission}}}"
                    )


def _asks_jev(suite: Suite) -> bool:
    """Whether a test that runs (or one it uses) has a `do:` or an `expect:`."""
    return any(
        isinstance(step.action, Do) or any(isinstance(c, Expect) for c in step.checks) for _, step in _steps(suite)
    )


def _steps(suite: Suite) -> Iterator[tuple[str, Step]]:
    """Every step the tests that run take, with the test that has it: a used test's steps once."""
    tests, seen = list(suite.tests), set[str]()
    while tests:
        test = tests.pop(0)
        for step in test.steps:
            yield test.name, step
            if isinstance(step.action, Use) and step.action.test not in seen:
                seen.add(step.action.test)
                tests.append(suite.library[step.action.test])


def _report_all(suites: Sequence[JunitSuite], *, files: int, junit: Path) -> int:
    """The exit code; with several device runs, a line totalling them all."""
    passed = sum(s.result.passed for s in suites)
    failed = sum(s.result.failed for s in suites)
    if len(suites) > 1:
        print(
            f"\nAll: {passed}/{passed + failed} passed ({files} file(s), {len(suites)} device run(s)). JUnit: {junit}"
        )
    return 0 if failed == 0 else 1


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
        raise TestFileError(
            f"{', '.join(str(f) for f in stray)}: no `app:` and not included by any test file. "
            "Add `app:` to run it, include it from a test file, or move it out of the folder"
        )
    return loaded


def _only(loaded: Loaded, names: Sequence[str]) -> Loaded:
    """Only the named tests, from whichever files have them."""
    known = {t.name for suite, _ in loaded for t in suite.tests}
    missing = [n for n in names if n not in known]
    for name in missing:
        users = [
            t.name
            for suite, _ in loaded
            if name in suite.library
            for t in suite.tests
            if any(isinstance(s.action, Use) and s.action.test == name for _, s in _steps(replace(suite, tests=(t,))))
        ]
        if any(name in suite.library for suite, _ in loaded):
            runs_in = f"--test one that does: {', '.join(users)}" if users else "and no test does"
            raise TestFileError(f"'{name}' is a library test: it runs only where a test uses it; {runs_in}")
    if missing:
        raise TestFileError(f"No test named: {', '.join(missing)}")
    kept = [(replace(s, tests=_named(s.tests, names)), env) for s, env in loaded]
    return [(s, env) for s, env in kept if s.tests]


def _named(tests: Sequence[Test], names: Sequence[str]) -> tuple[Test, ...]:
    return tuple(t for t in tests if t.name in names)


def _run_file(
    suite: Suite,
    env: dict[str, str],
    *,
    label: str,
    out: Path,
    runs: Runs,
    options: RunOptions,
    make_client: MakeClient,
) -> list[JobResult]:
    """Run one file on its devices, with its lockfile; prune the lockfile if asked."""
    printer = Printer(parallel=sum(len(suite.devices[p]) for p in suite.apps) > 1)
    if label:
        print(f"\n=== {label}{suite.path.suffix} ===", flush=True)
    jobs = plan(suite, out, label, printer)

    def connect() -> JevAsker:
        return make_client(suite.settings.model, env.get(API_KEY))

    model = LockedModel(suite.settings.model, suite.path.with_suffix(".lock.json"), options.lock, connect)
    try:
        done = runs.all(jobs, model, printer)
        if options.prune_lock:
            if any(r.result.failed for r in done):
                print(f"{model.path.name}: not pruned, because a test failed", flush=True)
            else:
                pruned = model.prune()
                print(f"{model.path.name}: pruned {pruned} unused {'entry' if pruned == 1 else 'entries'}", flush=True)
    finally:
        model.save()
    return done
