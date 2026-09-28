# Results and reports

## The console

Every step prints as it runs: the step, how long it took, what it acted on, and each check. A `do:` lists its steps: the saved ones it repeated, or the ones Jev worked out with their confidence.

```console
▶ Native screen
  ▸ use: Sign in
    ✓ do: Sign in with email "${DEMO_EMAIL}" and password "${DEMO_PASSWORD}" (2.2s) — 3 saved steps
        → type "${DEMO_EMAIL}" into text_field 'Email'
        → type "${DEMO_PASSWORD}" into password_field 'Password'
        → tap button 'Sign in'
        ✓ expect: The home screen is showing — Jev 0.96
        ✓ see: Welcome, ${DEMO_EMAIL}
  ✓ scroll_to: Open native screen (direction: down) (0.3s)
  ✓ tap: Open native screen (0.4s) — on button 'Open native screen'
      ✓ see: Native screen
  ✓ clear: Nickname (1.4s) — on text_field 'Guest'
  ✓ type: Bret (into: Nickname) (0.9s) — into text_field 'Nickname'
  ✓ hide_keyboard (0.3s)
  ✓ tap: Save nickname (0.4s) — on button 'SAVE NICKNAME'
      ✓ see: Saved: Bret
  ✓ tap: Dark theme (0.3s) — on switch 'Dark theme'
      ✓ see: Theme is dark
  ✓ tap: Delete account (0.4s) — on button 'DELETE ACCOUNT'
      ✓ expect: A dialog asks to confirm deleting the account — Jev 0.98
  ✓ tap: Delete (0.4s) — on button 'DELETE'
      ✓ see: Account deleted
  PASS Native screen (10.6s)
```

The first time, before its steps are saved, a `do:` shows what Jev chose:

```console
  ✓ do: Sign in with email "${EMAIL}" and password "${PASSWORD}" (3.8s) — 3 steps, worked out by Jev
      → type "${EMAIL}" into text_field 'Email'  (Jev, confidence 0.83)
      → type "${PASSWORD}" into password_field 'Password'  (Jev, confidence 0.45)
      → tap button 'Sign in'  (Jev, confidence 0.94)
      → done  (Jev, confidence 0.96)
      ✓ expect: The home screen is showing — Jev 0.96
```

A run ends with a summary per device: the tally, each failure with its reason, and how many Jev decisions came from the lockfile or were asked live, with the time and cost (TypeSafe's price per input token; "cost unknown" for a Jev version jevtest has no price for).

```console
17/17 passed in 142s
Jev: 26 decisions, 26 from lockfile, 0 asked live in 0.0s (0% of run time), $0.0000
Results: jevtest-results/20260928-160826/android/emulator-5554
```

And with failures:

```console
1/2 passed in 5s
  FAILED Check fails: see: Welcome back — Waited 2s until 'Welcome back' is on screen
Jev: 0 decisions, 0 from lockfile, 0 asked live in 0.0s (0% of run time), $0.0000
Results: r/20260928-153436/android/emulator-5554
```

`-v` adds every Jev question with its top three answers and their probabilities.

## Files

```
results/
  20260925-201517/                 # one folder per run
    junit.xml                      # every file, platform and device in the run
    android/
      emulator-5554/
        report.json
        001_FAIL_Swipe_to_delete_an_item.png
    ios/
      iPhone_17_Pro/
        report.json
```

When several files run, each gets its own folder first: `results/<run>/checkout/android/Pixel_8/`.

### `junit.xml`

One `<testsuite>` per file, platform and device (named like `jevtest.checkout.android.Pixel 8`), one `<testcase>` per test. A failed test has a `<failure>` whose message is the reason (`see: Welcome back — Waited 2s until 'Welcome back' is on screen`) and whose body is the test's full log.

### `report.json`

Everything about one device's run:

| Key | Contents |
|---|---|
| `file`, `platform`, `device`, `app`, `app_id`, `model` | what ran where |
| `passed`, `failed` | counts |
| `tests[]` | per test: `name`, `status`, `seconds`, `failure` (null when it passed), `log` (its console lines), `steps[]`, and `screenshot` when the app couldn't be started |
| `tests[].steps[]` | per step: `step` (as written), `status`, `seconds`; and when there is one: `detail` (what it acted on, or why it failed), `decisions` (each action Jev chose working out a `do:`: `did`, `confidence`, `probabilities`), `ran` (the saved steps a `do:` repeated), `checks[]` (`check`, `text`, `status`, `detail`), `steps` (a `use:`'s steps) and `screenshot` (the step that failed) |
| `model_calls[]` | every Jev request: `state` (the screen as Jev saw it, values shown as `${NAME}`), `questions`, `answers`, `from_lockfile`, `ms`, `cost`, `served_by` (the Jev version that answered) |

### Screenshots

The step that fails a test gets a screenshot, numbered in the order taken and named after the test: `001_FAIL_Check_fails.png`. `screenshot: name` steps save one too (`002_home.png`), once the screen has stopped moving.
