# WebViews and native screens

Apps are rarely one technology. jevtest reads what the operating system's accessibility layer reports, so **Flutter, native Android Views, UIKit, SwiftUI, React Native and in-app WebViews** all look the same to a test: elements with text, kinds and positions.

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
- name: Web form
  fresh: true
  steps:
    - use: Open the web page
    - type: { text: "Bret", into: Your name }
    - tap: Say hello
      see: Hello, Bret!
    - do: Choose Canada in the Country dropdown
      expect: Canada is the selected country
    - scroll_to: Submit form
      direction: down
    - tap: Submit form
      see: "Submitted: Canada"
```

For a WebView to be testable, its content needs to be accessible, which ordinary HTML is: labels, button text, `alt` text and `aria-label`s all come through.

## Native screens in a cross-platform app

The demo app is Flutter, with one screen built in native Android Views and UIKit (a text field, a switch, a native confirmation dialog and a real camera permission prompt). The same steps drive both.

## System prompts

Permission dialogs belong to the operating system, not the app. jevtest includes them in the screen (on iOS they come from SpringBoard, and the agent merges them in), so a test can check for one and answer it:

```yaml
- tap: Ask for camera
  expect: The system is asking whether to allow camera access
- do: Allow camera access
  see: "Camera: allowed"
```

A permission prompt the app asked for doesn't count as the app leaving the foreground.

## What jevtest can't see

Jev reads text only. Anything without an accessibility label is invisible: canvas drawing, games, and images with no label. Give such elements a label (`contentDescription`, `accessibilityLabel`, `Semantics`, `aria-label`) and they become testable, and accessible to people using screen readers.
