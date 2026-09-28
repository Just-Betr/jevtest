# Troubleshooting

Every error jevtest prints says what's wrong and what to do. This page collects the common ones, with the message you'll see.

## Test file errors

jevtest checks the whole file before touching a device and lists every problem at once:

```console
error: t.yaml has 2 problems:
  - Missing `device:`. Name the device for each platform in `app:`, e.g. {android: Pixel 4a, ios: iPhone 17 Pro}
  - Test #1 in t.yaml needs `name`, `fresh` (true: start from a clean install, false: carry on from the previous test) and `steps`
```

| Message | Fix |
|---|---|
| `Unknown step 'bakc'. A bare word must be one of back, clear_data, … for a plain-English goal write` `- do: bakc` | A typo, or a goal written without `do:`. |
| `'wait' must be a number, got '2' (remove the quotes)` | Numbers are written without quotes. |
| `'tap' needs text, got a number (to use 42 as text, put it in quotes)` | `tap: "42"`. |
| `'scroll_to' needs direction: (up, down, left or right)` | Add `direction: down` to the step. |
| `` `direction` belongs to scroll_to, not to tap `` | Options only go on the actions they apply to ([table](reference/steps.md#options)). |
| `` `timeout` only applies to a step that waits (for its element, its checks, or a do:/scroll_to: condition) `` (or `interval`, `max_actions`, `max_scrolls`, `confidence`) | The setting means nothing on that step: remove it, or move it to the step it was meant for ([settings](reference/test-file.md#settings-optional)). |
| `` `settle` is gone (jevtest 0.9): each step waits until what it needs is on screen; remove it `` | Remove `settle`: there's no waiting after an action any more. A step that needs longer gets `timeout:`. |
| `` `timeout` must be from 1 to 300, got 1000 `` | Each setting has [limits](reference/test-file.md#settings-optional). A step that needs more is usually waiting on something the app should do faster, or is two steps. |
| `t.yaml is not valid YAML: … A value starting with ${ must be quoted inside { } or [ ]` | `{android: "${PHONE}"}`. |
| ``Not set: ${PASSWORD}. Add them to .env next to the test file, or to the environment`` | Define the value. Only the `.env` **next to the test file** is read. |
| `PASSWORD is set in the environment and in …/.env to different values: remove one of them` | jevtest won't pick one. |
| `…/.env:3 is not a KEY=value line` | Fix or comment out (`#`) that line. |
| `Tests use each other in a loop: A -> B -> A` | A `use:` cycle. |
| `…/stray.yaml: no app: and not included by any test file` | In a folder run, every YAML must be a test file or an included library. |

## Devices

| Message | Fix |
|---|---|
| `No connected Android device called 'pixel 4a' (names are exact). Connected: 15241JEC211832 / Pixel 4a; emulator-5554 / sdk_gphone16k_arm64 / Pixel_10` | Use one of the listed names exactly (`Pixel 4a`). |
| `Several connected Android devices are called 'Pixel 4a' (A1, B2): name one by its serial` | Two identical phones: use serials. |
| `No booted simulator or connected iPhone called 'iPhone 16' (names are exact). Running: …` | Boot it (`xcrun simctl boot "iPhone 16"`), or use a listed name. |
| `Several devices are called 'iPhone 17 Pro' (X, Y): name one by its UDID` | Use the UDID. |
| `… is asleep or locked: unlock it` / `BH is locked: unlock it and keep it unlocked during the run` | Unlock the phone; consider a longer screen timeout while testing. |
| `The app is signed by team ABCDE12345, which is not signed into Xcode (signed in: …)` | jevtest signs its agent with your app's team: add that team's Apple Account in Xcode > Settings > Accounts. |
| `Runner.app is not signed for a real iPhone (it has no provisioning profile)` | Build the app for the device, signed with your team. |
| `Testing on a real iPhone needs signing: in Xcode, Settings > Accounts > + > Apple Account` | Sign into Xcode ([Real phones](guides/real-devices.md#iphone)). |
| `Runner.app is built for iPhoneSimulator, not a real iPhone (BH)…` | Build for the device, signed with your team. |
| `Setting location is only supported on the Android emulator` | Android phones can't take a simulated location. |
| `A real iPhone can't pre-grant permissions` | Tap the prompt in the test: `do: Allow camera access`. |

## Jev

| Message | Fix |
|---|---|
| `Waited 10s until the screen stopped moving` | A `do:` being worked out, or a `scroll_to:`, needs the screen to read the same twice in a row, and something on it keeps changing (a clock, a timer, a counter). Use exact steps there, or give the app a test mode where it holds still. |
| `TYPESAFE_API_KEY is not set: put it in the .env next to the test file, or in the environment` | Add the key, or run `--lock frozen` if everything is recorded. |
| `No steps are saved for this do: in tests.lock.json, and --lock frozen only repeats saved steps…` | This `do:` hasn't been worked out yet (or its text changed). Run `--lock record` and commit the lockfile. |
| `This screen and question are not in tests.lock.json, and --lock frozen only replays recorded decisions…` | An `expect:` (or a choice between exact matches) met a screen that wasn't recorded. Run `--lock record` and commit the lockfile. |
| `Waited 10s until the 2nd of 2 button 'Delete' is on screen; the screen shows 1, the saved step was made with 2` | The app changed since the `do:` was worked out. Run `--lock record`: it works the goal out again from where the saved steps stopped fitting. |
| `Jev HTTP 429: trying again in 0.5s (retry 1 of 4)` | Printed while TypeSafe is rate-limiting (429) or overloaded (529); jevtest retries, waiting as long as TypeSafe asks (up to a minute) or with backoff, and says so each time. |
| `Jev HTTP 401: …` | Check the key. |

## Tests that fail

- **`Could not find element 'X' on screen`**: no element says exactly *X* within `timeout`. Check the failure screenshot; maybe it's below the fold (`scroll_to:` first) or labelled differently.
- **`…; close but not exact: 'Save draft', 'Unsaved changes'`**: the screen has longer texts containing the target. Matching is exact (ignoring case), so write the whole text as the screen shows it: `tap: Save draft`. Close texts are only listed, never used.
- **`Jev says the goal is impossible from this screen`**: the goal can't be done from where the app is. Often a previous step didn't land; add a `see:` after it.
- **`Goal not reached after 10 actions (max_actions)`**: split the `do:` into smaller goals, one per step, or give a long form a higher `max_actions:`.
- **`Scrolled down to the end but never found 'X'`**: the text isn't in the list, or it's the other way (`direction: up`).
- **`Stuck repeating: tap button 'Next'`**: the action has no effect. The element may be disabled or covered.
- **`The app is no longer running (crashed or closed)`**: the app crashed. The log from `adb logcat` or the device's crash reports will say why.
- **`expect: … — Jev says false (0.31)`**: Jev judged the statement false. Read the screenshot: it's usually right. If the statement is ambiguous, make it concrete, or use `see:` for exact text.

## Web pages on Android: fields with no name

Android System **WebView 153** (a Play Store update in September 2026) stopped passing some names from web pages to Android's accessibility tree, which is all any test tool can read. Measured on the demo app, with WebView 146 for comparison:

| On the web page | WebView 146 | WebView 153 |
|---|---|---|
| `<input type="text">` with a `<label>` and a placeholder | named (`Your name`) | **no name at all**: not the label, the placeholder, `aria-label` or `title` |
| `<label><input type="radio"> Pro plan</label>` (a radio or checkbox inside its label) | named | no name, and the label's text is missing too |
| the same, with `aria-label="Pro plan"` on the input | named | named |

What to do:

- **Radios and checkboxes:** give the input an `aria-label` with its visible text. It's also what screen readers need.
- **Text fields:** nothing on the page brings the name back on WebView 153, so exact steps (`type: … into: Your name`) can't find the field. Use a goal instead: `do: Type "Bret" into the name field`. Jev picks the field from the screen, the step's output says so, and the lockfile replays the choice.

Run with `-v` to see every Jev question with its top answers and probabilities.
