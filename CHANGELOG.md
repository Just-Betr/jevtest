# Changelog

## Unreleased

- Jev unreachable says how often it tried and what to do: check the network, or `--lock frozen`, which needs none.
- A rejected API key (Jev HTTP 401) stops the run with exit 2 and says to check the key; it failed each test in
  turn with the same error, exit 1.
- iOS starts its agent again before the next test when something stopped it, as Android does: every later test
  used to fail with `Lost the iOS agent`. Measured by killing the agent mid-test on the simulator and the iPhone:
  that test fails, the next ones pass.
- On Windows, `jevtest` says it runs on macOS and Linux, instead of failing to import `fcntl`; the getting-started
  guide says so too.
- An app path written with `~` says a test file doesn't expand it, instead of only naming a path with `~` inside.
- Tested: an `.aab` build installs through bundletool and passes the emulator suite; `--prune-lock` removes exactly
  what the docs say, and refuses after a failure.
- A change jevtest can't note how to put back (the cache folder is full or read-only) is refused before it's made,
  with what to do, instead of a traceback; so is a cache folder that can't hold an agent build's in-use mark.
- `tap: Don't allow` never matched a permission prompt: Android and iOS write *Don’t* with a curly apostrophe
  (measured), and matching compared quotes exactly. Curly quotes now match the straight ones a keyboard types.
- On an iPhone, a `fresh: true` test could start with a permission the previous test decided: an uninstall
  doesn't always clear them (measured: a camera denial outlived one reinstall in three, so no prompt came). A
  reinstall now resets the app's permissions, on the simulator too, so every fresh test starts as a new install.
- Docs: WebView 146 names a web field by its label as well as its id (measured on Android 13); iOS 27 as iOS 26.
  The release steps named a version in `pyproject.toml`, which reads it from `jevtest/__init__.py`.
- Numbers you wrote are printed as written, never rounded: a step showed `location: -33.8688, 151.209` for
  151.2093, and `latitude 90.0000001` was refused as "got 90".
- A `location:` on an iPhone is cleared at the end of the run, so the phone uses its actual location again; jevtest
  didn't clear it before. It's set with `devicectl` now, whose simulation lasts until cleared, and a killed run's is
  cleared by the next run.
- Putting something back that fails (the device went away) is said, with what to set by hand; it was silent. The docs said a location goes back on every device: the Android emulator keeps
  the last one set, as it can't report where it was, and the docs now say so.
- A JUnit suite for a file in a folder is named with dots all the way (`jevtest.cart.checkout.android.Pixel 8`), not
  with the folder's slash (`jevtest.cart/checkout…`), which CI report viewers show as part of a class name.
- Docs: three troubleshooting messages now read exactly as jevtest prints them.
- A `grant:` can name an app's own permission on Android (`com.example.app.SCAN`): any full name, with its package,
  is accepted, as Android records every permission with one. Only `android.permission.…` names were before.
- A step a platform can't run is an error when the file loads, for every test in it, not only those a `--test` picks:
  `network:` or an Android-only `key:` (`home`, a key code, …) in a file that runs on iOS, and a `grant:` without names
  for each platform. An Android-only key used to fail on iOS only when the step ran.
- When the Android agent can't do a request, it says why (`Android agent /pixels: …`), instead of the run reporting
  the agent as lost. The agent now takes which fields are editable from jevtest, so the two can't disagree.
- What a killed run left changed is put back by the same code as at the end of a run, and named in words. An entry
  written by another jevtest version that this one can't read is named (`can't put back rotation (…): set it by
  hand`) instead of skipped without a word; that includes one left by a killed 0.9.7 run on iOS.
- A location out of range names the value and its limits (`latitude must be from -90 to 90, got 91`), as every
  other number does.
- Two jevtest versions running at once no longer delete each other's agent build: tidying the cache removed every
  older build, including one another run's `xcodebuild` was running its agent from. A run now marks the build it
  uses (a lock the operating system lets go of when the run ends, however it ends), and tidying skips it.
- An Android agent build that stopped partway (Ctrl-C while signing) left a partial APK that later runs took for
  built and installed. The APK, its `.idsig` and the signing key now appear whole or not at all; an `.idsig` from an
  earlier build is removed when the new one has none.
- An iPhone that failed to start after its agent started (no USB tunnel) left the agent's `xcodebuild` running.
- A cache folder that can't hold a device's claim (full, read-only) is a clear error saying what to do, not a
  traceback, and the claim's file is closed.
- An iOS `.zip` / `.ipa` build is unpacked once per device, not three times (to check it before the run, for its
  team, and to install it): the check reads its Info.plist from inside the archive.

## 0.9.7

- iOS: `hide_keyboard` right after typing could type a `.` instead of closing the keyboard (seen in landscape on an
  iPhone on iOS 27): the keys are still sliding in for about a second after the keyboard is up (measured), and the
  agent tapped Done where it was drawn at that moment. It now presses the return key as a key, which Done is.
- Several devices with one name (`iPhone 17 Pro` on iOS 26.3 and 26.5) are listed with what each runs, so you can
  tell which UDID to use: `D9656DC8-… (iOS 26.5), 657BA69D-… (iOS 26.3)`.
- Tested: every suite on the iOS 26.3, 26.4 and 26.5 simulators (a 17e's smaller screen included); the demo suite
  records and replays exactly on each.

## 0.9.6

- Android 12 and older: jevtest took the app to be in the foreground always, since those versions print no
  `topResumedActivity`: `home` then failed ("still in the foreground"), and a crash or a `do:` leaving the app went
  unnoticed. It now reads `mResumedActivity` there (measured on Android 12, 13, 15 and 17).
- Docs: which name finds a web page's text field depends on the WebView version (id on WebView 91, 146 and 153, label
  on 124 and iOS, measured): the webviews guide has the table, and says to use `do:` across devices. Verified: the CI
  guide's Android workflow runs as written on GitHub's Linux runners (Android 14 emulator).
- Docs: on WebView 124 (Android 15) a web dropdown doesn't report the option chosen, so no check can see it
  (measured); troubleshooting says to check what the choice leads to.

## 0.9.5

- A file listing a simulator and a real iPhone together ran the simulator's tests, then stopped with a setup error
  when the iPhone got the simulator build, so the tests dealt to the iPhone never ran. Each iOS device's build is now
  checked before the run. The test file reference says a file has one iOS build: use two files for both kinds.
- iOS simulator: `open_url:` with the app's own scheme stopped at iOS's "Open in “App”?" prompt and the app stayed in
  the background (a real iPhone didn't ask). Links now open through jevtest's agent on simulators too, as on iPhones.
  The demo app has a `jevtestdemo://` scheme and a deep-link test; it passes on all four test devices.
- An `.aab` without `bundletool` installed is an error before the run, not when each Android device starts.

## 0.9.4

- `--lock frozen`: a `do:` whose saved steps no longer fit the app says which saved step and what to do
  (`(saved step 4 of 4: if the app changed since this do: was worked out, run --lock record …)`).
- A key given twice in one place (two `see:` in a step, two `app:`) was silently dropped by YAML, which keeps the
  last: a failing check written first vanished and the test passed. It's now an error with both line numbers
  (`see: [A, B]` checks several texts). Tabs used for indenting get a hint: YAML allows only spaces.
- A `.env` value using another one (`URL=https://${HOST}/x`) reached the app as written, `${HOST}` and all, with no
  warning: many dotenv tools fill it in. It's now an error: write the whole value.
- A `.env` or test file saved by Windows Notepad (UTF-8 with a byte-order mark) failed: `.env:1 is not a KEY=value
  line`, on a line that looked right. Test files, `.env` and lockfiles are now read as UTF-8 whatever the machine's
  locale, a byte-order mark and Windows line endings are fine, and a file in another encoding says to save it as UTF-8
  (it was a Python traceback).
- Docs: "Writing tests" advised `--lock frozen` for CI, against the CI guide (`record` everyday, `frozen` for release
  gates); troubleshooting quotes the messages jevtest prints now; `use:` runs only the used test's steps.

## 0.9.3

- A device that's there but not ready is one clear error before the run, instead of a failure per test or "no
  device called …": an Android phone asleep or locked, an iPhone paired but not connected (`plug it in with USB,
  unlock it and keep it awake`), a simulator that isn't booted (`boot it (xcrun simctl boot "iPhone 16e")`).

- Android types any text: `José`, `日本`, emoji. adb's `input text` types only a US keyboard's keys, so a line with
  other letters now goes in at the cursor through jevtest's agent, all at once rather than key by key (ASCII is still
  typed key by key). Into a password field only while it's empty: Android hides what's in it. Tests that typed such
  text were an error before the run; now they run. Measured on Flutter, native and web fields, on Android 17 and 13
  (where the web page's focused field had to be found by walking the screen: WebView 146 answers with the WebView).

- The same letters in another Unicode encoding (an é stored as an e and an accent, as text copied from macOS
  often is) didn't match: `see: José` failed on a screen showing José. Text is now compared in one encoding (NFC).
  So is `${NAME}` masking: a value shown in the other encoding was printed and sent to Jev as it was.

## 0.9.2

- `grant:` takes several permissions (`grant: [camera, microphone]`), and names per platform, so a file that runs
  on both can grant: `grant: {android: android.permission.CAMERA, ios: camera}`. The simulator restarts the app once
  for the whole list. A file that runs on a real iPhone and has a `grant:` is an error before the run (Apple doesn't
  allow pre-granting), as is one that runs on both platforms with a `grant:` naming only one. The steps reference
  lists the names on each platform.
- Android 17 answers `grant:` of a permission the app doesn't declare with no error, and grants nothing, so the step
  passed (measured; Android 13 refuses it). jevtest now reads back what Android recorded, and fails with the reason:
  `Can't grant android.permission.RECORD_AUDIO: the app doesn't declare it in its manifest`.
- A run killed outright (`kill -9`, or a CI job past its grace period) left the device as its steps had set it: dark
  mode on, Wi-Fi off. jevtest now keeps what puts each change back on disk, and the next run on the device puts it
  back first: `putting back what a run that was stopped left changed: dark mode, network`. On Android, rotation is
  put back through the window manager's own lock too: a stopped agent's lock turned auto-rotate off again over the
  setting (measured).
- The agent cache (`~/.cache/jevtest`) only grew: every jevtest whose agent changed built another, about 150 MB for
  iOS, and every iOS run left a log named by its port. Measured: 6.5 GB. Older agent builds are now removed when an
  agent starts, and the iOS log is one per device, like Android's (the same cache measured 235 MB afterwards).

## 0.9.1

**Upgrading from 0.9.0: run once with `--lock record`, then commit the lockfile.** 0.9.0 could record an
`expect:` or a `do:` on a screen that was still settling (right after a launch, or a dialog sliding in), which 0.9.1
never takes for a still screen, so a `--lock frozen` replay of such a lockfile fails with `This screen and question
are not in …`. Measured on the getting-started test: 0.9.0 recorded the sign-in button 4 px lower than where it
settles. Add `--prune-lock` to drop the old entries.

Found by using 0.9.0 as a new user would: every action, option, error message and guide example, on the Android
emulator and the iOS simulator.

**Fixed**

- iOS: `scroll:` and `scroll_to:` could jump past their target in a web page. The finger moved at a flick's speed, so
  the page flung on after it lifted (measured: a 100 pt drag moved a WKWebView 470 pt). Scrolls now drag at
  300 pt/s, and the content moves as far as the finger. `swipe:` is still a flick.
- iOS: `grant:` while the app runs left it closed, because the simulator ends an app whose permissions change. The
  next step failed a minute later with an accessibility error. jevtest now starts the app again.
- Android: an `expect:` right after a system dialog appears could be judged on the dialog still sliding up (the tree
  reports it at its first place for about half a second), so the lockfile got a frame that a replay saw only
  sometimes: the demo's camera-prompt test failed one frozen replay in three. A screen now counts as stopped moving
  only once its text is also drawn the same twice (one screenshot per check; a spinner or a blinking cursor doesn't
  count).
- `screenshot:` waits until the screen stopped moving: right after a launch it saved the splash screen.
- `type:` without `into:` passed with no field taking keys, typing into nothing. It now waits until the keyboard is
  up (`timeout:` applies), and fails saying to tap the field first or name it with `into:`.
- A `do:` goal carried on after one of its moves left the app: pressing back on the first screen went to the
  phone's home screen, and Jev's next move tapped an app there (it opened the Play Store). The step now fails at
  once: `The app left the foreground after back`.
- A `do:` that ran out of `max_actions` listed the move it didn't make among those it did; the message now names
  it instead (`Jev's next would be …`).
- Android typed `%` as `\%`, and `%s` as a space (adb's `input text` has no escape for it): `50%` became `50\%`.
  Every character now arrives as written (measured on the emulator and a Pixel 4a).
- Android: when something stopped jevtest's agent mid-run (another tool using UI Automation), every later test on
  that device failed too. The next test now starts it again, and the lost test says what likely happened.
- `home` returned before the app had left the foreground, so the next step could still read the app's screen: `home`
  then `see: Sign in` passed. It now waits until the app has left (3 s at most).
- A wait that times out while the app isn't showing says so: `…; the app is in the background`.
- A target with two spaces, or a line break, between words never matched: the screen's text is read with them
  collapsed, and now the test's text is too.
- `--out` naming a file crashed with a traceback and exit code 1, as if a test had failed. It's now an error before
  anything runs.
- Two runs started in the same second with the same `--out` shared a results folder, and the second's `junit.xml`
  replaced the first's. Each run now gets its own (`20260928-120000-2`).
- Screenshot file names dropped letters outside A–Z: `ünïcode` became `n_code`, and a test named in Japanese
  became `screen`.
- iOS: `rotate:` to an orientation the app doesn't allow says so (most iPhone apps leave out
  `portrait_upside_down`).

**Caught before anything runs** (exit code 2), instead of partway through the run:

- a device a file names that isn't running (it failed only after the other devices had finished, and no JUnit file
  was written);
- a device another jevtest run is testing: two runs on one device restarted the app under each other, and both still
  passed. A run now claims its devices, and the claim ends with the run however it ends;
- a lockfile with git merge conflict markers says so, instead of only "not valid JSON";
- `--lock refresh` or `--lock off` without `TYPESAFE_API_KEY`, when a test has a `do:` or an `expect:`;
- a `network:` step in a test that runs on iOS;
- text Android can't type (`José`: adb's `input text` is ASCII only), which failed only after tapping the field;
- an unknown `key:` name (it failed after installing and launching the app). Android also takes `return`, like iOS;
- `wait:` or `background:` over 300 seconds (`wait: 99999` waited 27 hours);
- in `.env`, `KEY=value # comment`: is the comment part of the value? Put it on its own line, or quote the value;
- an `interval` longer than the `timeout`: the step checked once, then said it had waited the whole timeout.

**Clearer messages**

- A bare action with checks under it (`- back`, then `see:`) is a YAML error; it now says to write `- back:`. The
  hint about quoting `${NAME}` shows only when a `${` is inside `{ }` or `[ ]`.
- A `${NAME}` that isn't set names the test file and the exact `.env` it read.
- A folder run heads each file with its path in the folder (`=== sub/nav.yaml ===`).
- `app.ios points at a.apk, which is an Android build` (was `points at a android build`).
- Ctrl-C (or SIGTERM, SIGHUP) prints `stopped (SIGINT): devices put back; this run wrote no report`.
- `--help` for `--lock` and `--prune-lock` covers saved `do:` steps, not only recorded answers.
- `clear:` or `type: into:` on something that doesn't take text says what it is (`what says 'Sign in' doesn't take
  text (button)`).
- `open_url:` with a link no app handles says so, instead of printing the raw adb, simctl or agent error. On Android 13
  it passed: `am start` exits 0 there after printing its error (measured on a Pixel 4a), so `launch`, `resume` and
  `open_url` now read what it prints.
- A `do:` that Jev gives up on, when its goal has nothing in quotes, says `The goal has no "quoted" values, so Jev
  can't type anything` (`do: Type hello into Email` failed as `Stuck repeating: tap text_field 'Email'`).
- iOS: `key:` with no keyboard up failed with XCUITest's `Neither element nor any descendant has keyboard focus`;
  it now waits for the keyboard and says iOS presses keys only into a field.
- `grant:` with a permission Android won't grant says why (`not a changeable permission type`), not the first line
  of a Java stack trace.
- An `include:` that isn't there names the file that includes it.
- `jevtest run notes.txt` says test files are .yaml or .yml; `jevtest` alone says a COMMAND is required.
- `--test` with a test that only a library has said `No test named`; it now says it's a library test and which
  tests use it.
- A failure inside a `use:` names it: `FAILED Checkout: use: Sign in > see: Welcome — …`.
- Jev's API errors show TypeSafe's own message (`Jev HTTP 400: Unknown model: jev-9.9.9`), not the JSON body.
- A folder run no longer requires YAML in hidden folders (`.github/workflows/ci.yml`) to be a library; test files
  there still run.

**Docs**: Python 3.11 or newer (not 3.10); `grant:` takes the full Android permission name; the webviews example
(Android WebView fields have no name, so it types with `do:`) and the exact text the demo shows; `report.json`'s
keys and the console samples as they print now; how checks wait; `.env` in subfolders; what the lockfile holds
of values the app shows (their `${NAME}`, not the value).

## 0.9.0

**Every wait is a wait until.** A step waits until what it needs is true, checking every `interval` (0.25 s) for at
most its `timeout` (10 s), and otherwise fails saying what it waited for: `Waited 10s until an element says
'Save' on screen and stopped moving`. An element counts once it's in the same place at two checks in a row, so a tap
never lands where a button sliding in was a moment ago (on Android also drawn the same: a system dialog fading in
reports its final place at once). `expect:` asks Jev only about a screen that stopped moving.
`scroll_to:` scrolls until its element is clear of the screen's top and bottom 8%: just peeking in at the bottom,
it sat on the home-gesture strip, and a tap there sent the app home. There's no more waiting for the screen to "settle" after an action, no quiet windows, and the
on-device agents never wait (their `/idle` and `/change` calls are gone).

**`do:` goals are worked out once, then repeated.** The first run, Jev works out a `do:`'s steps, each on a
screen that stopped moving, and the lockfile saves them: the action and its element by kind and name (and which
one, when several have that name). Every later run repeats exactly those steps, each waiting until its element is
on screen, without asking Jev: replays no longer depend on a screen looking exactly as it did (a keyboard caught
mid-slide). With `--lock record`, steps that no longer fit the app are worked out again from where they stopped.

This changes test files and lockfiles:

- `settle` is gone: remove it (it's an error that says so). `interval` is new, and rarely needed.
- `tap:`, `type: into:`, `clear:` and `swipe: target:` match exact text only. A target no element says is never
  guessed by Jev any more: describe it in a `do:` step instead. (Jev still chooses between several exact matches.)
- `timeout` also applies to `do:` and `scroll_to:`.
- Lockfiles are version 2: record them again once with `--lock record`.

Also:

- `hide_keyboard` waits until the keyboard is gone (it slides away after the key or tap that closes it), and fails
  if it's still up after 3 seconds. Checking this showed that on iOS it often didn't close at all: it pressed
  Return, which a web field ignores. It now taps the Done on the bar above the keyboard, as a person would.
- iOS `rotate:` waits until the app has turned, like Android, and fails if it doesn't (an app locked to one
  orientation).
- The demo's native iOS screen closes its keyboard on Done, like a well-behaved UIKit field.
- When the keyboard closes over an element Jev picked and the screen then shows it twice, the error says so.

## 0.8.0

**Jev through TypeSafe directly.** jevtest calls TypeSafe's own API (`api.typesafe.ai`) instead of OpenRouter.
This changes setup:

- The key is `TYPESAFE_API_KEY` (create one at https://console.typesafe.ai/keys); `OPENROUTER_API_KEY` is no
  longer read.
- Models are TypeSafe's pinned versions: the default is `jev-1.13.0`, and a test file's `model` must be one
  (`jev-X.Y.Z`). An alias such as `jev-latest` is an error: it moves when a new Jev ships.
- The model name is part of every lockfile entry, so recorded lockfiles are asked again once with
  `--lock record`.
- Retries follow TypeSafe's docs: 408, 429 and 5xx, waiting as long as a `retry-after` header asks (up to a
  minute).
- The cost in the summary comes from TypeSafe's published price per input token.

Also:

- In landscape (or with any keyboard tall enough), the keyboard can cover the element a step needs, and a tap
  there typed a key instead. A `do:` goal now closes the keyboard first and finds the element again; `tap:`,
  `type:` and the other exact steps fail with "... is under the keyboard: close it first with a
  `hide_keyboard` step". A `type:` into a field that already has focus with the keyboard up types without tapping
  it (a tap would move the cursor, or with the keyboard over the field, hit a key).
- Android: typing into a web page's field in landscape failed with "The text field did not get keyboard focus":
  the page scrolls the focused field to the keyboard's edge, 0 pixels tall, and jevtest looked for it on screen.
  It now asks the device whether a text field has focus, wherever it is.
- A Jev request that hasn't answered in 15 seconds is asked again (was 30). Measured against TypeSafe with 74
  real requests: half answered within 0.2 s, but a quarter took 8 to 29 s, and in runs some took over 30 s.
- Jev is only asked about a screen that has stopped changing: two reads a still moment apart must agree. A
  screen caught mid-animation (a keyboard sliding up, a rotation) was recorded, and a later run never saw it
  again, so `--lock frozen` failed. On Android, `rotate:` also waits until the screen has turned.
- Android `rotate:` turns the screen through the agent's UiAutomation instead of the `user_rotation` setting,
  which a Pixel 4a on Android 13 ignored (the test passed only because the phone was already on its side).
  Android puts the device's own rotation state back when the agent stops.
- `--lock frozen`: a step that meets a screen not in the lockfile looks again when the screen changes, until its
  `timeout`, instead of failing at once.
- The demo's tests pass on a phone lying on its side: they scroll to what is below the screen in landscape and
  close the keyboard before tapping what it covers.
- **Security:** the iOS agent listened on every network interface without authentication, so while a test ran,
  another device on the same network could read the screen and tap. Each run now has a random token that every
  request must carry, and a simulator's agent listens on loopback only.
- **Privacy:** every `${NAME}` value the app shows is replaced by its name before the screen is sent to Jev or
  printed (the console, JSON and JUnit reports, Jev's moves and every message), as the docs promised. Lockfiles no longer depend on anyone's `.env`; recorded lockfiles are asked
  again once with `--lock record`.
- Ctrl-C during a multi-device run closes every device: puts back what steps changed, stops the agents.
- SIGTERM (a cancelled CI job) and SIGHUP (a closed terminal) stop a run the same way, instead of leaving the
  device's agent and port forward behind. The exit code is 128 + the signal: 143 and 129. A signal that was
  already ignored (`nohup`) stays ignored.
- A null character in a test-file value is a clear error instead of a crash.
- Docs: Android System WebView 153 gives web text fields no name, and radios or checkboxes inside their `<label>`
  none either; Troubleshooting says what to do. The demo's web form names its radios and checkbox with
  `aria-label`, and types into its text fields with `do:` goals.

## 0.7.2

- The Android agent reads the screen's rotation and a checkbox's state with the current Android APIs (the old ones
  are deprecated), and compiles without a warning.
- CI builds both on-device agents with warnings as errors, and type-checks the tests as well as the package.
- Internals: device tooling (finding devices, signing, building the agents) is separate from the `Device`
  classes; the lockfile and the command line depend on a small `JevAsker` protocol instead of the HTTP client.

## 0.7.1

**Exact text matching.** A step's text is matched exactly: the whole text, never part of a longer text (case
still doesn't matter, since platforms show the same text in different case). This changes behaviour: a test that
relied on "contains" now fails, and the error lists what the screen shows so the test file can be fixed.

- `tap:`, `double_tap:`, `long_press:`, `clear:`, `type: … into:` and `swipe: … target:` find the element whose
  text, hint or id is exactly the target. An element whose text joins parts (an iOS `Email: a@b.c`, an Android
  `Go (Go now)`) also matches each part on its own. `tap: Save` never taps *Unsaved changes* or *Save draft*.
- `see:`, `not_see:` and `scroll_to:` match one element's text exactly, not a part of any text on the screen:
  `see: "Taps: 2"` no longer passes on *Taps: 20*, and `not_see: Error` passes while *Error: none* shows.
- When nothing matches but a longer text contains the target, the step fails with `close but not exact: '…'`. Close texts are never used, and Jev isn't asked.
- When Jev chooses the element (a description such as `tap: the gear icon`, or among several exact matches), the
  step's output says so: `on button 'Settings' (chosen by Jev)`.
- Lockfiles: a question to Jev can change where the elements it chooses from changed, so a recorded run may ask
  Jev again once with `--lock record`.

## 0.7.0

**Engineering quality.** Test files, commands and lockfiles are unchanged.

- Output: a step reads as written, e.g. `tap: Ghost (timeout: 1)`, in the console, failures and the JSON report,
  instead of a Python dict.
- Fixed: text that isn't valid Unicode (a YAML escape like `"\ud800"`) is a clear test-file error instead of a crash.
- Fixed resource leaks: an iOS device that fails to start removes its temporary folder; a helper process that
  times out is reaped; helper output pipes are closed; the Jev client closes HTTP error responses it retries.
- Unexpected output from Xcode's tools, Jev or a lockfile is jevtest's own error saying what was expected, never
  a Python `KeyError`.
- One definition per action: each action's traits live on its type, and one table says how each YAML key reads.
- Tooling: uv with a lockfile; every ruff rule and ruff format; strict mypy and strict pyright with no `Any`;
  complexity at most 10; tests in random order with warnings as errors and Hypothesis properties; codespell;
  pip-audit; zizmor, with every GitHub Action pinned to a commit; Dependabot.

## 0.6.0

**Settings.** A test file can now tune how steps wait and how far they go, with defaults that suit most apps and
limits that keep a test from hiding a broken app. A file without settings runs exactly as before.

- A `settings:` block sets `timeout`, `settle`, `max_actions`, `max_scrolls`, `confidence` (how sure Jev must be
  for an `expect:` to pass) and `model` for every step in the file.
- A step can set `timeout`, `settle`, `max_actions` (on `do:`), `max_scrolls` (on `scroll_to:`) or `confidence`
  (with `expect:`) for itself. A setting on a step it means nothing for is an error.
- Every value has limits (for example `timeout` 1–300 s, `confidence` 0.5–0.99). `timeout: 0` is no longer
  accepted.

## 0.5.0

**Rebuilt on a clean architecture.** Test files, commands and output are unchanged, and recorded lockfiles still
match.

- Four layers with dependencies pointing inward: `domain` (types and ports, standard library only),
  `application` (the runner), `adapters` (devices, Jev, files, reports) and `cli` (the composition root). The rule
  is checked on every commit by import-linter.
- Steps, decisions, results and screens are frozen, typed values: one class per action and per move, so a step or
  a move can only carry what it needs.
- The package is fully typed (`mypy --strict`, `py.typed`), and every public class and function is documented; the
  docs have a generated [Python API](https://just-betr.github.io/jevtest/reference/api/) page and an
  [Architecture](https://just-betr.github.io/jevtest/architecture/) page.
- Tools' exceptions stop at the adapters; the rest of jevtest sees four failure types.
- A `fresh: true` test now also puts back device changes an earlier test made (`rotate:`, `dark_mode:`,
  `network:`, location), so one failing test can't leave the device rotated for the rest.
- An iOS app state XCUITest doesn't define is an error, not a guess.
- `scroll_to` calls it the end of the content only after two scrolls in a row move nothing: a real phone's web
  view sometimes ignores one.
- iOS: while a system alert is up, touches go to SpringBoard, and an agent call may take 150 s, since XCUITest
  waits up to 60 s for SpringBoard to settle before touching on a real iPhone.
- Requires Python 3.11 or newer.

## 0.4.0

**No settings.** A test file is just `app`, `device`, `include` and `tests`.

- `settings:` is gone. Each step waits up to 10 seconds for what it looks for; a step can say `timeout: 30`.
- The rest are fixed rules of jevtest, listed in the docs: 10 actions per `do:` goal, `expect:` passes above 0.5,
  and the Jev model is pinned per release (`typesafe/jev-1.13`).
- `scroll_to` scrolls until the text appears, stopping at the end of the content or after 50 scrolls; it also checks
  after its last scroll now.
- A real iPhone needs no team setting: jevtest signs its agent with the team that signed your app.
- The per-step `max_actions` and `max_scrolls` options are gone.

## 0.3.0

**Nothing is assumed any more.** Every value a run uses comes from the test file, the `.env` next to it or the command line; anything missing or wrong is an error that says what to fix.

- `device:` is required for every platform, and names must match exactly one device.
- `settings:` is required with every value: `model`, `max_actions`, `max_scrolls` (new), `timeout`, `settle`, `threshold`.
- `ios_team` is required for a real iPhone; the error lists the teams signed into Xcode.
- Every test needs `fresh: true` or `fresh: false`.
- Steps are exact: a bare word must be an action (`- bakc` is an error, not a goal); numbers, text and on/off must be the right type; options must belong to their action; `scroll_to` needs `direction`; `location` is `[lat, lon]`; `grant` needs the full Android permission name; `key` names are exact.
- The whole file is checked at once and every problem is reported together.
- `.env` is read only from next to the test file; a value set differently there and in the environment is an error.
- `jevtest run` requires `--lock record|frozen|refresh|off` (replacing `--frozen`, `--refresh-lock`, `--no-lock`) and `--out`.
- In a folder, a YAML file that is neither a test file nor an included library is an error.
- New `--prune-lock` removes recorded decisions a full passing run didn't use.
- Jev retries are printed.
- `rotate`, `dark_mode`, `network` and simulator `location` are put back when the run ends, on every platform.
- Results are always under `<platform>/<device>/`.

## 0.2.0

- `${NAME}` values from `.env` or the environment, kept out of logs, reports and Jev's goals.
- `include:` library files for sharing tests.
- `jevtest run` takes several files and folders.
- Several devices per platform, with the tests split across them and run at the same time; platforms run at the same time.
- Real iPhones: signing, installing and driving the agent over the USB tunnel.

## 0.1.0

- First version: YAML tests with actions and checks, Jev through OpenRouter, the lockfile, Android and iOS drivers with on-device agents, JUnit and JSON reports.
