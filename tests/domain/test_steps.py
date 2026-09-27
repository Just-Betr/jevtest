import pytest

from jevtest.domain.kinds import Direction, Gesture, Orientation
from jevtest.domain.steps import (
    SETTING_SCOPES,
    Back,
    Background,
    Clear,
    ClearData,
    DarkMode,
    Do,
    Expect,
    Grant,
    HideKeyboard,
    Home,
    Key,
    Launch,
    Location,
    Network,
    OpenUrl,
    Reinstall,
    Restart,
    Rotate,
    Screenshot,
    Scroll,
    ScrollTo,
    See,
    Stop,
    Swipe,
    Touch,
    TypeText,
    Use,
    Wait,
)

EVERY_ACTION = [
    Do("g"),
    Use("t"),
    Touch(Gesture.TAP, "x"),
    Clear("x"),
    TypeText("a"),
    TypeText("a", "Email"),
    Scroll(Direction.UP),
    Swipe(Direction.LEFT),
    Swipe(Direction.LEFT, "Row"),
    ScrollTo("x", Direction.DOWN),
    Key("enter"),
    Wait(1),
    Background(1),
    Rotate(Orientation.LANDSCAPE),
    Location(1, 2),
    OpenUrl("u"),
    DarkMode(on=True),
    Grant("p"),
    Network(on=False),
    Screenshot("s"),
    Launch(),
    Stop(),
    Restart(),
    ClearData(),
    Reinstall(),
    Back(),
    Home(),
    HideKeyboard(),
]


@pytest.mark.parametrize("action", EVERY_ACTION, ids=repr)
def test_every_action_says_what_it_is(action):
    """The traits the runner and loader rely on, spelled out for every action."""
    settles = not isinstance(action, Stop | ClearData | Reinstall | Home | Wait | Screenshot | ScrollTo | Do | Use)
    watches = not isinstance(action, Stop | ClearData | Reinstall | Home | Wait | Screenshot | Use)
    leaves = isinstance(action, Stop | ClearData | Reinstall | Home | OpenUrl)
    finds = isinstance(action, Touch | Clear) or action in (TypeText("a", "Email"), Swipe(Direction.LEFT, "Row"))
    assert (action.settles, action.watches_screen, action.app_may_leave, action.finds_element) == (
        settles,
        watches,
        leaves,
        finds,
    )


@pytest.mark.parametrize(
    ("name", "action", "checks", "applies"),
    [
        ("timeout", Touch(Gesture.TAP, "x"), (), True),
        ("timeout", Back(), (See("x"),), True),
        ("timeout", Back(), (), False),
        ("timeout", None, (See("x"),), True),
        ("settle", Back(), (), True),
        ("settle", Wait(1), (), False),
        ("settle", None, (See("x"),), False),
        ("max_actions", Do("g"), (), True),
        ("max_actions", Back(), (), False),
        ("max_scrolls", ScrollTo("x", Direction.DOWN), (), True),
        ("max_scrolls", Do("g"), (), False),
        ("confidence", None, (Expect("x"),), True),
        ("confidence", Do("g"), (See("x"),), False),
    ],
)
def test_each_setting_applies_where_it_means_something(name, action, checks, applies):
    assert SETTING_SCOPES[name].applies(action, checks) is applies
