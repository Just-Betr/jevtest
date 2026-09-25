"""jevtest command line.

  jevtest run tests.yaml [--platform android|ios] [--device ID] [--test NAME] [-v]
  jevtest screen --app app.apk      print the screen exactly as Jev receives it
  jevtest devices                   list Android devices and iOS simulators

Exit codes: 0 all tests passed, 1 a test failed, 2 setup error, 130 interrupted.
"""

from __future__ import annotations

import argparse
import json
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
from .spec import SpecError, load, platform_of


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


def make_driver(platform: str, device: str | None):
    if platform == "android":
        from .drivers.android import AndroidDriver
        return AndroidDriver(device)
    from .drivers.ios import IOSDriver
    return IOSDriver(device)


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
    platform, app = spec.app_for(args.platform)
    if not app.exists():
        raise SpecError(f"App not found: {app}")
    model = spec.settings.model
    jev = LockedJev(model, spec.path.with_suffix(".lock.json"), lock_mode(args), lambda: Jev(model=model))

    out = Path(args.out) / time.strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    driver = make_driver(platform, args.device)
    try:
        app_id = driver.install(app)
        print(f"jevtest {__version__} · {platform} · {app_id} · {model} · lockfile: {jev.mode}")
        results = Runner(spec, driver, Brain(jev), out,
                         verbose=args.verbose).run()
    finally:
        driver.close()
        jev.save()

    write_report(out, {"file": str(spec.path), "platform": platform, "app": str(app),
                       "app_id": app_id, "model": model}, results, jev.calls)
    junit = Path(args.junit) if args.junit else out / "junit.xml"
    write_junit(junit, f"jevtest.{platform}", results)
    print(summary(results, jev, out, junit))
    return 0 if results["failed"] == 0 else 1


def summary(results: dict, jev: LockedJev, out: Path, junit: Path) -> str:
    total = results["passed"] + results["failed"]
    run_s = sum(t["seconds"] for t in results["tests"])
    live = [c for c in jev.calls if not c.get("cached")]
    jev_s = sum(c["ms"] for c in live) / 1000
    cost = sum(c["usage"].get("cost", 0) for c in live)
    share = f" ({jev_s / run_s:.0%} of run time)" if run_s else ""
    lines = [f"\n{results['passed']}/{total} passed in {run_s:.0f}s"]
    lines += [f"  FAILED {t['name']}: {t['failure']}" for t in results["tests"] if t["status"] != "pass"]
    lines.append(f"Jev: {len(jev.calls)} decisions, {jev.hits} from lockfile, {len(live)} asked live"
                 f" in {jev_s:.1f}s{share}, ${cost:.4f}")
    lines.append(f"Report: {out / 'report.json'} · JUnit: {junit}")
    return "\n".join(lines)


def cmd_screen(args) -> int:
    app = Path(args.app).resolve() if args.app else None
    platform = args.platform or (platform_of(app) if app else None)
    if not platform:
        raise SpecError("Pass --app or --platform")
    driver = make_driver(platform, args.device)
    try:
        if app:
            driver.install(app)
            driver.launch()
            driver.wait_idle(5)
        s = driver.screen()
        print(json.dumps({"screen": s.to_state(), "keyboard_visible": s.keyboard_visible}, indent=2))
    finally:
        driver.close()
    return 0


def cmd_devices(args) -> int:
    from .drivers.android import devices
    from .drivers.ios import simulators
    try:
        print("Android:", ", ".join(devices()) or "(none connected)")
    except DriverError as e:
        print("Android:", e)
    try:
        for d in simulators():
            print(f"iOS: {d['name']} ({d['runtime']})  {d['udid']}  {d['state']}")
    except DriverError as e:
        print("iOS:", e)
    return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="jevtest", description="Mobile app tests driven by Jev.")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run a test file")
    r.add_argument("file")
    r.add_argument("--platform", choices=["android", "ios"])
    r.add_argument("--device", help="adb serial, or simulator name / UDID")
    r.add_argument("--test", action="append", help="only run this test (repeatable)")
    r.add_argument("--out", default="jevtest-results", help="results folder")
    r.add_argument("--junit", help="JUnit XML path (default: <results>/junit.xml)")
    r.add_argument("-v", "--verbose", action="store_true", help="print every Jev question and answer")
    r.add_argument("--frozen", action="store_true",
                   help="only use recorded Jev decisions; fail on anything new (for CI)")
    r.add_argument("--refresh-lock", action="store_true", help="ask Jev again and re-record every decision")
    r.add_argument("--no-lock", action="store_true", help="don't read or write the lockfile")
    r.set_defaults(fn=cmd_run)

    s = sub.add_parser("screen", help="print the current screen as Jev sees it")
    s.add_argument("--app", help="install and launch this build first")
    s.add_argument("--platform", choices=["android", "ios"])
    s.add_argument("--device")
    s.set_defaults(fn=cmd_screen)

    d = sub.add_parser("devices", help="list devices")
    d.set_defaults(fn=cmd_devices)
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
