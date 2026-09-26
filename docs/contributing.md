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
ruff check .                  # lint, including a docstring on every public object
mypy                          # types, strict
lint-imports                  # dependencies point inward
pytest --cov                  # unit tests; 100% line and branch coverage is required
mkdocs serve                  # the docs, at http://127.0.0.1:8000
pre-commit install            # run all of the above before each commit
```

The unit tests need no device and no network: devices, the clock and Jev are faked, and the driver tests parse real screen captures from `tests/adapters/devices/fixtures/`. They run in a few seconds.

The on-device agents (Java and Swift) can't be faked, so they have contract tests against a real emulator or simulator with the demo app built:

```bash
JEVTEST_DEVICE=android JEVTEST_DEVICE_NAME=emulator-5554 pytest tests/adapters/devices/test_contract.py
JEVTEST_DEVICE=ios JEVTEST_DEVICE_NAME="iPhone 17 Pro" pytest tests/adapters/devices/test_contract.py
```

A change to how steps behave should also pass the demo suite on a device:

```bash
jevtest run examples/demo.yaml --lock frozen --out results
```

## Layout

The package follows the [architecture](architecture.md): dependencies point inward, and `lint-imports` fails the build if one doesn't.

| Path | What |
|---|---|
| `jevtest/domain/` | What jevtest is: steps, screen, decisions, results, failures, fixed rules, and the ports. Standard library only. |
| `jevtest/application/` | How tests run: `runner.py` (the use case), `brain.py` (the questions for Jev), `planning.py` (sharding). |
| `jevtest/adapters/devices/` | Android (`adb` + a Java agent) and iOS (Xcode tools + a Swift XCUITest agent). Regenerate the iOS agent's project with `ruby scripts/generate_ios_agent_project.rb` after adding targets. |
| `jevtest/adapters/jev/` | The Jev HTTP client, its wire format, and the lockfile. |
| `jevtest/adapters/testfile/` | YAML test files into domain types, `.env` files, finding test files in folders. |
| `jevtest/adapters/reports/` | JUnit XML and the JSON report. |
| `jevtest/cli/` | Arguments, the console output, and `main.py`, the composition root. |
| `tests/` | Mirrors `jevtest/`, one test module per source module. |
| `demo_app/` | The Flutter demo app the example suite tests. |

## Principles

Changes should keep these true:

- **Nothing guessed.** No settings to get wrong, no silent fallbacks, exact names, exact types. A mistake is an error that says what to fix.
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
