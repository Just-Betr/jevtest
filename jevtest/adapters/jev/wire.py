"""Jev's wire format: jevtest's typed questions and answers to and from JSON.

The JSON produced here is also what the lockfile keys hash, so changing it invalidates recorded lockfiles.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from jevtest.domain.failures import ModelError
from jevtest.domain.model import Answer, Choice, Picked, Probability, Question, YesNo


def question_to_wire(question: Question) -> dict[str, Any]:
    """One question as Jev expects it."""
    if isinstance(question, Choice):
        return {"type": "choice", "instructions": dict(question.instructions), "criteria": dict(question.options)}
    return {"type": "noul", "instructions": dict(question.instructions)}


def questions_to_wire(questions: Mapping[str, Question]) -> dict[str, dict[str, Any]]:
    """Every question, by id, as Jev expects them."""
    return {qid: question_to_wire(q) for qid, q in questions.items()}


def answers_from_wire(raw: Mapping[str, Mapping[str, Any]], questions: Mapping[str, Question]) -> dict[str, Answer]:
    """Jev's answers as jevtest's types, checked against the questions asked.

    Raises:
        ModelError: An answer is missing, of the wrong kind, or outside the options offered.
    """
    if set(raw) != set(questions):
        raise ModelError(f"Jev answered {sorted(raw)}, expected {sorted(questions)}")
    return {qid: answer_from_wire(qid, raw[qid], question) for qid, question in questions.items()}


def answer_from_wire(qid: str, raw: Mapping[str, Any], question: Question) -> Answer:
    """One answer as jevtest's type, checked against its question.

    Raises:
        ModelError: It is of the wrong kind, or outside the options offered.
    """
    if isinstance(question, YesNo):
        p = raw.get("noul")
        if isinstance(p, bool) or not isinstance(p, int | float) or not 0 <= p <= 1:
            raise ModelError(f"Jev returned {p!r} for the yes/no question {qid}")
        return Probability(float(p))
    chosen = raw.get("choice")
    if chosen not in question.options:
        raise ModelError(f"Jev answered {chosen!r} for {qid}, which is not one of the options")
    confidence, probabilities = raw.get("confidence"), raw.get("probabilities")
    if not isinstance(confidence, int | float) or not isinstance(probabilities, dict):
        raise ModelError(f"Jev's answer for {qid} has no confidence or probabilities")
    return Picked(str(chosen), float(confidence), {str(k): float(v) for k, v in probabilities.items()})


def answer_to_wire(answer: Answer) -> dict[str, Any]:
    """One answer as Jev sends it (for the lockfile and reports)."""
    if isinstance(answer, Probability):
        return {"type": "noul", "noul": answer.yes}
    return {"type": "choice", "choice": answer.choice, "confidence": answer.confidence,
            "probabilities": dict(answer.probabilities)}
