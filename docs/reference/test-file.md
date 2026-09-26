# Test file reference

A test file is YAML with these top-level keys. **Nothing has a default:** every key marked required must be there, and anything unknown, misspelled or of the wrong type is an error. jevtest checks the whole file before touching a device and reports **every** problem at once.

```yaml
app: build/app.apk                        # required
device: { android: Pixel 8 }              # required
settings: { ... }                         # required
include: shared/auth.yaml                 # optional
tests: [ ... ]                            # required
```

## `app` (required)

The build to install, relative to the test file.

| File | Platform |
|---|---|
| `.apk`, `.aab` | Android (`.aab` needs `bundletool`) |
| `.app`, or a `.zip` / `.ipa` containing one | iOS: a simulator build for a simulator, a device build signed with your team for an iPhone |

One build, or one per platform:

```yaml
app: build/app.apk
# or
app:
  android: build/app.apk
  ios: build/Runner.app
```

With both, the tests run on both platforms **at the same time**. jevtest installs the build at the start of each run.

## `device` (required)

The device each platform in `app` runs on, by **exact** name. There is no "whichever is running": a name that matches no device, or more than one, is an error that lists what is connected.

| Platform | A device's name is |
|---|---|
| Android | its serial (`adb devices`), its model (`Pixel 8`), or an emulator's AVD name (`Pixel_10`) |
| iOS | a booted simulator's name or UDID, or a connected iPhone's name or UDID |

```yaml
device:
  android: Pixel 8
  ios: iPhone 17 Pro
```

A list splits the platform's tests across several devices, run at the same time ([details](../guides/large-suites.md#several-devices-at-once)):

```yaml
device:
  android: [Pixel 8, Pixel_10, emulator-5556]
```

## `settings` (required)

Every value is required.

| Setting | Type | Meaning |
|---|---|---|
| `model` | text | The Jev model. Pin a version (`typesafe/jev-1.13`); the lockfile is per model. |
| `max_actions` | whole number ≥ 1 | Actions Jev may take for one `do:` step. A step can override it. |
| `max_scrolls` | whole number ≥ 1 | Scrolls a `scroll_to:` may make. A step can override it. |
| `timeout` | number ≥ 0, seconds | How long checks and element lookups keep trying. A step can override it. |
| `settle` | number ≥ 0, seconds | The most to wait for the screen to stop changing after an action. |
| `threshold` | number between 0 and 1 | Jev's yes-probability an `expect:` needs to pass. |
| `ios_team` | text | *Only for a real iPhone:* the Apple team ID that signs jevtest's agent. Leaving it out is an error on a real iPhone, and the error lists the teams signed into Xcode. |

```yaml
settings:
  model: typesafe/jev-1.13
  max_actions: 8
  max_scrolls: 15
  timeout: 10
  settle: 3
  threshold: 0.5
```

## `include` (optional)

Library files whose tests this file can `use:`. A path, or a list of paths, relative to this file. [Details](../guides/large-suites.md#include-shared-tests).

## `tests` (required)

A list of tests, run in order.

| Key | Required | Meaning |
|---|---|---|
| `name` | yes | Unique across this file and everything it includes. |
| `fresh` | yes | `true`: stop the app, clear its data, launch it. `false`: continue from where the previous test left the app. |
| `steps` | yes | A non-empty list of [steps](steps.md). |

## `${NAME}` values

Any text in the file can contain `${NAME}`. The value comes from the `.env` next to the test file or from the environment; a name that isn't set in either is an error. In `app`, `device` and `settings` the value is filled in when the file loads. In steps it's filled in only when the app needs it, so logs and reports keep the name. [Details](../guides/large-suites.md#name-values-secrets-and-settings).

!!! warning "Quote `${NAME}` inside `{ }` and `[ ]`"
    YAML reads `{` as the start of a mapping, so `{android: ${PHONE}}` is invalid. Write `{android: "${PHONE}"}`, or use the block style (`android: ${PHONE}` on its own line).

## Types are exact

jevtest does not convert values for you:

| Written | Result |
|---|---|
| `wait: 2` | ✓ |
| `wait: "2"` | ✗ `'wait' must be a number, got '2' (remove the quotes)` |
| `tap: 42` | ✗ `'tap' needs text, got a number (to use 42 as text, put it in quotes)` |
| `scroll: DOWN` | ✗ `'scroll' must be one of up, down, left, right; got 'DOWN'` |
| `dark_mode: on` | ✓ (YAML reads `on`/`off` and `true`/`false` as true/false) |
| `dark_mode: light` | ✗ `'dark_mode' must be on or off (true or false), got 'light'` |
| `- bakc` | ✗ `Unknown step 'bakc' … for a plain-English goal write` `- do: bakc` |
