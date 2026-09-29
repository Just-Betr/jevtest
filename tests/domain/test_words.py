import pytest

from jevtest.domain.words import number_text, ordinal, plural


@pytest.mark.parametrize(
    ("n", "noun", "many", "said"),
    [(1, "file", None, "1 file"), (2, "file", None, "2 files"), (0, "file", None, "0 files"),
     (1, "entry", "entries", "1 entry"), (3, "entry", "entries", "3 entries")],
)  # fmt: skip
def test_plural(n, noun, many, said):
    assert plural(n, noun, many) == said


@pytest.mark.parametrize(("n", "said"), [(1, "1st"), (2, "2nd"), (3, "3rd"), (4, "4th"), (11, "11th"), (12, "12th"),
                                         (13, "13th"), (21, "21st"), (22, "22nd"), (111, "111th"), (101, "101st")])  # fmt: skip
def test_ordinal(n, said):
    assert ordinal(n) == said


@pytest.mark.parametrize(
    ("value", "said"),
    [(2.0, "2"), (2, "2"), (0.25, "0.25"), (151.2093, "151.2093"), (-33.8688, "-33.8688"), (90.0000001, "90.0000001"),
     (1e-7, "1e-07"), (300.0, "300")],
)  # fmt: skip
def test_a_number_is_shown_as_written_never_rounded(value, said):
    assert number_text(value) == said
