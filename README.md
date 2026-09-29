<h1 align="center">jevtest</h1>

<p align="center"><b>Plain-English end-to-end tests for Android and iOS apps.</b><br>
Write what a user does and what they should see. Jev works out the taps. CI replays every run exactly.</p>

<p align="center">
  <a href="https://github.com/Just-Betr/jevtest/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/Just-Betr/jevtest/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://just-betr.github.io/jevtest/contributing/"><img alt="Coverage" src="https://img.shields.io/endpoint?url=https%3A%2F%2Fjust-betr.github.io%2Fjevtest%2Fbadges%2Fcoverage.json"></a>
  <a href="https://pypi.org/project/jevtest/"><img alt="PyPI" src="https://img.shields.io/pypi/v/jevtest"></a>
  <a href="https://pypi.org/project/jevtest/"><img alt="Python" src="https://img.shields.io/pypi/pyversions/jevtest"></a>
  <a href="https://just-betr.github.io/jevtest/"><img alt="Docs" src="https://img.shields.io/badge/docs-just--betr.github.io%2Fjevtest-5e35b1"></a>
  <a href="https://github.com/Just-Betr/jevtest/blob/main/LICENSE"><img alt="MIT license" src="https://img.shields.io/badge/license-MIT-blue"></a>
</p>

<p align="center"><a href="https://just-betr.github.io/jevtest/"><b>Documentation</b></a> ·
<a href="https://just-betr.github.io/jevtest/getting-started/">Getting started</a> ·
<a href="https://just-betr.github.io/jevtest/reference/steps/">Steps</a> ·
<a href="https://just-betr.github.io/jevtest/guides/ci/">CI</a></p>

---

```yaml
app: build/app-debug.apk
device: { android: emulator-5554 }

tests:
  - name: Sign in
    fresh: true
    steps:
      - do: Sign in with email "${DEMO_EMAIL}" and password "${DEMO_PASSWORD}"
        expect: The home screen is showing
        see: Welcome, ${DEMO_EMAIL}
```

```console
$ jevtest run login.yaml --lock frozen --out results
jevtest 0.9.11 · android · emulator-5554 · dev.jevtest.jevtest_demo · jev-1.13.0 · lockfile: frozen

▶ Sign in
  ✓ do: Sign in with email "${DEMO_EMAIL}" and password "${DEMO_PASSWORD}" (2.6s) — 3 saved steps
      → type "${DEMO_EMAIL}" into text_field 'Email'
      → type "${DEMO_PASSWORD}" into password_field 'Password'
      → tap button 'Sign in'
      ✓ expect: The home screen is showing — Jev 0.96
      ✓ see: Welcome, ${DEMO_EMAIL}
  PASS Sign in (5.1s)

1/1 passed in 5s
Jev: 1 decision, 1 from lockfile, 0 asked live in 0.0s (0% of run time), $0.0000
Results: results/20260928-161344/android/emulator-5554
```

## Why jevtest

- **Tests read like the spec.** One action, then what should be true. `do:` takes a plain-English goal; `tap:`, `type:`, `swipe:`, `scroll_to:` and 22 more give exact control.
- **Deterministic.** Every Jev decision is recorded in a lockfile. The same screen always gets the same answer; `--lock frozen` replays a run exactly, with no network and no API key.
- **Nothing assumed.** No default device, no guessing what a typo meant, and settings with good defaults and strict limits. The whole test file is checked before a device is touched, and every problem is reported at once with what to fix.
- **Real apps, real phones.** Android emulators and phones, iOS simulators and iPhones. Native, Flutter, React Native and **in-app WebViews**, driven the same way. Animations stay on, and what a step changes on the device (orientation, dark mode, network, an iOS location) is put back.
- **Wait until, or fail.** Each step waits until what it needs is on screen, up to its timeout, then fails saying what it waited for. No sleeps.
- **Built for scale and CI.** `${SECRETS}` from `.env` or CI, shared test libraries, whole folders in one command, several devices at once, JUnit XML, JSON reports and failure screenshots.

## Install

```bash
pip install jevtest
```

You also need the platform tools for your apps: the Android SDK and a JDK, and/or Xcode. A [TypeSafe API key](https://console.typesafe.ai/keys) gives access to Jev. See [Getting started](https://just-betr.github.io/jevtest/getting-started/).

## Run

```bash
jevtest run tests.yaml --lock record --out results     # asks Jev about new screens, records the answers
jevtest run tests/ --lock frozen --out results         # a whole folder, replayed exactly from the lockfiles
```

## How it works

A small agent on the device reads the accessibility tree in milliseconds. jevtest describes the screen as text and asks [Jev](https://docs.typesafe.ai), TypeSafe's decision model, to **choose** the next action and element from the options on screen. Jev never writes free text, so everything typed comes from your test file. The steps it works out for each `do:` are saved, and every later run repeats them exactly, without asking Jev. [More](https://just-betr.github.io/jevtest/how-it-works/).

## Status

New, and tested end to end on a Flutter demo app with native and web screens: the Android emulator (API 37), a Pixel 4a (Android 13), iOS simulators (iOS 26) and an iPhone 17 (iOS 27).

Built to a high bar: a [clean architecture](https://just-betr.github.io/jevtest/architecture/) whose layer rules are checked on every commit; strict mypy and strict pyright with no `Any`; every ruff rule; no function above a complexity of 10; 100% line and branch coverage in random order, with Hypothesis properties on every input jevtest reads; dependencies audited and every CI action pinned to a commit. Issues and pull requests are welcome; see [Contributing](https://just-betr.github.io/jevtest/contributing/).

## License

[MIT](https://github.com/Just-Betr/jevtest/blob/main/LICENSE)
