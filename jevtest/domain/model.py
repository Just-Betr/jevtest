"""The questions jevtest asks a decision model, and the answers it gets back.

A decision model (Jev) never writes free text: every question offers options and it picks one (`Choice`), or it
gives the probability that something is true (`YesNo`). These types are what the application layer speaks; the
adapter turns them into the model's wire format.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Choice:
    """Pick one of `options` (option id -> description)."""

    instructions: Mapping[str, str]
    options: Mapping[str, str]


@dataclass(frozen=True)
class YesNo:
    """The probability that the instructions' question is true."""

    instructions: Mapping[str, str]


Question = Choice | YesNo
"""A question for the model."""


@dataclass(frozen=True)
class Picked:
    """The answer to a `Choice`: one of its option ids."""

    choice: str
    confidence: float
    probabilities: Mapping[str, float] = field(default_factory=dict[str, float])


@dataclass(frozen=True)
class Probability:
    """The answer to a `YesNo`: the probability of yes (0 to 1)."""

    yes: float


Answer = Picked | Probability
"""An answer from the model."""

State = Mapping[str, object]
"""What the model is shown alongside the questions: the screen, and whatever else the question needs."""


@dataclass(frozen=True)
class ModelCall:
    """One request to the model, for reports and ``-v`` output.

    Attributes:
        state: What the model was shown.
        questions: What it was asked.
        answers: What it answered.
        recorded: Whether the answers came from the lockfile rather than the model.
        ms: How long the model took (0 when recorded).
        cost: What the request cost in US dollars (0 when recorded; None when the price isn't known).
        served_by: The model version that answered.
    """

    state: State
    questions: Mapping[str, Question]
    answers: Mapping[str, Answer]
    recorded: bool
    ms: int = 0
    cost: float | None = 0.0
    served_by: str | None = None
