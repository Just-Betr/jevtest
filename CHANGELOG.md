# Changelog

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
