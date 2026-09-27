"""Steps: one test-file step into a domain `Step`.

A step is one action, then optional checks on the result::

    - do: Sign in with email "a@b.c" and password "pw"     <- action (Jev works it out)
      expect: The home screen greets the user              <- check (Jev judges)
      see: Welcome                                         <- check (exact text)

`ACTIONS` is the one place that says how each action key reads: its value, and the options it takes. What an
action *is* (whether it finds an element, waits for the screen, may leave the app) is on its domain type.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from jevtest.adapters.shapes import is_list, is_mapping
from jevtest.domain.failures import TestFileError
from jevtest.domain.kinds import Direction, Gesture, Orientation
from jevtest.domain.settings import DEFAULTS, STEP_SETTINGS, Settings
from jevtest.domain.steps import (
    SETTING_SCOPES,
    Action,
    Back,
    Background,
    Check,
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
    NotSee,
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
    Swipe,
    Touch,
    TypeText,
    Use,
    Wait,
)

from .values import choice, kind, number, on_off, setting, text

Options = Mapping[str, object]
"""A step's options (`direction:`, `into:`, `timeout:`, ...), as written."""

MAX_LATITUDE, MAX_LONGITUDE = 90, 180


@dataclass(frozen=True)
class ActionSpec:
    """How one action key reads.

    Attributes:
        parse: Turns the key, its value and the step's options into the action.
        options: The options this action takes, besides settings.
        takes_value: False for an action written as a bare word (``- back``).
    """

    parse: Callable[[str, object, Options], Action]
    options: frozenset[str] = frozenset()
    takes_value: bool = True


def _bare(action: Action) -> ActionSpec:
    def parse(key: str, value: object, _: Options) -> Action:
        if value is not None:
            raise TestFileError(f"'{key}' takes no value, got {value!r}")
        return action

    return ActionSpec(parse, takes_value=False)


def _text_of(make: Callable[[str], Action]) -> ActionSpec:
    return ActionSpec(lambda key, value, _: make(text(value, f"'{key}'")))


def _touch(key: str, value: object, _: Options) -> Action:
    return Touch(Gesture(key), text(value, f"'{key}'"))


def _type(_: str, value: object, options: Options) -> Action:
    if not isinstance(value, str):
        raise TestFileError(
            f"'type' needs text (`type: hello` or `type: {{text: hello, into: Email}}`), got {kind(value)}"
        )
    into = options.get("into")
    return TypeText(value, None if into is None else text(into, "into"))  # typed exactly as written


def _scroll_to(key: str, value: object, options: Options) -> Action:
    if "direction" not in options:
        raise TestFileError("'scroll_to' needs `direction:` (up, down, left or right)")
    return ScrollTo(text(value, f"'{key}'"), choice(options["direction"], Direction, "direction"))


def _swipe(key: str, value: object, options: Options) -> Action:
    target = options.get("target")
    return Swipe(choice(value, Direction, f"'{key}'"), None if target is None else text(target, "target"))


def _location(_: str, value: object, __: Options) -> Action:
    if not is_list(value) or len(value) != len(("latitude", "longitude")):
        raise TestFileError(f"location must be [latitude, longitude], e.g. [37.77, -122.41]; got {value!r}")
    lat = number(value[0], "latitude", minimum=-MAX_LATITUDE)
    lon = number(value[1], "longitude", minimum=-MAX_LONGITUDE)
    if lat > MAX_LATITUDE or lon > MAX_LONGITUDE:
        raise TestFileError(f"location {lat:g},{lon:g} is out of range")
    return Location(lat, lon)


ACTIONS: Mapping[str, ActionSpec] = {
    "do": _text_of(Do),
    "use": _text_of(Use),
    "tap": ActionSpec(_touch),
    "double_tap": ActionSpec(_touch),
    "long_press": ActionSpec(_touch),
    "clear": _text_of(Clear),
    "type": ActionSpec(_type, frozenset({"into"})),
    "scroll": ActionSpec(lambda key, value, _: Scroll(choice(value, Direction, f"'{key}'"))),
    "swipe": ActionSpec(_swipe, frozenset({"target"})),
    "scroll_to": ActionSpec(_scroll_to, frozenset({"direction"})),
    "key": _text_of(Key),
    "wait": ActionSpec(lambda key, value, _: Wait(number(value, f"'{key}'"))),
    "background": ActionSpec(lambda key, value, _: Background(number(value, f"'{key}'"))),
    "rotate": ActionSpec(lambda key, value, _: Rotate(choice(value, Orientation, f"'{key}'"))),
    "location": ActionSpec(_location),
    "open_url": _text_of(OpenUrl),
    "dark_mode": ActionSpec(lambda key, value, _: DarkMode(on_off(value, f"'{key}'"))),
    "grant": _text_of(Grant),
    "network": ActionSpec(lambda key, value, _: Network(on_off(value, f"'{key}'"))),
    "screenshot": _text_of(Screenshot),
    "launch": _bare(Launch()),
    "stop": _bare(Stop()),
    "restart": _bare(Restart()),
    "clear_data": _bare(ClearData()),
    "reinstall": _bare(Reinstall()),
    "back": _bare(Back()),
    "home": _bare(Home()),
    "hide_keyboard": _bare(HideKeyboard()),
}
"""Every action key, and how it reads."""

BARE_WORDS = sorted(key for key, spec in ACTIONS.items() if not spec.takes_value)
CHECKS: Mapping[str, Callable[[str], Check]] = {"expect": Expect, "see": See, "not_see": NotSee}
OPTIONS = frozenset[str]().union(*(spec.options for spec in ACTIONS.values())) | STEP_SETTINGS
TYPE_KEYS = frozenset({"text", "into"})


def parse_step(raw: object, settings: Settings = DEFAULTS) -> Step:
    """One step from the test file, run with the file's `settings` and any the step sets for itself.

    Raises:
        TestFileError: The step is wrong; the message says how to fix it.
    """
    if isinstance(raw, str):
        return _bare_word(raw, settings)
    if not is_mapping(raw):
        raise TestFileError(f"Step must be an action word or a mapping, got {raw!r}")
    step = _step_keys(raw)
    keys = [k for k in step if k in ACTIONS]
    checks = _checks(step)
    options = {k: v for k, v in step.items() if k in OPTIONS}
    if not keys:
        if not checks:
            raise TestFileError(f"Step {raw!r} has no action or check")
        _check_options(None, options)
        return Step(None, checks, _settings(None, checks, options, settings))
    key = keys[0]
    value = _unpack_type(step[key], options) if key == "type" else step[key]
    action = _action(key, value, options)
    return Step(action, checks, _settings(action, checks, options, settings), label(key, value, options))


def _step_keys(raw: Mapping[object, object]) -> dict[str, object]:
    """The step with its keys as text, after checking each is one a step can have, with one action at most."""
    if "text" in raw:
        raise TestFileError("`text` goes inside type: `type: {text: hello, into: Email}`")
    known = ACTIONS.keys() | CHECKS.keys() | OPTIONS
    unknown = [k for k in raw if not isinstance(k, str) or k not in known]
    if unknown:
        raise TestFileError(f"Step {raw!r} has unknown keys: {', '.join(sorted(map(str, unknown)))}")
    step = {k: v for k, v in raw.items() if isinstance(k, str)}
    actions = [k for k in step if k in ACTIONS]  # in the order written
    if len(actions) > 1:
        raise TestFileError(f"Step {raw!r} has more than one action ({', '.join(actions)}); split it into two steps")
    return step


def _bare_word(raw: str, settings: Settings) -> Step:
    if not raw.strip():
        raise TestFileError("Empty step")
    if raw not in BARE_WORDS:
        raise TestFileError(
            f"Unknown step {raw!r}. A bare word must be one of {', '.join(BARE_WORDS)}; "
            f"for a plain-English goal write `- do: {raw.strip()}`"
        )
    return Step(ACTIONS[raw].parse(raw, None, {}), settings=settings, label=raw)


def _action(key: str, value: object, options: Options) -> Action:
    """The action, after checking each of its options belongs to it."""
    _check_options(key, options)
    return ACTIONS[key].parse(key, value, options)


def _check_options(key: str | None, options: Options) -> None:
    """Each option that isn't a setting belongs to this action (None: a step that only checks)."""
    takes = ACTIONS[key].options if key is not None else frozenset[str]()
    stray = sorted(options.keys() - STEP_SETTINGS - takes)
    if stray:
        owners = " / ".join(sorted(k for k, s in ACTIONS.items() if stray[0] in s.options))
        raise TestFileError(f"`{stray[0]}` belongs to {owners}, not to {key or 'a checks-only step'}")


def _checks(raw: Mapping[str, object]) -> tuple[Check, ...]:
    """A step's checks, in the order written; each check key takes one text or a list of them."""
    checks: list[Check] = []
    for key, value in raw.items():
        if key in CHECKS:
            values = value if is_list(value) else [value]
            if not values:
                raise TestFileError(f"'{key}' needs at least one value")
            checks += [CHECKS[key](text(v, f"'{key}'")) for v in values]
    return tuple(checks)


def _settings(action: Action | None, checks: tuple[Check, ...], options: Options, base: Settings) -> Settings:
    """The file's settings with the ones this step sets for itself; each must mean something for this step."""
    changes: dict[str, float] = {}
    for name in sorted(options.keys() & STEP_SETTINGS):
        scope = SETTING_SCOPES[name]
        if not scope.applies(action, checks):
            raise TestFileError(f"`{name}` only applies to {scope.where}")
        changes[name] = setting(name, options[name])
    return base.changed(changes)


def _unpack_type(value: object, options: dict[str, object]) -> object:
    """`type: {text: .., into: ..}` into the text, with `into` moved to the options; any other value as it is."""
    if not is_mapping(value):
        return value
    bad = set(value) - TYPE_KEYS
    if bad:
        raise TestFileError(f"type has unknown keys: {', '.join(sorted(map(str, bad)))} (it takes text and into)")
    if "text" not in value:
        raise TestFileError("type needs `text:`: `type: {text: hello, into: Email}`")
    if "into" in options:
        raise TestFileError("`into` is given twice; put it inside type: {text: .., into: ..}")
    if "into" in value:
        options["into"] = value["into"]
    return value["text"]


def label(key: str, value: object, options: Options) -> str:
    """How a step's action reads on one line, e.g. ``scroll_to: Item 25 (direction: down, max_scrolls: 5)``."""
    head = key if value is None else f"{key}: {_shown(value)}"
    extras = ", ".join(f"{name}: {_shown(v)}" for name, v in options.items())
    return f"{head} ({extras})" if extras else head


def _shown(value: object) -> str:
    if isinstance(value, bool):
        return "on" if value else "off"
    if isinstance(value, float):
        return f"{value:g}"
    if is_list(value):
        return ", ".join(_shown(v) for v in value)
    return str(value)
