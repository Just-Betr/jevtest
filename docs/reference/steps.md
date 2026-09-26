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
| `expect: statement` | Jev's yes-probability for the statement, on the current screen, is above `settings.threshold` |
| `see: text` | some element's text or hint contains the text (case-insensitive, no model) |
| `not_see: text` | no element's text or hint contains it |

Each takes one value or a list. Checks keep trying until `timeout` (the step's, or `settings.timeout`). They look again only when the screen changes, so Jev is never asked the same question about the same screen twice.

## Actions

### Plain English

| Action | Does |
|---|---|
| `do: goal` | Jev picks actions (tap, double tap, long press, type, clear, swipe on an element, scroll, back, enter, hide the keyboard, wait for loading) until it judges the goal done. Values in `"quotes"` are what it may type. Fails if Jev says the goal is impossible from the screen, repeats one action three times, or needs more than `max_actions` actions. |
| `use: test name` | Runs that test's steps here. |

### Touch

| Action | Does |
|---|---|
| `tap: target` | Taps the element. |
| `double_tap: target` | Double-taps it. |
| `long_press: target` | Presses and holds it. |
| `swipe: up\|down\|left\|right` | Swipes across the screen, or on an element with `target:`. |
| `scroll: up\|down\|left\|right` | Scrolls the content one page. |
| `scroll_to: text` | Scrolls in `direction:` (required) until the text is on screen. Fails after `max_scrolls`. |

**Targets** are found by their visible text: an element whose text, hint or id is exactly the target; otherwise one whose text or hint contains it (both in code). Only a description that isn't on-screen text goes to Jev, which picks an element and then must confirm it. If several elements match, Jev chooses among those only. A target that can't be found fails the step after `timeout`.

### Text

| Action | Does |
|---|---|
| `type: text` | Types into the focused field. |
| `type: { text: "…", into: target }` | Taps the field, then types. Text is typed exactly as written, spaces included. |
| `clear: target` | Erases a text field. |
| `key: name` | Presses a key: `enter`, `delete`, `tab`, `escape`, `space` (and Android key codes). |
| `hide_keyboard` | Closes the on-screen keyboard. |

### Navigation and app lifecycle

| Action | Does |
|---|---|
| `back` | System back on Android. On iOS: the "Back" button, else the navigation bar's back button, else an edge swipe. |
| `home` | Goes to the home screen. The app may be in the background afterwards. |
| `launch` | Launches the app. |
| `stop` | Stops the app. |
| `restart` | Stops and launches the app. |
| `clear_data` | Stops the app and clears its data (iOS: reinstalls it). |
| `reinstall` | Uninstalls and installs the build again. |
| `background: seconds` | Sends the app to the background for that long, then brings it back. |
| `open_url: url` | Opens a deep link or URL. |

### Device

These change device state because the test asks for it. Anything jevtest changes, it changes back when the run ends.

| Action | Does |
|---|---|
| `rotate: portrait\|landscape\|landscape_right\|portrait_upside_down` | Rotates the device. |
| `dark_mode: on\|off` | Dark or light appearance. |
| `location: [latitude, longitude]` | Sets the GPS location (emulator, simulator, iPhone). |
| `grant: permission` | Grants a runtime permission: an Android permission name (`CAMERA`, `android.permission.CAMERA`) or a simulator service (`photos`, `camera`). Not possible on a real iPhone: tap the prompt with a step instead. |
| `network: on\|off` | Wi-Fi and mobile data (Android only). |

### Other

| Action | Does |
|---|---|
| `wait: seconds` | Waits a fixed time. Checks already wait for what they check, so this is rarely needed. |
| `screenshot: name` | Saves a PNG into the results folder. |

After every action, the step fails if the app crashed or left the foreground (except after actions that are meant to leave it: `stop`, `clear_data`, `reinstall`, `home`, `open_url`).

## Options

Each option belongs to certain actions; anywhere else it's an error.

| Option | On | Meaning |
|---|---|---|
| `timeout: seconds` | steps with checks, `tap`, `double_tap`, `long_press`, `clear`, `type` with `into`, `swipe` with `target` | Overrides `settings.timeout` for this step. |
| `max_actions: n` | `do` | Overrides `settings.max_actions`. |
| `direction: up\|down\|left\|right` | `scroll_to` (required) | Which way to scroll. |
| `max_scrolls: n` | `scroll_to` | Overrides `settings.max_scrolls`. |
| `target: text` | `swipe` | Swipe on this element instead of the whole screen. |
| `into: text` | `type` | The field to type into. Also written inside: `type: {text: …, into: …}`. |
