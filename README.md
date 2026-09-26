# jevtest

A mobile app test harness driven by **Jev**, TypeSafe AI's decision model, called through OpenRouter. No other model is used.

You give it an app build and a YAML test file, then run one command:

```bash
jevtest run tests.yaml        # or a folder of test files: jevtest run tests/
```

## How it works

```
 test step ──► read the screen ──► screen as text ──► Jev picks from options ──► do it ──► repeat
                (uiautomator /       (elements,          (next action, which        (adb /
                 XCUITest tree)       positions in words)   element, pass/fail)       XCUITest)
```

Jev reads text only and answers by **choosing from options you give it**. It never writes free text. So jevtest:

1. Reads the app's accessibility tree and turns it into a short list: `{"id": "e4", "type": "button", "text": "Sign in", "position": "top-center"}`.
2. Asks Jev one request with several questions: *what's the next action* (tap, type, scroll, back, done, impossible, …), *on which element*, and *which value from the step to type*.
3. Runs the action, waits for the UI to settle, and asks again until Jev answers `done` or `impossible`, or the action limit is hit.
4. Checks `expect:` steps with a Jev yes/no question and a probability threshold.

Text to type always comes from your test file (anything in `"quotes"`), never from the model.

## Deterministic runs: the lockfile

Jev's probabilities wobble slightly between identical calls, so a close call can come out differently. Measured on this project's own runs: 4 of 97 action decisions changed at least once across 20 identical repeats. Yes/no checks never changed.

jevtest makes runs deterministic with a **lockfile** next to your test file (`tests.yaml` → `tests.lock.json`). It stores Jev's answer for every exact screen + question:

- **The first time** a screen is seen, Jev is asked and the answer is recorded.
- **After that**, the same screen and question always get the same answer, with no network call.
- **If the app changes**, its screen text changes, so it's a new question and Jev is asked fresh. Recorded answers are never applied to a screen they weren't recorded on.

Commit the lockfile. In CI, run with `--frozen`: every decision must come from the lockfile, a new screen fails the run, and no API key or network is needed. `--refresh-lock` re-asks Jev for everything; `--no-lock` ignores the file.

The model is pinned (`typesafe/jev-1.13`), as TypeSafe recommends, so an alias update can't change behaviour underneath you.

## Setup

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e .
export OPENROUTER_API_KEY=sk-or-...        # https://openrouter.ai/keys (or put it in a .env file)
```

- **Android:** the Android SDK (platform-tools, build-tools, one platform) and a JDK. `bundletool` is needed for `.aab`. Tests on a device that is already connected or running (`adb devices`). The first run builds a 12 KB on-device agent (a few seconds, then cached in `~/.cache/jevtest`).
- **iOS:** Xcode and a **booted** simulator. The first run builds a small XCUITest agent (about a minute, then cached).

jevtest only tests. It does not start, stop or manage devices: start the emulator or boot the simulator yourself (or in CI), then run. It installs the build your test file names, uses its small on-device agent, and writes results.

Both agents stay running for the whole run and answer "what's on screen?" in milliseconds (Android ~3 ms, iOS ~40 ms). The iOS agent uses only public XCTest API.

**How jevtest waits.** There are no fixed sleeps:
- **After an action**, it waits until the screen has not changed for 150 ms (0.5 s after launching the app, because startup pauses longer). On Android "not changed" means the accessibility tree, plus the pixels whenever a window opened or closed: a dialog or permission prompt sliding in reports its final positions only when it lands, so only the pixels show it moving. Decoration inside a window that stays put (a tap ripple, a blinking cursor) moves nothing and is not waited for. On iOS the agent compares snapshots (iOS has no change events; WebDriverAgent and Maestro poll too).
- **jevtest never changes the app or the device to make tests easier.** Animations stay on; the only settings a run changes are ones a step asks for (like `rotate:`), and those are restored afterwards.
- **When a check or element isn't there yet**, it waits for the screen to differ from the one it last looked at, then looks again. Jev is only asked again when the screen actually changed.

**System prompts.** Permission dialogs are part of the screen on both platforms (on iOS they belong to SpringBoard, and the agent merges them in), so a test can `expect:` a prompt and `do: Allow camera access`. Android installs do not auto-grant permissions; use `grant:` to pre-grant one.

## Test file

```yaml
app: build/app.apk            # .apk / .aab (Android), .app / .zip / .ipa with a simulator .app (iOS)
# or both:  app: { android: app.apk, ios: Runner.app }   -> the tests run on each, at the same time

device:                       # optional; leave it out to use the device that is running
  android: Pixel 4a           # a phone's model, an emulator's AVD name, or a serial
  ios: iPhone 17 Pro          # a booted simulator's name, or a real iPhone's name, or a UDID
  # android: [Pixel 4a, Pixel_10]   -> several devices share the tests and run at the same time

include: shared/auth.yaml     # optional; tests other files keep, for `use:` (see "Large suites")

settings:                     # all optional
  model: typesafe/jev-1.13     # pinned; the lockfile is per model
  max_actions: 8              # Jev actions allowed per plain-English step
  timeout: 10                 # seconds to wait for expect / see / element lookups
  settle: 3.0                 # most seconds to wait for the UI to go idle after an action
  threshold: 0.5              # Jev yes-probability needed for expect to pass

tests:                        # run in this order
  - name: Sign in
    steps:
      - do: Sign in with email "${EMAIL}" and password "${PASSWORD}"   # action: Jev drives; values from .env
        expect: The home screen greets the user                     # check: Jev judges
        see: Welcome                                                # check: exact text

  - name: Counter
    steps:
      - use: Sign in                        # run the "Sign in" test's steps first
      - do: Tap the Add one button twice
        see: "Taps: 2"
```

**Every step is one action, then the checks that must hold after it.** Checks are optional, and a step can be checks only (`- expect: …`). Checks retry until `timeout`, so a slow screen still passes. To check several things of the same kind, give a list: `see: [Welcome, Log out]`.

**`use: <test name>`** runs another test's steps inside this one, so tests build on each other. Uses can nest, but a loop is an error. The used test still runs on its own too.

Each test starts from a clean install (clear data + launch) unless you set `fresh: false`. The first failing step or check stops that test, takes a screenshot, and the run moves on to the next test.

### Steps

**Checks** (go under any action, or alone):

| Check | Passes when |
|---|---|
| `expect: statement` | Jev says the statement is true of the screen |
| `see: text` / `not_see: text` | The exact text is / isn't on screen (case-insensitive, no model) |

**Actions** (one per step):

| Action | What it does |
|---|---|
| `- do: sentence` (or just `- sentence`) | Jev works out the taps and typing to reach this goal. Values in `"quotes"` are what it may type. `max_actions: N` overrides the limit |
| `- use: test name` | Run another test's steps here |
| `- tap: target` / `double_tap:` / `long_press:` | Finds the element: exact label first, then an element containing that text (both in code); only a description that isn't on-screen text ("the login button") goes to Jev, which picks and then confirms |
| `- type: {text: "abc", into: Email}` | Type into a field. Without `into:`, types into the focused field |
| `- clear: Email` | Erase a text field |
| `- scroll: down` (up/left/right) | Scroll the content |
| `- swipe: up` or `- swipe: left` + `target: Item 3` | Finger swipe across the screen or on one element |
| `- scroll_to: Item 30` | Scroll until that text is on screen (matched in code, like `see:`; `direction:`, `max_scrolls:`) |
| `- key: enter` | enter, delete, tab, escape, space (+ Android keycodes) |
| `- back` / `- home` / `- hide_keyboard` | Navigation. iOS has no system back button, so `back` taps the nav bar back button or does the edge swipe |
| `- launch` / `- stop` / `- restart` / `- clear_data` / `- reinstall` | App lifecycle |
| `- background: 3` | Send the app to the background for N seconds, then bring it back |
| `- wait: 2` | Sleep |
| `- rotate: landscape` | portrait, landscape, landscape_right, portrait_upside_down |
| `- open_url: myapp://path` | Deep link |
| `- location: 37.77,-122.41` | GPS (Android emulator, iOS simulator) |
| `- dark_mode: on` | Dark or light appearance |
| `- grant: CAMERA` | Runtime permission (Android permission name, or simctl service e.g. `photos`) |
| `- network: off` | Wi-Fi and data off/on (Android only; the simulator shares the Mac's network) |
| `- screenshot: name` | Save a PNG into the results folder |

A bare word like `- back` is fine alone. To put checks under it, add a colon: `- back:` then the checks on the next lines.

After every action the harness also fails the step if the app crashed or left the foreground.

## Large suites

Four things keep a big app's tests manageable. None of them is needed for a small one.

**`${NAME}` values: logins, secrets, per-environment settings.** Anywhere in a test file, `${NAME}` is replaced by the value of `NAME` from a `.env` file (next to the test file, or in the folder you run from) or from the environment, which wins, so CI secrets need no file. A name that isn't set is an error before anything runs. Values are only put in when the app needs them (the text typed, the text compared, the element searched for, the URL opened). Jev's goals, the logs and the reports keep `${NAME}`. What the app itself shows on screen is part of the screen Jev reads (a password field shows only dots; a typed email is visible), so put secrets in password fields.

```
# .env  (git-ignored)
EMAIL=qa@example.com
PASSWORD=...
```

**`include:` shares tests between files.** A library file has only `tests:` (and its own `include:`). Its tests don't run by themselves; files that include it can `use:` them. Paths are relative to the including file; names must be unique across all of them.

```yaml
# shared/auth.yaml
tests:
  - name: Sign in
    steps:
      - do: Sign in with email "${EMAIL}" and password "${PASSWORD}"
        see: Welcome

# checkout.yaml
app: build/app.apk
include: shared/auth.yaml
tests:
  - name: Buy one item
    steps:
      - use: Sign in
      - do: Add the first item to the cart and check out
        expect: The order is confirmed
```

**A folder runs as one suite.** `jevtest run tests/` runs every test file in it and its subfolders in name order (a file without `app:` is a library and is skipped). Each file keeps its own lockfile. `--test NAME` picks tests from any of the files. There is one exit code and one `junit.xml` for the lot.

**Several devices run at once.** List devices to split a platform's tests across them: `device: { android: [Pixel 4a, Pixel 8, Pixel_10] }`. Tests are dealt out in order, and a `fresh: false` test stays on the same device as the test before it, since it continues from where that one left the app. Each platform in `app:` also runs at the same time as the others. While several devices run, each test's log is printed in one piece, tagged with its device: `[android · Pixel 8] ▶ Checkout`.

A typical project:

```
mobile-tests/
  .env                  # EMAIL, PASSWORD (git-ignored; CI uses secrets)
  shared/
    auth.yaml           # Sign in, Sign out
    navigation.yaml     # Open settings, Open the cart, ...
  login.yaml            # app:, device:, include:, tests:
  checkout.yaml
  settings.yaml
  *.lock.json           # recorded Jev decisions, committed
```

## Testing on a real phone

**Android phone:** turn on Developer options > USB debugging, plug it in, tap **Allow**. That's all. Name it in `device:` (for example `android: Pixel 4a`) and keep it unlocked while tests run.

**iPhone:** Apple only runs code on an iPhone when it is signed by a developer account the phone trusts, so each person does this once:

1. **Xcode > Settings > Accounts > + > Apple Account**: sign in (a free Apple ID works; a paid account avoids 7-day expiry).
2. **On the iPhone:** Settings > Privacy & Security > **Developer Mode** on; then Settings > Developer > **Enable UI Automation**.
3. **Plug it in**, tap **Trust**, and keep it unlocked while tests run.
4. **Your app must be a device build signed with your team** (an `.app`, or an `.ipa` containing one; Release, or any build that runs without a debugger attached).
5. Put the iPhone's name in the test file: `device: { ios: My iPhone }`.

The first run builds jevtest's agent, signs it with your Xcode team and registers the phone with that team (about a minute); later runs reuse it. jevtest reaches the phone through the USB connection Xcode already keeps to it, so nothing else needs installing. Every step works on a real iPhone except `grant:` (iOS doesn't allow pre-granting permissions; let the test tap the permission prompt instead). If several teams are signed into Xcode, choose one with `settings: { ios_team: ABCDE12345 }`.

## Commands

```bash
jevtest run PATH... [--test NAME]... [-v] [--out DIR] [--frozen | --refresh-lock | --no-lock]
```

`PATH` is a test file or a folder of them; give as many as you like.

| Exit code | Meaning |
|---|---|
| 0 | every test passed |
| 1 | a test failed |
| 2 | setup error (bad test file, no device, missing key, …) |
| 130 | interrupted |

### Output

Every run prints each step, the actions Jev chose, and each check. It ends with a summary: failures with their reason, and how many Jev decisions came from the lockfile vs. were asked live, with time and cost. `-v` also prints every Jev question with its top answers and probabilities.

Each run writes to `jevtest-results/<timestamp>/`:
- `junit.xml` for CI test reporting: one suite per file, platform and device;
- `<platform>/report.json` with every step, check and Jev request/answer, and a screenshot of every failure. With several devices this is `<platform>/<device>/`; when running several files, each file gets its own folder first (`checkout/android/...`).

### CI

```yaml
# GitHub Actions, on a macOS runner with a simulator
- run: pip install jevtest
- run: jevtest run tests/ --frozen
  env: { EMAIL: "${{ secrets.QA_EMAIL }}", PASSWORD: "${{ secrets.QA_PASSWORD }}" }
- uses: actions/upload-artifact@v4
  if: always()
  with: { name: jevtest-results, path: jevtest-results }
```

## Demo app

`demo_app/` is a small Flutter app that exercises every action: login, counter, double tap, long press dialog, switch, content that loads a second later, long list, swipe to delete, detail page, an in-app **WebView** with plain HTML (input, button, checkbox, link), and a **native screen** (Android Views / iOS UIKit, not Flutter) with a text field, switch, native confirm dialog and a real **camera permission prompt**. The login password is `hunter22`.

```bash
cd demo_app
flutter build apk --debug
# iOS simulator (Flutter 3.38 + Xcode 27 needs arm64-only):
flutter build ios --simulator --debug --config-only
LANG=en_US.UTF-8 pod install --project-directory=ios
xcodebuild -workspace ios/Runner.xcworkspace -scheme Runner -configuration Debug -sdk iphonesimulator \
  -derivedDataPath build/ios_sim ARCHS=arm64 ONLY_ACTIVE_ARCH=YES -quiet
cd .. && cp examples/.env.example examples/.env && jevtest run examples/demo.yaml

# iOS device build (for examples/demo_iphone.yaml), signed with your team:
cd demo_app && flutter build ios --release --config-only
xcodebuild -workspace ios/Runner.xcworkspace -scheme Runner -configuration Release \
  -destination 'id=<iPhone UDID>' -derivedDataPath build/ios_device \
  -allowProvisioningUpdates DEVELOPMENT_TEAM=<your team id> CODE_SIGN_STYLE=Automatic
```

## Limits

- Jev is text-only, so anything with no accessibility label (canvas, games, unlabeled images) is invisible to it.
- `input text` on Android is ASCII only (non-ASCII text fails the step clearly).
- **In-app WebViews are fully supported** on both platforms: their HTML elements appear like native ones and are driven the same way. The example suite covers typing, clearing and retyping, password fields, a `<select>` dropdown, radio buttons, checkboxes, form submission, links, JavaScript `alert()` shown as an app dialog, navigation between pages of a web app, and scrolling a long page.
- iOS runs are slower than Android on the demo app: an XCUITest tap costs ~0.55 s. Animations are never turned off: jevtest tests the app as users see it, and waits for it.
- Very large screens are snapshotted in full on iOS (no depth limit yet).
- Tested on the Flutter demo app with native and web screens, on the Android emulator (API 37), a Pixel 4a (Android 13), iOS 26 simulators and an iPhone 17 (iOS 27).

## Development

```bash
pip install -e '.[dev]'
ruff check jevtest tests
pytest --cov                                 # 100% line + branch coverage is enforced in CI
ruby scripts/generate_ios_agent_project.rb   # only if you change the iOS agent's targets
```

The unit tests need no device and no network: devices, the clock and Jev are faked, and the driver tests parse real screen captures from `tests/fixtures/`.

The on-device agents (Java and Swift) are tested against a real emulator or simulator with the demo app built:

```bash
JEVTEST_DEVICE=android pytest tests/test_devices.py
JEVTEST_DEVICE=ios pytest tests/test_devices.py
```

MIT licensed.
