"""jevtest command line.

  jevtest run tests.yaml [--platform android|ios] [--device ID] [--test NAME]
  jevtest screen --app app.apk      print the screen exactly as Jev receives it
  jevtest devices                   list Android devices and iOS simulators
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
from .runner import Runner, write_report
from .spec import SpecError, load, platform_of


def load_env(*dirs: Path):
    """Read KEY=value lines from .env files; real environment variables win."""
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


def cmd_run(args) -> int:
    spec = load(args.file)
    load_env(Path.cwd(), spec.path.parent)
    if args.test:
        spec.tests = [t for t in spec.tests if t.name in args.test]
        if not spec.tests:
            raise SpecError(f"No test named {args.test}")
    platform, app = spec.app_for(args.platform)
    if not app.exists():
        raise SpecError(f"App not found: {app}")
    jev = Jev(model=spec.settings.model)  # fail fast if the key is missing

    out = Path(args.out) / time.strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    driver = make_driver(platform, args.device)
    try:
        app_id = driver.install(app)
        print(f"jevtest {__version__} · {platform} · {app_id} · {spec.settings.model}")
        runner = Runner(spec, driver, Brain(jev, spec.settings.threshold), out)
        results = runner.run()
    finally:
        driver.close()
    write_report(out, {"file": str(spec.path), "platform": platform, "app": str(app),
                       "app_id": app_id, "model": spec.settings.model}, results, jev.calls)
    total = results["passed"] + results["failed"]
    print(f"\n{results['passed']}/{total} passed · {len(jev.calls)} Jev calls · report: {out / 'report.json'}")
    return 0 if results["failed"] == 0 else 1


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
            time.sleep(2)
        s = driver.screen()
        print(json.dumps({"screen": s.to_state(), "keyboard_visible": s.keyboard_visible,
                          "app_running": s.app_running}, indent=2))
    finally:
        driver.close()
    return 0


def cmd_devices(args) -> int:
    try:
        from .drivers.android import devices
        print("Android:", ", ".join(devices()) or "(none connected)")
    except DriverError as e:
        print("Android:", e)
    try:
        from .drivers.ios import simulators
        for d in simulators():
            if "iPhone" in d["name"] or "iPad" in d["name"]:
                print(f"iOS: {d['name']} ({d['runtime']})  {d['udid']}  {d['state']}")
    except DriverError as e:
        print("iOS:", e)
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="jevtest", description="Mobile app tests driven by Jev.")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run a test file")
    r.add_argument("file")
    r.add_argument("--platform", choices=["android", "ios"])
    r.add_argument("--device", help="adb serial or simulator name/UDID")
    r.add_argument("--test", action="append", help="only run this test (repeatable)")
    r.add_argument("--out", default="jevtest-results", help="results folder")
    r.set_defaults(fn=cmd_run)

    s = sub.add_parser("screen", help="print the current screen as Jev sees it")
    s.add_argument("--app", help="install and launch this build first")
    s.add_argument("--platform", choices=["android", "ios"])
    s.add_argument("--device")
    s.set_defaults(fn=cmd_screen)

    d = sub.add_parser("devices", help="list devices")
    d.set_defaults(fn=cmd_devices)

    args = p.parse_args(argv)
    try:
        return args.fn(args)
    except (SpecError, DriverError, JevError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
