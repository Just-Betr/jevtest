import pytest

from jevtest.application.planning import shard
from jevtest.domain.steps import Test


def make_tests(*names):
    return [Test(name, not name.endswith("+"), ()) for name in names]


@pytest.mark.parametrize(("names", "devices", "shards"), [
    (["A", "B", "C"], 2, [["A", "C"], ["B"]]),
    (["A", "B+", "C", "D+", "E+"], 2, [["A", "B+"], ["C", "D+", "E+"]]),  # a chain stays on one device
    (["A+", "B"], 3, [["A+"], ["B"], []]),  # the first test always starts a group
])
def test_shard(names, devices, shards):
    assert [[t.name for t in s] for s in shard(make_tests(*names), devices)] == shards
