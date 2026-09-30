# WebViews and native screens

Apps are rarely one technology. jevtest reads what the operating system's accessibility layer reports, so **Flutter, native Android Views, Jetpack Compose, UIKit, SwiftUI, React Native and in-app WebViews** all look the same to a test: elements with text, kinds and positions.

## In-app WebViews

HTML inside a WebView (`WKWebView` on iOS, `android.webkit.WebView` on Android) appears like native elements and is driven the same way. The demo app's web page covers:

- typing, clearing and retyping in `<input>` fields, including password fields
- `<select>` dropdowns (`do: Choose Canada in the Country dropdown`)
- radio buttons and checkboxes
- form submission and links
- a JavaScript `alert()`, which the app shows as a native dialog
- navigating between pages of a web app
- scrolling a long page (`scroll_to:`)

```yaml
- name: Open the web page
  fresh: true
  steps:
    - type: { text: "${EMAIL}", into: Email }
    - type: { text: "${PASSWORD}", into: Password }
    - hide_keyboard
    - tap: Sign in
      see: Welcome, ${EMAIL}
    - scroll_to: Open web page
      direction: down
    - tap: Open web page
      see: Web Greeter

- name: Web form
  fresh: true
  steps:
    - use: Open the web page
    - do: Type "Bret" into the Your name field and press Say hello
      see: Hello, Bret!
    - do: Choose Canada in the Country dropdown
      expect: Canada is the selected country
    - scroll_to: Submit form
      direction: down
    - tap: Submit form
      see: "Submitted: Canada, Free plan, without a password"
```

For a WebView to be testable, its content needs to be accessible, which ordinary HTML is: button text, link text, `alt` text and `aria-label`s all come through.

**Which name finds a web field depends on the WebView.** Measured on the demo page's `<label for="name">Your name</label><input id="name">`:

| | `into: Your name` (the label) | `into: name` (the HTML `id`) |
|---|---|---|
| Android 12, WebView 91 | ✗ | ✓ |
| Android 15, WebView 124 | ✓ | ✗ |
| Android 13, WebView 146 | ✓ | ✓ |
| Android 17, WebView 153 | ✗ | ✓ |
| iOS 26 and 27 | ✓ | ✗ |

So no exact name works on every device, and a WebView update can change it. On iOS a web radio button, and a `<select>`, have no element type of their own (measured: XCUITest reports them as `other`): a radio shows its state as its value, so it reads `Pro plan: 1` when chosen and `Pro plan: 0` when not, and `see: "Pro plan: 1"` checks it. The example types with `do:`, which sees the label next to the field whatever the WebView reports. An `aria-label` names a checkbox or radio on both platforms (measured on the page's checkbox: `tap: I agree to the terms` works on each), but not a text field on WebView 153 ([Troubleshooting](../troubleshooting.md)).

## Native screens in a cross-platform app

The demo app is Flutter, with one screen built in native Android Views and UIKit (a text field, a switch, a native confirmation dialog and a real camera permission prompt). The same steps drive both.

## What each toolkit reports

Each toolkit tells the accessibility layer about its elements its own way, and a test names them as they're reported. Checked with a SwiftUI, a UIKit, a Jetpack Compose, a Material Views and a React Native app:

- **React Native on iOS** reads a pressable's texts as one element, as VoiceOver does: a row showing *Gadget 1* and *$2* is `Gadget 1, $2` there, and two texts on Android. A step that names one says `close but not exact: 'Gadget 1, $2'`: write the whole text in the iOS file.
- **A control with no label** (a Compose `Switch` or `Slider` beside a `Text`, with nothing joining them) can't be named: the text beside it is only a text, and `tap:` on it taps the text. Describe the control instead (`do: Turn on the switch next to Dark theme`), or give it a label, which screen readers need too (`Modifier.toggleable` on the row, `contentDescription`, `accessibilityLabel`).
- **A menu picker** (SwiftUI `Picker` with `.menu`) opens from its value, as with a finger: `tap: Vanilla`, not its label.
- **Swipe actions** on an iOS list row show after a short swipe and run after a long one: `distance: 40` to show them, `distance: 90` to run the first. See [`distance`](../reference/steps.md#options).
- **A picker wheel** (UIKit `UIPickerView`) shows XCUITest only its selected row, so its other rows can't be tapped by name: `type: Blue` with `into:` naming the wheel turns it, and a `do:` goal can with a quoted value (`do: Choose "Blue" in the Color wheel`).
- **A control stretched wider than it's drawn** (a UIKit stepper or date picker filling a stack view) is reported at the stretched size, and a tap lands in its middle, where nothing may be drawn. XCUITest's own tap does the same. Let such controls keep their own size.

## System prompts

Permission dialogs belong to the operating system, not the app. jevtest includes them in the screen (on iOS they come from SpringBoard, and the agent merges them in), so a test can check for one and answer it:

```yaml
- tap: Ask for camera
  expect: The system is asking whether to allow camera access
- do: Allow camera access
  see: "Camera: allowed"
```

A permission prompt the app asked for doesn't count as the app leaving the foreground.

**Saving a password.** After a sign-in, the phone may offer to save the password over the app. On Android (a phone with a Google account) start the test with `autofill: off`. On iOS 26 the simulator asks every time a fresh install signs in, and hides the app from the screen until it's answered, so answer it: `tap: Not Now` (measured: asked in each of 15 fresh tests). jevtest can't turn iOS's AutoFill off.

## What jevtest can't see

Jev reads text only. Anything without an accessibility label is invisible: canvas drawing, games, and images with no label. Give such elements a label (`contentDescription`, `accessibilityLabel`, `Semantics`, `aria-label`) and they become testable, and accessible to people using screen readers.
