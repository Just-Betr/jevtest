"""jevtest command line.

  jevtest run tests.yaml [--test NAME] [-v]

The test file says which app to test on which device (`app:` and `device:`); jevtest runs the
tests on each platform it lists. It uses devices that are already running (a phone, an emulator,
a booted simulator); it does not start, stop or manage devices.

Exit codes: 0 all tests passed, 1 a test failed, 2 setup error, 130 interrupted.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from . import __version__
from .brain import Brain
from .drivers.base import DriverError
from .jev import Jev, JevError
from .lock import LockedJev
from .runner import Runner, write_junit, write_report
from .spec import SpecError, load


def load_env(*dirs: Path):
    """Read KEY=value lines from .env files. Real environment variables win."""
    for d in dirs:
        f = d / ".env"
        if not f.is_file():
            continue
        for line in f.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.removeprefix("export ").split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def make_driver(platform: str, device: str | None, ios_team: str = ""):
    if platform == "android":
        from .drivers.android import AndroidDriver
        return AndroidDriver(device)
    from .drivers.ios import IOSDriver
    return IOSDriver(device, team=ios_team)


def lock_mode(args) -> str:
    chosen = [m for m, on in (("frozen", args.frozen), ("refresh", args.refresh_lock),
                              ("off", args.no_lock)) if on]
    if len(chosen) > 1:
        raise SpecError("Use only one of --frozen, --refresh-lock, --no-lock")
    return chosen[0] if chosen else "record"


def cmd_run(args) -> int:
    spec = load(args.file)
    load_env(Path.cwd(), spec.path.parent)
    if args.test:
        missing = [n for n in args.test if n not in {t.name for t in spec.tests}]
        if missing:
            raise SpecError(f"No test named: {', '.join(missing)}")
        spec.tests = [t for t in spec.tests if t.name in args.test]
    for app in spec.apps.values():
        if not app.exists():
            raise SpecError(f"App not found: {app}")
    model = spec.settings.model
    jev = LockedJev(model, spec.path.with_suffix(".lock.json"), lock_mode(args), lambda: Jev(model=model))
    out = Path(args.out) / time.strftime("%Y%m%d-%H%M%S")
    failed = 0
    try:
        for platform, app in spec.apps.items():  # each platform in the file, on its device
            failed += run_platform(spec, platform, app, jev, out / platform, args.verbose)
    finally:
        jev.save()
    return 0 if failed == 0 else 1


def run_platform(spec, platform: str, app: Path, jev: LockedJev, out: Path, verbose: bool) -> int:
    """Run every test on this platform's device; return how many failed."""
    out.mkdir(parents=True, exist_ok=True)
    driver = make_driver(platform, spec.devices.get(platform), spec.settings.ios_team)
    driver.settle, driver.timeout = spec.settings.settle, spec.settings.timeout
    calls_before = len(jev.calls)
    try:
        app_id = driver.install(app)
        print(f"jevtest {__version__} · {platform} · {app_id} · {spec.settings.model} · lockfile: {jev.mode}")
        results = Runner(spec, driver, Brain(jev), out, verbose=verbose).run()
    finally:
        driver.close()
    calls = jev.calls[calls_before:]
    write_report(out, {"file": str(spec.path), "platform": platform, "app": str(app), "app_id": app_id,
                       "model": spec.settings.model}, results, calls)
    write_junit(out / "junit.xml", f"jevtest.{platform}", results)
    print(summary(results, calls, out))
    return results["failed"]


def summary(results: dict, calls: list[dict], out: Path) -> str:
    total = results["passed"] + results["failed"]
    run_s = sum(t["seconds"] for t in results["tests"])
    live = [c for c in calls if not c.get("cached")]
    jev_s = sum(c["ms"] for c in live) / 1000
    cost = sum(c["usage"].get("cost", 0) for c in live)
    share = f" ({jev_s / run_s:.0%} of run time)" if run_s else ""
    lines = [f"\n{results['passed']}/{total} passed in {run_s:.0f}s"]
    lines += [f"  FAILED {t['name']}: {t['failure']}" for t in results["tests"] if t["status"] != "pass"]
    lines.append(f"Jev: {len(calls)} decisions, {len(calls) - len(live)} from lockfile, {len(live)} asked live"
                 f" in {jev_s:.1f}s{share}, ${cost:.4f}")
    lines.append(f"Results: {out} (report.json, junit.xml)")
    return "\n".join(lines)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="jevtest", description="Mobile app tests driven by Jev.")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run a test file")
    r.add_argument("file")
    r.add_argument("--test", action="append", help="only run this test (repeatable)")
    r.add_argument("--out", default="jevtest-results", help="results folder")
    r.add_argument("-v", "--verbose", action="store_true", help="print every Jev question and answer")
    r.add_argument("--frozen", action="store_true",
                   help="only use recorded Jev decisions; fail on anything new (for CI)")
    r.add_argument("--refresh-lock", action="store_true", help="ask Jev again and re-record every decision")
    r.add_argument("--no-lock", action="store_true", help="don't read or write the lockfile")
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
