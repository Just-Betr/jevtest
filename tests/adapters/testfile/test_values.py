import datetime
import math

import pytest

from jevtest.adapters.testfile.values import choice, kind, model, number, on_off, setting, text
from jevtest.domain.failures import TestFileError
from jevtest.domain.kinds import Direction


@pytest.mark.parametrize(
    ("value", "said"),
    [
        (None, "nothing"),
        (True, "true/false"),
        (2, "a number"),
        (2.5, "a number"),
        ("a", "text"),
        ([], "a list"),
        ({}, "a mapping"),
        (datetime.date(2026, 1, 1), "date"),
    ],
)
def test_kind_says_what_a_value_is(value, said):
    assert kind(value) == said


def test_values_of_the_right_kind_pass_through_exactly():
    assert number(2, "x") == 2.0 and text("Save", "x") == "Save" and on_off(value=True, what="x") is True
    assert choice("down", Direction, "x") is Direction.DOWN
    assert setting("max_actions", 4) == 4 and setting("confidence", 0.8) == 0.8
    assert model("jev-2.0.0") == "jev-2.0.0"


@pytest.mark.parametrize(
    ("read", "message"),
    [
        (lambda: number("2", "wait"), r"wait must be a number, got '2' \(remove the quotes\)"),
        (lambda: number(True, "wait"), "wait must be a number, got True$"),
        (lambda: number(math.inf, "wait"), "wait must be a finite number"),
        (lambda: number(-1, "wait"), "wait must be at least 0, got -1"),
        (lambda: text(42, "tap"), r"tap needs text, got a number \(to use 42 as text, put it in quotes\)"),
        (lambda: text(" a", "tap"), "without leading or trailing spaces"),
        (lambda: text("a\x00b", "app"), "app has a null character in it"),
        (lambda: choice("DOWN", Direction, "scroll"), "scroll must be one of up, down, left, right; got 'DOWN'"),
        (lambda: on_off("yes", "dark_mode"), r"dark_mode must be on or off \(true or false\), got 'yes'"),
        (lambda: setting("max_actions", 2.5), "`max_actions` must be a whole number from 1 to 50, got 2.5"),
        (lambda: setting("timeout", 0), "`timeout` must be from 1 to 300, got 0"),
        (lambda: model("gpt-5"), "`model` must be a pinned Jev version"),
        (lambda: model("jev-1.13.0-beta"), "`model` must be a pinned Jev version"),
        (lambda: model("jev-1.13"), "`model` must be a pinned Jev version"),  # the old name: not a version
    ],
)
def test_the_wrong_value_is_an_error_that_says_what_to_write(read, message):
    with pytest.raises(TestFileError, match=message):
        read()
