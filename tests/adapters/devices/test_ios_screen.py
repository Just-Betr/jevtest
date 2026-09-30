import json
from pathlib import Path

from jevtest.adapters.devices.ios_screen import AgentElement, AgentTree, parse_tree

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


def test_a_swiftui_toggle_is_its_labelled_switch_where_its_knob_is():
    """XCUITest reports a SwiftUI Toggle as a labelled switch across its row holding the unlabelled switch a
    finger turns (measured on iOS 26.5): it is kept once, as UIKit reports a switch beside its label."""
    row: AgentElement = {"type": "switch", "label": "Newsletter", "value": "1", "x": 16, "y": 168, "w": 370, "h": 52}
    text: AgentElement = {"type": "text", "label": "Newsletter", "x": 32, "y": 183, "w": 82, "h": 21}
    knob: AgentElement = {"type": "switch", "label": "", "value": "1", "x": 309, "y": 180, "w": 63, "h": 28}
    uikit: AgentElement = {"type": "switch", "label": "Wifi", "value": "0", "x": 309, "y": 300, "w": 51, "h": 31}
    lone: AgentElement = {
        "type": "switch",
        "label": "",
        "value": "0",
        "x": 309,
        "y": 400,
        "w": 51,
        "h": 31,
    }  # in no row
    s = parse_tree({"width": 402, "height": 874, "elements": [row, text, knob, uikit, lone]})
    assert [(e.kind, e.text, e.bounds, e.checked) for e in s.elements] == [
        ("switch", "Newsletter", (309, 180, 372, 208), True),
        ("text", "Newsletter", (32, 183, 114, 204), None),
        ("switch", "Wifi", (309, 300, 360, 331), False),
        ("switch", "", (309, 400, 360, 431), False),
    ]


def test_a_slider_says_where_its_thumb_is():
    """Only the agent knows: XCTest's position is exact even when the app says something else as the value."""
    slider: AgentElement = {
        "type": "slider",
        "label": "Bass",
        "value": "Soft",
        "position": 0.2,
        "x": 32,
        "y": 400,
        "w": 338,
        "h": 31,
    }
    button: AgentElement = {"type": "button", "label": "Go", "x": 0, "y": 0, "w": 50, "h": 20}
    s = parse_tree({"width": 402, "height": 874, "elements": [slider, button]})
    assert [(e.text, e.position) for e in s.elements] == [("Bass: Soft", 0.2), ("Go", None)]


def test_scroll_views_with_no_label_are_kept_only_as_where_content_scrolls():
    """A SwiftUI horizontal ScrollView is reported with no label: not an element, but a carousel to scroll along."""
    carousel: AgentElement = {"type": "scroll_view", "label": "", "x": 32, "y": 739, "w": 338, "h": 34}
    table: AgentElement = {"type": "list", "label": "", "x": 0, "y": 0, "w": 402, "h": 874}
    offscreen: AgentElement = {"type": "scroll_view", "label": "", "x": 0, "y": 900, "w": 402, "h": 100}
    chip: AgentElement = {"type": "button", "label": "Tag 1", "x": 32, "y": 739, "w": 63, "h": 34}
    s = parse_tree({"width": 402, "height": 874, "elements": [table, carousel, carousel, offscreen, chip]})
    assert [e.text for e in s.elements] == ["Tag 1"]
    assert s.scrollers == ((0, 0, 402, 874), (32, 739, 370, 773))


def test_the_apps_bars_are_kept_as_where_the_page_is_not():
    nav: AgentElement = {"type": "navigation_bar", "label": "", "x": 0, "y": 62, "w": 402, "h": 114}
    tabs: AgentElement = {"type": "tab_bar", "label": "Tab Bar", "x": 0, "y": 791, "w": 402, "h": 83}
    s = parse_tree({"width": 402, "height": 874, "elements": [nav, tabs]})
    assert s.bars == ((0, 62, 402, 176), (0, 791, 402, 874))
    assert s.page == (0, 176, 402, 791)


def test_a_picker_wheel_takes_a_value():
    """XCUITest reports only a wheel's selected value: `type:` turns it (`Device.choose`), so it takes a value."""
    wheel: AgentElement = {"type": "picker", "label": "", "value": "Red", "x": 25, "y": 213, "w": 352, "h": 291}
    [e] = parse_tree({"width": 402, "height": 874, "elements": [wheel]}).elements
    assert (e.kind, e.text, e.editable, e.value) == ("picker", "Red", True, "Red")


def test_each_line_of_a_label_or_value_can_be_named_on_its_own():
    tab: AgentElement = {"type": "button", "label": "Form\nTab 2 of 3", "x": 0, "y": 0, "w": 100, "h": 50}
    card: AgentElement = {
        "type": "other",
        "label": "Order",
        "value": "Paid\nShipped",
        "x": 0,
        "y": 60,
        "w": 100,
        "h": 50,
    }
    a, b = parse_tree({"width": 402, "height": 874, "elements": [tab, card]}).elements
    assert (a.text, a.parts) == ("Form Tab 2 of 3", ("Form", "Tab 2 of 3"))
    assert (b.text, b.parts) == ("Order: Paid Shipped", ("Order", "Paid Shipped", "Paid", "Shipped"))


def test_what_ios_says_is_adjustable_is_kept_as_moved_by_dragging_along_it():
    """A Flutter slider is `.other` to XCUITest: only its adjustable trait says a drag moves it."""
    rating: AgentElement = {
        "type": "other",
        "label": "Rating",
        "value": "50%",
        "adjustable": True,
        "x": 16,
        "y": 446,
        "w": 370,
        "h": 48,
    }
    label: AgentElement = {"type": "text", "label": "Rating: 3", "x": 16, "y": 494, "w": 370, "h": 20}
    a, b = parse_tree({"width": 402, "height": 874, "elements": [rating, label]}).elements
    assert (a.kind, a.text, a.adjustable, a.position) == ("text", "Rating: 50%", True, None)
    assert b.adjustable is False


def test_parse_rules():
    data: AgentTree = {
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
    # a text that joins a label and a value keeps each, so either can be matched exactly on its own
    parts = {e.text: e.parts for e in s.elements if e.parts}
    assert parts == {
        "Email: a@b.c": ("Email", "a@b.c"),
        "Country: Canada": ("Country", "Canada"),
        "Size: Large": ("Size", "Large"),
    }
    assert s.elements[3].parts == ()  # a value with no label is the whole text
