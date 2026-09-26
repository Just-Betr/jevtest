import io

import pytest

from jevtest.adapters.testfile.loader import parse_step
from jevtest.cli.console import Printer, describe_action, describe_step, summary
from jevtest.domain.results import RunResult


@pytest.mark.parametrize(("raw", "written"), [
    ("back", "back"), ("clear_data", "clear_data"), ("hide_keyboard", "hide_keyboard"),
    ({"do": "Sign in"}, "do: Sign in"), ({"use": "Sign in"}, "use: Sign in"), ({"tap": "OK"}, "tap: OK"),
    ({"double_tap": "OK"}, "double_tap: OK"), ({"clear": "Email"}, "clear: Email"), ({"type": "a"}, "type: a"),
    ({"type": {"text": "a", "into": "Email"}}, "type: a into='Email'"), ({"scroll": "up"}, "scroll: up"),
    ({"swipe": "left"}, "swipe: left"), ({"swipe": "left", "target": "Row"}, "swipe: left target='Row'"),
    ({"scroll_to": "End", "direction": "down"}, "scroll_to: End direction='down'"), ({"key": "enter"}, "key: enter"),
    ({"wait": 2}, "wait: 2.0"), ({"background": 0.5}, "background: 0.5"),
    ({"rotate": "landscape"}, "rotate: landscape"),
    ({"location": [1, 2]}, "location: 1.0,2.0"), ({"open_url": "app://x"}, "open_url: app://x"),
    ({"dark_mode": True}, "dark_mode: on"), ({"network": False}, "network: off"), ({"grant": "p"}, "grant: p"),
    ({"screenshot": "s"}, "screenshot: s"),
])
def test_actions_are_shown_as_written(raw, written):
    assert describe_action(parse_step(raw).action) == written


def test_a_checks_only_step_has_no_action_to_show():
    assert describe_step(parse_step({"see": "x"})) == ""


def test_parallel_output_is_tagged_and_printed_whole():
    out = io.StringIO()
    Printer(parallel=True, out=out).block("android · Pixel", ["\n▶ T", "  PASS T (1.0s)"])
    assert out.getvalue() == "\n[android · Pixel] ▶ T\n[android · Pixel]   PASS T (1.0s)\n"


def test_summary_with_no_time(tmp_path):
    text = "\n".join(summary(RunResult(()), [], tmp_path))
    assert "0/0 passed in 0s" in text and "of run time" not in text
