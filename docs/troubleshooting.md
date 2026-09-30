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
| ``'scroll_to' needs `direction:` (up, down, left or right)`` | Add `direction: down` to the step. |
| `` `direction` belongs to scroll_to, not to tap `` | Options only go on the actions they apply to ([table](reference/steps.md#options)). |
| `` `timeout` only applies to a step that waits (for its element, its checks, or a do:/scroll_to: condition) `` (or `interval`, `max_actions`, `max_scrolls`, `confidence`) | The setting means nothing on that step: remove it, or move it to the step it was meant for ([settings](reference/test-file.md#settings-optional)). |
| `` `settle` is gone (jevtest 0.9): each step waits until what it needs is on screen; remove it `` | Remove `settle`: there's no waiting after an action any more. A step that needs longer gets `timeout:`. |
| `` `timeout` must be from 1 to 300, got 1000 `` | Each setting has [limits](reference/test-file.md#settings-optional). A step that needs more is usually waiting on something the app should do faster, or is two steps. |
| `t.yaml is not valid YAML: … A value starting with ${ must be quoted inside { } or [ ]` | `{android: "${PHONE}"}`. |
| ``t.yaml uses ${PASSWORD}, which is not set. Add it to /…/.env (the .env next to this test file), or to the environment`` | Define the value. Only the `.env` **next to the test file** is read, not one in a folder above. |
| `…/.env:2: is ' #…' a comment or part of PASSWORD?` | Put the comment on its own line, or quote the value. |
| `On line 5, a step with checks under it needs a colon after its action:` `- back:` | A bare action (`- back`) with `see:` under it needs a colon. |
| `'wait' must be from 0 to 300 seconds, got 600` / `Unknown key 'Enter'. Keys: …` | Waits and background times are capped at 300 s; key names are exact and listed. |
| `` `interval` (2s) is longer than `timeout` (1s): a step would check only once `` | Shorten `interval`, or lengthen `timeout`. |
| `Test 'T': grant: has no ios permission, and the file runs on ios` / `'camera' isn't a full Android permission name` | Name the permission for every platform the file runs on: `grant: {android: android.permission.CAMERA, ios: camera}` ([names](reference/steps.md#grant-permission-names)). |
| `Test 'T': network: can't run on iOS` / `Test 'T': key: home is Android only` | Put tests with `network:` or an Android-only key in a file whose `app:` is Android only. |
| `--lock refresh asks Jev about every do: and expect:, and t.yaml has them, but TYPESAFE_API_KEY is not set` | Add the key, or use `--lock frozen`. |
| `'Sign in' is a library test: it runs only where a test uses it; --test one that does: …` | `--test` a test that uses it. |
| `PASSWORD is set in the environment and in …/.env to different values: remove one of them` | jevtest won't pick one. |
| `…/.env:3 is not a KEY=value line` | Fix or comment out (`#`) that line. |
| `Tests use each other in a loop: A -> B -> A` | A `use:` cycle. |
| ``…/stray.yaml: no `app:` and not included by any test file`` | In a folder run, every YAML must be a test file or an included library. |

## Devices

| Message | Fix |
|---|---|
| `No connected Android device called 'pixel 4a' (names are exact). Connected: 15241JEC211832 / Pixel 4a; emulator-5554 / sdk_gphone16k_arm64 / Pixel_10` | Use one of the listed names exactly (`Pixel 4a`). |
| `Several connected Android devices are called 'Pixel 4a' (A1, B2): name one by its serial` | Two identical phones: use serials. |
| `No booted simulator or connected iPhone called 'iPhone 16' (names are exact). Running: …` | No device has that name: use a listed one exactly. |
| `The simulator 'iPhone 16e' isn't booted: boot it (xcrun simctl boot "iPhone 16e")` | Boot it: jevtest never boots devices. |
| `The iPhone 'BH' is paired but not connected: plug it in with USB, unlock it and keep it awake` | The phone is asleep, locked or unplugged. |
| `android · emulator-5554: another jevtest run (pid 1234) is testing it: wait for it to finish, or use another device` | Two runs can't share a device: the second would restart the app under the first. |
| `putting back what a run that was stopped left changed: dark mode, network` | Not an error: a run killed outright left those changed, and this one puts them back first. |
| `stopping the iOS agent a stopped run left running (pid 62281)` | Not an error: a run killed outright couldn't stop its agent on the simulator or iPhone, and this one stops it first, as it does an Android agent a run left. |
| `can't put back rotation (another jevtest version changed it): set it by hand` | A run of another jevtest version was killed with that changed, and wrote down how to put it back in a way this version doesn't read. Set it back on the device yourself. |
| `Several devices are called 'iPhone 17 Pro' (X, Y): name one by its UDID` | Use the UDID. |
| `Lost the Android agent during /tree …` / `Lost the iOS agent during /state …; the next test starts it again`, then `the iOS agent had stopped: starting it again` | Something stopped jevtest's agent (another UI automation tool, Xcode, a simulator hiccup): that test fails and the next one starts the agent again. On an iPhone, check it's unlocked and plugged in. |
| `… is asleep or locked: unlock it` / `BH is locked: unlock it and keep it unlocked during the run` | Unlock the phone; consider a longer screen timeout while testing. |
| `BH asked for your passcode to allow UI testing, and it wasn't given: unlock it, enter the passcode when it asks, …` | The iPhone asked to authenticate UI testing and nobody answered (XCUITest: `Authentication canceled`, `Not authorized for performing UI testing actions`). Unlock it, enter the passcode when it asks, keep it unlocked. |
| `emulator-5554 has no room to install the app (INSUFFICIENT_STORAGE): free some space on it, …` | The device is full. Uninstall apps you don't need, or wipe an emulator's data (Device Manager > Wipe Data). |
| `The app is signed by team ABCDE12345, which is not signed into Xcode (signed in: …)` | jevtest signs its agent with your app's team: add that team's Apple Account in Xcode > Settings > Accounts. |
| `Runner.app is not signed for a real iPhone (it has no provisioning profile)` | Build the app for the device, signed with your team. |
| `Runner.app is built for iPhoneSimulator, not a real iPhone (BH)…` | Build for the device, signed with your team. |
| `Setting location is only supported on the Android emulator` | Android phones can't take a simulated location. |
| `t.yaml runs on the iPhone BH, where jevtest can't pre-grant permissions` | Run that test on a simulator, or tap the prompt in the test: `do: Allow camera access`. |
| `Can't grant android.permission.RECORD_AUDIO: the app doesn't declare it in its manifest` | Declare it in the app, or leave it out of `grant:`. |
| `No keyboard came up within 3 seconds: iOS presses keys only into a field, so tap one first` | Tap the field before `key:`. |
| `No app on the device opens myapp://x: check the link, and that the app registers its scheme` | Check the link and the app's URL scheme. |

## Jev

| Message | Fix |
|---|---|
| `Waited 10s until the screen stopped moving` | A `do:` being worked out, or a `scroll_to:`, needs the screen to read the same twice in a row, and something on it keeps changing (a clock, a timer, a counter). Use exact steps there, or give the app a test mode where it holds still. |
| `TYPESAFE_API_KEY is not set: put it in the .env next to the test file, or in the environment` | Add the key, or run `--lock frozen` if everything is recorded. |
| `No steps are saved for this do: in tests.lock.json, and --lock frozen only repeats saved steps…` | This `do:` hasn't been worked out yet (or its text changed). Run `--lock record` and commit the lockfile. |
| `This screen and question are not in tests.lock.json, and --lock frozen only replays recorded decisions…` | An `expect:` (or a choice between exact matches) met a screen that wasn't recorded. Run `--lock record` and commit the lockfile. |
| `Waited 10s until the 2nd of 2 button 'Delete' is on screen and stopped moving; the screen shows 1, the saved step was made with 2` | The app changed since the `do:` was worked out, or this device shows it differently from the one it was worked out on. For a changed app, run `--lock record`: it works the goal out again from where the saved steps stopped fitting. For a device that differs (two Android versions can name a web field differently), give it a test file, and so a lockfile, of its own. |
| `Jev HTTP 429: trying again in 0.5s (retry 1 of 4)` | Printed while TypeSafe is rate-limiting (429) or overloaded (529); jevtest retries, waiting as long as TypeSafe asks (up to a minute) or with backoff, and says so each time. |
| `Jev unreachable after 4 retries (…): check the network` | No connection to TypeSafe's API for about 8 s. That test fails and the next one tries again. With every decision recorded, `--lock frozen` needs no network. |
| `Jev HTTP 401: …` | Check the key. The run stops there (exit 2): every question would be refused the same way. |
| `Jev HTTP 400: Unknown model: jev-1.12.0 (settings: model names a Jev version TypeSafe serves; …)` | `settings: model` names a version TypeSafe doesn't serve. The run stops there (exit 2). |

## Tests that fail

- **`Waited 10s until an element says 'X' on screen and stopped moving`**: no element says exactly *X* within `timeout`. Check the failure screenshot; maybe it's below the fold (`scroll_to:` first) or labelled differently. If it ends `; the app is in the background` or `; the app isn't running`, an earlier step left the app.
- **`…; the keyboard is up, and the app may not show it while it is …`**: added when what a step looks for isn't on screen and the keyboard is up. In landscape an Android keyboard leaves the app a strip, and fields there can lose their labels. Close it first with `hide_keyboard`, as a person would.
- **``…; the app has it off screen: bring it on screen first, e.g. with `scroll_to:` ``**: added when what a step looks for isn't on screen, but the app reports it where the screen doesn't show it, such as a web page's text below the screen. `see:` checks what's on screen, as a person looks: scroll to it first.
- **`…; what says 'Sign in' doesn't take text (button)`**: `type: into:` or `clear:` named something that isn't a field.
- **`The text field did not get keyboard focus within 3 seconds: …`** (Android): `type:` or `clear:` tapped the field, and it never took the keys. The end says what jevtest saw: *no text field has the focus* (the tap reached none: something over the app took it, or the field isn't a text field), *the focus stayed on the field that had it before the tap* (the tap didn't reach the new field: seen once on a phone as a notification came in), or *a text field has the focus, but the keyboard isn't up*. Run it again; if it keeps happening, the screenshot shows what was over the field.
- **`Waited 10s until the keyboard is up (a field takes typed text); tap the field first, or name it with into:`**: `type:` without `into:` needs a field with the keyboard up.
- **`…; text 'Gadget 1' is under a system bar, like the status bar, which takes a touch there instead of the app …`**: the app draws that element under Android's status bar (or navigation bar), where a finger reaches the phone, not the app. jevtest touches the part of an element outside the bars; this one has none. Keep what the app wants touched inside the safe area (in React Native, `SafeAreaView` from `react-native-safe-area-context`: the core one pads only on iOS).
- **A "Save password to Google?" sheet covers the app** after a sign-in, on a phone with a Google account, and the next steps can't find anything: Android's autofill service is offering to save what the test typed. Start the test with `autofill: off`.
- **A "Save Password?" alert covers the app on iOS** after a sign-in, and nothing on the app can be found until it's answered: add `tap: Not Now` after the sign-in.
- **`…; close but not exact: 'Save draft', 'Unsaved changes'`**: the screen has longer texts containing the target. Matching is exact (ignoring case), so write the whole text as the screen shows it: `tap: Save draft`. Close texts are only listed, never used.
- **`Jev says the goal is impossible from this screen`**: the goal can't be done from where the app is. Often a previous step didn't land; add a `see:` after it.
- **`Goal not reached after 10 actions (max_actions); Jev's next would be …`**: split the `do:` into smaller goals, one per step, or give a long form a higher `max_actions:`.
- **`… (Jev types only a goal's "quoted" values, and this goal has none: if it needs to type, quote them)`**: added to a failed goal with nothing in quotes: when Jev says it's impossible or runs out of `max_actions`, and when it's stuck on a text field. If it needed typing, put what to type in quotes: `do: Type "hello" into Email`.
- **`The app left the foreground after back`**: a `do:` move left the app (back on the first screen), so the goal stopped there.
- **`Scrolled down to the end but never found 'X'`**: the text isn't in the list, or it's the other way (`direction: up`).
- **`Stuck repeating: tap button 'Next', which changes nothing on the screen`**: Jev chose the same action again after it left the screen as it was, twice. The element may be disabled or covered. (Repeating an action that does change the screen, like scrolling down a long list, is fine.)
- **`The app is no longer running (crashed or closed)`**, or a step's wait ending **`; the app isn't running`**: the app crashed. The log from `adb logcat` or the device's crash reports will say why.
- **On an iPhone with iOS 27, the app isn't running from the first step**, and its crash report (`xcrun devicectl device copy from --device <UDID> --domain-type systemCrashLogs --source / --destination crashes`) shows `UIApplicationEvaluateRuntimeIssueForNoSceneLifecycleAdoption`: iOS 27 ends an app that hasn't adopted the scene lifecycle when it's launched for testing (measured: the same build ran when opened by hand). Adopt it (a Flutter app: `FlutterSceneDelegate` in `Info.plist`, as the demo app does).
- **`expect: … — Waited 10s until Jev judged it true of a screen that stopped moving; Jev says false (0.31)`**: Jev judged the statement false. Read the screenshot: it's usually right. If the statement is ambiguous, make it concrete, or use `see:` for exact text.

## Web pages on Android: fields with no name

Android System **WebView 153** (a Play Store update in September 2026) stopped passing some names from web pages to Android's accessibility tree, which is all any test tool can read. Measured on the demo app, with WebView 146 for comparison:

| On the web page | WebView 146 | WebView 153 |
|---|---|---|
| `<input type="text">` with a `<label>` and a placeholder | named (`Your name`) | **no name at all**: not the label, the placeholder, `aria-label` or `title` |
| `<label><input type="radio"> Pro plan</label>` (a radio or checkbox inside its label) | named | no name, and the label's text is missing too |
| the same, with `aria-label="Pro plan"` on the input | named | named |

What to do:

- **Radios and checkboxes:** give the input an `aria-label` with its visible text. It's also what screen readers need.
- **Dropdowns:** WebView 124 (Android 15's) reports a `<select>` by its label only, not the option chosen (measured: not in its text, description or state). No check can see the choice there, so `expect: Canada is the selected country` fails although Canada shows. Check what the choice leads to instead (`see: "Submitted: Canada, …"`). WebView 153 does report it.
- **Text fields:** which name finds a web `<input>` depends on the WebView: its HTML `id` on WebView 91, 146 and 153, its `<label>` on WebView 124 and 146 and on iOS ([measured](guides/webviews.md)). For tests that run on more than one device, use a goal: `do: Type "Bret" into the name field`. Jev picks the field from the screen, the step's output says so, and the lockfile replays the choice.

Run with `-v` to see every Jev question with its top answers and probabilities.
