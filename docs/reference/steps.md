# Steps and checks reference

A step is **one action**, then optional **checks**, then optional **options**. A step can also be checks only.

```yaml
- tap: Save                 # action
  see: Saved                # check
  timeout: 5                # option
```

A few actions take no value. Write them as a bare word (`- back`), or as a key when checks follow (`- back:` with the checks on the next lines).

## Checks

| Check | Passes when |
|---|---|
| `expect: statement` | Jev finds the statement more likely true than false on the current screen (yes-probability above the [`confidence`](test-file.md#settings-optional) setting, 0.5 by default) |
| `see: text` | an element says exactly the text ([matching](#matching)), no model |
| `not_see: text` | no element says exactly the text |

Each takes one value or a list. A check waits until it holds: it reads the screen, and if the check doesn't hold yet, reads it again after `interval` (0.25 s by default), for up to 10 seconds or the step's `timeout:` ([settings](test-file.md#settings-optional)), then fails with `Waited 10s until …`. `expect:` asks Jev only about a screen that stopped moving (it read the same twice in a row), never about a frame of an animation.

## Actions

### Plain English

| Action | Does |
|---|---|
| `do: goal` | What a person would do, in plain English. The first run, Jev picks the steps (tap, double tap, long press, type, clear, swipe on an element, scroll, back, enter, hide the keyboard), each on a screen that stopped moving, until it judges the goal done; the steps are saved in the lockfile. Every later run repeats the saved steps, each waiting until its element is on screen. Values in `"quotes"` are what it may type. Fails if Jev says the goal is impossible from the screen, repeats an action that changes nothing on the screen, or needs more than `max_actions` (10 by default). |
| `use: test name` | Runs that test's steps here. |

### Touch

| Action | Does |
|---|---|
| `tap: target` | Taps the element. |
| `double_tap: target` | Double-taps it. |
| `long_press: target` | Presses and holds it. |
| `swipe: up\|down\|left\|right` | Swipes across the screen, or on an element with `target:`. On an element, it goes along the element's line across what it's in, as a finger would: its list row, pager or carousel (or just the element, in none), starting on the row, or on the element in a pager or carousel. It moves 70% of the way across (60% up or down), or `distance:`, and lifts while moving, a flick. It never starts where the phone takes a swipe inward as its own gesture (back from the outer 15% of the width, home from the bottom 8%, the notifications from the top 8%): it moves in instead. On a slider, `left` or `right` drags its thumb all the way to that end. |
| `scroll: up\|down\|left\|right` | Scrolls the content: the finger moves across 60% of the screen (above the keyboard, if it's up), and the content moves as far, never flinging on. With `along: text`, it scrolls what that text is in instead, such as a carousel, dragging along its line. |
| `scroll_to: text` | Scrolls in `direction:` (required) until an element says exactly the text, clear of the screen's top and bottom 8% (phones keep those edges for their own gestures, like the home swipe). Fails when the content stops moving (the end) or after `max_scrolls` (50 by default) without the text on screen. With `along: text` it scrolls what that text is in, such as a carousel: `scroll_to: Tag 18`, `direction: right`, `along: Tag 1`. |

**Targets** are found by their exact text ([matching](#matching)): the step waits until an element says it, then acts. If several elements match, the one you can act on wins (a switch over its label); if that still leaves several, Jev chooses among those only, and the step says `(chosen by Jev among 2 exact matches)`. A target is never guessed: one no element says fails the step after 10 seconds (or the step's `timeout:`) with `Waited 10s until an element says '…' on screen`. To describe something instead (`the red delete icon`), use `do:`.

### Matching

Text is matched **exactly**: the whole text, never part of a longer text. Case doesn't matter, because platforms show the same text in different case (Android draws many buttons in capitals; the permission prompt says *Don't allow* on Android and *Don't Allow* on iOS). Nor do curly quotes: both prompts write *Don’t* with a curly apostrophe, and `tap: Don't allow`, typed with a keyboard's straight one, matches it. An element matches a target when one of these is the target:

| | Example: the element | matches |
|---|---|---|
| its text, as shown | an iOS field showing `Email: a@b.c` | `Email: a@b.c` |
| each part its text is made of | the same field | `Email`, and `a@b.c` |
| | an Android button with text `Go` and description `Go now`, shown `Go (Go now)` | `Go (Go now)`, `Go`, `Go now` |
| its hint (placeholder) | a field with the hint `Search` | `Search` |
| its id (resource id or accessibility identifier) | `login_button` | `login_button` |

So `tap: Save` never taps *Unsaved changes* or *Save draft*, and `see: "Taps: 2"` never passes on *Taps: 20*; `tap: Save nickname` does tap *SAVE NICKNAME*. Spaces and line breaks between words count as one space, both in the screen's text and in yours: `see: "Taps:  2"` matches *Taps: 2*.

When nothing matches but a longer text contains the target, the step fails and lists what's there, so you can fix the test file. A close text is never used:

```
✗ tap: Save (10.0s) — Waited 10s until an element says 'Save' on screen and stopped moving; close but not exact: 'Unsaved changes', 'Save draft'
```

`not_see:` is exact too: `not_see: Error` passes while the screen shows *Error: none*. To check that no error of any wording is showing, use `expect:`.

### Text

| Action | Does |
|---|---|
| `type: text` | Types into the field that has focus: waits until the keyboard is up, and fails if it never comes (keys would go nowhere). |
| `type: { text: "…", into: target }` | Taps the field, then types. Text is typed exactly as written, spaces included. Any text, on both platforms: `José`, `日本`, emoji. On Android, a line with letters beyond a US keyboard's goes in at the cursor all at once rather than key by key, and into a password field only while it's empty. |
| `clear: target` | Erases a text field. |
| `key: name` | Presses a key. On both platforms: `enter` (or `return`), `delete` (or `backspace`), `tab`, `escape`, `space`. On Android also `back`, `home`, `menu`, `search`, `app_switch`, `power`, `volume_up`, `volume_down`, `dpad_up`, `dpad_down`, `dpad_left`, `dpad_right`, `move_home`, `move_end`, or a key code number in quotes (`key: "67"`). Names are exact and checked when the file loads, as is an Android-only key in a file that also runs on iOS. iOS presses keys only into a field, so there the step waits until the keyboard is up. |
| `hide_keyboard` | Closes the on-screen keyboard. On iOS it taps the Done above the keyboard, else the keyboard's hide key, else presses Return; in a field of several lines, where Return adds a line and nothing else closes the keyboard, it fails and leaves the text as it was. |

### Navigation and app lifecycle

| Action | Does |
|---|---|
| `back` | System back on Android. On iOS: the "Back" button, else the navigation bar's back button, else an edge swipe. |
| `home` | Goes to the home screen. The app may be in the background afterwards. |
| `launch` | Launches the app, and waits until it's in the foreground. An app that's already running (after `home`, say) comes back as it was; `restart` starts it anew. |
| `stop` | Stops the app. |
| `restart` | Stops and launches the app. |
| `clear_data` | Stops the app and clears its data (iOS: reinstalls it). |
| `reinstall` | Uninstalls and installs the build again. |
| `background: seconds` | Sends the app to the background for that long (at most 300), then brings it back. |
| `open_url: url` | Opens a deep link or URL. |

### Device

These change device state because the test asks for it. Anything jevtest changes, it changes back when the run ends, however it ends: a run killed outright (`kill -9`, a CI job past its grace period) can't, so the next run on that device puts it back first, and says so.

| Action | Does |
|---|---|
| `rotate: portrait\|landscape\|landscape_right\|portrait_upside_down` | Rotates the device, and waits until the app has turned. An app that doesn't allow the orientation fails the step: most iPhone apps leave out `portrait_upside_down` (`UISupportedInterfaceOrientations` in the app's Info.plist). |
| `dark_mode: on\|off` | Dark or light appearance. |
| `location: [latitude, longitude]` | Sets the GPS location (Android emulator, iOS simulator, iPhone; not an Android phone). An iOS device goes back to its actual location at the end of the run; the Android emulator keeps it. |
| `grant: permissions` | Grants runtime permissions, so the app never asks. One name, a list, or names per platform ([below](#grant-permission-names)). |
| `network: on\|off` | Wi-Fi and mobile data. Android only: a file whose `app:` includes iOS and runs a test with `network:` is an error when it loads. |
| `autofill: off` | Turns off Android's autofill service, so no password manager (Google's, on a phone with a Google account) covers the app offering to save what a sign-in typed. Only `off`: the device's own service is put back after the run. Android only, like `network:`. |

### `grant:` permission names

Each platform names a permission its own way, so a file that runs on both gives each its names:

```yaml
- grant: android.permission.CAMERA                          # a file that runs on Android only
- grant: [camera, microphone]                               # iOS simulator only: a list grants several
- grant:                                                    # a file that runs on both
    android: [android.permission.CAMERA, android.permission.RECORD_AUDIO]
    ios: [camera, microphone]
```

| Permission | Android | iOS simulator |
|---|---|---|
| Camera | `android.permission.CAMERA` | `camera` |
| Microphone | `android.permission.RECORD_AUDIO` | `microphone` |
| Location, while in use | `android.permission.ACCESS_FINE_LOCATION` | `location` |
| Location, always | `android.permission.ACCESS_BACKGROUND_LOCATION` | `location-always` |
| Photos | `android.permission.READ_MEDIA_IMAGES` (Android 13+) | `photos` (`photos-add`: add only) |
| Contacts | `android.permission.READ_CONTACTS` | `contacts` |
| Calendar | `android.permission.READ_CALENDAR` | `calendar` |
| Motion and fitness | `android.permission.ACTIVITY_RECOGNITION` | `motion` |
| Notifications | `android.permission.POST_NOTIFICATIONS` (Android 13+) | none: the simulator can't pre-grant them |
| Reminders | none | `reminders` |

- **Android:** the full name, with its package (`android.permission.CAMERA`, or an app's own `com.example.app.SCAN`), and only a permission the app declares in its manifest and Android grants at run time. Anything else fails, saying why (`not a changeable permission type`).
- **iOS simulator:** a service `xcrun simctl privacy` accepts. It accepts `camera` too, which its help doesn't list (measured with Xcode 27.0). The simulator ends an app whose permissions change, so jevtest starts it again, once for the whole list: put `grant:` first in a test.
- **A real iPhone** can't be granted permissions (Apple doesn't allow it). A file that runs on one and has a `grant:` is an error before the run: run that test on a simulator, or tap the permission prompt with a step instead.
- A file that runs on both platforms with a `grant:` that doesn't name both is an error before the run.

### Other

| Action | Does |
|---|---|
| `wait: seconds` | Waits a fixed time, at most 300 seconds. Checks already wait for what they check, so this is rarely needed. |
| `screenshot: name` | Waits until the screen stopped moving (so it isn't a frame of a launch or an animation), then saves a PNG into the results folder. To capture a particular screen, check for it first: `see:` on the step before. |

After every action, the step fails if the app crashed or left the foreground (except after actions that are meant to leave it: `stop`, `clear_data`, `reinstall`, `home`, `open_url`).

## Options

Each option belongs to certain actions; anywhere else it's an error.

| Option | On | Meaning |
|---|---|---|
| `timeout: seconds` | steps that wait: steps with checks, `tap`, `double_tap`, `long_press`, `clear`, `type`, `swipe` with `target`, `do`, `scroll_to`, `screenshot` | How long this step waits until what it needs is true, instead of the file's `timeout` (10 s by default). |
| `interval: seconds` | the same steps as `timeout` | How often this step checks again (0.25 s by default). |
| `max_actions: n` | `do` | Actions this goal may take (10 by default). |
| `max_scrolls: n` | `scroll_to` | Scrolls this step may make (50 by default). |
| `confidence: p` | steps with `expect` | How sure Jev must be for this step's `expect:` checks to pass (0.5 by default). |
| `direction: up\|down\|left\|right` | `scroll_to` (required) | Which way to scroll. |
| `target: text` | `swipe` | Swipe on this element instead of the whole screen. |
| `along: text` | `scroll`, `scroll_to` | Scroll what this text is in (a carousel, a scrolling box) by dragging along its line, instead of the page. `scroll_to:` finds it once, before scrolling moves it away. |
| `distance: percent` | `swipe` | How far the finger moves, in percent of what it swipes (10 to 90). An iOS list row shows its swipe actions after up to half its width, and runs the first (Archive, Delete) after 60%: `distance: 40` to show them and tap one, the default 70 to run it. A row that's dismissed by a swipe (Flutter's `Dismissible`, Compose's `SwipeToDismissBox`) needs the default. |
| `into: text` | `type` | The field to type into. Also written inside: `type: {text: …, into: …}`. |
