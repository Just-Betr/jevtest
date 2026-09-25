"""Fast tests: no device, no network. Jev is replaced by a scripted fake."""

from pathlib import Path

import pytest

from jevtest.brain import Brain, quoted_values
from jevtest.screen import Element, Screen
from jevtest.spec import SpecError, load, parse_step


class FakeJev:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.asked = []

    def ask(self, state, questions):
        self.asked.append((state, questions))
        return self.answers.pop(0)


def login_screen():
    return Screen(width=1000, height=2000, elements=[
        Element(kind="text_field", hint="Email", editable=True, bounds=(0, 100, 1000, 200)),
        Element(kind="password_field", hint="Password", editable=True, bounds=(0, 250, 1000, 350)),
        Element(kind="button", text="Sign in", clickable=True, bounds=(0, 400, 1000, 500)),
    ])


def test_parse_steps():
    assert parse_step("back").kind == "back"
    assert parse_step("Log in as admin").kind == "do"
    s = parse_step({"type": None, "text": "a", "into": "Email"})
    assert (s.kind, s.opts["into"]) == ("type", "Email")
    nested = parse_step({"type": {"text": "a", "into": "Email"}})
    assert (nested.value, nested.opts) == (None, {"text": "a", "into": "Email"})
    with pytest.raises(SpecError):
        parse_step({"type": {"txt": "a"}})
    with pytest.raises(SpecError):
        parse_step({"tap": "x", "bogus": 1})
    with pytest.raises(SpecError):
        parse_step({"tap": "x", "do": "y"})  # two actions


def test_action_then_checks():
    s = parse_step({"do": "Sign in", "expect": "Home shows", "see": ["Welcome", "Log out"]})
    assert s.kind == "do"
    assert s.checks == [("expect", "Home shows"), ("see", "Welcome"), ("see", "Log out")]
    only = parse_step({"expect": "Home shows"})
    assert only.kind is None and only.checks == [("expect", "Home shows")]
    with pytest.raises(SpecError):
        parse_step({"timeout": 3})


def write(tmp_path, body):
    (tmp_path / "a.apk").write_text("")
    f = tmp_path / "t.yaml"
    f.write_text("app: a.apk\ntests:\n" + body)
    return f


def test_use_links_tests(tmp_path):
    spec = load(write(tmp_path, """
  - name: Sign in
    steps: [{do: sign in}]
  - name: Counter
    steps: [{use: Sign in}, {do: tap add, see: "Taps: 1"}]
"""))
    assert spec.tests[1].steps[0].used is spec.tests[0]


def test_use_errors(tmp_path):
    with pytest.raises(SpecError, match="no test has that name"):
        load(write(tmp_path, "  - name: A\n    steps: [{use: Nope}]\n"))
    with pytest.raises(SpecError, match="loop"):
        load(write(tmp_path, "  - name: A\n    steps: [{use: B}]\n  - name: B\n    steps: [{use: A}]\n"))


def test_load_example():
    spec = load(Path(__file__).parent.parent / "examples" / "demo.yaml")
    assert set(spec.apps) == {"android", "ios"}
    assert spec.tests[0].steps[0].checks[0][0] == "expect"
    with pytest.raises(SpecError):
        spec.app_for(None)  # two platforms: must pick one


def test_quoted_values():
    assert quoted_values('email "a@b.c" and password “x y”') == ["a@b.c", "x y"]


def test_state_is_words_not_numbers():
    state = login_screen().to_state()
    assert state[0] == {"id": "e1", "type": "text_field", "hint": "Email", "position": "top-center"}


def test_next_action_type():
    jev = FakeJev({
        "action": {"choice": "type", "confidence": 0.9, "probabilities": {}},
        "target": {"choice": "e3"},
        "field": {"choice": "e2"},
        "value": {"choice": "v1"},
    })
    d = Brain(jev).next_action('Sign in with "a@b.c" and "pw"', login_screen(), [])
    assert (d.action, d.element.hint, d.text) == ("type", "Password", "pw")
    _, questions = jev.asked[0]
    assert "press_enter" not in questions["action"]["criteria"]  # keyboard is hidden
    assert set(questions["value"]["criteria"]) == {"v0", "v1"}


def test_next_action_without_values_cannot_type():
    jev = FakeJev({"action": {"choice": "tap", "confidence": 1}, "target": {"choice": "e3"},
                   "field": {"choice": "e1"}})
    d = Brain(jev).next_action("Press sign in", login_screen(), [])
    assert d.element.text == "Sign in"
    assert "type" not in jev.asked[0][1]["action"]["criteria"]


def test_locate_exact_match_skips_jev():
    jev = FakeJev()
    el = Brain(jev).locate("sign in", login_screen())
    assert el.text == "Sign in" and not jev.asked


def test_locate_uses_jev_and_not_on_screen():
    jev = FakeJev({"element": {"choice": "e3"}}, {"element": {"choice": "not_on_screen"}})
    b = Brain(jev)
    assert b.locate("the login button", login_screen()).text == "Sign in"
    assert b.locate("settings gear", login_screen()) is None
