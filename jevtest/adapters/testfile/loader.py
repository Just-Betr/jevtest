"""Test files: YAML into the domain's types, checked completely before any device work.

A bad test file fails at once with every problem and its fix, never halfway through a run. Nothing is assumed:
every value a run uses is written in the file (or the .env next to it); anything missing, misspelled or of the
wrong type is an error.

A step is one action, then optional checks on the result::

    - do: Sign in with email "a@b.c" and password "pw"     <- action (Jev works it out)
      expect: The home screen greets the user              <- check (Jev judges)
      see: Welcome                                         <- check (exact text)
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any, TypeVar

import yaml

from jevtest.domain.failures import TestFileError
from jevtest.domain.kinds import Direction, Gesture, Orientation, Platform
from jevtest.domain.steps import (
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
    Suite,
    Swipe,
    Test,
    Touch,
    TypeText,
    Use,
    Wait,
)
from jevtest.domain.variables import VARIABLE, fill

ANDROID_EXT = {".apk", ".aab"}
IOS_EXT = {".app", ".zip", ".ipa"}

# Actions with no value: written as a bare word ("- back") or a key ("- back:").
BARE: dict[str, Action] = {"launch": Launch(), "stop": Stop(), "restart": Restart(), "clear_data": ClearData(),
                           "reinstall": Reinstall(), "back": Back(), "home": Home(), "hide_keyboard": HideKeyboard()}
TEXT_ACTIONS = {"do", "use", "tap", "double_tap", "long_press", "clear", "scroll_to", "key", "open_url", "grant",
                "screenshot"}
ACTIONS = TEXT_ACTIONS | {"wait", "background", "scroll", "swipe", "type", "rotate", "location", "dark_mode",
                          "network"}
CHECKS: dict[str, Callable[[str], Check]] = {"expect": Expect, "see": See, "not_see": NotSee}
OPTIONS = {"timeout", "target", "direction", "text", "into"}
# Which actions each option belongs to. `timeout` is for steps that wait for something (below).
OPTION_ACTIONS = {"direction": {"scroll_to"}, "target": {"swipe"}, "into": {"type"}, "text": {"type"}}
LOCATING = {"tap", "double_tap", "long_press", "clear"}  # find an element first, so they can time out

E = TypeVar("E", Direction, Orientation)
MAX_LATITUDE, MAX_LONGITUDE = 90, 180


def platform_of(path: Path) -> Platform:
    """The platform an app build is for, from its file extension.

    Raises:
        TestFileError: The extension isn't an Android or iOS build.
    """
    ext = path.suffix.lower()
    if ext in ANDROID_EXT:
        return Platform.ANDROID
    if ext in IOS_EXT:
        return Platform.IOS
    raise TestFileError(f"Unknown app type '{path.name}' (use .apk/.aab for Android, .app/.zip/.ipa for iOS)")


# --- values ------------------------------------------------------------------------------------------------------

def _kind(value: object) -> str:
    if value is None:
        return "nothing"
    return {bool: "true/false", int: "a number", float: "a number", str: "text", list: "a list",
            dict: "a mapping"}.get(type(value), type(value).__name__)


def _looks_numeric(text: str) -> bool:
    try:
        float(text)
    except ValueError:
        return False
    return True


def _number(value: object, what: str, minimum: float = 0) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        hint = " (remove the quotes)" if isinstance(value, str) and _looks_numeric(value) else ""
        raise TestFileError(f"{what} must be a number, got {value!r}{hint}")
    if not math.isfinite(value):
        raise TestFileError(f"{what} must be a finite number, got {value}")
    if value < minimum:
        raise TestFileError(f"{what} must be at least {minimum:g}, got {value:g}")
    return float(value)


def _text(value: object, what: str) -> str:
    if not isinstance(value, str):
        hint = f" (to use {value!r} as text, put it in quotes)" if isinstance(value, int | float | bool) else ""
        raise TestFileError(f"{what} needs text, got {_kind(value)}{hint}")
    if value.strip() == "" or value != value.strip():
        raise TestFileError(f"{what} needs text without leading or trailing spaces, got {value!r}")
    return value


def _enum(value: object, enum: type[E], what: str) -> E:
    allowed = [e.value for e in enum]
    if value not in allowed:
        raise TestFileError(f"{what} must be one of {', '.join(allowed)}; got {value!r}")
    return enum(value)


def _on_off(value: object, what: str) -> bool:
    if not isinstance(value, bool):
        raise TestFileError(f"{what} must be on or off (true or false), got {value!r}")
    return value


def _location(value: object) -> Location:
    if not isinstance(value, list) or len(value) != len(("latitude", "longitude")):
        raise TestFileError(f"location must be [latitude, longitude], e.g. [37.77, -122.41]; got {value!r}")
    lat = _number(value[0], "latitude", minimum=-MAX_LATITUDE)
    lon = _number(value[1], "longitude", minimum=-MAX_LONGITUDE)
    if lat > MAX_LATITUDE or lon > MAX_LONGITUDE:
        raise TestFileError(f"location {lat:g},{lon:g} is out of range")
    return Location(lat, lon)


# --- steps -------------------------------------------------------------------------------------------------------

def _action(kind: str, value: object, opts: Mapping[str, Any]) -> Action:  # noqa: C901, PLR0911, PLR0912
    """The action a step's key and value describe, with its options."""
    what = f"'{kind}'"
    if kind in BARE:
        if value is not None:
            raise TestFileError(f"{what} takes no value, got {value!r}")
        return BARE[kind]
    if kind == "type":
        if not isinstance(value, str):
            raise TestFileError(f"'type' needs text (`type: hello` or `type: {{text: hello, into: Email}}`), "
                                f"got {_kind(value)}")
        into = opts.get("into")
        return TypeText(value, None if into is None else _text(into, "into"))  # typed exactly as written
    if kind in ("wait", "background"):
        seconds = _number(value, what)
        return Wait(seconds) if kind == "wait" else Background(seconds)
    if kind == "scroll":
        return Scroll(_enum(value, Direction, what))
    if kind == "swipe":
        target = opts.get("target")
        return Swipe(_enum(value, Direction, what), None if target is None else _text(target, "target"))
    if kind == "rotate":
        return Rotate(_enum(value, Orientation, what))
    if kind == "location":
        return _location(value)
    if kind in ("dark_mode", "network"):
        on = _on_off(value, what)
        return DarkMode(on) if kind == "dark_mode" else Network(on)
    text = _text(value, what)
    match kind:
        case "do":
            return Do(text)
        case "use":
            return Use(text)
        case "tap" | "double_tap" | "long_press":
            return Touch(Gesture(kind), text)
        case "clear":
            return Clear(text)
        case "scroll_to":
            if "direction" not in opts:
                raise TestFileError("'scroll_to' needs `direction:` (up, down, left or right)")
            return ScrollTo(text, _enum(opts["direction"], Direction, "direction"))
        case "key":
            return Key(text)
        case "open_url":
            return OpenUrl(text)
        case "grant":
            return Grant(text)
        case _:
            return Screenshot(text)


def _check_options(kind: str | None, opts: Mapping[str, Any], has_checks: bool) -> float | None:
    """Check each option belongs to this action; return the step's timeout, if it has one."""
    for k in opts:
        if k == "timeout":
            waits = has_checks or kind in LOCATING or (kind == "type" and "into" in opts) \
                or (kind == "swipe" and "target" in opts)
            if not waits:
                raise TestFileError("timeout only applies to a step that finds an element or has checks")
        elif kind not in OPTION_ACTIONS[k]:
            owners = " / ".join(sorted(OPTION_ACTIONS[k]))
            raise TestFileError(f"`{k}` belongs to {owners}, not to {kind or 'a checks-only step'}")
    return _number(opts["timeout"], "timeout") if "timeout" in opts else None


def parse_step(raw: object) -> Step:
    """One step from the test file.

    Raises:
        TestFileError: The step is wrong; the message says how to fix it.
    """
    if isinstance(raw, str):
        if not raw.strip():
            raise TestFileError("Empty step")
        if raw not in BARE:
            raise TestFileError(f"Unknown step {raw!r}. A bare word must be one of {', '.join(sorted(BARE))}; "
                                f"for a plain-English goal write `- do: {raw.strip()}`")
        return Step(BARE[raw], source=raw)
    if not isinstance(raw, dict):
        raise TestFileError(f"Step must be an action word or a mapping, got {raw!r}")
    unknown = set(raw) - set(BARE) - ACTIONS - set(CHECKS) - OPTIONS
    if unknown:
        raise TestFileError(f"Step {raw!r} has unknown keys: {', '.join(sorted(map(str, unknown)))}")
    actions = [k for k in raw if k in BARE or k in ACTIONS]
    if len(actions) > 1:
        raise TestFileError(f"Step {raw!r} has more than one action ({', '.join(actions)}); split it into two steps")
    checks = _checks(raw)
    opts = {k: v for k, v in raw.items() if k in OPTIONS}
    if "text" in opts:
        raise TestFileError("`text` goes inside type: `type: {text: hello, into: Email}`")
    if not actions:
        if not checks:
            raise TestFileError(f"Step {raw!r} has no action or check")
        return Step(None, checks, _check_options(None, opts, True), raw)
    kind = actions[0]
    value = _unpack_type(raw[kind], opts) if kind == "type" else raw[kind]
    timeout = _check_options(kind, opts, bool(checks))
    return Step(_action(kind, value, opts), checks, timeout, raw)


def _checks(raw: Mapping[str, Any]) -> tuple[Check, ...]:
    """A step's checks, in the order written; each check key takes one text or a list of them."""
    checks: list[Check] = []
    for k in raw:
        if k in CHECKS:
            values = raw[k] if isinstance(raw[k], list) else [raw[k]]
            if not values:
                raise TestFileError(f"'{k}' needs at least one value")
            checks += [CHECKS[k](_text(v, f"'{k}'")) for v in values]
    return tuple(checks)


def _unpack_type(value: object, opts: dict[str, Any]) -> object:
    """`type: {text: .., into: ..}` into the text, with `into` moved to the options; any other value as it is."""
    if not isinstance(value, dict):
        return value
    bad = set(value) - {"text", "into"}
    if bad:
        raise TestFileError(f"type has unknown keys: {', '.join(sorted(map(str, bad)))} (it takes text and into)")
    if "text" not in value:
        raise TestFileError("type needs `text:`: `type: {text: hello, into: Email}`")
    if "into" in opts:
        raise TestFileError("`into` is given twice; put it inside type: {text: .., into: ..}")
    if "into" in value:
        opts["into"] = value["into"]
    return value["text"]


# --- the file ------------------------------------------------------------------------------------------------------

def _apps(raw: object, base: Path) -> dict[Platform, Path]:
    if raw is None:
        raise TestFileError("Missing `app:` (path to .apk/.aab/.app/.zip/.ipa, or {android: ..., ios: ...})")
    pairs = raw.items() if isinstance(raw, dict) else [(None, raw)]
    apps: dict[Platform, Path] = {}
    for plat, p in pairs:
        if plat not in (None, "android", "ios"):
            raise TestFileError(f"`app` keys must be android and/or ios, got {plat!r}")
        path = (base / _text(p, "app")).resolve()
        detected = platform_of(path)
        if plat and plat != detected:
            raise TestFileError(f"app.{plat} points at a {detected} build: {path.name}")
        apps[detected] = path
    return apps


def _devices(raw: object, apps: Mapping[Platform, Path],
             variables: Mapping[str, str]) -> dict[Platform, tuple[str, ...]]:
    """Which device(s) each platform runs on. Several devices share the tests and run at the same time."""
    example = "{android: Pixel 4a, ios: iPhone 17 Pro}"
    if raw is None:
        raise TestFileError(f"Missing `device:`. Name the device for each platform in `app:`, e.g. {example}")
    if not isinstance(raw, dict):
        raise TestFileError(f"`device` must name a device per platform, e.g. {example}")
    unknown = set(raw) - {"android", "ios"}
    if unknown:
        raise TestFileError(f"`device` keys must be android and/or ios, got {', '.join(sorted(map(str, unknown)))}")
    devices: dict[Platform, tuple[str, ...]] = {}
    for plat, names in raw.items():
        platform = Platform(plat)
        if platform not in apps:
            raise TestFileError(f"`device` names an {plat} device, but `app` has no {plat} build")
        listed = names if isinstance(names, list) else [names]
        if not listed:
            raise TestFileError(f"device.{plat} needs at least one device")
        devices[platform] = tuple(fill(_text(n, f"device.{plat}"), variables) for n in listed)
        if len(set(devices[platform])) != len(devices[platform]):
            raise TestFileError(f"device.{plat} lists a device twice")
    missing = [p.value for p in apps if p not in devices]
    if missing:
        raise TestFileError(f"`device` has no {' or '.join(missing)} device, "
                            f"but `app` has an {' and '.join(missing)} build")
    return devices


def _check_uses(tests: list[Test]) -> None:
    """Every `use:` names a test, and tests don't use each other in a loop."""
    by_name = {t.name: t for t in tests}
    for t in tests:
        for step in t.steps:
            if isinstance(step.action, Use) and step.action.test not in by_name:
                raise TestFileError(f"Test '{t.name}' uses '{step.action.test}', but no test has that name")

    def visit(t: Test, path: list[str]) -> None:
        if t.name in path:
            raise TestFileError("Tests use each other in a loop: " + " -> ".join([*path, t.name]))
        for step in t.steps:
            if isinstance(step.action, Use):
                visit(by_name[step.action.test], [*path, t.name])

    for t in tests:
        visit(t, [])


def _tests(raw: object, where: str, problems: list[str]) -> list[Test]:
    """The tests under `tests:`. Each bad test adds its problem to `problems`, so all are reported."""
    if not isinstance(raw, list) or not raw:
        problems.append(f"No tests found under `tests:` in {where}")
        return []
    tests = []
    for i, t in enumerate(raw, 1):
        try:
            tests.append(_test(t, i, where))
        except TestFileError as e:
            problems.append(str(e))
    return tests


def _test(t: object, i: int, where: str) -> Test:
    if not isinstance(t, dict) or "name" not in t or "steps" not in t or "fresh" not in t:
        raise TestFileError(f"Test #{i} in {where} needs `name`, `fresh` (true: start from a clean install, "
                            "false: carry on from the previous test) and `steps`")
    name = _text(t["name"], f"Test #{i} in {where}: name")
    unknown = set(t) - {"name", "steps", "fresh"}
    if unknown:
        raise TestFileError(f"Test '{name}' has unknown keys: {', '.join(sorted(map(str, unknown)))}")
    if not isinstance(t["steps"], list) or not t["steps"]:
        raise TestFileError(f"Test '{name}' needs at least one step")
    steps = []
    for n, raw in enumerate(t["steps"], 1):
        try:
            steps.append(parse_step(raw))
        except TestFileError as e:
            raise TestFileError(f"Test '{name}', step {n}: {e}") from None
    return Test(name, _on_off(t["fresh"], f"Test '{name}': fresh"), tuple(steps))


def read_yaml(path: Path) -> dict[str, Any]:
    """A YAML file that must be a mapping.

    Raises:
        TestFileError: It's missing, isn't valid YAML, or isn't a mapping.
    """
    try:
        data = yaml.safe_load(path.read_text())
    except FileNotFoundError:
        raise TestFileError(f"Test file not found: {path}") from None
    except yaml.YAMLError as e:
        hint = ""
        if "${" in str(e):
            hint = ('\nA value starting with ${ must be quoted inside { } or [ ]: {android: "${PHONE}"}, '
                    'not {android: ${PHONE}}')
        raise TestFileError(f"{path.name} is not valid YAML: {e}{hint}") from None
    if not isinstance(data, dict):
        raise TestFileError(f"{path.name} must be a YAML mapping")
    return data


def _included(data: Mapping[str, Any], path: Path, chain: tuple[Path, ...]) -> list[tuple[Path, dict[str, Any]]]:
    """The library files a test file includes (and those include), in order, each once."""
    raw = data.get("include") or []
    files = raw if isinstance(raw, list) else [raw]
    found: list[tuple[Path, dict[str, Any]]] = []
    for entry in files:
        lib = (path.parent / _text(entry, f"include in {path.name}")).resolve()
        if lib in chain:
            raise TestFileError("Files include each other in a loop: " + " -> ".join(p.name for p in (*chain, lib)))
        lib_data = read_yaml(lib)
        unknown = set(lib_data) - {"include", "tests"}
        if unknown:
            raise TestFileError(f"{lib.name} is included, so it can only have `include` and `tests` "
                                f"(found {', '.join(sorted(map(str, unknown)))})")
        for nested in [*_included(lib_data, lib, (*chain, lib)), (lib, lib_data)]:
            if nested[0] not in (p for p, _ in found):
                found.append(nested)
    return found


def _strings(value: object) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from _strings(k)
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


def _variables(documents: list[Mapping[str, Any]], env: Mapping[str, str]) -> dict[str, str]:
    names = sorted({name for doc in documents for text in _strings(doc) for name in VARIABLE.findall(text)})
    missing = [n for n in names if n not in env]
    if missing:
        raise TestFileError(f"Not set: {', '.join('${' + n + '}' for n in missing)}. "
                            "Add them to .env next to the test file, or to the environment (e.g. CI secrets)")
    return {n: env[n] for n in names}


def _fill_all(value: object, variables: Mapping[str, str]) -> object:
    if isinstance(value, str):
        return fill(value, variables)
    if isinstance(value, dict):
        return {k: _fill_all(v, variables) for k, v in value.items()}
    return value


def load(path: str | Path, env: Mapping[str, str]) -> Suite:
    """A test file and the library files it includes, as a `Suite`.

    Args:
        path: The test file.
        env: Where ``${NAME}`` values come from (the environment plus the .env next to the file).

    Raises:
        TestFileError: Anything is wrong. Every problem in the file is reported at once, in file order.
    """
    path = Path(path).resolve()
    data = read_yaml(path)
    if "settings" in data:
        raise TestFileError("`settings` is not part of a test file: each step waits up to 10 seconds, or its own "
                            "`timeout:`; everything else is a fixed rule (see the docs' Test file page)")
    unknown = set(data) - {"app", "device", "tests", "include"}
    if unknown:
        raise TestFileError(f"Unknown top-level keys: {', '.join(sorted(map(str, unknown)))} "
                            "(a test file has app, device, include and tests)")
    libraries = _included(data, path, (path,))
    variables = _variables([data] + [d for _, d in libraries], env)
    problems: list[str] = []

    apps: dict[Platform, Path] = {}
    devices: dict[Platform, tuple[str, ...]] = {}
    try:
        apps = _apps(_fill_all(data.get("app"), variables), path.parent)
        devices = _devices(data.get("device"), apps, variables)
    except TestFileError as e:
        problems.append(str(e))
    tests = _tests(data.get("tests"), path.name, problems)
    shared = [t for lib, d in libraries for t in _tests(d.get("tests"), lib.name, problems)]
    names = [t.name for t in tests + shared]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        problems.append(f"Test names must be unique (across included files too): {', '.join(dupes)}")
    if not problems:
        try:
            _check_uses(tests + shared)
        except TestFileError as e:
            problems.append(str(e))
    if len(problems) == 1:
        raise TestFileError(problems[0])
    if problems:
        raise TestFileError(f"{path.name} has {len(problems)} problems:\n" + "\n".join(f"  - {p}" for p in problems))
    return Suite(path, apps, devices, tuple(tests), {t.name: t for t in tests + shared}, variables,
                 tuple(lib for lib, _ in libraries))


def is_test_file(path: Path) -> bool:
    """A file with `app:` is a test file to run; one without is a library other files include."""
    try:
        return "app" in read_yaml(path)
    except TestFileError:
        return True  # run it, so load() says what is wrong with it
