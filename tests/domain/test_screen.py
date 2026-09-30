import dataclasses

import pytest

from jevtest.domain.kinds import Direction
from jevtest.domain.screen import Element, Screen
from tests.conftest import el, login_screen


def test_elements_are_numbered_and_the_callers_are_left_alone():
    original = el("button", "Go")
    s = Screen(10, 10, (original,))
    assert s.elements[0].id == "e1" and original.id == ""


def test_a_swipe_goes_along_its_line_across_the_row_or_scroller_it_is_in():
    """A finger swipes a row, a pager or a carousel, not just the text on it, which can be far narrower."""
    outer = el("cell", bounds=(0, 0, 1000, 1000))
    row = el("cell", bounds=(0, 400, 1000, 500))
    milk = el("text", "Milk", bounds=(20, 420, 120, 480))
    slider = el("slider", position=0.5, bounds=(20, 420, 980, 480))
    pager = el("other", scrollable=True, bounds=(0, 1100, 1000, 1400))  # Android: a scrolling container
    page = el("text", "Help page", bounds=(40, 1200, 240, 1250))
    chip = el("button", "Tag 1", bounds=(40, 1500, 140, 1550))  # in an iOS scroll view with no label
    alone = el("text", "Alone", bounds=(20, 1700, 120, 1780))
    s = Screen(1000, 2000, (outer, row, milk, slider, pager, page, chip, alone), scrollers=((30, 1490, 970, 1560),))
    _, _, milk, slider, _, page, chip, alone = s.elements
    left, up = Direction.LEFT, Direction.UP
    # along its line across the smallest cell, from anywhere on it (a row takes a touch anywhere)
    assert s.swiped(milk, left) == ((0, 420, 1000, 480), (0, 420, 1000, 480))
    assert s.swiped(milk, up) == ((20, 400, 120, 500), (20, 400, 120, 500))  # up and down: along its column
    # across a scroller, from on the element: that's the item the finger moves
    assert s.swiped(page, left) == ((0, 1200, 1000, 1250), page.bounds)
    assert s.swiped(chip, left) == ((30, 1500, 970, 1550), chip.bounds)
    assert s.swiped(slider, left) == (slider.bounds, slider.bounds)  # a slider is swiped itself: its thumb moves
    assert s.swiped(alone, left) == (alone.bounds, alone.bounds)  # in nothing that scrolls


def test_the_page_is_the_screen_less_the_keyboard_and_the_apps_bars():
    """A drag that starts on a navigation bar moves nothing: page drags keep to the page between the bars."""
    plain = Screen(400, 800)
    bars = Screen(400, 800, bars=((0, 60, 400, 176), (0, 730, 400, 800)))
    typing = Screen(400, 800, bars=((0, 60, 400, 176), (0, 730, 400, 800)), keyboard_visible=True, keyboard_top=500)
    assert (plain.page, bars.page, typing.page) == ((0, 0, 400, 800), (0, 176, 400, 730), (0, 176, 400, 500))


def test_the_page_is_the_biggest_thing_that_scrolls_clear_of_bars_and_keyboard():
    """A scroll from 80% of a landscape screen started on a Compose navigation bar: drag what scrolls instead."""
    column = el("scroll_view", scrollable=True, bounds=(0, 300, 2424, 870))
    chips = el("list", scrollable=True, bounds=(0, 400, 2424, 500))
    assert Screen(2424, 1080, (column, chips)).page == (0, 300, 2424, 870)
    # an iOS list with no label, under a navigation bar that overlaps it: clipped to below the bar
    ios = Screen(402, 874, scrollers=((0, 0, 402, 874),), bars=((0, 62, 402, 176),))
    assert ios.page == (0, 176, 402, 874)
    # what scrolls is all under the keyboard: the page above the keyboard instead
    low = Screen(
        400, 800, (el("list", scrollable=True, bounds=(0, 600, 400, 800)),), keyboard_visible=True, keyboard_top=500
    )
    assert low.page == (0, 0, 400, 500)


def test_lookups():
    s = login_screen()
    assert [e.id for e in s.elements] == ["e1", "e2", "e3"]
    assert s.by_id("e3").text == "Sign in"
    assert [e.hint for e in s.editable] == ["Email", "Password"]
    with pytest.raises(KeyError):
        s.by_id("e9")


def test_shows_only_an_exact_text_of_one_element():
    s = login_screen()
    assert s.shows("Sign in") and s.shows("Email")  # a text, a hint
    assert s.shows("SIGN IN") and s.shows("email")  # case doesn't matter
    assert not s.shows("mail") and not s.shows("Sign") and not s.shows("Sign in now")


@pytest.mark.parametrize(
    ("wanted", "on_screen"),
    [("Taps: 2", "Taps: 20"), ("Save", "Unsaved changes"), ("Save", "Save draft"), ("Welcome", "Welcome back")],
)
def test_see_never_passes_on_a_longer_text(wanted, on_screen):
    assert not Screen(10, 10, (el("text", on_screen),)).shows(wanted)


def test_an_element_says_its_text_parts_hint_and_id_exactly():
    field = el("text_field", "Email: a@b.c", parts=("Email", "a@b.c"), hint="you@example.com", resource_id="email")
    assert all(field.says(t) for t in ("Email: a@b.c", "Email", "a@b.c", "you@example.com", "email", "EMAIL: A@B.C"))
    assert not any(field.says(t) for t in ("a@b", "Email:", "mail", ""))
    assert field.names() == ("Email: a@b.c", "Email", "a@b.c", "you@example.com", "email")


def test_a_targets_spaces_and_line_breaks_count_as_the_screens_do():
    """The screen's text is read with its spaces and line breaks collapsed to one space; a target is too."""
    taps = Element("text", "Taps: 2")
    assert taps.says("Taps:  2") and taps.says("Taps:\n2")
    assert not taps.says("Taps:2")


def test_the_same_letters_in_another_encoding_match():
    """An é stored whole (U+00E9) and one written as an e and an accent (U+0301) are the same text."""
    composed, decomposed = "Jos\u00e9", "Jose\u0301"
    assert Element("text", decomposed).says(composed) and Element("text", composed).says(decomposed)
    assert not Element("text", "Jose").says(composed)  # a different letter is still different


def test_near_lists_longer_texts_containing_the_target_only():
    s = Screen(10, 10, tuple(el("text", t) for t in ("Save draft", "SAVE", "Unsaved changes", "Cancel", "Save")))
    assert s.near("Save") == ("Save draft", "Unsaved changes")  # SAVE and Save match; they aren't near
    assert Screen(10, 10, tuple(el("text", f"Save {i}") for i in range(9))).near("Save") == tuple(
        f"Save {i}" for i in range(5)
    )


def test_screens_are_values():
    assert login_screen() == login_screen()
    moved = dataclasses.replace(login_screen().elements[2], bounds=(0, 401, 1000, 501))
    assert login_screen() != Screen(1000, 2000, (*login_screen().elements[:2], moved))
    with pytest.raises(dataclasses.FrozenInstanceError):
        login_screen().width = 5  # type: ignore[misc]


@pytest.mark.parametrize(
    ("bounds", "region"),
    [((0, 0, 10, 10), "top-left"), ((140, 140, 160, 160), "middle-center"), ((290, 290, 300, 300), "bottom-right")],
)
def test_regions(bounds, region):
    s = Screen(300, 300, (el(bounds=bounds),))
    assert s.region(s.elements[0]) == region


def test_content_height_excludes_the_keyboard():
    assert Screen(10, 2000).content_height == 2000
    assert Screen(10, 2000, keyboard_visible=True, keyboard_top=1200).content_height == 1200
    assert Screen(10, 2000, keyboard_visible=True, keyboard_top=0).content_height == 2000  # unknown
    assert Screen(10, 2000, keyboard_visible=False, keyboard_top=1200).content_height == 2000


def test_under_keyboard_is_where_a_tap_would_hit_a_key():
    s = Screen(10, 2000, keyboard_visible=True, keyboard_top=1200)
    assert s.under_keyboard(el(bounds=(0, 1150, 10, 1260)))  # its middle is on the keyboard
    assert not s.under_keyboard(el(bounds=(0, 1100, 10, 1290)))  # partly covered, but its middle is above
    assert s.under_keyboard(el(bounds=(0, 1100, 10, 1300)))  # its middle is exactly where the keyboard starts
    assert not Screen(10, 2000).under_keyboard(el(bounds=(0, 1900, 10, 2000)))


def test_a_field_takes_the_keys_when_it_has_focus_and_the_keyboard_is_up():
    field = el("text_field", editable=True, focused=True, bounds=(0, 0, 10, 10))
    assert Screen(10, 10, keyboard_visible=True).takes_keys(field)
    assert not Screen(10, 10).takes_keys(field)  # no keyboard: e.g. a web view, where everything reports focus
    assert not Screen(10, 10, keyboard_visible=True).takes_keys(dataclasses.replace(field, focused=False))


def test_clear_of_edges_is_away_from_the_top_and_bottom_8_percent():
    s = Screen(10, 2000)
    assert s.clear_of_edges(el(bounds=(0, 150, 10, 170))) and not s.clear_of_edges(el(bounds=(0, 140, 10, 170)))
    assert s.clear_of_edges(el(bounds=(0, 1830, 10, 1850))) and not s.clear_of_edges(el(bounds=(0, 1830, 10, 1852)))
    up = Screen(10, 2000, keyboard_visible=True, keyboard_top=1000)  # measured from the keyboard's edge
    assert not up.clear_of_edges(el(bounds=(0, 900, 10, 950)))


def test_a_tap_on_a_barrier_behind_a_dialog_lands_off_the_dialog():
    """Measured: Flutter's `Dismiss` barrier fills the screen, with an alert at its middle; a tap there stays on the
    alert. The alert's box isn't reported, only what it holds."""
    barrier = el("button", "Dismiss", clickable=True, bounds=(0, 142, 1080, 2424))
    text, ok = el("text", "Hello", bounds=(236, 1120, 845, 1173)), el("button", "OK", bounds=(677, 1236, 845, 1362))
    s = Screen(1080, 2424, (text, ok, barrier))
    x, y = s.elements[2].tap_point
    assert not (236 <= x < 845 and 1120 <= y < 1362)  # off the box around what the dialog holds
    assert 2424 * 0.08 <= y <= 2424 * 0.92
    assert s.elements[2].center == (540, 1283)  # its middle stays, for all but a touch on it
    assert s.elements[1].tap_point == ok.center  # anything else is touched in its middle


def test_a_barrier_is_tapped_in_its_middle_when_nothing_is_there():
    barrier = el("button", "Dismiss", clickable=True, bounds=(0, 0, 1000, 2000))
    top = el("text", "Title", bounds=(0, 100, 1000, 200))
    assert Screen(1000, 2000, (barrier,)).elements[0].center == (500, 1000)  # nothing over it
    assert Screen(1000, 2000, (top, barrier)).elements[1].center == (500, 1000)  # nothing over its middle
    backdrop = el("image", "Backdrop", bounds=(0, 0, 1000, 2000))  # doesn't take taps
    assert Screen(1000, 2000, (text := el("text", "Hi", bounds=(400, 900, 600, 1100)), backdrop)).elements[
        1
    ].center == (
        500,
        1000,
    )
    assert text.center == (500, 1000)
    covered = el("text", "Everything", bounds=(0, 1, 1000, 1999))  # over every point it could use
    assert Screen(1000, 2000, (covered, barrier)).elements[1].center == (500, 1000)


def test_in_lane_is_the_middle_inside_the_lane_along_the_scroll():
    """Measured on an iPhone: a chip peeking in at a carousel's end, its middle past the carousel's end at 370."""
    s, carousel = Screen(402, 874), (32, 708, 370, 739)
    peeking, shown = el(bounds=(354, 708, 402, 739)), el(bounds=(200, 708, 260, 739))
    assert not s.in_lane(peeking, carousel, Direction.RIGHT) and s.in_lane(shown, carousel, Direction.RIGHT)
    assert s.in_lane(peeking, None, Direction.RIGHT)  # no lane: only the screen's edges
    column = (100, 100, 150, 800)  # up and down, a lane is as wide as what it was found by: only its height counts
    assert s.in_lane(el(bounds=(0, 300, 400, 340)), column, Direction.DOWN)
    assert not s.in_lane(el(bounds=(100, 790, 150, 830)), column, Direction.DOWN)
    assert not s.in_lane(el(bounds=(200, 10, 260, 40)), carousel, Direction.LEFT)  # inside, but at the top edge


def test_element_label_and_points():
    assert Element("button", "OK", bounds=(0, 0, 10, 20)).center == (5, 10)
    assert Element("text_field", bounds=(0, 0, 100, 20)).end == (92, 10)  # just inside the right edge
    assert Element("text_field", bounds=(0, 0, 4, 20)).end == (3, 10)  # tiny fields stay inside
    assert Element("button", "OK").label() == "button 'OK'"
    assert Element("text_field", hint="Email").label() == "text_field 'Email'"
    assert Element("image", resource_id="logo").label() == "image 'logo'"
    assert Element("image").label() == "image '(no label)'"


@pytest.mark.parametrize(
    ("shown", "written"),
    [
        ("Don\u2019t allow", "Don't allow"),  # Android's permission prompt (measured)
        ("Don\u2019t Allow", "don't allow"),  # iOS's
        ("\u201cQuoted\u201d", '"quoted"'),
        ("It\u2018s", "It's"),
        ("Don't allow", "Don\u2019t allow"),  # either way round
    ],
)
def test_curly_quotes_match_the_straight_ones_a_keyboard_types(shown, written):
    assert Element("button", shown).says(written)
    unquoted = "".join(c for c in written if c not in "'\"\u2018\u2019\u201c\u201d")
    assert not Element("button", shown).says(unquoted)  # a quote still has to be there
