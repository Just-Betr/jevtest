import pytest

from jevtest.adapters.jev.wire import answer_to_wire, answers_from_wire, questions_to_wire
from jevtest.domain.failures import ModelError
from jevtest.domain.model import Choice, Picked, Probability, YesNo

QUESTIONS = {"action": Choice({"goal": "g"}, {"tap": "Tap", "back": "Back"}), "check": YesNo({"statement": "s"})}
PICKED = {"type": "choice", "choice": "tap", "confidence": 0.8, "probabilities": {"tap": 0.8, "back": 0.2}}


def test_questions_in_jevs_format():
    assert questions_to_wire(QUESTIONS) == {
        "action": {"type": "choice", "instructions": {"goal": "g"}, "criteria": {"tap": "Tap", "back": "Back"}},
        "check": {"type": "noul", "instructions": {"statement": "s"}},
    }


def test_answers_become_typed_and_back():
    answers = answers_from_wire({"action": PICKED, "check": {"type": "noul", "noul": 1}}, QUESTIONS)
    assert answers == {"action": Picked("tap", 0.8, {"tap": 0.8, "back": 0.2}), "check": Probability(1.0)}
    assert answer_to_wire(answers["action"]) == PICKED
    assert answer_to_wire(answers["check"]) == {"type": "noul", "noul": 1.0}


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ({"action": PICKED}, r"Jev answered \['action'\], expected \['action', 'check'\]"),
        ({"action": {**PICKED, "choice": "fly"}, "check": {"noul": 0.5}}, "'fly' for action, which is not one of"),
        ({"action": {"choice": "tap"}, "check": {"noul": 0.5}}, "answer for action has no confidence or probabilities"),
        ({"action": PICKED, "check": {"noul": True}}, "True for the yes/no question check"),
        ({"action": PICKED, "check": {"noul": 1.5}}, "1.5 for the yes/no question check"),
        ({"action": PICKED, "check": {"noul": "yes"}}, "'yes' for the yes/no question check"),
        ({"action": PICKED, "check": {}}, "None for the yes/no question check"),
    ],
)
def test_answers_outside_the_question_are_errors(raw, message):
    with pytest.raises(ModelError, match=message):
        answers_from_wire(raw, QUESTIONS)
