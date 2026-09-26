"""Splitting a file's tests across several devices."""

from __future__ import annotations

from collections.abc import Sequence

from jevtest.domain.steps import Test


def shard(tests: Sequence[Test], devices: int) -> list[tuple[Test, ...]]:
    """Deal the tests out to `devices` devices, in order, one group per device in turn.

    A `fresh: false` test goes where the test before it went, since it carries on from where that one left the
    app. A device can end up with nothing to run.
    """
    groups: list[list[Test]] = []
    for t in tests:
        if t.fresh or not groups:
            groups.append([t])
        else:
            groups[-1].append(t)
    shards: list[list[Test]] = [[] for _ in range(devices)]
    for i, group in enumerate(groups):
        shards[i % devices] += group
    return [tuple(s) for s in shards]
