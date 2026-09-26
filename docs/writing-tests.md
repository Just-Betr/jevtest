# Writing tests

## The shape of a step

**Every step is one action, then the checks that must hold after it.**

```yaml
- do: Sign in with email "${EMAIL}" and password "${PASSWORD}"   # the action
  expect: The home screen greets the user                        # check: Jev judges
  see: Welcome                                                   # check: exact text
```

- The **action** is what happens: a plain-English goal (`do:`) or an exact step (`tap:`, `type:`, `swipe:`, …). [All actions](reference/steps.md).
- The **checks** are what must be true afterwards. They keep trying for up to 10 seconds (or the step's [`timeout:`](reference/test-file.md#settings-optional)), so a screen that takes a moment still passes, and a screen that never gets there fails with the reason.
- A step can be checks only: `- see: Welcome`.
- The first failing action or check stops the test, takes a screenshot, and the run moves on to the next test.

## `do:` — let Jev work it out

```yaml
- do: Turn notifications on
  see: Notifications are on
```

Jev looks at the screen and picks the next action (tap, type, scroll, back, …) until it says the goal is done. A goal it judges impossible from the screen fails the step at once, and so does one that takes more than 10 actions (its `max_actions`) or repeats itself. A bigger goal is two steps.

**Text to type goes in `"quotes"`.** Jev picks which quoted value goes in which field; it never makes up text. Anything you want typed must be in the goal:

```yaml
- do: Search for "running shoes" and open the first result
```

Values can come from `.env` or the environment with `${NAME}`: `do: Sign in with password "${PASSWORD}"`. Jev sees `${PASSWORD}`; only the app gets the value. See [Large suites](guides/large-suites.md#name-values-secrets-and-per-machine-values).

## Exact steps — when you know what to do

```yaml
- tap: Add to cart                        # the element labelled "Add to cart"
- type: { text: "2", into: Quantity }     # into a field
- swipe: left
  target: Item 3                          # swipe on an element
- scroll_to: Terms and conditions
  direction: down
- back
```

Exact steps don't ask Jev what to do. They still find elements by their visible text: an exact label first, then an element containing the text, both in code. Only a description that isn't on-screen text (`tap: the red delete icon`) asks Jev which element it means, and Jev must then confirm its pick.

## Checks

| Check | Passes when |
|---|---|
| `expect: statement` | Jev judges the statement more likely true than false on the screen |
| `see: text` | the text is on screen (case-insensitive substring, no model) |
| `not_see: text` | the text is not on screen |

Several of the same kind take a list: `see: [Welcome, Log out]`.

Prefer `see:` when you know the exact text: it's instant, free and exact. Use `expect:` for things only a reader would judge: *"an error explains the password is too short"*, *"the list is sorted by price"*.

## Tests

```yaml
tests:
  - name: Add to cart
    fresh: true
    steps:
      - use: Sign in
      - tap: Running shoes
      - tap: Add to cart
        see: "Cart (1)"
```

| Key | Meaning |
|---|---|
| `name` | Unique across the file and everything it includes. Used in logs, reports, `--test` and `use:`. |
| `fresh` | Required. `true`: stop the app, clear its data and launch it first. `false`: carry on from where the previous test left the app (launching it if it isn't running). |
| `steps` | The steps, in order. |

## Composing tests with `use:`

`use: <test name>` runs another test's steps inside this one, so tests build on each other:

```yaml
  - name: Sign in
    fresh: true
    steps:
      - do: Sign in with email "${EMAIL}" and password "${PASSWORD}"
        see: Welcome

  - name: Change the display name
    fresh: true
    steps:
      - use: Sign in
      - tap: Profile
      - clear: Display name
      - type: { text: "Ada", into: Display name }
      - tap: Save
        see: Saved
```

Uses nest. A test that uses itself, directly or through others, is an error before anything runs. To share tests between files, put them in a library file and [`include:` it](guides/large-suites.md#include-shared-tests).

## Writing good tests

- **One behaviour per test.** A failure then names the behaviour that broke.
- **Start fresh** (`fresh: true`) unless a test really continues another. Fresh tests can run on any device in any order; see [several devices](guides/large-suites.md#several-devices-at-once).
- **Check after every action that matters.** `see:` after a tap proves the tap landed; without it, the next step fails somewhere less obvious.
- **Name elements by their visible text.** It's what a user sees, it survives refactors, and it's matched in code without asking Jev.
- **Keep `do:` goals small and concrete.** "Sign in", "Add the first result to the cart". A long goal needs more actions and more judgement.
- **Commit the lockfile** and run CI with `--lock frozen`.
