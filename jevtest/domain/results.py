"""What happened when tests ran: plain records, built by the runner, read by reports and the console."""

from __future__ import annotations

from dataclasses import dataclass

from .decisions import Decision
from .kinds import Status
from .model import ModelCall
from .steps import Check, Step


@dataclass(frozen=True)
class CheckResult:
    """How one check went.

    Attributes:
        check: The check.
        status: Pass or fail.
        detail: What was found (e.g. Jev's probability), or why it failed.
        model_calls: The model requests the check made.
    """

    check: Check
    status: Status
    detail: str | None = None
    model_calls: tuple[ModelCall, ...] = ()


@dataclass(frozen=True)
class StepResult:
    """How one step went.

    Attributes:
        step: The step.
        status: Pass or fail.
        seconds: How long it took.
        detail: What the action did (e.g. which element it tapped), or why it failed.
        decisions: For a `do:` step, the moves Jev chose, in order.
        model_calls: The model requests the action made (the checks keep their own).
        checks: The checks that ran, in order; the first failure stops them.
        steps: For a `use:` step, the used test's step results.
        screenshot: The screenshot taken when this step failed the test.
    """

    step: Step
    status: Status
    seconds: float
    detail: str | None = None
    decisions: tuple[Decision, ...] = ()
    model_calls: tuple[ModelCall, ...] = ()
    checks: tuple[CheckResult, ...] = ()
    steps: tuple[StepResult, ...] = ()
    screenshot: str | None = None

    @property
    def failure(self) -> str | None:
        """Plain-English reason for the step's first failure, or None if it passed."""
        if self.status is Status.PASS:
            return None
        for inner in self.steps:
            if inner.status is Status.FAIL:
                return inner.failure
        for check in self.checks:
            if check.status is Status.FAIL:
                return f"{check.check.name}: {check.check.text} — {check.detail}"
        return f"{self.step.source} — {self.detail or ''}"


@dataclass(frozen=True)
class TestResult:
    """How one test went.

    Attributes:
        name: The test's name.
        status: Pass or fail.
        seconds: How long it took.
        steps: The step results, up to and including the first failure.
        start_failure: Why the app could not be started, if it couldn't.
        screenshot: The screenshot taken when the app could not be started.
    """

    __test__ = False  # not a pytest test class

    name: str
    status: Status
    seconds: float
    steps: tuple[StepResult, ...] = ()
    start_failure: str | None = None
    screenshot: str | None = None

    @property
    def failure(self) -> str | None:
        """Plain-English reason the test failed, or None if it passed."""
        if self.start_failure is not None:
            return f"(start app) — {self.start_failure}"
        for step in self.steps:
            if step.status is Status.FAIL:
                return step.failure
        return None


@dataclass(frozen=True)
class RunResult:
    """The results of every test a device ran."""

    tests: tuple[TestResult, ...]

    @property
    def passed(self) -> int:
        """How many tests passed."""
        return sum(t.status is Status.PASS for t in self.tests)

    @property
    def failed(self) -> int:
        """How many tests failed."""
        return len(self.tests) - self.passed

    @property
    def seconds(self) -> float:
        """How long the tests took, added up."""
        return sum(t.seconds for t in self.tests)
