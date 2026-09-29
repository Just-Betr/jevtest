# CI

jevtest is built to run unattended: one exit code, JUnit XML for your CI's test view, a JSON report and failure screenshots to upload.

## Which lock mode in CI?

| Mode | Saved `do:` steps and recorded answers | New goals, changed screens | Needs the API key | Good for |
|---|---|---|---|---|
| `record` | repeated exactly | asks Jev, saves the steps and answers | yes | **everyday CI**: tests follow UI changes without anyone re-recording |
| `frozen` | repeated exactly | **fails the run** | no | release gates, reproducing a failure exactly, offline runs |

With `record`, each `do:` repeats its saved steps; only a new goal, or one whose saved steps no longer fit the app (an element was renamed), goes to Jev, and only from where the saved steps stopped fitting. Each new decision costs a fraction of a cent. What's recorded in CI stays in that run's workspace unless you commit it back, so re-record locally and commit the lockfile when the app changes.

With `frozen`, nothing new is asked: a `do:` without saved steps fails with `No steps are saved for this do: in tests.lock.json, and --lock frozen only repeats saved steps`, and a saved step whose element never shows up fails with what the screen shows instead. That's exact but strict: a renamed button means re-recording locally and committing the lockfile.

## GitHub Actions

=== "Android (emulator)"

    ```yaml title=".github/workflows/e2e.yml"
    name: e2e
    on: [pull_request]
    jobs:
      android:
        runs-on: ubuntu-latest
        steps:
          - uses: actions/checkout@v4
          - uses: actions/setup-python@v5
            with: { python-version: "3.12" }
          - run: pip install jevtest
          - name: Enable KVM
            run: |
              echo 'KERNEL=="kvm", GROUP="kvm", MODE="0666", OPTIONS+="static_node=kvm"' | sudo tee /etc/udev/rules.d/99-kvm4all.rules
              sudo udevadm control --reload-rules && sudo udevadm trigger --name-match=kvm
          - uses: reactivecircus/android-emulator-runner@v2
            with:
              api-level: 34
              arch: x86_64
              avd-name: ci
              script: jevtest run tests/ --platform android --lock record --out results
            env:
              TYPESAFE_API_KEY: ${{ secrets.TYPESAFE_API_KEY }}
              EMAIL: ${{ secrets.QA_EMAIL }}
              PASSWORD: ${{ secrets.QA_PASSWORD }}
              ANDROID_DEVICE: ci
          - uses: actions/upload-artifact@v4
            if: always()
            with: { name: jevtest-results, path: results }
    ```

=== "iOS (simulator)"

    ```yaml title=".github/workflows/e2e.yml"
    name: e2e
    on: [pull_request]
    jobs:
      ios:
        runs-on: macos-15
        steps:
          - uses: actions/checkout@v4
          - uses: actions/setup-python@v5
            with: { python-version: "3.12" }
          - run: pip install jevtest
          - run: xcrun simctl boot "iPhone 16"
          - run: jevtest run tests/ --platform ios --lock record --out results
            env:
              TYPESAFE_API_KEY: ${{ secrets.TYPESAFE_API_KEY }}
              IOS_DEVICE: iPhone 16
          - uses: actions/upload-artifact@v4
            if: always()
            with: { name: jevtest-results, path: results }
    ```

!!! note "Starting points"
    These workflows show the shape: a running device, the key and secrets from CI, results uploaded. Add your app's build step before the run, and pick the API level or simulator your app targets.

Each job runs its own platform's part of the files (`--platform`), so a file that runs on both needs no iOS build, device or `${IOS_…}` values on the Linux job, and no Android ones on the Mac job. Test files take the device name from the environment (`device: { android: "${ANDROID_DEVICE}" }`), so the same file runs locally and in CI.

## Test reports

`results/<run>/junit.xml` has one `<testsuite>` per file, platform and device, and one `<testcase>` per test with the failure reason and the full step log. Most CI systems read it directly; on GitHub Actions, a JUnit reporter action such as `mikepenz/action-junit-report` shows it on the pull request.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | every test passed |
| 1 | a test failed |
| 2 | setup error: bad test file, missing value, no such device, … |
| 130 | interrupted (Ctrl-C) |
| 143 | stopped by SIGTERM, as CI sends when a job is cancelled: devices are put back and agents stopped first |
