"""Jev's wire format: jevtest's typed questions and answers to and from JSON.

The JSON produced here is also what the lockfile keys hash, so changing it invalidates recorded lockfiles.
Answers come from the network or a lockfile, so every field is checked before it becomes a domain type.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, TypedDict, TypeGuard

from jevtest.adapters.shapes import is_json_object
from jevtest.domain.failures import ModelError
from jevtest.domain.model import Answer, Choice, Picked, Probability, Question, YesNo


class WireChoice(TypedDict):
    """A choice question as Jev takes it."""

    type: Literal["choice"]
    instructions: dict[str, object]
    criteria: dict[str, str]


class WireYesNo(TypedDict):
    """A yes/no question as Jev takes it."""

    type: Literal["noul"]
    instructions: dict[str, object]


class WirePicked(TypedDict):
    """A choice answer as Jev sends it."""

    type: Literal["choice"]
    choice: str
    confidence: float
    probabilities: dict[str, float]


class WireProbability(TypedDict):
    """A yes/no answer as Jev sends it."""

    type: Literal["noul"]
    noul: float


WireQuestion = WireChoice | WireYesNo
WireAnswer = WirePicked | WireProbability
RawAnswers = Mapping[str, Mapping[str, object]]
"""Answers by question id, as parsed JSON: not yet checked."""


def question_to_wire(question: Question) -> WireQuestion:
    """One question as Jev expects it."""
    if isinstance(question, Choice):
        return {"type": "choice", "instructions": dict(question.instructions), "criteria": dict(question.options)}
    return {"type": "noul", "instructions": dict(question.instructions)}


def questions_to_wire(questions: Mapping[str, Question]) -> dict[str, WireQuestion]:
    """Every question, by id, as Jev expects them."""
    return {qid: question_to_wire(q) for qid, q in questions.items()}


def answers_from_wire(raw: RawAnswers, questions: Mapping[str, Question]) -> dict[str, Answer]:
    """Jev's answers as jevtest's types, checked against the questions asked.

    Raises:
        ModelError: An answer is missing, of the wrong kind, or outside the options offered.
    """
    if set(raw) != set(questions):
        raise ModelError(f"Jev answered {sorted(raw)}, expected {sorted(questions)}")
    return {qid: answer_from_wire(qid, raw[qid], question) for qid, question in questions.items()}


def answer_from_wire(qid: str, raw: Mapping[str, object], question: Question) -> Answer:
    """One answer as jevtest's type, checked against its question.

    Raises:
        ModelError: It is of the wrong kind, or outside the options offered.
    """
    if isinstance(question, YesNo):
        p = raw.get("noul")
        if not _is_number(p) or not 0 <= p <= 1:
            raise ModelError(f"Jev returned {p!r} for the yes/no question {qid}")
        return Probability(float(p))
    chosen = raw.get("choice")
    if not isinstance(chosen, str) or chosen not in question.options:
        raise ModelError(f"Jev answered {chosen!r} for {qid}, which is not one of the options")
    confidence, probabilities = raw.get("confidence"), raw.get("probabilities")
    missing = ModelError(f"Jev's answer for {qid} has no confidence or probabilities")
    if not _is_number(confidence) or not is_json_object(probabilities):
        raise missing
    checked: dict[str, float] = {}
    for option, p in probabilities.items():
        if not _is_number(p):
            raise missing
        checked[option] = float(p)
    return Picked(chosen, float(confidence), checked)


def answer_to_wire(answer: Answer) -> WireAnswer:
    """One answer as Jev sends it (for the lockfile and reports)."""
    if isinstance(answer, Probability):
        return {"type": "noul", "noul": answer.yes}
    return {
        "type": "choice",
        "choice": answer.choice,
        "confidence": answer.confidence,
        "probabilities": dict(answer.probabilities),
    }


def _is_number(value: object) -> TypeGuard[float]:
    return isinstance(value, int | float) and not isinstance(value, bool)
