# Test file reference

A test file is YAML with four top-level keys. Every key marked required must be there, and anything unknown, misspelled or of the wrong type is an error. jevtest checks the whole file before touching a device and reports **every** problem at once.

```yaml
app: build/app.apk                        # required
device: { android: Pixel 8 }              # required
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

Any text in the file can contain `${NAME}`. The value comes from the `.env` next to the test file or from the environment; a name that isn't set in either is an error. In `app` and `device` the value is filled in when the file loads. In steps it's filled in only when the app needs it, so logs and reports keep the name. [Details](../guides/large-suites.md#name-values-secrets-and-per-machine-values).

!!! warning "Quote `${NAME}` inside `{ }` and `[ ]`"
    YAML reads `{` as the start of a mapping, so `{android: ${PHONE}}` is invalid. Write `{android: "${PHONE}"}`, or use the block style (`android: ${PHONE}` on its own line).

## Fixed rules

These are part of jevtest, not settings. They're the same for everyone, and a change to them is a new jevtest release.

| Rule | |
|---|---|
| Waiting | A step waits up to **10 seconds** for what it looks for (a check, an element). A step can say `timeout: 30`. |
| `do:` goals | At most **10 actions**. A bigger goal is two steps. |
| `scroll_to:` | Scrolls until the text appears, stopping at the **end of the content** or after **50 scrolls**. |
| `expect:` | Passes when Jev finds the statement **more likely true than false** (yes-probability above 0.5). |
| After an action | jevtest waits until the screen stops changing, for at most **3 seconds**. |
| Model | **`typesafe/jev-1.13`**, printed at the start of every run. |
| iPhone signing | jevtest's agent is signed with **the team that signed your app**. |

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
