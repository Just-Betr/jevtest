from pathlib import Path

import pytest

from jevtest.domain.kinds import Direction, Gesture, Orientation, Platform
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
    Step,
    Stop,
    Suite,
    Swipe,
    Test,
    Touch,
    TypeText,
    Use,
    Wait,
    waits,
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
    Grant(((None, ("p",)),)),
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
    leaves = isinstance(action, Stop | ClearData | Reinstall | Home | OpenUrl)
    waiting = isinstance(action, Touch | Clear | TypeText | Do | ScrollTo | Screenshot) or action == Swipe(
        Direction.LEFT, "Row"
    )
    assert (action.app_may_leave, waits(action)) == (leaves, waiting)


@pytest.mark.parametrize(
    ("name", "action", "checks", "applies"),
    [
        ("timeout", Touch(Gesture.TAP, "x"), (), True),
        ("timeout", Back(), (See("x"),), True),
        ("timeout", Back(), (), False),
        ("timeout", None, (See("x"),), True),
        ("timeout", Do("g"), (), True),
        ("timeout", ScrollTo("x", Direction.DOWN), (), True),
        ("interval", Touch(Gesture.TAP, "x"), (), True),
        ("interval", None, (See("x"),), True),
        ("interval", Back(), (), False),
        ("interval", Wait(1), (), False),
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


def test_a_grant_has_names_per_platform_or_for_every_platform():
    everywhere = Grant(((None, ("camera",)),))
    assert everywhere.names_on(Platform.ANDROID) == everywhere.names_on(Platform.IOS) == ("camera",)
    per = Grant(((Platform.IOS, ("camera",)),))
    assert per.names_on(Platform.IOS) == ("camera",)
    assert per.names_on(Platform.ANDROID) is None


def test_a_suite_takes_each_tests_steps_once_however_many_use_it():
    back, home = Step(Back()), Step(Home())
    shared = Test("Shared", fresh=False, steps=(home,))
    first = Test("First", fresh=True, steps=(back, Step(Use("Shared")), Step(Use("Shared"))))
    second = Test("Second", fresh=True, steps=(Step(Use("Shared")),))
    suite = Suite(Path("t.yaml"), {}, {}, (first, shared, second), {t.name: t for t in (first, shared, second)}, {})
    assert [(name, step.action) for name, step in suite.steps()] == [
        ("First", Back()),
        ("First", Use("Shared")),
        ("First", Use("Shared")),
        ("Shared", Home()),
        ("Second", Use("Shared")),
    ]
