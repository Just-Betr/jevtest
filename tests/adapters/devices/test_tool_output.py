import plistlib

import pytest

from jevtest.adapters.devices.tool_output import (
    as_list,
    as_object,
    as_text,
    dig,
    parse_json,
    parse_plist,
    text_at,
    texts,
)
from jevtest.domain.failures import DeviceError


def test_the_expected_shapes_pass_through():
    assert as_object({"a": 1}, "x") == {"a": 1}
    assert as_list([1], "x") == [1]
    assert as_text("s", "x") == "s"
    assert texts(["a", "b"], "x") == ["a", "b"]
    assert parse_json('{"a": [1]}', "x") == {"a": [1]}
    assert parse_plist(plistlib.dumps({"a": "b"}), "x") == {"a": "b"}


@pytest.mark.parametrize(
    ("read", "value"),
    [
        (as_object, [1]),
        (as_list, {"a": 1}),
        (as_text, 5),
        (texts, ["a", 1]),
        (texts, "a"),
    ],
)
def test_an_unexpected_shape_says_what_was_expected(read, value):
    with pytest.raises(DeviceError, match="devicectl's list is not what jevtest expects"):
        read(value, "devicectl's list")


def test_unexpected_tool_output_is_shown_short():
    with pytest.raises(DeviceError) as e:
        as_object("x" * 1000, "a value")
    assert len(str(e.value)) < 260


def test_dig_and_text_at_treat_a_missing_path_as_absent():
    data = {"a": {"b": "c", "n": 1}}
    assert dig(data, "a", "b") == "c"
    assert dig(data, "a", "b", "deeper") is None
    assert dig(data, "missing", "b") is None
    assert text_at(data, "a", "b") == "c"
    assert text_at(data, "a", "n") == ""


def test_bad_json_and_plists_are_device_errors():
    with pytest.raises(DeviceError, match="the agent's reply is not what jevtest expects"):
        parse_json("<html>", "the agent's reply")
    with pytest.raises(DeviceError, match="Info.plist can't be read"):
        parse_plist(b"not a plist", "Info.plist")
    with pytest.raises(DeviceError, match="Info.plist is not what jevtest expects"):
        parse_plist(plistlib.dumps(["a"]), "Info.plist")
