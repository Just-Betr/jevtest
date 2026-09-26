from jevtest.adapters.testfile.loader import parse_step
from jevtest.domain.kinds import Status
from jevtest.domain.results import CheckResult, RunResult, StepResult, TestResult
from jevtest.domain.steps import NotSee, See

BACK = parse_step("back")


def test_a_passing_step_has_no_failure():
    assert StepResult(BACK, Status.PASS, 1.0).failure is None


def test_the_failure_is_the_first_failing_check():
    step = StepResult(BACK, Status.FAIL, 1.0, checks=(CheckResult(See("A"), Status.PASS),
                                                      CheckResult(NotSee("B"), Status.FAIL, "still on screen")))
    assert step.failure == "not_see: B — still on screen"


def test_the_failure_of_a_step_without_checks_is_its_action():
    assert StepResult(BACK, Status.FAIL, 1.0, "no sensor").failure == "back — no sensor"
    assert StepResult(BACK, Status.FAIL, 1.0).failure == "back — "


def test_a_use_step_fails_with_its_inner_failure():
    inner = StepResult(BACK, Status.FAIL, 1.0, "x")
    outer = StepResult(parse_step({"use": "T"}), Status.FAIL, 1.0, steps=(StepResult(BACK, Status.PASS, 0.1), inner))
    assert outer.failure == "back — x"


def test_test_results():
    passing = TestResult("A", Status.PASS, 1.0)
    failing = TestResult("B", Status.FAIL, 2.5, (StepResult(BACK, Status.FAIL, 2.5, "x"),))
    assert passing.failure is None and failing.failure == "back — x"
    assert TestResult("C", Status.FAIL, 0.0, start_failure="locked").failure == "(start app) — locked"
    run = RunResult((passing, failing))
    assert (run.passed, run.failed, run.seconds) == (1, 1, 3.5)
