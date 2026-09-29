# Test file reference

A test file is YAML with five top-level keys. Every key marked required must be there, and anything unknown, misspelled or of the wrong type is an error. jevtest checks the whole file before touching a device and reports **every** problem at once.

```yaml
app: build/app.apk                        # required
device: { android: Pixel 8 }              # required
include: shared/auth.yaml                 # optional
settings: { timeout: 20 }                 # optional
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

A file has one iOS build, and a simulator build runs only on simulators, a device build only on real iPhones. To test on both, use two files (they can `include:` the same tests). A device the build doesn't suit is an error before the run.

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

## `settings` (optional)

Most test files need none: the defaults suit most apps. A `settings:` block changes a default for every step in the file, and a step can change one for itself.

| Setting | Default | Limits | What it does | A step can set it on |
|---|---|---|---|---|
| `timeout` | `10` | 1–300 s | How long a step waits until what it needs is true: its element on screen, its checks passing, a `do:` or `scroll_to:` screen that stopped moving. Then it fails. | steps that wait: with checks, an element, `do:`, `scroll_to:` |
| `interval` | `0.25` | 0.05–2 s, and no longer than `timeout` | How often a waiting step checks again. | the same steps as `timeout` |
| `max_actions` | `10` | 1–50 | Actions a `do:` goal may take before it fails. | `do:` |
| `max_scrolls` | `50` | 1–500 | Scrolls a `scroll_to:` may make. It also stops at the end of the content. | `scroll_to:` |
| `confidence` | `0.5` | 0.5–0.99 | An `expect:` passes when Jev's probability that the statement is true is above this. `0.5` means "more likely true than false". | steps with an `expect:` |
| `model` | `jev-1.13.0` | a pinned Jev version (`jev-X.Y.Z`) | The Jev version that decides and judges. Changing it re-asks Jev: it's part of every lockfile entry. | the whole file only |

```yaml
settings:
  confidence: 0.8        # every expect: in this file needs Jev at least 80% sure

tests:
  - name: Upload
    fresh: false
    steps:
      - tap: Upload
      - see: Upload complete
        timeout: 60      # this one step waits up to a minute
```

The limits are where a setting stops tuning a test and starts hiding a problem: a 10-minute wait, a 200-action goal or an `expect:` that passes when Jev thinks it's false would all let a broken app pass. Values outside them, and a setting on a step it means nothing for, are errors. Included files have no settings of their own: their tests run with the settings of the file being run.

**When to change one:**

- **`timeout`** on the one step that's slow for a reason (an upload, a payment). Raise it for the file only if the whole app is slow, e.g. a debug build.
- **`max_actions`**: lower it to hold a goal to a short path; raise it for a long form. A goal that needs more than 20 is usually two steps.
- **`confidence`**: raise it (0.8 is a good strict value) when an `expect:` must not pass on a guess.

## `tests` (required)

A list of tests, run in order.

| Key | Required | Meaning |
|---|---|---|
| `name` | yes | Unique across this file and everything it includes. |
| `fresh` | yes | `true`: stop the app, clear its data, launch it. `false`: continue from where the previous test left the app. |
| `steps` | yes | A non-empty list of [steps](steps.md). |

## `${NAME}` values

Any text in the file can contain `${NAME}`. The value comes from the `.env` next to the test file or from the environment; a name that isn't set in either is an error. In `app` and `device` the value is filled in when the file loads. In steps it's filled in only when the app needs it, so logs and reports keep the name.

The reverse holds for what the app shows: before a screen is sent to Jev or printed, every value on it is replaced by its `${NAME}`. An app that shows the signed-in user's email sends Jev `Welcome, ${DEMO_EMAIL}`, so the value never reaches Jev, the logs or the lockfile, and a lockfile recorded with one person's values replays with another's. [Details](../guides/large-suites.md#name-values-secrets-and-per-machine-values).

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
| `wait: 600` | ✗ `'wait' must be from 0 to 300 seconds, got 600` |
| `key: Enter` | ✗ `Unknown key 'Enter'. Keys: backspace, delete, enter, …` |
| `key: 67` | ✗ `'key' needs text, got a number (to use 67 as text, put it in quotes)`; `key: "67"` ✓ |
| `- back` with `see:` under it | ✗ YAML error, then `On line N, a step with checks under it needs a colon after its action:` `- back:` |
