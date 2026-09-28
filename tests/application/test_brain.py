import dataclasses
import json
from collections.abc import Sequence

import pytest

from jevtest.application.brain import ACTIONS, MAX_OPTIONS, Brain, Located, _picked, _yes, describe, quoted_values
from jevtest.domain.decisions import PressEnter, TypeInto, WaitForScreen
from jevtest.domain.failures import ModelError
from jevtest.domain.model import Picked, Probability
from jevtest.domain.screen import Element, Screen
from tests.conftest import FakeModel, act, confirm, el, login_screen, pick, yes

# --- what the model is shown ------------------------------------------------------------


def test_the_screen_is_described_in_words_not_numbers():
    assert describe(login_screen())[0] == {"id": "e1", "type": "text_field", "hint": "Email", "position": "top-center"}


def test_the_description_includes_only_meaningful_flags():
    s = Screen(
        300,
        300,
        (
            el("switch", "Wifi", checked=False, bounds=(0, 0, 10, 10)),
            el(
                "button",
                "Go",
                enabled=False,
                focused=True,
                selected=True,
                scrollable=True,
                resource_id="go",
                bounds=(290, 290, 300, 300),
            ),
        ),
    )
    first, second = describe(s)
    assert first == {"id": "e1", "type": "switch", "text": "Wifi", "position": "top-left", "checked": False}
    assert second == {
        "id": "e2",
        "type": "button",
        "text": "Go",
        "resource_id": "go",
        "position": "bottom-right",
        "enabled": False,
        "focused": True,
        "selected": True,
        "scrollable": True,
    }


@pytest.mark.parametrize(
    ("goal", "values"),
    [
        ('email "a@b.c" and password “x y”', ["a@b.c", "x y"]),
        ("no quotes here", []),
        ('empty "" is ignored', []),
    ],
)
def test_quoted_values(goal, values):
    assert quoted_values(goal) == values


# --- next_action -----------------------------------------------------------------------


def test_next_action_type_picks_field_and_value():
    model = FakeModel(act("type", field="e2", value="v1"))
    d = Brain(model).next_action('Sign in with "a@b.c" and "pw"', login_screen(), [])
    assert isinstance(d.move, TypeInto) and (d.move.element.hint, d.move.text) == ("Password", "pw")
    assert d.move.describe() == "type \"pw\" into password_field 'Password'"
    assert d.confidence == 0.9 and d.probabilities == {"type": 0.9, "other": 0.1}
    state, questions = model.asked[0]
    assert state["actions_taken"] == ["(none yet)"]
    assert set(questions) == {"action", "target", "field", "value"}
    assert questions["value"]["criteria"] == {"v0": '"a@b.c"', "v1": '"pw"'}


def test_next_action_offers_only_possible_actions():
    model = FakeModel(act("tap", target="e3"))
    d = Brain(model).next_action("Press sign in", login_screen(), ["tap x"])
    assert d.move.describe() == "tap button 'Sign in'"
    options = model.asked[0][1]["action"]["criteria"]
    assert "type" not in options  # no quoted values
    assert "clear" in options  # there are fields
    assert "press_enter" not in options and "hide_keyboard" not in options  # keyboard hidden
    assert model.asked[0][0]["actions_taken"] == ["tap x"]


def test_next_action_with_keyboard_and_no_fields():
    s = Screen(10, 10, (el("button", "Go", bounds=(0, 0, 10, 10)),), keyboard_visible=True)
    model = FakeModel(act("press_enter"))
    d = Brain(model).next_action('Type "x"', s, [])
    options = model.asked[0][1]["action"]["criteria"]
    assert {"press_enter", "hide_keyboard"} <= set(options)
    assert "type" not in options and "clear" not in options and "field" not in model.asked[0][1]
    assert d.move == PressEnter()


def test_next_action_on_empty_screen_offers_no_touch():
    model = FakeModel({"action": {"type": "choice", "choice": "wait", "confidence": 0.5, "probabilities": {}}})
    d = Brain(model).next_action("Anything", Screen(10, 10), [])
    options = model.asked[0][1]["action"]["criteria"]
    assert not {"tap", "double_tap", "long_press", "swipe_left_on", "swipe_right_on"} & set(options)
    assert "target" not in model.asked[0][1]
    assert d.move == WaitForScreen()


@pytest.mark.parametrize(
    ("action", "described"),
    [
        ("clear", "clear text_field 'Email'"),
        ("swipe_left_on", "swipe_left button 'Sign in'"),
        ("swipe_right_on", "swipe_right button 'Sign in'"),
        ("scroll_up", "scroll up"),
        ("back", "back"),
        ("hide_keyboard", "hide keyboard"),
        ("impossible", "impossible"),
        ("long_press", "long_press button 'Sign in'"),
    ],
)
def test_each_action_becomes_its_move(action, described):
    model = FakeModel(act(action, target="e3", field="e1"))
    d = Brain(model).next_action("x", login_screen(keyboard_visible=True), [])
    assert d.move.describe() == described


def test_options_are_capped_for_the_model_limit():
    s = Screen(10, 10, tuple(el("button", f"b{i}", bounds=(0, 0, 10, 10)) for i in range(300)))
    model = FakeModel(act("tap", target="e1"))
    Brain(model).next_action("tap", s, [])
    assert len(model.asked[0][1]["target"]["criteria"]) == MAX_OPTIONS


def test_option_descriptions_mention_state():
    s = Screen(
        10,
        10,
        (
            el("switch", "Wifi", checked=True, bounds=(0, 0, 10, 10)),
            el("switch", "BT", checked=False, enabled=False, bounds=(0, 0, 10, 10)),
        ),
    )
    model = FakeModel(act("tap"))
    Brain(model).next_action("x", s, [])
    target = model.asked[0][1]["target"]["criteria"]
    assert "(currently on)" in target["e1"]
    assert "(currently off)" in target["e2"] and "(disabled)" in target["e2"]


def test_every_action_has_a_description():
    assert all(ACTIONS.values())


def test_quoted_values_are_capped():
    goal = " ".join(f'"v{i}"' for i in range(300))
    model = FakeModel(act("done"))
    Brain(model).next_action(goal, login_screen(), [])
    assert len(model.asked[0][1]["value"]["criteria"]) == MAX_OPTIONS


# --- locate / check --------------------------------------------------------------------


def located(brain: Brain, target: str, screen: Screen, candidates: Sequence[Element] | None = None) -> Located:
    """What `Brain.locate` found; the test fails here if it found nothing."""
    found = brain.locate(target, screen, candidates)
    assert found is not None
    return found


def screen_of(*elements):
    return Screen(10, 10, tuple(dataclasses.replace(e, bounds=(0, 0, 10, 10)) for e in elements))


def test_locate_exact_unique_match_skips_the_model():
    model = FakeModel()
    b = Brain(model)
    assert b.locate("Sign in", login_screen()) == Located(login_screen().elements[2])
    assert located(b, "Email", login_screen()).element.hint == "Email"
    assert b.locate("SIGN IN", login_screen()) == Located(login_screen().elements[2])  # case doesn't matter
    assert located(b, "don't allow", screen_of(el("button", "Don't Allow"))).element.text == "Don't Allow"
    assert not model.asked


@pytest.mark.parametrize(
    ("target", "on_screen"),
    [
        ("Save", ["Unsaved changes", "Save draft"]),  # the target inside longer texts
        ("Taps: 2", ["Taps: 20"]),
        ("Delete", ["Delete account", "Delete photo"]),
    ],
)
def test_a_near_match_is_never_used_and_jev_is_not_asked(target, on_screen):
    model = FakeModel()
    s = screen_of(*(el("button", text, clickable=True) for text in on_screen))
    assert Brain(model).locate(target, s) is None
    assert not model.asked


@pytest.mark.parametrize("target", ["Email: a@b.c", "Email", "a@b.c"])
def test_an_ios_label_and_value_each_match_exactly(target):
    field = el("text_field", "Email: a@b.c", parts=("Email", "a@b.c"), editable=True)
    assert located(Brain(FakeModel()), target, screen_of(field)).element.text == "Email: a@b.c"


@pytest.mark.parametrize("target", ["Go (Go now)", "Go", "Go now"])
def test_an_android_text_and_description_each_match_exactly(target):
    button = el("button", "Go (Go now)", parts=("Go", "Go now"), clickable=True)
    assert located(Brain(FakeModel()), target, screen_of(button)).element.text == "Go (Go now)"


def test_locate_by_resource_id():
    s = Screen(10, 10, (el("image", resource_id="logo", bounds=(0, 0, 10, 10)),))
    assert located(Brain(FakeModel()), "logo", s).element.resource_id == "logo"


def test_locate_prefers_the_one_actionable_exact_match():
    s = screen_of(el("text", "Dark theme"), el("switch", "Dark theme", clickable=True))
    model = FakeModel()
    assert located(Brain(model), "Dark theme", s).element.kind == "switch" and not model.asked


def test_several_exact_matches_jev_chooses_among_those_only():
    s = screen_of(
        el("button", "Delete", clickable=True),
        el("button", "Delete", clickable=True),
        el("button", "Delete account", clickable=True),
    )
    model = FakeModel(pick("e2"), confirm())
    found = Brain(model).locate("Delete", s)
    assert found is not None and found == Located(s.elements[1], "chosen by Jev among 2 exact matches")
    assert set(model.asked[0][1]["element"]["criteria"]) == {"e1", "e2", "not_on_screen"}
    assert found.describe() == "button 'Delete' (chosen by Jev among 2 exact matches)"


def test_a_description_goes_to_jev_which_must_confirm_and_says_so():
    model = FakeModel(pick("e3"), confirm(0.9))
    found = Brain(model).locate("the login button", login_screen())
    assert found is not None
    assert found.element.text == "Sign in" and found.describe() == "button 'Sign in' (chosen by Jev)"
    question = model.asked[1][1]["is_target"]
    assert question["type"] == "noul" and question["instructions"]["target"] == "the login button"


def test_locate_rejects_a_closest_but_wrong_pick():
    """A Choice always picks something; the yes/no check catches a target that isn't there."""
    assert Brain(FakeModel(pick("e3"), confirm(0.2))).locate("Here is more content", login_screen()) is None


def test_locate_not_on_screen():
    assert Brain(FakeModel(pick("not_on_screen"))).locate("settings gear", login_screen()) is None


def test_locate_with_no_candidates():
    assert Brain(FakeModel()).locate("x", login_screen(), candidates=[]) is None


def test_locate_limits_candidates():
    model = FakeModel(pick("e1"), confirm())
    s = login_screen()
    Brain(model).locate("the first box", s, candidates=s.editable)
    assert set(model.asked[0][1]["element"]["criteria"]) == {"e1", "e2", "not_on_screen"}


def test_check_returns_probability():
    model = FakeModel(yes(0.83))
    assert Brain(model).check("Home shows", login_screen()) == 0.83
    assert model.asked[0][1]["check"]["type"] == "noul"


# --- answers of the wrong kind ----------------------------------------------------------------


def test_answers_must_be_of_the_kind_asked():
    assert _picked(Picked("a", 1.0)).choice == "a" and _yes(Probability(0.5)) == 0.5
    with pytest.raises(ModelError, match="Expected a choice"):
        _picked(Probability(0.5))
    with pytest.raises(ModelError, match="Expected a yes/no probability"):
        _yes(Picked("a", 1.0))


def test_jev_is_never_sent_a_variables_value():
    """What the app shows (a signed-in email) reaches Jev as its ${NAME}: in the screen, options and questions."""
    screen = screen_of(el("text", "Welcome, ann@x.io"), el("button", "Log out ann@x.io", clickable=True))
    model = FakeModel(yes(0.9), pick("e2"), confirm())
    brain = Brain(model, {"EMAIL": "ann@x.io"})
    assert brain.check("Welcome, ann@x.io is showing", screen) == 0.9
    located(brain, "the log out button for ann@x.io", screen)
    sent = json.dumps(model.asked)
    assert "ann@x.io" not in sent
    assert "Welcome, ${EMAIL}" in sent and "the log out button for ${EMAIL}" in sent
