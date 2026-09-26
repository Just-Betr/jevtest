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
| `--prune-lock` | no | After a run where every test passed, remove recorded decisions the run didn't use. |
| `-v`, `--verbose` | no | Also print every Jev question with its top answers and probabilities. |
| `--version` | no | Print the version. |

## Lock modes

| `--lock` | Recorded decision | Unrecorded decision | Writes the lockfile |
|---|---|---|---|
| `record` | used | Jev is asked; the answer is recorded | yes |
| `frozen` | used | **the step fails** | no |
| `refresh` | ignored: Jev is asked again | Jev is asked | yes, overwriting |
| `off` | ignored | Jev is asked | no |

## `--prune-lock`

The lockfile only grows: a screen that no longer exists keeps its recorded answers. `--prune-lock` removes them. It only acts after a run of **every** test in which **every** test passed, because a failing test stops early and skips screens that are still real. So it refuses to combine with `--test` or `--lock off`, and after a failure it says `not pruned, because a test failed`.

Prune with the same devices you record on: decisions recorded on one device's screens are unused on another's.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | every test passed |
| 1 | a test failed |
| 2 | setup error: bad test file, missing value, no such device, bad arguments |
| 130 | interrupted (Ctrl-C) |

## Environment

| Variable | Meaning |
|---|---|
| `OPENROUTER_API_KEY` | Your OpenRouter key, for Jev. From the environment or the `.env` next to the test file. Not needed when every decision comes from the lockfile. |
| `JEVTEST_CACHE` | Where the built on-device agents are cached (default `~/.cache/jevtest`). |
