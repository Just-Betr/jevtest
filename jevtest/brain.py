"""Every decision the harness makes with Jev lives here.

Jev only picks from options we give it, so each decision is a Choice (or a
yes/no Noul) over things the harness can actually do on the current screen.
Text to type always comes from the test file, never from the model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .jev import Jev, choice, noul
from .screen import Element, Screen

QUOTED = re.compile(r'"([^"]+)"|“([^”]+)”')
MAX_OPTIONS = 250  # Jev allows 255 options per Choice

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
    "scroll_down": "Scroll down: the element the goal needs is not on screen and may be further down.",
    "scroll_up": "Scroll up: the element the goal needs is not on screen and may be further up.",
    "scroll_left": "Scroll left to reveal content to the left.",
    "scroll_right": "Scroll right to reveal content to the right.",
    "back": "Go back to the previous screen, or dismiss the current dialog or menu.",
    "press_enter": "Press the Enter / Return key to submit what was typed.",
    "hide_keyboard": "Close the on-screen keyboard because it covers something the goal needs.",
    "wait": "Wait: the screen is still loading or animating.",
    "impossible": "The goal cannot be achieved: nothing on this screen moves toward it and "
                  "scrolling or going back will not help.",
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


def _state(screen: Screen, **extra) -> dict:
    state = {"screen": screen.to_state(), "keyboard_visible": screen.keyboard_visible}
    state.update(extra)
    return state


class Brain:
    def __init__(self, jev: Jev, threshold: float = 0.5):
        self.jev = jev
        self.threshold = threshold

    def next_action(self, goal: str, screen: Screen, actions_taken: list[str]) -> Decision:
        """One Jev call: what to do next toward `goal`, plus (speculatively) on what."""
        values = quoted_values(goal)
        fields = screen.editable
        kinds = dict(ACTIONS)
        if not (values and fields):
            kinds.pop("type")
        if not fields:
            kinds.pop("clear")
        if not screen.keyboard_visible:
            kinds.pop("hide_keyboard")
            kinds.pop("press_enter")

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
        a = answers["action"]
        d = Decision(action=a["choice"], confidence=a.get("confidence", 0.0),
                     probabilities=a.get("probabilities", {}))
        if d.action in TOUCH:
            d.element = screen.by_id(answers["target"]["choice"])
        elif d.action in ("type", "clear"):
            d.element = screen.by_id(answers["field"]["choice"])
            if d.action == "type":
                d.text = values[int(answers["value"]["choice"][1:])]
        return d

    def locate(self, target: str, screen: Screen, candidates: list[Element] | None = None) -> Element | None:
        """Find the element the test file names. Exact unique text match skips the model."""
        candidates = screen.elements if candidates is None else candidates
        t = target.strip().lower()
        exact = [el for el in candidates
                 if t in (el.text.lower(), el.hint.lower(), el.resource_id.lower())]
        if len(exact) == 1:
            return exact[0]
        if not candidates:
            return None
        options = _element_options(screen, candidates)
        options["not_on_screen"] = "No element on the screen is `target`."
        ans = self.jev.ask(_state(screen), {"element": choice(
            {"target": target, "question": "Which element on the screen is `target`?"}, options)})
        pick = ans["element"]["choice"]
        return None if pick == "not_on_screen" else screen.by_id(pick)

    def check(self, statement: str, screen: Screen) -> float:
        """Probability that `statement` is true of the current screen."""
        ans = self.jev.ask(_state(screen), {"check": noul(
            {"statement": statement, "question": "Is `statement` true of the current `screen`?"})})
        return ans["check"]["noul"]
