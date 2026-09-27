import pytest

from jevtest.domain.settings import DEFAULTS, LIMITS, STEP_SETTINGS, WHOLE, Settings


def test_the_defaults_are_within_their_limits():
    for name, (low, high) in LIMITS.items():
        assert low <= getattr(DEFAULTS, name) <= high


def test_every_numeric_setting_can_be_changed_on_a_step():
    assert set(LIMITS) == STEP_SETTINGS and WHOLE <= STEP_SETTINGS


def test_changed_keeps_the_rest_and_counts_whole():
    changed = DEFAULTS.changed({"timeout": 20, "max_actions": 4.0})
    assert changed == Settings(timeout=20, max_actions=4)
    assert isinstance(changed.max_actions, int)
    assert DEFAULTS.changed({}) == DEFAULTS


def test_changed_rejects_a_name_that_isnt_a_numeric_setting():
    with pytest.raises(KeyError, match="Not numeric settings: model"):
        DEFAULTS.changed({"model": 1})
