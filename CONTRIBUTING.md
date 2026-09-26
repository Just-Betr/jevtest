# Contributing to jevtest

Thanks for helping. The full guide (setup, checks, layout, principles, building the demo app) is at
**https://just-betr.github.io/jevtest/contributing/**, and in [`docs/contributing.md`](docs/contributing.md).

The short version:

```bash
pip install -e '.[dev,docs]'
ruff check .
pytest --cov        # 100% line and branch coverage is required
```

Changes should keep jevtest's principles: nothing assumed (a mistake is an error that says what to fix),
deterministic runs, no fixed sleeps, never changing the app or the device beyond what a step asks, and Jev as the
only model.
