import pytest

from jevtest.domain.decisions import (
    ClearField,
    CloseKeyboard,
    Finished,
    GoBack,
    Impossible,
    PressEnter,
    ScrollPage,
    SwipeElement,
    TouchElement,
    TypeInto,
    WaitForScreen,
)
from jevtest.domain.kinds import Direction, Gesture
from tests.conftest import el

FIELD = el("text_field", hint="Email")


@pytest.mark.parametrize(
    ("move", "described"),
    [
        (Finished(), "done"),
        (Impossible(), "impossible"),
        (WaitForScreen(), "wait"),
        (GoBack(), "back"),
        (PressEnter(), "press enter"),
        (CloseKeyboard(), "hide keyboard"),
        (ScrollPage(Direction.DOWN), "scroll down"),
        (TouchElement(Gesture.DOUBLE_TAP, FIELD), "double_tap text_field 'Email'"),
        (SwipeElement(Direction.LEFT, FIELD), "swipe_left text_field 'Email'"),
        (TypeInto(FIELD, "${EMAIL}"), "type \"${EMAIL}\" into text_field 'Email'"),
        (ClearField(FIELD), "clear text_field 'Email'"),
    ],
)
def test_moves_describe_themselves_as_the_model_is_shown_them(move, described):
    """These strings are part of every recorded decision: they must not change."""
    assert move.describe() == described
