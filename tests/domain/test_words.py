import pytest

from jevtest.domain.words import ordinal, plural


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
