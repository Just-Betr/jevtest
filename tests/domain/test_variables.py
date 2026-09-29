import pytest

from jevtest.domain.variables import fill, hide, names_in


def test_names_and_filling():
    assert names_in("Hi ${NAME}, your ${A_1} ${x}") == ["NAME", "A_1", "x"]
    assert fill("Hi ${NAME}!", {"NAME": "Ann"}) == "Hi Ann!"
    assert fill("no variables $NAME {NAME}", {}) == "no variables $NAME {NAME}"
    with pytest.raises(KeyError):
        fill("${MISSING}", {})


def test_hide_puts_names_back_longest_value_first():
    variables = {"USER": "ann", "EMAIL": "ann@x.io", "EMPTY": ""}
    assert hide("Welcome, ann@x.io (ann)", variables) == "Welcome, ${EMAIL} (${USER})"
    assert hide("nothing secret", variables) == "nothing secret"
    assert hide(fill("${EMAIL}", variables), variables) == "${EMAIL}"


def test_a_value_shown_in_another_encoding_is_still_hidden():
    assert hide("Welcome, Rene\u0301e", {"NAME": "Ren\u00e9e"}) == "Welcome, ${NAME}"
    assert hide("Welcome, Ren\u00e9e", {"NAME": "Rene\u0301e"}) == "Welcome, ${NAME}"
