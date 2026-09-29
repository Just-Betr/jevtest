# Getting started

From nothing to a passing test in about ten minutes.

## 1. Install

jevtest runs on macOS and Linux, with Python 3.11 or newer. iOS needs a Mac.

```bash
pip install jevtest
```

Then the platform tools for the apps you test:

=== "Android"

    - The **Android SDK** with platform-tools, build-tools and one platform (Android Studio installs all three), and a **JDK** (`javac`).
    - `bundletool` on your `PATH` if you test `.aab` bundles.
    - A running emulator or a phone with USB debugging on. Check with `adb devices`.

    The first run builds jevtest's 20 KB on-device agent with the SDK's own tools (a few seconds, then cached in `~/.cache/jevtest`).

=== "iOS"

    - **Xcode** with the iOS platform installed.
    - A **booted simulator** (`xcrun simctl list devices booted`), or a connected iPhone ([Real phones](guides/real-devices.md)).

    The first run builds jevtest's small XCUITest agent (about a minute, then cached in `~/.cache/jevtest`).

jevtest only tests. It never boots, shuts down or reconfigures a device: start the emulator or simulator yourself (or in CI), then run.

## 2. Get a Jev API key

jevtest uses exactly one model: TypeSafe's **Jev**, through TypeSafe's own API. Create a key at [console.typesafe.ai/keys](https://console.typesafe.ai/keys).

Put it in a `.env` file **next to your test file** (and keep `.env` out of git):

```bash title=".env"
TYPESAFE_API_KEY=apikey_...
```

Or set it in the environment, as you would for CI secrets. If both set it to different values, jevtest stops and tells you, rather than picking one.

!!! tip "A recorded run needs no key"
    Once a run is recorded in the lockfile, `--lock frozen` replays it with no key and no network. See [How it works](how-it-works.md#the-lockfile).

## 3. Write a test file

Find your device's exact name:

```console
$ adb devices
List of devices attached
emulator-5554   device
```

```yaml title="tests.yaml"
app: build/app-debug.apk                  # your build, relative to this file

device:
  android: emulator-5554                  # exact serial, model or AVD name

tests:
  - name: App opens on the sign-in screen
    fresh: true                           # start from a clean install
    steps:
      - expect: The sign in screen is showing
        see: Sign in
```

That's the whole file: the build, the device, and the tests. Each step waits up to 10 seconds for what it looks for; a step that needs longer says so with `timeout: 30`.

## 4. Run it

```bash
jevtest run tests.yaml --lock record --out results
```

- `--lock record` repeats what `tests.lock.json` already has (the steps each `do:` took, Jev's answer for each `expect:` on each screen) and asks Jev only about anything new, saving it there.
- `--out results` is where reports, JUnit XML and failure screenshots go (each run gets its own timestamped folder).

Both flags are required, so every run says how it treats the lockfile and where its results go.

```console
jevtest 0.9.7 · android · emulator-5554 · dev.jevtest.jevtest_demo · jev-1.13.0 · lockfile: record

▶ App opens on the sign-in screen
  ✓ expect: The sign in screen is showing — Jev 0.98
  ✓ see: Sign in
  PASS App opens on the sign-in screen (2.5s)

1/1 passed in 2s
Jev: 1 decision, 0 from lockfile, 1 asked live in 0.3s (11% of run time), $0.0000
Results: results/20260926-115141/android/emulator-5554
```

Commit `tests.yaml` and `tests.lock.json`. Every recorded screen now gets the same answer on every run, and every `do:` repeats the same steps.

- **Everyday, and in CI:** `--lock record`. Saved steps and recorded screens replay exactly; only what the app hasn't shown before goes to Jev.
- **Exact replay:** `--lock frozen`. Everything must come from the lockfile, so no key or network is needed, and a `do:` with no saved steps or a screen that isn't recorded fails the run. Good for release gates and reproducing a failure.

See [Which lock mode in CI?](guides/ci.md#which-lock-mode-in-ci)

## 5. Try the demo app

The repository has a Flutter demo app with 18 tests covering every kind of step: sign-in, lists, swipes, dialogs, a native screen, a camera permission prompt, an in-app web app and a deep link.

```bash
git clone https://github.com/Just-Betr/jevtest && cd jevtest
cd demo_app && flutter build apk --debug && cd ..
cp examples/.env.example examples/.env                  # then set your key and device names
jevtest run examples/demo.yaml --lock frozen --out results
```

Building the iOS demo is in [Contributing](contributing.md#demo-app).

## Next

- [Writing tests](writing-tests.md): steps, checks, composing tests.
- [Large suites](guides/large-suites.md): secrets, shared tests, folders, several devices at once.
- [CI](guides/ci.md): GitHub Actions setup.
