import json

from jevtest.adapters.reports.screen_notes import ScreenNotesFiles, as_html, as_json, step_for
from jevtest.domain.inspection import ElementNotes, ScreenNotes, notes
from jevtest.domain.screen import Element, Screen


def note(kind="button", names=("OK",), state=(), shared=None, bounds=(0, 0, 10, 10)):
    return ElementNotes(kind, names, shared or {}, bounds, state)


def test_the_three_files_are_written_beside_the_screenshot(tmp_path):
    shot = tmp_path / "001_home.png"
    screen = Screen(400, 800, (Element(kind="button", text="Sign in", bounds=(100, 700, 300, 760)),))
    ScreenNotesFiles().write(shot, notes(screen, lambda t: t))
    assert "'Sign in'" in (tmp_path / "001_home.txt").read_text()
    dump = json.loads((tmp_path / "001_home.json").read_text())
    assert dump["screenshot"] == "001_home.png"
    assert dump["screen"] == {"width": 400, "height": 800, "keyboard_visible": False}
    assert dump["elements"] == [
        {
            "kind": "button",
            "find_by": "Sign in",
            "names": ["Sign in"],
            "shared": {},
            "bounds": [100, 700, 300, 760],
            "state": [],
        }
    ]
    assert 'src="001_home.png"' in (tmp_path / "001_home.html").read_text()


def test_json_says_what_its_fields_mean():
    dump = as_json("x.png", ScreenNotes(1, 1, False, ()))
    assert "find_by" in str(dump["about"]) and "either" in str(dump["shared"])


def test_a_step_types_into_a_field_and_taps_anything_else():
    assert step_for(note("text_field", ("Email",), ("editable",))) == "type: { text: '...', into: 'Email' }"
    assert step_for(note("button", ("Don't save",))) == "tap: 'Don''t save'"  # YAML's single quotes
    assert step_for(note("image", ())) == ""


def test_the_page_boxes_each_element_where_it_is_on_the_screenshot():
    page = as_html(
        "a<b.png",
        ScreenNotes(
            400,
            800,
            False,
            (
                note(names=("Home",), shared={"Home": 2}, bounds=(0, 400, 200, 600), state=("selected",)),
                note("image", (), bounds=(10, 10, 5, 5)),  # no size: drawn with none
            ),
        ),
    )
    assert 'style="left:0.000%;top:50.000%;width:50.000%;height:25.000%"' in page
    assert "width:0.000%;height:0.000%" in page
    assert 'data-step="tap: &#x27;Home&#x27;"' in page
    assert "<em>(2 on screen)</em>" in page and '<span class="state">selected</span>' in page
    assert "<em>(no name)</em>" in page
    assert 'src="a&lt;b.png"' in page  # escaped


def test_a_screen_with_no_size_still_makes_a_page():
    assert "<ol" in as_html("x.png", ScreenNotes(0, 0, False, (note(),)))
