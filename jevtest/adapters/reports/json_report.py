"""The JSON report: every step, check and model request of one device's run."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from jevtest.domain.model import Choice, ModelCall, Picked, Question
from jevtest.domain.results import CheckResult, RunResult, StepResult, TestResult


def write_report(
    path: Path,
    about: Mapping[str, object],
    result: RunResult,
    logs: Mapping[str, Sequence[str]],
    calls: Sequence[ModelCall],
) -> None:
    """Write the report.

    Args:
        path: The file to write.
        about: What ran where: file, platform, device, app, app id, model.
        result: What happened.
        logs: Each test's console log, by test name.
        calls: Every model request the run made.
    """
    report = {
        **about,
        "passed": result.passed,
        "failed": result.failed,
        "tests": [_test(t, logs.get(t.name, ())) for t in result.tests],
        "model_calls": [_call(c) for c in calls],
    }
    path.write_text(json.dumps(report, indent=2, default=str))


def _test(t: TestResult, log: Sequence[str]) -> dict[str, object]:
    out: dict[str, object] = {
        "name": t.name,
        "status": t.status.value,
        "seconds": t.seconds,
        "failure": t.failure,
        "log": list(log),
        "steps": [_step(s) for s in t.steps],
    }
    if t.screenshot:
        out["screenshot"] = t.screenshot
    return out


def _step(s: StepResult) -> dict[str, object]:
    out: dict[str, object] = {"step": s.step.label, "status": s.status.value, "seconds": s.seconds}
    if s.detail:
        out["detail"] = s.detail
    if s.ran:
        out["ran"] = list(s.ran)
    if s.decisions:
        out["decisions"] = [
            {"did": d.move.describe(), "confidence": d.confidence, "probabilities": dict(d.probabilities)}
            for d in s.decisions
        ]
    if s.checks:
        out["checks"] = [_check(c) for c in s.checks]
    if s.steps:
        out["steps"] = [_step(inner) for inner in s.steps]
    if s.screenshot:
        out["screenshot"] = s.screenshot
    return out


def _check(c: CheckResult) -> dict[str, object]:
    return {"check": c.check.name, "text": c.check.text, "status": c.status.value, "detail": c.detail}


def _call(c: ModelCall) -> dict[str, object]:
    return {
        "from_lockfile": c.recorded,
        "ms": c.ms,
        "cost": c.cost,
        "served_by": c.served_by,
        "state": c.state,
        "questions": {qid: _question(q) for qid, q in c.questions.items()},
        "answers": {
            qid: {"choice": a.choice, "confidence": a.confidence, "probabilities": dict(a.probabilities)}
            if isinstance(a, Picked)
            else {"yes": a.yes}
            for qid, a in c.answers.items()
        },
    }


def _question(q: Question) -> dict[str, object]:
    kind = {"choose_from": dict(q.options)} if isinstance(q, Choice) else {"yes_or_no": True}
    return {"instructions": dict(q.instructions), **kind}
