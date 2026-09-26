"""Every decision the harness makes with Jev lives here.

Jev only picks from options we give it, so each decision is a Choice (or a
yes/no Noul) over things the harness can actually do on the current screen.
Text to type always comes from the test file, never from the model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .jev import JevError, choice, noul
from .screen import Element, Screen

QUOTED = re.compile(r'"([^"]+)"|“([^”]+)”')
MAX_OPTIONS = 250  # Jev allows 255 options per Choice
CONFIRM = 0.5      # yes-probability needed to accept a located element

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
TOUCH = {"tap", "double_tap", "long_press", "swipe_left_on", "swipe_right_on"}


@dataclass
class Decision:
    action: str
    element: Element | None = None
    text: str | None = None
    confidence: float = 0.0
    probabilities: dict = field(default_factory=dict)

    def describe(self) -> str:
        if self.action == "type":
            return f'type "{self.text}" into {self.element.label()}'
        if self.element is not None:
            return f"{self.action.replace('_on', '')} {self.element.label()}"
        return self.action.replace("_", " ")


def quoted_values(goal: str) -> list[str]:
    return [a or b for a, b in QUOTED.findall(goal)]


def _element_options(screen: Screen, elements: list[Element]) -> dict:
    opts = {}
    for el in elements[:MAX_OPTIONS]:
        desc = f"The {el.label()} at the {screen.region(el)} of the screen"
        if el.checked is not None:
            desc += " (currently on)" if el.checked else " (currently off)"
        if not el.enabled:
            desc += " (disabled)"
        opts[el.id] = desc
    return opts


def _picked(answers: dict, qid: str, options) -> str:
    """The option Jev chose for `qid`; anything outside the options is an error, never a guess."""
    chosen = answers[qid].get("choice")
    if chosen not in options:
        raise JevError(f"Jev answered {chosen!r} for {qid}, which is not one of the options")
    return chosen


def _probability(answers: dict, qid: str) -> float:
    p = answers[qid].get("noul")
    if not isinstance(p, (int, float)) or isinstance(p, bool) or not 0 <= p <= 1:
        raise JevError(f"Jev returned {p!r} for a yes/no question")
    return float(p)


def _state(screen: Screen, **extra) -> dict:
    state = {"screen": screen.to_state(), "keyboard_visible": screen.keyboard_visible}
    state.update(extra)
    return state


class Brain:
    def __init__(self, jev):
        self.jev = jev  # a Jev client or a LockedJev: anything with ask() and calls

    def next_action(self, goal: str, screen: Screen, actions_taken: list[str]) -> Decision:
        """One Jev call: what to do next toward `goal`, plus (speculatively) on what."""
        values = quoted_values(goal)[:MAX_OPTIONS]
        fields = screen.editable
        kinds = dict(ACTIONS)
        if not (values and fields):
            kinds.pop("type")
        if not fields:
            kinds.pop("clear")
        if not screen.keyboard_visible:
            kinds.pop("hide_keyboard")
            kinds.pop("press_enter")
        if not screen.elements:
            for k in TOUCH:
                kinds.pop(k)

        state = _state(screen, actions_taken=actions_taken or ["(none yet)"])
        questions = {
            "action": choice({"goal": goal, "question":
                              "What is the single next action needed to achieve `goal`?"}, kinds),
        }
        if screen.elements:
            questions["target"] = choice(
                {"goal": goal, "question": "If the next action toward `goal` is to tap, double tap, "
                                           "long press or swipe an element, which element?"},
                _element_options(screen, screen.elements))
        if "type" in kinds or "clear" in kinds:
            questions["field"] = choice(
                {"goal": goal, "question": "If the next action toward `goal` is to type into or clear "
                                           "a text field, which text field?"},
                _element_options(screen, fields))
        if "type" in kinds:
            questions["value"] = choice(
                {"goal": goal, "question": "Which value from `goal` should be typed next? "
                                           "Skip values already typed in `actions_taken`."},
                {f"v{i}": f'"{v}"' for i, v in enumerate(values)})

        answers = self.jev.ask(state, questions)
        d = Decision(action=_picked(answers, "action", kinds), confidence=answers["action"].get("confidence", 0.0),
                     probabilities=answers["action"].get("probabilities", {}))
        if d.action in TOUCH:
            d.element = screen.by_id(_picked(answers, "target", questions["target"]["criteria"]))
        elif d.action in ("type", "clear"):
            d.element = screen.by_id(_picked(answers, "field", questions["field"]["criteria"]))
            if d.action == "type":
                d.text = values[int(_picked(answers, "value", questions["value"]["criteria"])[1:])]
        return d

    def locate(self, target: str, screen: Screen, candidates: list[Element] | None = None) -> Element | None:
        """Find the element the test file names. Text is matched in code first (exact, then
        contained); only a description that is not on-screen text goes to Jev."""
        candidates = screen.elements if candidates is None else candidates
        t = target.strip().lower()
        for matches in (
            [el for el in candidates if t in (el.text.lower(), el.hint.lower(), el.resource_id.lower())],
            [el for el in candidates if t in el.text.lower() or t in el.hint.lower()],
        ):
            if len(matches) > 1:  # e.g. a label and the switch beside it: the one you can act on
                matches = [el for el in matches if el.clickable or el.editable] or matches
            if len(matches) == 1:
                return matches[0]
            if matches:  # several elements carry that text: let Jev choose among them only
                candidates = matches
                break
        if not candidates:
            return None
        options = _element_options(screen, candidates)
        options["not_on_screen"] = "No element on the screen is `target`."
        ans = self.jev.ask(_state(screen), {"element": choice(
            {"target": target, "question": "Which element on the screen is `target`?"}, options)})
        pick = _picked(ans, "element", options)
        if pick == "not_on_screen":
            return None
        # A Choice always picks the closest option, even when the target is not on screen at all, so
        # confirm the pick with a yes/no question (the pattern TypeSafe's docs recommend).
        el = screen.by_id(pick)
        confirm = self.jev.ask(_state(screen), {"is_target": noul(
            {"target": target, "element": options[pick],
             "question": "Is `element` the element described by `target`?"})})
        return el if _probability(confirm, "is_target") > CONFIRM else None

    def check(self, statement: str, screen: Screen) -> float:
        """Probability that `statement` is true of the current screen."""
        ans = self.jev.ask(_state(screen), {"check": noul(
            {"statement": statement, "question": "Is `statement` true of the current `screen`?"})})
        return _probability(ans, "check")
