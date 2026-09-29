# Contributing

Issues and pull requests are welcome at [github.com/Just-Betr/jevtest](https://github.com/Just-Betr/jevtest).

## Setup

jevtest uses [uv](https://docs.astral.sh/uv/). `uv.lock` pins every tool, so your checks match CI's exactly.

```bash
git clone https://github.com/Just-Betr/jevtest && cd jevtest
uv sync --all-groups
uv run pre-commit install       # run every check below before each commit
```

## Checks

CI runs each of these on every push; pre-commit runs them before each commit.

| Check | Command | What it holds the code to |
|---|---|---|
| Lint | `uv run ruff check .` | Every ruff rule. The few that are off are listed in `pyproject.toml`, each with its reason. |
| Format | `uv run ruff format --check .` | One format everywhere. |
| Types | `uv run mypy` | The package `--strict`, plus unreachable code and the extra error codes (explicit `@override`, redundant expressions, ...). The tests too: every function body is checked; pytest's test functions and fixtures aren't annotated. |
| Types, second opinion | `uv run pyright` | The package in strict mode, the tests in standard mode. Parsed YAML and JSON are typed from the parser to the domain: no `Any`. |
| Architecture | `uv run lint-imports` | Dependencies point inward; the domain and use cases do no I/O. |
| Complexity | (part of the lint) | No function above a cyclomatic complexity of 10. |
| Tests | `uv run pytest --cov` | 100% line and branch coverage, random order, warnings are errors, Hypothesis properties. |
| Spelling | `uv run codespell` | Code and docs. |
| Package | `uv build && uvx twine check --strict dist/*` | The wheel builds, its metadata is valid, and it runs installed on its own. |
| Agents | CI only (needs the Android SDK and Xcode) | The Android agent compiles with every `javac` warning as an error and builds into an APK the way jevtest builds it; the iOS agent builds with Swift warnings as errors. |
| Security | `uv export --locked --all-groups --no-emit-project --format requirements-txt > requirements.txt && uvx pip-audit --strict --disable-pip -r requirements.txt`, `uvx zizmor --persona=pedantic .github/workflows` | No known vulnerability in a locked dependency; the GitHub workflows pass zizmor's pedantic audit, with every action pinned to a commit. |
| Docs | `uv run mkdocs build --strict` | No broken links or anchors. `uv run mkdocs serve` previews them at http://127.0.0.1:8000. |

The unit tests need no device and no network: devices, the clock and Jev are faked, and the driver tests parse real screen captures from `tests/adapters/devices/fixtures/`. They run in a few seconds.

The on-device agents (Java and Swift) can't be faked, so they have contract tests against a real emulator or simulator with the demo app built:

```bash
JEVTEST_DEVICE=android JEVTEST_DEVICE_NAME=emulator-5554 uv run pytest tests/adapters/devices/test_contract.py
JEVTEST_DEVICE=ios JEVTEST_DEVICE_NAME="iPhone 17 Pro" uv run pytest tests/adapters/devices/test_contract.py
```

A change to how steps behave should also pass the demo suite on a device:

```bash
uv run jevtest run examples/demo.yaml --lock frozen --out results
```

## Layout

The package follows the [architecture](architecture.md): dependencies point inward, and `lint-imports` fails the build if one doesn't.

| Path | What |
|---|---|
| `jevtest/domain/` | What jevtest is: steps, screen, decisions, results, failures, settings, and the ports. Standard library only. |
| `jevtest/application/` | How tests run: `runner.py` (the use case), `brain.py` (the questions for Jev), `planning.py` (sharding). |
| `jevtest/adapters/devices/` | Android (`adb` + a Java agent) and iOS (Xcode tools + a Swift XCUITest agent). Shared: `common.py` (the base device, running tools, putting back what steps changed), `cache.py` (agent builds and what runs mark as in use), `claim.py`, `finder.py`, and `tool_says.py` (every tool message jevtest recognises). Per platform: the `Device` (`android.py`, `ios.py`), its tooling on the computer (`android_tools.py`, `ios_tools.py`: finding devices, signing, building the agent), and its screen parser (`android_screen.py`, `ios_screen.py`). Regenerate the iOS agent's project with `ruby scripts/generate_ios_agent_project.rb` after adding targets. |
| `jevtest/adapters/jev/` | The Jev HTTP client, its wire format, and the lockfile. |
| `jevtest/adapters/testfile/` | YAML test files into domain types: `values.py` (single values), `steps.py` (the `ACTIONS` table), `loader.py` (the file), plus `.env` files and finding test files in folders. |
| `jevtest/adapters/reports/` | JUnit XML and the JSON report. |
| `jevtest/cli/` | Arguments, the console output, and `main.py`, the composition root. |
| `tests/` | Mirrors `jevtest/`, one test module per source module. |
| `demo_app/` | The Flutter demo app the example suite tests. |

## Principles

Changes should keep these true:

- **Nothing guessed.** No silent fallbacks, exact names, exact types. A setting has a default that suits most apps and limits that keep tests honest. A mistake is an error that says what to fix.
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

1. Update `__version__` in `jevtest/__init__.py` (`pyproject.toml` reads it from there) and `CHANGELOG.md`.
2. Tag `vX.Y.Z` and push the tag. The release workflow builds and publishes to PyPI.
