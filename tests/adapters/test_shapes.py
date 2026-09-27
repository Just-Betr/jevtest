from jevtest.adapters.shapes import is_json_object, is_list, is_mapping, objects_by_key


def test_shapes():
    assert is_mapping({1: "a"}) and not is_mapping([1])
    assert is_json_object({"a": 1}) and not is_json_object("a")
    assert is_list([1]) and not is_list({"a": 1})


def test_objects_by_key():
    assert objects_by_key({"q": {"a": 1}}) == {"q": {"a": 1}}
    assert objects_by_key({"q": {"a": 1}, "r": 2}) is None
    assert objects_by_key(["q"]) is None
