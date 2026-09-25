# jevtest

A mobile app test harness driven by **Jev**, TypeSafe AI's decision model, called through OpenRouter. No other model is used.

You give it an app build and a YAML test file, then run one command:

```bash
jevtest run tests.yaml
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

## Setup

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e .
export OPENROUTER_API_KEY=sk-or-...        # https://openrouter.ai/keys
```

- **Android:** `adb` plus Android SDK build-tools (for `aapt2`). `bundletool` is needed for `.aab`. Uses the connected device or emulator, or boots your first AVD.
- **iOS:** Xcode. Uses the booted simulator, or boots an iPhone. The first run builds a small XCUITest agent (about a minute, then cached in `~/.cache/jevtest`).

## Test file

```yaml
app: build/app.apk            # .apk / .aab (Android), .app / .zip / .ipa with a simulator .app (iOS)
# or both:  app: { android: app.apk, ios: Runner.app }  and pick with --platform

settings:                     # all optional
  model: ~typesafe/jev-latest
  max_actions: 8              # Jev actions allowed per plain-English step
  timeout: 10                 # seconds to wait for expect / see / element lookups
  settle: 1.0                 # pause after each action
  threshold: 0.5              # Jev yes-probability needed for expect to pass

tests:                        # run in this order
  - name: Sign in
    steps:
      - do: Sign in with email "me@x.dev" and password "hunter22"   # action: Jev drives
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
| `- tap: target` / `double_tap:` / `long_press:` | Jev finds the element you describe (an exact unique label match skips Jev) |
| `- type: {text: "abc", into: Email}` | Type into a field. Without `into:`, types into the focused field |
| `- clear: Email` | Erase a text field |
| `- scroll: down` (up/left/right) | Scroll the content |
| `- swipe: up` or `- swipe: left` + `target: Item 3` | Finger swipe across the screen or on one element |
| `- scroll_to: Item 30` | Scroll until Jev sees it (`direction:`, `max_scrolls:`) |
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

## Commands

```bash
jevtest run tests.yaml [--platform android|ios] [--device SERIAL|UDID|NAME] [--test NAME] [--out DIR]
jevtest screen --app app.apk      # print the current screen exactly as Jev receives it
jevtest devices
```

Exit code is `0` when everything passes, `1` when a test fails, `2` for setup errors.

Results go to `jevtest-results/<timestamp>/`: `report.json` (every step, and every Jev request and answer with its probabilities) plus screenshots.

## Demo app

`demo_app/` is a small Flutter app that exercises every action (login, counter, double tap, long press dialog, switch, long list, swipe to delete, detail page). The login password is `hunter22`.

```bash
cd demo_app
flutter build apk --debug
# iOS simulator (Flutter 3.38 + Xcode 27 needs arm64-only):
xcodebuild -project ios/Runner.xcodeproj -scheme Runner -configuration Debug -sdk iphonesimulator \
  -derivedDataPath build/ios_sim ARCHS=arm64 ONLY_ACTIVE_ARCH=YES -quiet
cd .. && jevtest run examples/demo.yaml --platform android
```

## Limits (v0.1)

- iOS runs on the **simulator** only. A device `.ipa` can't be installed there; build with `-sdk iphonesimulator`.
- Jev is text-only, so anything with no accessibility label (canvas, games, unlabeled images) is invisible to it.
- `input text` on Android is ASCII only.

## Development

```bash
pip install -e . pytest && pytest -q
ruby scripts/generate_ios_agent_project.rb   # only if you change the iOS agent's targets
```

MIT licensed.
