# Command line

jevtest has one command.

```bash
jevtest run PATH... --lock MODE --out DIR [--test NAME]... [--prune-lock] [-v]
```

| Argument | Required | Meaning |
|---|---|---|
| `PATH...` | yes | Test files and/or folders. A folder runs every test file in it and its subfolders ([rules](../guides/large-suites.md#folders)). |
| `--lock MODE` | yes | How the [lockfile](../how-it-works.md#the-lockfile) is used. |
| `--out DIR` | yes | Where results go. Each run adds a timestamped folder inside it. |
| `--test NAME` | no | Only run this test. Repeatable; names can come from any of the files. |
| `--prune-lock` | no | After a run where every test passed, remove saved steps and recorded answers the run didn't use. |
| `-v`, `--verbose` | no | Also print every Jev question with its top answers and probabilities. |
| `--version` | no | Print the version. |

## Lock modes

| `--lock` | A `do:` with saved steps | A `do:` without | Other Jev questions (`expect:`, …) | Writes the lockfile |
|---|---|---|---|---|
| `record` | repeats them; if one no longer fits, Jev works the rest out again | Jev works it out; the steps are saved | recorded answer, else Jev (recorded) | yes |
| `frozen` | repeats them; if one no longer fits, **the step fails** | **the step fails** | recorded answer, else **the step fails** | no |
| `refresh` | Jev works it out again | Jev works it out | Jev is asked again | yes, overwriting |
| `off` | Jev works it out | Jev works it out | Jev is asked | no |

## `--prune-lock`

The lockfile only grows: a `do:` or a screen that no longer exists keeps its saved steps or recorded answers. `--prune-lock` removes them, and the answers Jev gave while working out a `do:` whose steps are now saved (later runs repeat the steps without asking). It only acts after a run of **every** test in which **every** test passed, because a failing test stops early and skips screens that are still real. So it refuses to combine with `--test` or `--lock off`, and after a failure it says `not pruned, because a test failed`.

Prune with the same devices you record on: decisions recorded on one device's screens are unused on another's.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | every test passed |
| 1 | a test failed |
| 2 | setup error: bad test file, missing value, no such device, bad arguments |
| 130 | interrupted (Ctrl-C) |
| 143 | stopped by SIGTERM (a cancelled CI job) |
| 129 | stopped by SIGHUP (the terminal closed) |

## Environment

| Variable | Meaning |
|---|---|
| `TYPESAFE_API_KEY` | Your [TypeSafe API key](https://console.typesafe.ai/keys), for Jev. From the environment or the `.env` next to the test file. Not needed when every decision comes from the lockfile. |
| `JEVTEST_CACHE` | Where the built on-device agents are cached (default `~/.cache/jevtest`). |
