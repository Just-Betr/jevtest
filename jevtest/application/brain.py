"""The questions jevtest asks its decision model, and what it does with the answers.

The model only picks from options it is given, so every question is a `Choice` over things jevtest can actually
do on the current screen, or a `YesNo`. Text to type always comes from the test file, never from the model.

The wording of the questions and of the screen description is part of every recorded decision: change it and
recorded lockfiles no longer match.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from jevtest.domain.decisions import (
    ClearField,
    CloseKeyboard,
    Decision,
    Finished,
    GoBack,
    Impossible,
    Move,
    PressEnter,
    ScrollPage,
    SwipeElement,
    TouchElement,
    TypeInto,
    WaitForScreen,
)
from jevtest.domain.failures import ModelError
from jevtest.domain.kinds import Direction, Gesture
from jevtest.domain.model import Answer, Choice, Picked, Probability, Question, State, YesNo
from jevtest.domain.ports import DecisionModel
from jevtest.domain.screen import Element, Screen

QUOTED = re.compile(r'"([^"]+)"|“([^”]+)”')
MAX_OPTIONS = 250  # the model allows 255 options per Choice
CONFIRM = 0.5  # yes-probability needed to accept an element the model located

ACTIONS = {
    "done": "The goal is already fully achieved. Pick this when `actions_taken` already did "
    "everything the goal asks, or the screen already shows the result the goal wants.",
    "tap": "Tap (click, press, select, open, toggle) an element on the screen.",
    "double_tap": "Double tap an element. Only when the goal asks for a double tap.",
    "long_press": "Long press (press and hold) an element. Only when the goal asks to hold or long press.",
    "swipe_left_on": "Swipe left on one element, e.g. to delete a list row or reveal its actions.",
    "swipe_right_on": "Swipe right on one element.",
    "type": "Type one of the goal's quoted values into a text field.",
    "clear": "Erase the existing text in a text field.",
    "scroll_down": "Scroll down: the element the goal needs is not in `screen` and may be further down "
    "(or under the keyboard).",
    "scroll_up": "Scroll up: the element the goal needs is not on screen and may be further up.",
    "scroll_left": "Scroll left to reveal content to the left.",
    "scroll_right": "Scroll right to reveal content to the right.",
    "back": "Go back to the previous screen, or dismiss the current dialog or menu.",
    "press_enter": "Press the Enter / Return key to submit what was typed.",
    "hide_keyboard": "Close the on-screen keyboard. It covers the lower part of the screen, so an element the goal "
    "needs that is not in `screen` may be hidden under it.",
    "wait": "Wait: the screen is still loading or animating.",
    "impossible": "The goal cannot be achieved at all: the keyboard is closed, and neither scrolling nor going "
    "back could reveal what the goal needs.",
}
TOUCH = {"tap": Gesture.TAP, "double_tap": Gesture.DOUBLE_TAP, "long_press": Gesture.LONG_PRESS}
SWIPE_ON = {"swipe_left_on": Direction.LEFT, "swipe_right_on": Direction.RIGHT}
SCROLL = {f"scroll_{d}": d for d in Direction}
SIMPLE: dict[str, Move] = {
    "done": Finished(),
    "impossible": Impossible(),
    "wait": WaitForScreen(),
    "back": GoBack(),
    "press_enter": PressEnter(),
    "hide_keyboard": CloseKeyboard(),
}


def quoted_values(goal: str) -> list[str]:
    """The values a goal puts in quotes: the only text the model may have typed."""
    return [a or b for a, b in QUOTED.findall(goal)]


def describe(screen: Screen) -> list[dict[str, object]]:
    """The screen as the model reads it: one short record per element, positions in words."""
    out: list[dict[str, object]] = []
    for el in screen.elements:
        d: dict[str, object] = {"id": el.id, "type": el.kind}
        if el.text:
            d["text"] = el.text
        if el.hint:
            d["hint"] = el.hint
        if el.resource_id:
            d["resource_id"] = el.resource_id
        d["position"] = screen.region(el)
        if not el.enabled:
            d["enabled"] = False
        if el.checked is not None:
            d["checked"] = el.checked
        if el.focused:
            d["focused"] = True
        if el.selected:
            d["selected"] = True
        if el.scrollable:
            d["scrollable"] = True
        out.append(d)
    return out


def _state(screen: Screen, **extra: object) -> State:
    return {"screen": describe(screen), "keyboard_visible": screen.keyboard_visible, **extra}


def _element_options(screen: Screen, elements: Sequence[Element]) -> dict[str, str]:
    opts: dict[str, str] = {}
    for el in elements[:MAX_OPTIONS]:
        desc = f"The {el.label()} at the {screen.region(el)} of the screen"
        if el.checked is not None:
            desc += " (currently on)" if el.checked else " (currently off)"
        if not el.enabled:
            desc += " (disabled)"
        opts[el.id] = desc
    return opts


def _picked(answer: Answer) -> Picked:
    if not isinstance(answer, Picked):
        raise ModelError(f"Expected a choice from the model, got {answer!r}")
    return answer


def _yes(answer: Answer) -> float:
    if not isinstance(answer, Probability):
        raise ModelError(f"Expected a yes/no probability from the model, got {answer!r}")
    return answer.yes


def _by_text(target: str, pool: Sequence[Element]) -> Element | list[Element]:
    """The element whose text is `target`, else the one containing it.

    When several match, those are returned for the model to choose among; when none do, an empty list.
    """
    t = target.strip().lower()
    for matches in (_exact(t, pool), _containing(t, pool)):
        found = _prefer_actionable(matches)
        if len(found) == 1:
            return found[0]
        if found:
            return found
    return []


def _exact(t: str, pool: Sequence[Element]) -> list[Element]:
    return [el for el in pool if t in (el.text.lower(), el.hint.lower(), el.resource_id.lower())]


def _containing(t: str, pool: Sequence[Element]) -> list[Element]:
    return [el for el in pool if t in el.text.lower() or t in el.hint.lower()]


def _prefer_actionable(matches: list[Element]) -> list[Element]:
    """Of several matches, the ones a user can act on: a label beside its switch means the switch."""
    actionable = [el for el in matches if el.clickable or el.editable]
    return actionable if len(matches) > 1 and actionable else matches


def _offered_actions(screen: Screen, values: Sequence[str]) -> dict[str, str]:
    """The actions that make sense on this screen: no typing without a field and a value, and so on."""
    kinds = dict(ACTIONS)
    if not (values and screen.editable):
        kinds.pop("type")
    if not screen.editable:
        kinds.pop("clear")
    if not screen.keyboard_visible:
        kinds.pop("hide_keyboard")
        kinds.pop("press_enter")
    if not screen.elements:
        for k in (*TOUCH, *SWIPE_ON):
            kinds.pop(k)
    return kinds


def _goal_questions(goal: str, screen: Screen, values: Sequence[str]) -> dict[str, Question]:
    """The questions for one move: the action, and the element, field and value it may need."""
    kinds = _offered_actions(screen, values)
    questions: dict[str, Question] = {
        "action": Choice({"goal": goal, "question": "What is the single next action needed to achieve `goal`?"}, kinds)
    }
    if screen.elements:
        questions["target"] = Choice(
            {
                "goal": goal,
                "question": "If the next action toward `goal` is to tap, double tap, "
                "long press or swipe an element, which element?",
            },
            _element_options(screen, screen.elements),
        )
    if "type" in kinds or "clear" in kinds:
        questions["field"] = Choice(
            {
                "goal": goal,
                "question": "If the next action toward `goal` is to type into or clear a text field, which text field?",
            },
            _element_options(screen, screen.editable),
        )
    if "type" in kinds:
        questions["value"] = Choice(
            {
                "goal": goal,
                "question": "Which value from `goal` should be typed next? "
                "Skip values already typed in `actions_taken`.",
            },
            {f"v{i}": f'"{v}"' for i, v in enumerate(values)},
        )
    return questions


class Brain:
    """jevtest's judgement: the next move toward a goal, which element a target means, and checks.

    Args:
        model: Where the questions go (Jev behind its lockfile, or a fake).
    """

    def __init__(self, model: DecisionModel) -> None:
        self.model = model

    def next_action(self, goal: str, screen: Screen, actions_taken: Sequence[str]) -> Decision:
        """One model request: the next move toward `goal` from `screen`.

        The target, field and value are asked in the same request (they're only used when the chosen
        action needs them), so each move costs one round trip.
        """
        values = quoted_values(goal)[:MAX_OPTIONS]
        questions = _goal_questions(goal, screen, values)
        answers = self.model.ask(_state(screen, actions_taken=list(actions_taken) or ["(none yet)"]), questions)
        action = _picked(answers["action"])
        return Decision(
            self._move(action.choice, answers, screen, values), action.confidence, dict(action.probabilities)
        )

    @staticmethod
    def _move(action: str, answers: Mapping[str, Answer], screen: Screen, values: Sequence[str]) -> Move:
        """The move for the chosen action, with the element or value it needs from the same answers."""
        get = answers.__getitem__
        if action in SIMPLE:
            return SIMPLE[action]
        if action in SCROLL:
            return ScrollPage(SCROLL[action])
        if action in TOUCH:
            return TouchElement(TOUCH[action], screen.by_id(_picked(get("target")).choice))
        if action in SWIPE_ON:
            return SwipeElement(SWIPE_ON[action], screen.by_id(_picked(get("target")).choice))
        field = screen.by_id(_picked(get("field")).choice)
        if action == "clear":
            return ClearField(field)
        return TypeInto(field, values[int(_picked(get("value")).choice[1:])])

    def locate(self, target: str, screen: Screen, candidates: Sequence[Element] | None = None) -> Element | None:
        """The element the test file names, or None if it isn't on the screen.

        Text is matched in code first: an exact label, then an element containing the text. Only a
        description that isn't on-screen text goes to the model, which picks an element and then must
        confirm it (a Choice always picks the closest option, even when the target isn't there).
        """
        pool: Sequence[Element] = screen.elements if candidates is None else candidates
        found = _by_text(target, pool)
        if isinstance(found, Element):
            return found
        return self._ask_which(target, screen, found or pool) if (found or pool) else None

    def _ask_which(self, target: str, screen: Screen, pool: Sequence[Element]) -> Element | None:
        """The model picks the element `target` describes from `pool`, then must confirm it."""
        options = _element_options(screen, pool)
        options["not_on_screen"] = "No element on the screen is `target`."
        pick = _picked(
            self.model.ask(
                _state(screen),
                {
                    "element": Choice(
                        {"target": target, "question": "Which element on the screen is `target`?"}, options
                    )
                },
            )["element"]
        )
        if pick.choice == "not_on_screen":
            return None
        confirm = self.model.ask(
            _state(screen),
            {
                "is_target": YesNo(
                    {
                        "target": target,
                        "element": options[pick.choice],
                        "question": "Is `element` the element described by `target`?",
                    }
                )
            },
        )
        return screen.by_id(pick.choice) if _yes(confirm["is_target"]) > CONFIRM else None

    def check(self, statement: str, screen: Screen) -> float:
        """The model's probability that `statement` is true of the screen."""
        answers = self.model.ask(
            _state(screen),
            {"check": YesNo({"statement": statement, "question": "Is `statement` true of the current `screen`?"})},
        )
        return _yes(answers["check"])
