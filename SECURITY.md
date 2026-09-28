# Security

Please report security problems privately through GitHub's
[private vulnerability reporting](https://github.com/Just-Betr/jevtest/security/advisories/new), not in a public
issue.

## What jevtest sends where

- **To TypeSafe (Jev), at `api.typesafe.ai`:** the screen as text (element kinds, labels, positions) and the questions about it. Text
  the app shows on screen is part of that; password fields show only dots.
- **`${NAME}` values** from `.env` or the environment are put into the app only when a step needs them. Logs,
  reports and everything sent to Jev keep the `${NAME}`, including where the app shows the value on screen.
- **The lockfile** stores Jev's answers and the screens they were asked about. Review it before committing if your
  app shows sensitive data on screen.
- Nothing else leaves the machine.
