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


def test_each_line_of_a_label_can_be_named_on_its_own():
    """Flutter describes a tab as its label and its place on two lines: `tap: Form` names it."""
    xml = """<hierarchy rotation="0">
      <node class="android.view.View" clickable="true" content-desc="Form&#10;Tab 2 of 3" bounds="[0,0][100,100]"/>
      <node class="android.widget.Button" text="Go" content-desc="Go now&#10;quickly" bounds="[0,0][100,100]"/>
    </hierarchy>"""
    tab, go = parse_hierarchy(ET.fromstring(xml), 1080, 2424)
    assert (tab.text, tab.parts) == ("Form Tab 2 of 3", ("Form", "Tab 2 of 3"))
    assert go.parts == ("Go", "Go now quickly", "Go now", "quickly")
    assert tab.says("Form") and tab.says("Tab 2 of 3") and not tab.says("Tab")


def test_a_compose_text_field_is_named_by_the_label_inside_it():
    """Compose reports a TextField as an EditText with no hint holding its label as a TextView, empty or filled
    (measured with Material 3 on Android 17): the label is the field's hint, and not a text of its own."""
    xml = """<hierarchy rotation="0">
      <node class="android.widget.EditText" text="" hint="" clickable="true" bounds="[63,332][1017,500]">
        <node class="android.view.View" text="" bounds="[63,353][1017,500]"/>
        <node class="android.widget.TextView" text="Email address" bounds="[105,395][387,458]"/>
        <node class="android.widget.TextView" text="Your work email" bounds="[105,470][387,490]"/>
      </node>
      <node class="android.widget.EditText" text="••••" password="true" clickable="true" bounds="[63,532][1017,700]">
        <node class="android.widget.TextView" text="Password" bounds="[105,532][303,574]"/>
      </node>
      <node class="android.widget.EditText" text="" hint="Search" clickable="true" bounds="[63,732][1017,800]">
        <node class="android.widget.TextView" text="Recent" bounds="[105,740][303,790]"/>
      </node>
      <node class="android.widget.EditText" text="" clickable="true" bounds="[63,832][1017,900]"/>
    </hierarchy>"""
    els = parse_hierarchy(ET.fromstring(xml), 1080, 2424)
    assert [(e.kind, e.text, e.hint, e.value) for e in els] == [
        ("text_field", "", "Email address", ""),
        ("text", "Your work email", "", ""),  # only the first text inside is the label
        ("password_field", "••••", "Password", "••••"),
        ("text_field", "", "Search", ""),  # a hint of its own stays
        ("text", "Recent", "", ""),
        ("text_field", "", "", ""),
    ]


def test_a_password_fields_text_is_only_how_long_it_is():
    """Measured on Android 17: a Views password field reported its text as typed; React Native's shows its last
    character for a moment. Neither goes to Jev or into the output, only the length that `clear:` deletes."""
    xml = """<hierarchy rotation="0">
      <node class="android.widget.EditText" text="hunter22" password="true" hint="Password" bounds="[0,0][900,100]"/>
      <node class="android.widget.EditText" text="•••••2" password="true" hint="PIN" bounds="[0,200][900,300]"/>
      <node class="android.widget.EditText" text="plain" password="false" hint="Name" bounds="[0,400][900,500]"/>
    </hierarchy>"""
    els = parse_hierarchy(ET.fromstring(xml), 1080, 2424)
    assert [(e.text, e.value) for e in els] == [("••••••••", "••••••••"), ("••••••", "••••••"), ("plain", "plain")]
    assert not any("hunter22" in e.label() for e in els)


def test_what_the_app_reports_off_screen_is_kept_as_text_only():
    """Measured on Android 17: a web page reports its content below the screen as `[0,0][0,0]`."""
    xml = """<hierarchy rotation="0">
      <node class="android.view.View" text="Show more" bounds="[63,2300][275,2400]"/>
      <node class="android.view.View" text="Here is more content." bounds="[0,0][0,0]"/>
      <node class="android.widget.EditText" text="secret1" password="true" bounds="[0,0][0,0]"/>
      <node class="android.view.View" text="" bounds="[0,0][0,0]"/>
    </hierarchy>"""
    screen = parse_screen(xml, lambda _: (1080, 2424))
    assert [e.text for e in screen.elements] == ["Show more"]
    assert screen.offscreen == ("Here is more content.",)  # never a password's text
    assert screen.off_screen("here is  more content.") and not screen.off_screen("Show more")


def test_a_slider_is_kept_labelled_or_not_with_where_its_thumb_is():
    """A Compose Slider is a SeekBar with no label: it can still be swiped. A range elsewhere is no slider's."""
    xml = """<hierarchy rotation="0">
      <node class="android.widget.SeekBar" text="" position="0.5" bounds="[42,903][1038,1019]"/>
      <node class="android.widget.SeekBar" content-desc="Brightness bar" position="0.3" bounds="[42,1528][1038,1575]"/>
      <node class="android.widget.SeekBar" text="" bounds="[42,1600][1038,1650]"/>
      <node class="android.widget.ProgressBar" content-desc="Loading" position="0.7" bounds="[42,1700][1038,1750]"/>
    </hierarchy>"""
    els = parse_hierarchy(ET.fromstring(xml), 1080, 2424)
    assert [(e.kind, e.text, e.position) for e in els] == [
        ("slider", "", 0.5),
        ("slider", "Brightness bar", 0.3),
        ("slider", "", None),  # an agent that doesn't say where its thumb is
        ("progress", "Loading", None),
    ]


def test_what_a_system_bar_covers_is_left_out_of_where_an_element_is_touched():
    """An app drawn under the status bar: a tap on a switch's middle there reached the status bar, not the switch
    (measured on Android 17). What's left of each element is where it's touched; what's wholly under stays readable."""
    xml = """<hierarchy rotation="0" bars="0,0,1080,142;0,2300,1080,2424;1000,142,1080,2300;0,142,40,2300">
      <node class="android.widget.Switch" checkable="true" content-desc="Gift wrap" bounds="[853,105][975,176]"/>
      <node class="android.widget.TextView" text="Title" bounds="[63,40][500,120]"/>
      <node class="android.widget.Button" text="Next" clickable="true" bounds="[63,2250][500,2350]"/>
      <node class="android.widget.Button" text="Side" clickable="true" bounds="[900,500][1050,600]"/>
      <node class="android.widget.Button" text="Left" clickable="true" bounds="[20,700][200,800]"/>
      <node class="android.widget.Button" text="Clear" clickable="true" bounds="[63,500][500,600]"/>
    </hierarchy>"""
    s = parse_screen(xml, lambda _: (1080, 2424))
    assert [(e.text, e.bounds) for e in s.elements] == [
        ("Gift wrap", (853, 142, 975, 176)),  # its top is under the status bar
        ("Title", (63, 40, 500, 120)),  # wholly under it: read as it is, and never touched
        ("Next", (63, 2250, 500, 2300)),  # its bottom is under a navigation bar
        ("Side", (900, 500, 1000, 600)),  # its right is under a side bar (landscape)
        ("Left", (40, 700, 200, 800)),  # its left is under one on the left (turned the other way)
        ("Clear", (63, 500, 500, 600)),
    ]
    assert s.system_bars == ((0, 0, 1080, 142), (0, 2300, 1080, 2424), (1000, 142, 1080, 2300), (0, 142, 40, 2300))
    assert [s.under_system_bar(e) for e in s.elements] == [False, True, False, False, False, False]


def test_a_toast_the_agent_saw_is_on_screen_where_its_window_is():
    """A toast isn't in the app's window: the agent adds it, with its text and its window's place, while it shows."""
    xml = """<hierarchy rotation="0" package="dev.demo">
      <node class="android.widget.TextView" text="About" bounds="[0,200][1080,300]"/>
      <node index="0" text="Saved to favourites" class="android.widget.Toast" package="android" content-desc=""
            bounds="[295,2183][784,2298]"></node>
    </hierarchy>"""
    _, toast = parse_screen(xml, lambda _: (1080, 2424)).elements
    assert (toast.kind, toast.text, toast.bounds) == ("toast", "Saved to favourites", (295, 2183, 784, 2298))


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
