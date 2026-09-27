import json
from pathlib import Path

from jevtest.adapters.devices.ios_screen import parse_tree

FIX = Path(__file__).parent / "fixtures"


# --- parsing real agent trees -------------------------------------------------------------------


def test_parse_login_tree():
    s = parse_tree(json.loads((FIX / "ios_login.json").read_text()))
    assert (s.width, s.height) == (402, 874)
    # Flutter reports its obscured password field as a plain text field on iOS.
    assert [(e.kind, e.text) for e in s.elements] == [
        ("text", "Sign in"),
        ("text_field", "Email"),
        ("text_field", "Password"),
        ("button", "Sign in"),
    ]


def test_parse_webview_tree():
    s = parse_tree(json.loads((FIX / "ios_webview.json").read_text()))
    texts = [e.text for e in s.elements]
    assert {"Web Greeter", "Say hello", "Nobody greeted yet", "I agree to the terms", "Show more"} <= set(texts)
    assert not any("scroll bar" in t for t in texts)
    assert next(e for e in s.elements if e.kind == "switch").checked is False
    # WebKit marks every element "focused"; only a text field's focus means anything
    assert [e.kind for e in s.elements if e.focused] == ["text_field"]
    assert next(e for e in s.elements if e.kind == "text_field").editable


def test_parse_rules():
    data = {
        "width": 100,
        "height": 200,
        "keyboard": True,
        "keyboard_top": 150,
        "elements": [
            {"type": "application", "label": "App", "x": 0, "y": 0, "w": 100, "h": 200},
            {"type": "other", "label": "", "x": 0, "y": 0, "w": 100, "h": 200},
            {"type": "other", "label": "Card", "x": 0, "y": 0, "w": 50, "h": 50},
            {"type": "text", "label": "Hi", "value": "Hi", "x": 0, "y": 0, "w": 50, "h": 50},
            {"type": "text", "label": "Hi", "value": "Hi", "x": 0, "y": 0, "w": 50, "h": 50},
            {"type": "text", "label": "Tiny", "x": 0, "y": 0, "w": 1, "h": 1},
            {"type": "text", "label": "Vertical scroll bar, 2 pages", "x": 0, "y": 0, "w": 5, "h": 50},
            {
                "type": "text_field",
                "label": "Email",
                "value": "a@b.c",
                "placeholder": "Email",
                "x": 0,
                "y": 0,
                "w": 50,
                "h": 20,
                "focused": True,
            },
            {"type": "text_field", "label": "", "value": "typed", "x": 0, "y": 30, "w": 50, "h": 20},
            {"type": "password_field", "label": "Password", "value": "•••", "x": 0, "y": 60, "w": 50, "h": 20},
            {"type": "switch", "label": "Wifi", "value": "1", "x": 0, "y": 90, "w": 50, "h": 20},
            {
                "type": "button",
                "label": "Go",
                "identifier": "go",
                "x": -10,
                "y": 190,
                "w": 50,
                "h": 50,
                "enabled": False,
            },
            {"type": "list", "label": "", "identifier": "feed", "x": 0, "y": 0, "w": 100, "h": 100},
            {"type": "dropdown", "label": "Country", "value": "Canada", "x": 0, "y": 120, "w": 50, "h": 20},
            {"type": "other", "label": "Size", "value": "Large", "x": 0, "y": 140, "w": 50, "h": 20},  # web <select>
            {"type": "text", "label": "Same", "value": " Same", "x": 0, "y": 160, "w": 50, "h": 20},
        ],
    }
    s = parse_tree(data)
    assert [(e.kind, e.text) for e in s.elements] == [
        ("text", "Card"),
        ("text", "Hi"),
        ("text_field", "Email: a@b.c"),
        ("text_field", "typed"),
        ("password_field", "Password"),
        ("switch", "Wifi"),
        ("button", "Go"),
        ("list", ""),
        ("dropdown", "Country: Canada"),
        ("text", "Size: Large"),
        ("text", "Same"),
    ]  # values that add information
    go = s.elements[6]
    assert go.bounds == (0, 190, 40, 200) and go.enabled is False and go.clickable and go.resource_id == "go"
    assert s.elements[2].focused and s.elements[5].checked is True
    assert [e.value for e in s.elements if e.editable] == ["a@b.c", "typed", "•••"]
    assert s.keyboard_visible and s.keyboard_top == 150
