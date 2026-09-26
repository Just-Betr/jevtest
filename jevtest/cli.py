"""jevtest command line.

  jevtest run PATH... --lock MODE --out DIR [--test NAME] [-v]

A test file says which app to test on which device(s) (`app:` and `device:`); jevtest runs its
tests on each platform it lists. A folder runs every test file in it; any other YAML in it must be
a library that one of those files includes. Several devices for a platform share its tests and run
at the same time, as do the platforms. It uses devices that are already running (a phone, an
emulator, a booted simulator); it does not start, stop or manage devices. Nothing is assumed:
anything missing or wrong is an error that says what to fix.

Exit codes: 0 all tests passed, 1 a test failed, 2 setup error, 130 interrupted.
"""

from __future__ import annotations

import argparse
import dataclasses
import io
import os
import re
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from . import __version__
from .brain import Brain
from .drivers.base import DriverError
from .jev import Jev, JevError
from .lock import LockedJev
from .runner import Runner, write_junit, write_report
from .spec import MODEL, SETTLE, TIMEOUT, Spec, SpecError, Test, is_test_file, load

API_KEY = "OPENROUTER_API_KEY"


ENV_LINE = re.compile(r"(?:export )?([A-Za-z_][A-Za-z0-9_]*)=(.*)")


def read_env(folder: Path) -> dict[str, str]:
    """The environment plus the KEY=value lines of `folder`/.env. A name set in both to different
    values is an error: which one is meant is not guessed."""
    f = folder / ".env"
    env = dict(os.environ)
    if not f.is_file():
        return env
    seen: dict[str, str] = {}
    for n, line in enumerate(f.read_text().splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = ENV_LINE.fullmatch(line.strip())
        if not m:
            raise SpecError(f"{f}:{n} is not a KEY=value line")
        key, value = m.groups()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if key in seen:
            raise SpecError(f"{f}:{n} sets {key} a second time")
        if key in os.environ and os.environ[key] != value:
            raise SpecError(f"{key} is set in the environment and in {f} to different values: "
                            "remove one of them")
        seen[key] = env[key] = value
    return env


def test_files(paths: list[str]) -> tuple[list[Path], list[Path]]:
    """The test files to run (as given, or every YAML file with `app:` in a folder, by name), and the
    other YAML files found in the folders, which must turn out to be included libraries."""
    files: list[Path] = []
    others: list[Path] = []
    for p in map(Path, paths):
        if p.is_dir():
            for f in sorted(f for f in p.rglob("*") if f.suffix in (".yaml", ".yml") and f.is_file()):
                (files if is_test_file(f) else others).append(f)
        elif p.exists():
            files.append(p)
        else:
            raise SpecError(f"Test file not found: {p}")
    unique = list(dict.fromkeys(f.resolve() for f in files))
    if not unique:
        raise SpecError(f"No test files (YAML with `app:`) in {', '.join(paths)}")
    return unique, [f.resolve() for f in others]


def make_driver(platform: str, device: str, app: Path):
    if platform == "android":
        from .drivers.android import AndroidDriver
        return AndroidDriver(device)
    from .drivers.ios import IOSDriver
    return IOSDriver(device, app)


@dataclass
class Job:
    """Some of a file's tests, on one device."""
    spec: Spec
    platform: str
    device: str
    out: Path
    name: str  # e.g. "login · android · Pixel 4a"

    @property
    def app(self) -> Path:
        return self.spec.apps[self.platform]


def shard(tests: list[Test], n: int) -> list[list[Test]]:
    """Deal the tests out to n devices, in order. A `fresh: false` test goes where the test before it
    went, since it carries on from where that one left the app."""
    groups: list[list[Test]] = []
    for t in tests:
        if t.fresh or not groups:
            groups.append([t])
        else:
            groups[-1].append(t)
    shards: list[list[Test]] = [[] for _ in range(n)]
    for i, group in enumerate(groups):
        shards[i % n] += group
    return shards


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("_") or "device"


def plan(spec: Spec, out: Path, label: str) -> list[Job]:
    jobs = []
    for platform in spec.apps:
        devices = spec.devices[platform]
        for device, tests in zip(devices, shard(spec.tests, len(devices)), strict=True):
            name = " · ".join(filter(None, (label, platform, device)))
            if not tests:
                print(f"[{name}] no tests left for this device ({len(devices)} devices, fewer groups of tests)",
                      flush=True)
                continue
            jobs.append(Job(dataclasses.replace(spec, tests=tests), platform, device,
                            out / platform / slug(device), name))
    return jobs


class Printer:
    """Output of devices running at the same time: each test's log is printed whole, tagged with
    its device, so lines never interleave. A single device streams its log as it goes."""

    def __init__(self, parallel: bool, out=None):
        self.parallel = parallel
        self.out = out or sys.stdout
        self.lock = threading.Lock()

    def block(self, job: Job, text: str):
        if self.parallel:
            text = "\n".join(f"[{job.name}] {line}" if line else line for line in text.split("\n"))
        with self.lock:
            print(text, file=self.out, flush=True)


def run_job(job: Job, jev: LockedJev, verbose: bool, printer: Printer) -> dict:
    """Run the job's tests on its device; return its results."""
    job.out.mkdir(parents=True, exist_ok=True)
    spec = job.spec
    driver = make_driver(job.platform, job.device, job.app)
    driver.settle, driver.timeout = SETTLE, TIMEOUT
    try:
        app_id = driver.install(job.app)
        printer.block(job, f"jevtest {__version__} · {job.name} · {app_id} · {MODEL} · "
                           f"lockfile: {jev.mode}")
        if printer.parallel:
            runner = Runner(spec, driver, Brain(jev), job.out, verbose=verbose, out=io.StringIO(),
                            on_test=lambda r: printer.block(job, "\n".join(r["log"])))
        else:
            runner = Runner(spec, driver, Brain(jev), job.out, verbose=verbose, out=printer.out)
        results = runner.run()
    finally:
        driver.close()
    write_report(job.out, {"file": str(spec.path), "platform": job.platform, "device": job.device,
                           "app": str(job.app), "app_id": app_id, "model": MODEL},
                 results, jev.calls)
    printer.block(job, summary(results, jev.calls, job.out))
    return results


def run_jobs(jobs: list[Job], jev: LockedJev, verbose: bool, printer: Printer) -> list[dict]:
    """Run the jobs at the same time, one thread per device. A setup error on any device is raised
    once they have all finished."""
    if len(jobs) == 1:
        return [run_job(jobs[0], jev.fork(), verbose, printer)]
    results: list = [None] * len(jobs)

    def work(i: int, job: Job, fork: LockedJev):
        try:
            results[i] = run_job(job, fork, verbose, printer)
        except Exception as e:  # reported below, after the other devices finish
            results[i] = e
    threads = [threading.Thread(target=work, args=(i, job, jev.fork()), daemon=True)
               for i, job in enumerate(jobs)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    for job, r in zip(jobs, results, strict=True):
        if isinstance(r, Exception):
            raise type(r)(f"{job.name}: {r}") if isinstance(r, (SpecError, DriverError, JevError)) else r
    return results


def cmd_run(args) -> int:
    files, others = test_files(args.file)
    root = Path(os.path.commonpath([f.parent for f in files]))
    loaded = []
    for f in files:
        env = read_env(f.parent)
        loaded.append((load(f, env), env))
    included = {lib for spec, _ in loaded for lib in spec.includes}
    stray = [f for f in others if f not in included]
    if stray:
        raise SpecError(f"{', '.join(str(f) for f in stray)}: no `app:` and not included by any test file. "
                        "Add `app:` to run it, include it from a test file, or move it out of the folder")
    if args.test:
        names = {t.name for spec, _ in loaded for t in spec.tests}
        missing = [n for n in args.test if n not in names]
        if missing:
            raise SpecError(f"No test named: {', '.join(missing)}")
        for spec, _ in loaded:
            spec.tests = [t for t in spec.tests if t.name in args.test]
        loaded = [(spec, env) for spec, env in loaded if spec.tests]
    for spec, _ in loaded:
        for app in spec.apps.values():
            if not app.exists():
                raise SpecError(f"App not found: {app}")
    mode = args.lock
    if args.prune_lock and (args.test or mode == "off"):
        raise SpecError("--prune-lock needs every test to run (no --test) and a lockfile (not --lock off)")
    out = Path(args.out) / time.strftime("%Y%m%d-%H%M%S")
    suites: list[tuple[str, dict]] = []
    for spec, env in loaded:  # one file at a time; its devices at the same time
        label = spec.path.relative_to(root).with_suffix("").as_posix() if len(loaded) > 1 else ""
        jobs = plan(spec, out / label if label else out, label)
        jev = LockedJev(MODEL, spec.path.with_suffix(".lock.json"), mode,
                        lambda env=env: Jev(model=MODEL, api_key=env.get(API_KEY)))
        if label:
            print(f"\n=== {spec.path.name} ===", flush=True)
        try:
            results = run_jobs(jobs, jev, args.verbose, Printer(parallel=len(jobs) > 1))
            if args.prune_lock:
                if any(r["failed"] for r in results):
                    print(f"{jev.path.name}: not pruned, because a test failed", flush=True)
                else:
                    print(f"{jev.path.name}: pruned {jev.prune()} unused decision(s)", flush=True)
        finally:
            jev.save()
        suites += [("jevtest." + job.name.replace(" · ", "."), r) for job, r in zip(jobs, results, strict=True)]
    write_junit(out / "junit.xml", suites)
    passed = sum(r["passed"] for _, r in suites)
    failed = sum(r["failed"] for _, r in suites)
    if len(suites) > 1:
        print(f"\nAll: {passed}/{passed + failed} passed ({len(loaded)} file(s), {len(suites)} device run(s)). "
              f"JUnit: {out / 'junit.xml'}")
    return 0 if failed == 0 else 1


def summary(results: dict, calls: list[dict], out: Path) -> str:
    total = results["passed"] + results["failed"]
    run_s = sum(t["seconds"] for t in results["tests"])
    live = [c for c in calls if not c.get("cached")]
    jev_s = sum(c["ms"] for c in live) / 1000
    cost = sum(c["usage"].get("cost", 0) for c in live)
    share = f" ({jev_s / run_s:.0%} of run time)" if run_s else ""
    lines = [f"\n{results['passed']}/{total} passed in {run_s:.0f}s"]
    lines += [f"  FAILED {t['name']}: {t['failure']}" for t in results["tests"] if t["status"] != "pass"]
    lines.append(f"Jev: {len(calls)} decision{'' if len(calls) == 1 else 's'}, {len(calls) - len(live)} from lockfile, "
                 f"{len(live)} asked live"
                 f" in {jev_s:.1f}s{share}, ${cost:.4f}")
    lines.append(f"Results: {out}")
    return "\n".join(lines)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="jevtest", description="Mobile app tests driven by Jev.")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run test files, or every test file in a folder")
    r.add_argument("file", nargs="+", metavar="PATH", help="test files and/or folders of them")
    r.add_argument("--test", action="append", metavar="NAME", help="only run this test (repeatable)")
    r.add_argument("--lock", required=True, choices=["record", "frozen", "refresh", "off"],
                   help="record: use recorded Jev decisions, ask Jev about new screens and record the answers; "
                        "frozen: only recorded decisions, a new screen fails the run (no key or network needed); "
                        "refresh: ask Jev again about everything and re-record; off: no lockfile")
    r.add_argument("--out", required=True, metavar="DIR",
                   help="results folder (each run adds a timestamped folder in it)")
    r.add_argument("--prune-lock", action="store_true",
                   help="after a run where every test passed, drop recorded decisions it didn't use")
    r.add_argument("-v", "--verbose", action="store_true", help="print every Jev question and answer")
    r.set_defaults(fn=cmd_run)

    return p


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        return args.fn(args)
    except (SpecError, DriverError, JevError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
