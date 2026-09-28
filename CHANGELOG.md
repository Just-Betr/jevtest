# Changelog

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
