import pytest

from jevtest.domain.variables import fill, names_in


def test_names_and_filling():
    assert names_in("Hi ${NAME}, your ${A_1} ${x}") == ["NAME", "A_1", "x"]
    assert fill("Hi ${NAME}!", {"NAME": "Ann"}) == "Hi Ann!"
    assert fill("no variables $NAME {NAME}", {}) == "no variables $NAME {NAME}"
    with pytest.raises(KeyError):
        fill("${MISSING}", {})
