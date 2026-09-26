# Contributing

Issues and pull requests are welcome at [github.com/Just-Betr/jevtest](https://github.com/Just-Betr/jevtest).

## Setup

```bash
git clone https://github.com/Just-Betr/jevtest && cd jevtest
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[dev,docs]'
```

## Checks

```bash
ruff check .                  # lint
pytest --cov                  # unit tests; 100% line and branch coverage is required
mkdocs serve                  # the docs, at http://127.0.0.1:8000
```

The unit tests need no device and no network: devices, the clock and Jev are faked, and the driver tests parse real screen captures from `tests/fixtures/`. They run in a few seconds.

The on-device agents (Java and Swift) can't be faked, so they have contract tests against a real emulator or simulator with the demo app built:

```bash
JEVTEST_DEVICE=android JEVTEST_DEVICE_NAME=emulator-5554 pytest tests/test_devices.py
JEVTEST_DEVICE=ios JEVTEST_DEVICE_NAME="iPhone 17 Pro" pytest tests/test_devices.py
```

A change to how steps behave should also pass the demo suite on a device:

```bash
jevtest run examples/demo.yaml --lock frozen --out results
```

## Layout

| Path | What |
|---|---|
| `jevtest/spec.py` | Loads and validates test files. Every rule and error message lives here. |
| `jevtest/runner.py` | Runs tests step by step against one device. |
| `jevtest/brain.py` | The questions jevtest asks Jev, and reading the answers. |
| `jevtest/jev.py`, `jevtest/lock.py` | The Jev client and the lockfile. |
| `jevtest/cli.py` | The `run` command: files, folders, devices in parallel, reports. |
| `jevtest/drivers/` | Android (`adb` + agent) and iOS (`simctl`/`devicectl` + agent). |
| `jevtest/android_agent/` | The on-device Android agent (Java), built with the SDK's own tools. |
| `jevtest/ios_agent/` | The XCUITest agent (Swift). Regenerate its project with `ruby scripts/generate_ios_agent_project.rb` after adding targets. |
| `demo_app/` | The Flutter demo app the example suite tests. |

## Principles

Changes should keep these true:

- **Nothing assumed.** No defaults, no guessing, no silent fallbacks. A mistake is an error that says what to fix.
- **Deterministic.** The same app and lockfile give the same run.
- **No fixed sleeps.** Wait for the device to reach a state, never for a time.
- **Never change the app or the device** beyond what a step asks, and put those back.
- **Jev only.** jevtest uses no other model.

## Demo app

```bash
cd demo_app
flutter build apk --debug

# iOS simulator
flutter build ios --simulator --debug --config-only
LANG=en_US.UTF-8 pod install --project-directory=ios
xcodebuild -workspace ios/Runner.xcworkspace -scheme Runner -configuration Debug -sdk iphonesimulator \
  -derivedDataPath build/ios_sim ARCHS=arm64 ONLY_ACTIVE_ARCH=YES -quiet

# iPhone, signed with your team
flutter build ios --release --config-only
xcodebuild -workspace ios/Runner.xcworkspace -scheme Runner -configuration Release \
  -destination 'id=<iPhone UDID>' -derivedDataPath build/ios_device \
  -allowProvisioningUpdates DEVELOPMENT_TEAM=<team id> CODE_SIGN_STYLE=Automatic
```

## Releasing

1. Update the version in `pyproject.toml` and `jevtest/__init__.py`, and `CHANGELOG.md`.
2. Tag `vX.Y.Z` and push the tag. The release workflow builds and publishes to PyPI.
