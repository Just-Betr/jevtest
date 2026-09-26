import dataclasses

import pytest

from jevtest.domain.screen import Element, Screen

from ..conftest import el, login_screen


def test_elements_are_numbered_and_the_callers_are_left_alone():
    original = el("button", "Go")
    s = Screen(10, 10, (original,))
    assert s.elements[0].id == "e1" and original.id == ""


def test_lookups():
    s = login_screen()
    assert [e.id for e in s.elements] == ["e1", "e2", "e3"]
    assert s.by_id("e3").text == "Sign in"
    assert [e.hint for e in s.editable] == ["Email", "Password"]
    assert s.texts() == ["Email", "Password", "Sign in"]
    with pytest.raises(KeyError):
        s.by_id("e9")


def test_shows_ignores_case_and_matches_part_of_a_text():
    s = login_screen()
    assert s.shows("sign IN") and s.shows("mail") and not s.shows("Welcome")


def test_screens_are_values():
    assert login_screen() == login_screen()
    moved = dataclasses.replace(login_screen().elements[2], bounds=(0, 401, 1000, 501))
    assert login_screen() != Screen(1000, 2000, (*login_screen().elements[:2], moved))
    with pytest.raises(dataclasses.FrozenInstanceError):
        login_screen().width = 5  # type: ignore[misc]


@pytest.mark.parametrize(("bounds", "region"), [
    ((0, 0, 10, 10), "top-left"), ((140, 140, 160, 160), "middle-center"), ((290, 290, 300, 300), "bottom-right")])
def test_regions(bounds, region):
    s = Screen(300, 300, (el(bounds=bounds),))
    assert s.region(s.elements[0]) == region


def test_content_height_excludes_the_keyboard():
    assert Screen(10, 2000).content_height == 2000
    assert Screen(10, 2000, keyboard_visible=True, keyboard_top=1200).content_height == 1200
    assert Screen(10, 2000, keyboard_visible=True, keyboard_top=0).content_height == 2000  # unknown
    assert Screen(10, 2000, keyboard_visible=False, keyboard_top=1200).content_height == 2000


def test_element_label_and_points():
    assert Element("button", "OK", bounds=(0, 0, 10, 20)).center == (5, 10)
    assert Element("text_field", bounds=(0, 0, 100, 20)).end == (92, 10)  # just inside the right edge
    assert Element("text_field", bounds=(0, 0, 4, 20)).end == (3, 10)     # tiny fields stay inside
    assert Element("button", "OK").label() == "button 'OK'"
    assert Element("text_field", hint="Email").label() == "text_field 'Email'"
    assert Element("image", resource_id="logo").label() == "image 'logo'"
    assert Element("image").label() == "image '(no label)'"
