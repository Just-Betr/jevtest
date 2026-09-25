import pytest

from jevtest.brain import ACTIONS, MAX_OPTIONS, Brain, Decision, quoted_values
from jevtest.jev import JevError
from jevtest.screen import Element, Screen

from .conftest import FakeJev, act, el, login_screen, pick, yes

# --- screen --------------------------------------------------------------------------

def test_ids_and_state_use_words_not_numbers():
    s = login_screen()
    assert [e.id for e in s.elements] == ["e1", "e2", "e3"]
    assert s.to_state()[0] == {"id": "e1", "type": "text_field", "hint": "Email", "position": "top-center"}
    assert s.by_id("e3").text == "Sign in"
    assert [e.hint for e in s.editable] == ["Email", "Password"]
    assert s.texts() == ["Email", "Password", "Sign in"]


def test_state_includes_only_meaningful_flags():
    s = Screen(width=300, height=300, elements=[
        el("switch", "Wifi", checked=False, bounds=(0, 0, 10, 10)),
        el("button", "Go", enabled=False, focused=True, selected=True, scrollable=True,
           resource_id="go", bounds=(290, 290, 300, 300)),
    ])
    first, second = s.to_state()
    assert first == {"id": "e1", "type": "switch", "text": "Wifi", "position": "top-left", "checked": False}
    assert second == {"id": "e2", "type": "button", "text": "Go", "resource_id": "go", "position": "bottom-right",
                      "enabled": False, "focused": True, "selected": True, "scrollable": True}


@pytest.mark.parametrize("bounds,region", [
    ((0, 0, 10, 10), "top-left"), ((140, 140, 160, 160), "middle-center"), ((290, 290, 300, 300), "bottom-right")])
def test_regions(bounds, region):
    s = Screen(width=300, height=300, elements=[el(bounds=bounds)])
    assert s.region(s.elements[0]) == region


def test_element_label_and_center():
    assert Element("button", "OK", bounds=(0, 0, 10, 20)).center == (5, 10)
    assert Element("button", "OK").label() == "button 'OK'"
    assert Element("text_field", hint="Email").label() == "text_field 'Email'"
    assert Element("image", resource_id="logo").label() == "image 'logo'"
    assert Element("image").label() == "image '(no label)'"


# --- quoted values -----------------------------------------------------------------

@pytest.mark.parametrize("goal,values", [
    ('email "a@b.c" and password “x y”', ["a@b.c", "x y"]),
    ("no quotes here", []),
    ('empty "" is ignored', []),
])
def test_quoted_values(goal, values):
    assert quoted_values(goal) == values


# --- next_action -----------------------------------------------------------------------

def test_next_action_type_picks_field_and_value():
    jev = FakeJev(act("type", field="e2", value="v1"))
    d = Brain(jev).next_action('Sign in with "a@b.c" and "pw"', login_screen(), [])
    assert (d.action, d.element.hint, d.text) == ("type", "Password", "pw")
    assert d.describe() == "type \"pw\" into password_field 'Password'"
    state, questions = jev.asked[0]
    assert state["actions_taken"] == ["(none yet)"]
    assert set(questions) == {"action", "target", "field", "value"}
    assert questions["value"]["criteria"] == {"v0": '"a@b.c"', "v1": '"pw"'}


def test_next_action_offers_only_possible_actions():
    jev = FakeJev(act("tap", target="e3"))
    d = Brain(jev).next_action("Press sign in", login_screen(), ["tap x"])
    assert d.element.text == "Sign in" and d.describe() == "tap button 'Sign in'"
    options = jev.asked[0][1]["action"]["criteria"]
    assert "type" not in options                     # no quoted values
    assert "clear" in options                        # there are fields
    assert "press_enter" not in options and "hide_keyboard" not in options  # keyboard hidden
    assert jev.asked[0][0]["actions_taken"] == ["tap x"]


def test_next_action_with_keyboard_and_no_fields():
    s = Screen(width=10, height=10, elements=[el("button", "Go", bounds=(0, 0, 10, 10))], keyboard_visible=True)
    jev = FakeJev(act("press_enter"))
    d = Brain(jev).next_action('Type "x"', s, [])
    options = jev.asked[0][1]["action"]["criteria"]
    assert {"press_enter", "hide_keyboard"} <= set(options)
    assert "type" not in options and "clear" not in options and "field" not in jev.asked[0][1]
    assert d.element is None and d.describe() == "press enter"


def test_next_action_on_empty_screen_offers_no_touch():
    jev = FakeJev({"action": {"type": "choice", "choice": "wait", "confidence": 0.5, "probabilities": {}}})
    d = Brain(jev).next_action("Anything", Screen(width=10, height=10), [])
    options = jev.asked[0][1]["action"]["criteria"]
    assert not {"tap", "double_tap", "long_press", "swipe_left_on", "swipe_right_on"} & set(options)
    assert "target" not in jev.asked[0][1]
    assert d.action == "wait"


@pytest.mark.parametrize("action,expected", [
    ("clear", "clear text_field 'Email'"), ("swipe_left_on", "swipe_left text_field 'Email'"),
    ("scroll_down", "scroll down"), ("done", "done")])
def test_describe(action, expected):
    field = login_screen().elements[0]
    element = field if action in ("clear", "swipe_left_on") else None
    assert Decision(action, element=element).describe() == expected


def test_options_are_capped_for_jev_limit():
    s = Screen(width=10, height=10, elements=[el("button", f"b{i}", bounds=(0, 0, 10, 10)) for i in range(300)])
    jev = FakeJev(act("tap", target="e1"))
    Brain(jev).next_action("tap", s, [])
    assert len(jev.asked[0][1]["target"]["criteria"]) == MAX_OPTIONS


def test_option_descriptions_mention_state():
    s = Screen(width=10, height=10, elements=[
        el("switch", "Wifi", checked=True, bounds=(0, 0, 10, 10)),
        el("switch", "BT", checked=False, enabled=False, bounds=(0, 0, 10, 10))])
    jev = FakeJev(act("tap"))
    Brain(jev).next_action("x", s, [])
    target = jev.asked[0][1]["target"]["criteria"]
    assert "(currently on)" in target["e1"]
    assert "(currently off)" in target["e2"] and "(disabled)" in target["e2"]


def test_every_action_has_a_description():
    assert all(ACTIONS.values())


# --- locate / check --------------------------------------------------------------------

def test_locate_exact_unique_match_skips_jev():
    jev = FakeJev()
    b = Brain(jev)
    assert b.locate("sign in", login_screen()).text == "Sign in"
    assert b.locate("EMAIL", login_screen()).hint == "Email"
    assert not jev.asked


def test_locate_by_resource_id():
    s = Screen(width=10, height=10, elements=[el("image", resource_id="logo", bounds=(0, 0, 10, 10))])
    assert Brain(FakeJev()).locate("logo", s).resource_id == "logo"


def test_locate_ambiguous_exact_match_asks_jev():
    s = Screen(width=10, height=10, elements=[el("text", "Sign in", bounds=(0, 0, 10, 10)),
                                              el("button", "Sign in", bounds=(0, 0, 10, 10))])
    jev = FakeJev(pick("e2"))
    assert Brain(jev).locate("Sign in", s).kind == "button"
    assert "not_on_screen" in jev.asked[0][1]["element"]["criteria"]


def test_locate_not_on_screen():
    assert Brain(FakeJev(pick("not_on_screen"))).locate("settings gear", login_screen()) is None


def test_locate_with_no_candidates():
    assert Brain(FakeJev()).locate("x", login_screen(), candidates=[]) is None


def test_locate_limits_candidates():
    jev = FakeJev(pick("e1"))
    s = login_screen()
    Brain(jev).locate("the first box", s, candidates=s.editable)
    assert set(jev.asked[0][1]["element"]["criteria"]) == {"e1", "e2", "not_on_screen"}


def test_check_returns_probability():
    jev = FakeJev(yes(0.83))
    assert Brain(jev).check("Home shows", login_screen()) == 0.83
    assert jev.asked[0][1]["check"]["type"] == "noul"


@pytest.mark.parametrize("answers", [
    {"action": {"choice": "fly"}},
    {"action": {"choice": "tap"}, "target": {"choice": "e99"}},
    {"action": {"choice": "clear"}, "target": {"choice": "e1"}, "field": {"choice": "e3"}},
])
def test_answers_outside_the_options_are_errors(answers):
    with pytest.raises(JevError, match="not one of the options"):
        Brain(FakeJev(answers)).next_action("x", login_screen(), [])


def test_locate_answer_outside_options():
    with pytest.raises(JevError, match="not one of the options"):
        Brain(FakeJev(pick("e42"))).locate("something", login_screen())


@pytest.mark.parametrize("value", [None, "yes", 1.5, -0.1])
def test_check_rejects_bad_probabilities(value):
    with pytest.raises(JevError, match="yes/no"):
        Brain(FakeJev({"check": {"type": "noul", "noul": value}})).check("x", login_screen())


def test_quoted_values_are_capped():
    goal = " ".join(f'"v{i}"' for i in range(300))
    jev = FakeJev(act("done"))
    Brain(jev).next_action(goal, login_screen(), [])
    assert len(jev.asked[0][1]["value"]["criteria"]) == MAX_OPTIONS
