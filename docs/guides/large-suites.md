# Large suites

Four features keep a big app's tests manageable. A small app needs none of them.

## `${NAME}` values: secrets and per-machine values

Anywhere in a test file, `${NAME}` is replaced by the value of `NAME`:

- from the **`.env` file next to the test file**, or
- from the **environment** (for CI secrets).

A name that isn't set is an error before anything runs. A name set in both places to **different** values is also an error: jevtest won't guess which one you meant.

```bash title=".env  (keep it out of git)"
TYPESAFE_API_KEY=apikey_...
EMAIL=qa@example.com
PASSWORD=correct-horse
ANDROID_DEVICE=Pixel 8
```

```yaml
device: { android: "${ANDROID_DEVICE}" }
tests:
  - name: Sign in
    fresh: true
    steps:
      - do: Sign in with email "${EMAIL}" and password "${PASSWORD}"
        see: Welcome
```

**Where values go.** In `app` and `device`, values are filled in when the file loads. In steps, a value is filled in only when the app needs it: the text typed, the text compared, the element searched for, the URL opened. Logs, reports and the goals sent to Jev keep `${NAME}`.

**What the app shows.** Before a screen goes to Jev, the console, a report or the lockfile, every value on it is replaced by its `${NAME}` (screenshots are pictures, so they show it as the app does): an app that shows the signed-in email sends Jev `Welcome, ${EMAIL}`, and the lockfile has `${EMAIL}`, never the address. Only exact values are replaced: a value the app shows changed (cut short, in capitals, reformatted) is shown as it is. A password field's text is never passed on at all, only its length, as dots (iOS reports none): some Android fields report the text as typed, and React Native's shows its last character for a moment.

`.env` format: one `KEY=value` per line; a value is used as written, so `URL=https://${HOST}/x` is an error (write the whole value); `export KEY=value` and quoted values (`"…"` or `'…'`) are fine; lines starting with `#` are comments. The value is everything after `=`, so `PASSWORD=pw # mine` is an error rather than a guess: put the comment on its own line, or quote the value (`PASSWORD="pw # mine"`). Anything else, or a key set twice, is an error with its line number.

**Folders.** Each test file reads the `.env` in its own folder only. In a folder run, a test file in `sub/` doesn't see the `.env` of the folder above; the error names the `.env` it read. Keep test files next to their `.env`, and libraries in subfolders (libraries use the values of the test file that includes them).

## `include:` shared tests

A **library file** holds tests other files use. It has only `tests:` (and its own `include:`):

```yaml title="shared/auth.yaml"
tests:
  - name: Sign in
    fresh: true
    steps:
      - do: Sign in with email "${EMAIL}" and password "${PASSWORD}"
        see: Welcome
```

```yaml title="checkout.yaml"
app: build/app.apk
device: { android: Pixel 8 }
include: shared/auth.yaml

tests:
  - name: Buy one item
    fresh: true
    steps:
      - use: Sign in
      - do: Add the first item to the cart and check out
        expect: The order is confirmed
```

- Tests in an included file **don't run on their own**; files that include it can `use:` them.
- Paths are relative to the including file. Libraries can include libraries; a loop is an error.
- Test names must be unique across a file and everything it includes.
- `${NAME}` values in a library come from the `.env` next to the **test file** being run.

## Folders

```bash
jevtest run tests/ --lock record --out results
```

runs every test file in `tests/` and its subfolders, in name order, hidden folders included. Other YAML in hidden folders (`.github/workflows/ci.yml`) is left alone. Every other YAML file in the folder must be either:

- a **test file** (it has `app:`), which runs, or
- a **library** that one of those test files includes.

Anything else is an error that names the file, so a test file that lost its `app:` line is never silently skipped.

Each file keeps its own lockfile. `--test NAME` (repeatable) picks tests from any of the files. There is one exit code and one `junit.xml` for the whole folder. Files run one after another; within a file, devices run at the same time.

## Several devices at once

List devices to split a platform's tests across them:

```yaml
device:
  android: [Pixel 8, Pixel_10, emulator-5556]
  ios: [iPhone 17 Pro, iPhone 16]
```

- Tests are dealt out **in order**, one group per device in turn.
- A `fresh: false` test stays on the device of the test before it, since it continues from where that one left the app.
- Each platform in `app:` also runs at the same time as the others.
- A device left with nothing to run says so: `[android · emulator-5556] no tests left for this device`.
- While several devices run, each test's log is printed in one piece, tagged with its device, so output never interleaves:

```console
[android · Pixel 8] ▶ Checkout
[android · Pixel 8]   ✓ tap: Buy (0.4s) — on button 'Buy'
[android · Pixel 8]   PASS Checkout (6.1s)
```

Each device writes its own report under `results/<run>/<platform>/<device>/`.

**Measured.** The demo suite, when it had 17 tests: Android emulator and iOS simulator one after the other took 104 s + 212 s. The emulator, a Pixel 4a and the simulator at once took 218 s for all 34 test runs.

## A typical project

```
mobile-tests/
  .env                  # keys and logins (git-ignored; CI uses secrets)
  shared/
    auth.yaml           # Sign in, Sign out
    navigation.yaml     # Open settings, Open the cart, ...
  login.yaml            # app:, device:, include:, tests:
  checkout.yaml
  settings.yaml
  *.lock.json           # recorded Jev decisions, committed
```

```bash
jevtest run mobile-tests/ --lock record --out results
```
