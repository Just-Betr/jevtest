import xml.etree.ElementTree as ET
from pathlib import Path

from jevtest.adapters.devices.android_screen import has_empty_webview, parse_hierarchy, parse_screen, typing_ready

FIX = Path(__file__).parent / "fixtures"
LOGIN = (FIX / "android_login.xml").read_text()
WEB = (FIX / "android_webview.xml").read_text()
EMPTY_WEB = (
    '<hierarchy rotation="0"><node class="android.widget.FrameLayout" bounds="[0,0][100,100]">'
    '<node class="android.webkit.WebView" bounds="[0,0][100,100]"/></node></hierarchy>'
)


def test_parse_login_screen():
    els = parse_hierarchy(ET.fromstring(LOGIN), 1080, 2424)
    assert [(e.kind, e.text, e.hint) for e in els] == [
        ("text", "Sign in", ""),
        ("text_field", "", "Email"),
        ("password_field", "", "Password"),
        ("button", "Sign in", ""),
    ]
    email = els[1]
    assert email.editable and email.enabled and email.bounds == (63, 352, 1017, 499)
    assert email.value == ""


def test_parse_webview_content():
    els = parse_hierarchy(ET.fromstring(WEB), 1080, 2424)
    kinds = {(e.kind, e.text or e.hint) for e in els}
    assert {
        ("text", "Web Greeter"),
        ("text_field", "Your name"),
        ("button", "Say hello"),
        ("checkbox", "I agree to the terms"),
    } <= kinds
    assert next(e for e in els if e.kind == "checkbox").checked is False


def test_parse_rules():
    xml = """<hierarchy rotation="0">
      <node class="android.widget.TextView" package="com.android.systemui" text="12:00" bounds="[0,0][100,50]"/>
      <node class="android.widget.TextView" text="Off screen" bounds="[0,3000][100,3100]"/>
      <node class="android.widget.TextView" text="Partly off" bounds="[-50,10][60,40]"/>
      <node class="android.widget.FrameLayout" bounds="[0,0][100,100]"/>
      <node class="android.widget.Button" text="Go" content-desc="Go now" clickable="true" enabled="false"
            bounds="[0,0][100,100]"/>
      <node class="android.widget.Switch" checkable="true" checked="true" text="Wifi" bounds="[0,0][100,100]"/>
      <node class="android.view.View" clickable="true" content-desc="Card" bounds="[0,0][100,100]"/>
      <node class="android.view.View" content-desc="Label" bounds="[0,0][100,100]"/>
      <node class="androidx.recyclerview.widget.RecyclerView" scrollable="true" bounds="[0,0][100,100]"/>
      <node class="com.custom.Thing" resource-id="dev.demo:id/thing" bounds="[0,0][100,100]"/>
      <node class="android.widget.TextView" text="  lots   of
            space " bounds="[0,0][100,100]"/>
      <node class="android.widget.TextView" text="no bounds"/>
    </hierarchy>"""
    els = parse_hierarchy(ET.fromstring(xml), 1080, 2424)
    assert [(e.kind, e.text) for e in els] == [
        ("text", "Partly off"),
        ("button", "Go (Go now)"),
        ("switch", "Wifi"),
        ("button", "Card"),
        ("text", "Label"),
        ("list", ""),
        ("thing", ""),
        ("text", "lots of space"),
    ]
    assert els[0].bounds == (0, 10, 60, 40)
    assert els[1].enabled is False and els[2].checked is True and els[5].scrollable
    assert els[6].resource_id == "thing"
    # a text and a different description are both shown, and each can be matched exactly on its own
    assert els[1].parts == ("Go", "Go now") and els[2].parts == ()


def test_empty_webview_detection():
    assert has_empty_webview(EMPTY_WEB)
    assert not has_empty_webview(WEB)
    assert not has_empty_webview(LOGIN)


# --- tools and devices ---------------------------------------------------------------------


def test_parse_screen_sizes_by_rotation_and_reads_the_keyboard():
    xml = (
        '<hierarchy rotation="1" ime="true" ime-top="700">'
        '<node class="android.widget.Button" text="OK" bounds="[0,0][100,100]"/></hierarchy>'
    )
    screen = parse_screen(xml, lambda rotation: (2000, 1000) if rotation == 1 else (1000, 2000))
    assert (screen.width, screen.height, screen.keyboard_visible, screen.keyboard_top) == (2000, 1000, True, 700)
    assert [e.text for e in screen.elements] == ["OK"]


def _tree(ime: str, cls: str, focused: str) -> str:
    return (
        f'<hierarchy rotation="1" ime="{ime}" ime-top="344"><node class="{cls}" focused="{focused}" '
        'bounds="[66,345][2139,345]"/></hierarchy>'
    )


def test_typing_is_ready_with_the_keyboard_up_and_a_text_field_focused_even_off_screen():
    assert typing_ready(_tree("true", "android.widget.EditText", "true"))
    assert typing_ready(_tree("true", "android.widget.AutoCompleteTextView", "true"))
    assert not typing_ready(_tree("false", "android.widget.EditText", "true"))  # no keyboard: keys are dropped
    assert not typing_ready(_tree("true", "android.widget.EditText", "false"))
    assert not typing_ready(_tree("true", "android.widget.Button", "true"))  # focus, but nothing to type into
