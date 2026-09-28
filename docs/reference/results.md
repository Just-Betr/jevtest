# Results and reports

## The console

Every step prints as it runs: the step, how long it took, what it acted on, the actions Jev chose with their confidence, and each check.

```console
▶ Native screen
  ▸ use: Sign in
    ✓ do: Sign in with email "${DEMO_EMAIL}" and password "${DEMO_PASSWORD}" (1.8s) — 3 action(s)
        → type "${DEMO_EMAIL}" into text_field 'Email'  (confidence 0.83)
        → type "${DEMO_PASSWORD}" into password_field 'Password'  (confidence 0.76)
        → tap button 'Sign in'  (confidence 0.93)
        → done  (confidence 0.96)
        ✓ expect: The home screen is showing — Jev 0.95
        ✓ see: Welcome, ${DEMO_EMAIL}
  ✓ scroll_to: Open native screen direction='down' (0.0s)
  ✓ tap: Open native screen (0.9s) — on button 'Open native screen'
      ✓ see: Native screen
  ✓ clear: Nickname (0.6s) — on text_field 'Guest'
  ✓ type: Bret into='Nickname' (0.3s) — into text_field 'Nickname'
  ✓ tap: Save nickname (0.3s) — on button 'SAVE NICKNAME'
      ✓ see: Saved: Bret
  ✓ tap: Dark theme (0.2s) — on switch 'Dark theme'
      ✓ see: Theme is dark
  ✓ tap: Delete account (1.2s) — on button 'DELETE ACCOUNT'
      ✓ expect: A dialog asks to confirm deleting the account — Jev 0.98
  ✓ tap: Delete (0.6s) — on button 'DELETE'
      ✓ see: Account deleted
  PASS Native screen (8.4s)
```

A run ends with a summary per device: the tally, each failure with its reason, and how many Jev decisions came from the lockfile or were asked live, with the time and cost (TypeSafe's price per input token; "cost unknown" for a Jev version jevtest has no price for).

```console
17/17 passed in 111s
Jev: 109 decisions, 109 from lockfile, 0 asked live in 0.0s (0% of run time), $0.0000
Results: results/20260925-205234/android/emulator-5554
```

And with failures (from a run during development):

```console
12/14 passed in 104s
  FAILED Swipe to delete an item: see: Item 26 deleted — not on screen
  FAILED Camera permission prompt: expect: The system is asking whether to allow camera access — Jev says false (0.06)
Jev: 95 decisions, 79 from lockfile, 16 asked live in 3.3s (3% of run time), $0.0009
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

One `<testsuite>` per file, platform and device (named like `jevtest.checkout.android.Pixel 8`), one `<testcase>` per test. A failed test has a `<failure>` whose message is the reason (`see: Item 26 deleted — not on screen`) and whose body is the test's full log.

### `report.json`

Everything about one device's run:

| Key | Contents |
|---|---|
| `file`, `platform`, `device`, `app`, `app_id`, `model` | what ran where |
| `passed`, `failed` | counts |
| `tests[]` | per test: `name`, `status`, `seconds`, `failure`, `log`, and `steps[]` |
| `tests[].steps[]` | per step: the step as written, `status`, `seconds`, `detail`, Jev's `decisions` with probabilities, `checks[]`, nested `steps` for `use:`, and `screenshot` for the failing step |
| `jev_calls[]` | every Jev request: the screen as Jev saw it, the questions, the answers, whether it came from the lockfile, time and cost |

### Screenshots

The step that fails a test gets a screenshot, named after the test. `screenshot: name` steps save one too.
