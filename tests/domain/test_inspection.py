from jevtest.domain.inspection import HEADER, notes
from jevtest.domain.screen import Element, Screen


def el(kind="button", text="", **kw):
    return Element(kind=kind, text=text, bounds=kw.pop("bounds", (0, 0, 10, 10)), **kw)


def test_each_element_has_its_names_bounds_and_state():
    screen = Screen(
        400,
        800,
        (
            el(
                "text_field",
                "",
                hint="Email",
                resource_id="email",
                editable=True,
                focused=True,
                bounds=(10, 20, 390, 60),
            ),
            el("switch", "Dark theme", checked=True),
            el("button", "Pay", enabled=False),
            el("progress"),
        ),
        keyboard_visible=True,
    )
    n = notes(screen, lambda t: t)
    assert (n.width, n.height, n.keyboard_visible) == (400, 800, True)
    field, switch, pay, progress = n.elements
    assert field.names == ("Email", "email") and field.bounds == (10, 20, 390, 60)
    assert field.state == ("focused", "editable")
    assert switch.state == ("checked",) and pay.state == ("disabled",) and progress.state == ()
    assert progress.find_by == "" and progress.names == ()


def test_a_name_more_than_one_element_has_says_how_many_and_a_name_of_its_own_is_the_one_to_use():
    """Several elements say Home (a tab and a heading): a step naming Home may find either."""
    screen = Screen(400, 800, (el("text", "Home"), el("button", "Home", resource_id="tab_home"), el("text", "Home")))
    heading, tab, other = notes(screen, lambda t: t).elements
    assert heading.shared == {"Home": 3} and tab.shared == {"Home": 3}
    assert tab.find_by == "tab_home"  # its own name
    assert heading.find_by == "Home"  # none of its own: its first
    assert other.find_by == "Home"


def test_an_element_saying_a_name_twice_counts_once():
    screen = Screen(400, 800, (el("button", "Save", hint="Save"),))
    assert notes(screen, lambda t: t).elements[0].shared == {}


def test_names_are_masked():
    screen = Screen(400, 800, (el("text", "Hi a@b.c"),))
    assert notes(screen, lambda t: t.replace("a@b.c", "${E}")).elements[0].names == ("Hi ${E}",)


def test_the_text_lists_each_element_and_how_many_share_a_name():
    screen = Screen(400, 800, (el("text", "Home"), el("button", "Home", resource_id="tab"), el("image")))
    assert notes(screen, lambda t: t).text().splitlines() == [
        HEADER,
        "text           'Home' (2 on screen)",
        "button         'Home' (2 on screen) | 'tab'",
        "image          (no name)",
    ]
