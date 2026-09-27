"""Properties that hold for any input, checked with Hypothesis.

Everything that reads outside input (test files, .env files, Jev's replies, lockfiles, the agents' screen dumps)
either returns its result or raises jevtest's own error with a message. It never crashes with a Python error.
"""

import contextlib
import json
import string
import xml.etree.ElementTree as ET
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from jevtest.adapters.devices.android_screen import parse_hierarchy
from jevtest.adapters.devices.ios_screen import AgentTree, parse_tree
from jevtest.adapters.jev.lockfile import _entries, request_key
from jevtest.adapters.jev.wire import answers_from_wire, questions_to_wire
from jevtest.adapters.testfile.env import read_env
from jevtest.adapters.testfile.loader import load
from jevtest.adapters.testfile.steps import ACTIONS, CHECKS, OPTIONS, parse_step
from jevtest.domain.failures import ModelError, TestFileError
from jevtest.domain.model import Choice, YesNo

SETTINGS = settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])

scalars = st.none() | st.booleans() | st.integers() | st.floats(allow_nan=True) | st.text(max_size=12)
yaml_values = st.recursive(
    scalars,
    lambda inner: st.lists(inner, max_size=4) | st.dictionaries(st.text(max_size=8) | st.integers(), inner, max_size=4),
    max_leaves=12,
)
step_keys = st.sampled_from(sorted({*ACTIONS, *CHECKS, *OPTIONS, "text", "nonsense"}))
steps = st.dictionaries(step_keys, yaml_values, min_size=1, max_size=4) | st.sampled_from(sorted(ACTIONS)) | yaml_values


@SETTINGS
@given(steps)
def test_any_step_is_a_step_or_a_clear_error(raw):
    with contextlib.suppress(TestFileError):  # any other exception fails the test
        step = parse_step(raw)
        assert step.action is not None or step.checks


@SETTINGS
@given(st.dictionaries(st.sampled_from(["app", "device", "settings", "include", "tests", "x"]), yaml_values))
def test_any_test_file_is_a_suite_or_a_clear_error(tmp_path_factory, document):
    folder = tmp_path_factory.mktemp("file")
    (folder / "a.apk").write_text("")
    path = folder / "t.yaml"
    path.write_text(json.dumps(document, default=str))  # JSON is YAML
    with contextlib.suppress(TestFileError):
        load(path, {})


@SETTINGS
@given(st.text(alphabet=string.printable, max_size=200))
def test_any_env_file_is_values_or_a_clear_error(tmp_path_factory, content):
    folder: Path = tmp_path_factory.mktemp("env")
    (folder / ".env").write_text(content)
    with contextlib.suppress(TestFileError):
        values = read_env(folder, environ={})
        assert all(isinstance(k, str) and isinstance(v, str) for k, v in values.items())


json_values = st.recursive(
    st.none() | st.booleans() | st.integers() | st.floats(allow_nan=False) | st.text(max_size=8),
    lambda inner: st.lists(inner, max_size=3) | st.dictionaries(st.text(max_size=8), inner, max_size=3),
    max_leaves=10,
)
QUESTIONS = {"action": Choice({"q": "?"}, {"tap": "Tap", "back": "Back"}), "check": YesNo({"q": "?"})}


@SETTINGS
@given(
    st.dictionaries(st.sampled_from(["action", "check", "other"]), st.dictionaries(st.text(max_size=8), json_values))
)
def test_any_jev_reply_is_answers_or_a_clear_error(raw):
    """Answers arrive as objects (the client and the lockfile check that); what's inside can be anything."""
    with contextlib.suppress(ModelError):
        answers_from_wire(raw, QUESTIONS)


@SETTINGS
@given(json_values)
def test_any_lockfile_is_decisions_or_a_clear_error(data):
    with contextlib.suppress(ModelError):
        _entries(data, "t.lock.json")


@SETTINGS
@given(st.dictionaries(st.text(max_size=5), st.text(max_size=5), max_size=5))
def test_the_lockfile_key_ignores_the_order_questions_are_written_in(state):
    wire = questions_to_wire(QUESTIONS)
    reordered = dict(reversed(list(wire.items())))
    assert request_key("m", state, wire) == request_key("m", dict(reversed(list(state.items()))), reordered)


attributes = st.dictionaries(
    st.sampled_from(
        [
            "class",
            "text",
            "content-desc",
            "bounds",
            "package",
            "resource-id",
            "clickable",
            "checkable",
            "password",
            "checked",
            "enabled",
            "focused",
            "scrollable",
            "hint",
            "long-clickable",
            "selected",
        ]
    ),
    st.text(max_size=20) | st.sampled_from(["true", "false", "[0,0][100,50]", "[-5,10][2000,3000]"]),
)


@SETTINGS
@given(st.lists(attributes, max_size=8))
def test_any_android_dump_parses_to_elements_on_screen(nodes):
    root = ET.Element("hierarchy")
    for attrs in nodes:
        ET.SubElement(root, "node", attrs)
    for el in parse_hierarchy(root, 1080, 2400):
        x1, y1, x2, y2 = el.bounds
        assert 0 <= x1 < x2 <= 1080 and 0 <= y1 < y2 <= 2400


ios_elements = st.fixed_dictionaries(
    {
        "type": st.sampled_from(["button", "text", "other", "text_field", "switch", "application", "list"]),
        "x": st.floats(-100, 500),
        "y": st.floats(-100, 1000),
        "w": st.floats(0, 500),
        "h": st.floats(0, 500),
    },
    optional={
        "label": st.text(max_size=20),
        "value": st.none() | st.text(max_size=10),
        "placeholder": st.text(max_size=8),
        "identifier": st.text(max_size=8),
        "enabled": st.booleans(),
        "focused": st.booleans(),
    },
)


@SETTINGS
@given(st.lists(ios_elements, max_size=10))
def test_any_ios_tree_parses_to_distinct_elements_on_screen(elements):
    tree: AgentTree = {"width": 402, "height": 874, "elements": elements}
    screen = parse_tree(tree)
    keys = [(e.kind, e.text, e.bounds) for e in screen.elements]
    assert len(keys) == len(set(keys))
    assert all(0 <= e.bounds[0] < e.bounds[2] <= 402 and 0 <= e.bounds[1] < e.bounds[3] <= 874 for e in screen.elements)
