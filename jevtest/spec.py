"""Loads and validates a jevtest YAML file.

Everything is checked here, before any device work: a bad test file fails
immediately with the step and the reason, never halfway through a run.

Nothing is assumed. Every value a run uses is written in the file (or in the
.env next to it); anything missing, misspelled or of the wrong type is an error.

A step is one action, then optional checks on the result:

    - do: Sign in with email "a@b.c" and password "pw"     <- action (Jev works it out)
      expect: The home screen greets the user              <- check (Jev judges)
      see: Welcome                                         <- check (exact text)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ANDROID_EXT = {".apk", ".aab"}
IOS_EXT = {".app", ".zip", ".ipa"}

# Actions with no value: written as a bare word ("- back") or a key ("- back:").
BARE = {"launch", "stop", "restart", "clear_data", "reinstall", "back", "home", "hide_keyboard"}
TEXT_ACTIONS = {"do", "use", "tap", "double_tap", "long_press", "clear", "scroll_to", "key",
                "open_url", "grant", "screenshot"}
ACTIONS = TEXT_ACTIONS | {"wait", "background", "scroll", "swipe", "type", "rotate", "location",
                          "dark_mode", "network"}
CHECKS = {"expect", "see", "not_see"}
OPTIONS = {"timeout", "target", "direction", "text", "into"}
# Which actions each option belongs to. `timeout` is for steps that wait for something (below).
OPTION_ACTIONS = {"direction": {"scroll_to"}, "target": {"swipe"}, "into": {"type"}, "text": {"type"}}
LOCATING = {"tap", "double_tap", "long_press", "clear"}  # find an element first, so they can time out

DIRECTIONS = ("up", "down", "left", "right")
ORIENTATIONS = ("portrait", "landscape", "landscape_right", "portrait_upside_down")


VARIABLE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class SpecError(ValueError):
    pass


def fill(text: str, variables: dict[str, str]) -> str:
    """Put variable values into `text` (for typing, comparing, locating): never into logs or Jev's goals."""
    return VARIABLE.sub(lambda m: variables[m.group(1)], text)


# jevtest's fixed rules. They are part of what jevtest is, not settings: the docs list them and every run
# prints the model. A step can wait longer with `timeout:`.
MODEL = "typesafe/jev-1.13"  # the Jev version this release of jevtest is built and tested against
TIMEOUT = 10.0               # seconds a step waits for what it looks for (checks, elements)
MAX_ACTIONS = 10             # actions a `do:` goal may take; a bigger goal is split into steps
MAX_SCROLLS = 50             # scrolls a `scroll_to:` may make (it also stops at the end of the content)
THRESHOLD = 0.5              # an `expect:` passes when Jev finds it more likely true than false
SETTLE = 3.0                 # most seconds to wait for the screen to stop changing after an action


@dataclass
class Step:
    kind: str | None                 # the action, or None for a checks-only step
    value: object = None             # normalized: str, float, bool, (lat, lon), ...
    opts: dict = field(default_factory=dict)
    checks: list[tuple[str, str]] = field(default_factory=list)  # (expect|see|not_see, text)
    raw: object = None
    used: Test | None = None         # the test a `use:` step runs

    def title(self) -> str:
        if self.kind is None:
            return ""
        shown = [f"{k}={v!r}" for k, v in self.opts.items() if k != "timeout"]
        head = self.kind if self.value is None else f"{self.kind}: {_show(self.value)}"
        return " ".join([head] + shown)


def _show(value) -> str:
    if isinstance(value, bool):
        return "on" if value else "off"
    if isinstance(value, tuple):
        return ",".join(map(str, value))
    return str(value)


@dataclass
class Test:
    __test__ = False  # not a pytest test class

    name: str
    steps: list[Step]
    fresh: bool  # start from a clean install (true) or carry on from the previous test (false)


@dataclass
class Spec:
    path: Path
    apps: dict[str, Path]      # platform -> build, in the order the file lists them
    tests: list[Test]
    devices: dict[str, list[str]]  # platform -> device names (every platform in apps has at least one)
    variables: dict[str, str]      # ${NAME} -> value, from the .env next to the file / the environment
    includes: list[Path] = field(default_factory=list)  # library files it includes, directly or not


def platform_of(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in ANDROID_EXT:
        return "android"
    if ext in IOS_EXT:
        return "ios"
    raise SpecError(f"Unknown app type '{path.name}' (use .apk/.aab for Android, .app/.zip/.ipa for iOS)")


# --- value parsing -------------------------------------------------------------

def _kind(value) -> str:
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


def _number(value, what: str, minimum: float = 0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        hint = " (remove the quotes)" if isinstance(value, str) and _looks_numeric(value) else ""
        raise SpecError(f"{what} must be a number, got {value!r}{hint}")
    if value != value or value in (float("inf"), float("-inf")):
        raise SpecError(f"{what} must be a finite number, got {value}")
    if value < minimum:
        raise SpecError(f"{what} must be at least {minimum:g}, got {value:g}")
    return float(value)


def _text(value, what: str) -> str:
    if not isinstance(value, str):
        hint = f" (to use {value!r} as text, put it in quotes)" if isinstance(value, (int, float, bool)) else ""
        raise SpecError(f"{what} needs text, got {_kind(value)}{hint}")
    if value.strip() == "" or value != value.strip():
        raise SpecError(f"{what} needs text without leading or trailing spaces, got {value!r}")
    return value


def _choice(value, allowed: tuple, what: str) -> str:
    if value not in allowed:
        raise SpecError(f"{what} must be one of {', '.join(allowed)}; got {value!r}")
    return value


def _on_off(value, what: str) -> bool:
    if not isinstance(value, bool):
        raise SpecError(f"{what} must be on or off (true or false), got {value!r}")
    return value


def _location(value) -> tuple[float, float]:
    if not isinstance(value, list) or len(value) != 2:
        raise SpecError(f"location must be [latitude, longitude], e.g. [37.77, -122.41]; got {value!r}")
    lat, lon = _number(value[0], "latitude", minimum=-90), _number(value[1], "longitude", minimum=-180)
    if lat > 90 or lon > 180:
        raise SpecError(f"location {lat:g},{lon:g} is out of range")
    return lat, lon


def _value(kind: str, value):
    """Validate and normalize one action's value."""
    what = f"'{kind}'"
    if kind in BARE:
        if value is not None:
            raise SpecError(f"{what} takes no value, got {value!r}")
        return None
    if kind in TEXT_ACTIONS:
        return _text(value, what)
    if kind in ("wait", "background"):
        return _number(value, what)
    if kind in ("scroll", "swipe"):
        return _choice(value, DIRECTIONS, what)
    if kind == "type":
        if not isinstance(value, str):
            raise SpecError(f"'type' needs text (`type: hello` or `type: {{text: hello, into: Email}}`), "
                            f"got {_kind(value)}")
        return value  # typed exactly as written, spaces included
    if kind == "rotate":
        return _choice(value, ORIENTATIONS, what)
    if kind == "location":
        return _location(value)
    return _on_off(value, what)  # dark_mode, network


def _options(kind: str | None, opts: dict, has_checks: bool) -> dict:
    """Check each option belongs to this action and has the right type."""
    for k in opts:
        if k == "timeout":
            waits = has_checks or kind in LOCATING or (kind == "type" and "into" in opts) \
                or (kind == "swipe" and "target" in opts)
            if not waits:
                raise SpecError("timeout only applies to a step that finds an element or has checks")
        elif kind not in OPTION_ACTIONS[k]:
            owners = " / ".join(sorted(OPTION_ACTIONS[k]))
            raise SpecError(f"`{k}` belongs to {owners}, not to {kind or 'a checks-only step'}")
    out = dict(opts)
    if "timeout" in out:
        out["timeout"] = _number(out["timeout"], "timeout")
    if "direction" in out:
        out["direction"] = _choice(out["direction"], DIRECTIONS, "direction")
    for k in ("target", "into"):
        if k in out:
            out[k] = _text(out[k], k)
    if kind == "scroll_to" and "direction" not in out:
        raise SpecError("'scroll_to' needs `direction:` (up, down, left or right)")
    return out


def parse_step(raw) -> Step:
    if isinstance(raw, str):
        if not raw.strip():
            raise SpecError("Empty step")
        if raw not in BARE:
            raise SpecError(f"Unknown step {raw!r}. A bare word must be one of {', '.join(sorted(BARE))}; "
                            f"for a plain-English goal write `- do: {raw.strip()}`")
        return Step(raw, raw=raw)
    if not isinstance(raw, dict):
        raise SpecError(f"Step must be an action word or a mapping, got {raw!r}")

    unknown = set(raw) - BARE - ACTIONS - CHECKS - OPTIONS
    if unknown:
        raise SpecError(f"Step {raw!r} has unknown keys: {', '.join(sorted(map(str, unknown)))}")
    actions = [k for k in raw if k in BARE or k in ACTIONS]
    if len(actions) > 1:
        raise SpecError(f"Step {raw!r} has more than one action ({', '.join(actions)}); "
                        "split it into two steps")

    checks = []
    for k in raw:
        if k in CHECKS:
            values = raw[k] if isinstance(raw[k], list) else [raw[k]]
            if not values:
                raise SpecError(f"'{k}' needs at least one value")
            checks += [(k, _text(v, f"'{k}'")) for v in values]

    opts = {k: v for k, v in raw.items() if k in OPTIONS}
    if "text" in opts:
        raise SpecError("`text` goes inside type: `type: {text: hello, into: Email}`")
    if not actions:
        if not checks:
            raise SpecError(f"Step {raw!r} has no action or check")
        return Step(None, opts=_options(None, opts, True), checks=checks, raw=raw)

    kind = actions[0]
    value = raw[kind]
    if kind == "type" and isinstance(value, dict):  # `type: {text: .., into: ..}`
        bad = set(value) - {"text", "into"}
        if bad:
            raise SpecError(f"type has unknown keys: {', '.join(sorted(map(str, bad)))} (it takes text and into)")
        if "text" not in value:
            raise SpecError("type needs `text:`: `type: {text: hello, into: Email}`")
        if "into" in opts:
            raise SpecError("`into` is given twice; put it inside type: {text: .., into: ..}")
        value = dict(value)
        opts.update({k: v for k, v in value.items() if k == "into"})
        value = value["text"]
    return Step(kind, _value(kind, value), _options(kind, opts, bool(checks)), checks, raw)


# --- file ----------------------------------------------------------------------

def _apps(raw, base: Path) -> dict[str, Path]:
    if raw is None:
        raise SpecError("Missing `app:` (path to .apk/.aab/.app/.zip/.ipa, or {android: ..., ios: ...})")
    pairs = raw.items() if isinstance(raw, dict) else [(None, raw)]
    apps = {}
    for plat, p in pairs:
        if plat not in (None, "android", "ios"):
            raise SpecError(f"`app` keys must be android and/or ios, got {plat!r}")
        path = (base / _text(p, "app")).resolve()
        detected = platform_of(path)
        if plat and plat != detected:
            raise SpecError(f"app.{plat} points at a {detected} build: {path.name}")
        apps[detected] = path
    return apps


def _devices(raw, apps: dict[str, Path], variables: dict[str, str]) -> dict[str, list[str]]:
    """Which device(s) each platform runs on. Several devices share the tests and run at the same time."""
    example = "{android: Pixel 4a, ios: iPhone 17 Pro}"
    if raw is None:
        raise SpecError(f"Missing `device:`. Name the device for each platform in `app:`, e.g. {example}")
    if not isinstance(raw, dict):
        raise SpecError(f"`device` must name a device per platform, e.g. {example}")
    unknown = set(raw) - {"android", "ios"}
    if unknown:
        raise SpecError(f"`device` keys must be android and/or ios, got {', '.join(sorted(map(str, unknown)))}")
    devices = {}
    for plat, names in raw.items():
        if plat not in apps:
            raise SpecError(f"`device` names an {plat} device, but `app` has no {plat} build")
        names = names if isinstance(names, list) else [names]
        if not names:
            raise SpecError(f"device.{plat} needs at least one device")
        devices[plat] = [fill(_text(n, f"device.{plat}"), variables) for n in names]
        if len(set(devices[plat])) != len(devices[plat]):
            raise SpecError(f"device.{plat} lists a device twice")
    missing = [p for p in apps if p not in devices]
    if missing:
        raise SpecError(f"`device` has no {' or '.join(missing)} device, "
                        f"but `app` has an {' and '.join(missing)} build")
    return devices


def _link_uses(tests: list[Test]):
    """Point each `use:` step at the test it names, and refuse loops."""
    by_name = {t.name: t for t in tests}
    for t in tests:
        for step in t.steps:
            if step.kind == "use":
                if step.value not in by_name:
                    raise SpecError(f"Test '{t.name}' uses '{step.value}', but no test has that name")
                step.used = by_name[step.value]

    def visit(t: Test, path: list[str]):
        if t.name in path:
            raise SpecError("Tests use each other in a loop: " + " -> ".join(path + [t.name]))
        for step in t.steps:
            if step.used:
                visit(step.used, path + [t.name])

    for t in tests:
        visit(t, [])


def _tests(raw, where: str, problems: list[str]) -> list[Test]:
    """The tests under `tests:`. Each bad test adds its problem to `problems`, so all are reported."""
    if not isinstance(raw, list) or not raw:
        problems.append(f"No tests found under `tests:` in {where}")
        return []
    tests = []
    for i, t in enumerate(raw, 1):
        try:
            tests.append(_test(t, i, where))
        except SpecError as e:
            problems.append(str(e))
    return tests


def _test(t, i: int, where: str) -> Test:
    if not isinstance(t, dict) or "name" not in t or "steps" not in t or "fresh" not in t:
        raise SpecError(f"Test #{i} in {where} needs `name`, `fresh` (true: start from a clean install, "
                        "false: carry on from the previous test) and `steps`")
    name = _text(t["name"], f"Test #{i} in {where}: name")
    unknown = set(t) - {"name", "steps", "fresh"}
    if unknown:
        raise SpecError(f"Test '{name}' has unknown keys: {', '.join(sorted(map(str, unknown)))}")
    if not isinstance(t["steps"], list) or not t["steps"]:
        raise SpecError(f"Test '{name}' needs at least one step")
    steps = []
    for n, raw in enumerate(t["steps"], 1):
        try:
            steps.append(parse_step(raw))
        except SpecError as e:
            raise SpecError(f"Test '{name}', step {n}: {e}") from None
    return Test(name=name, steps=steps, fresh=_on_off(t["fresh"], f"Test '{name}': fresh"))


def _read(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text())
    except FileNotFoundError:
        raise SpecError(f"Test file not found: {path}") from None
    except yaml.YAMLError as e:
        hint = ""
        if "${" in str(e):
            hint = ('\nA value starting with ${ must be quoted inside { } or [ ]: {android: "${PHONE}"}, '
                    'not {android: ${PHONE}}')
        raise SpecError(f"{path.name} is not valid YAML: {e}{hint}") from None
    if not isinstance(data, dict):
        raise SpecError(f"{path.name} must be a YAML mapping")
    return data


def _included(data: dict, path: Path, chain: tuple[Path, ...]) -> list[tuple[Path, dict]]:
    """The library files a test file includes (and those include), in order, each once."""
    raw = data.get("include") or []
    files = raw if isinstance(raw, list) else [raw]
    found: list[tuple[Path, dict]] = []
    for entry in files:
        lib = (path.parent / _text(entry, f"include in {path.name}")).resolve()
        if lib in chain:
            raise SpecError("Files include each other in a loop: " + " -> ".join(p.name for p in (*chain, lib)))
        lib_data = _read(lib)
        unknown = set(lib_data) - {"include", "tests"}
        if unknown:
            raise SpecError(f"{lib.name} is included, so it can only have `include` and `tests` "
                            f"(found {', '.join(sorted(map(str, unknown)))})")
        for nested in [*_included(lib_data, lib, (*chain, lib)), (lib, lib_data)]:
            if nested[0] not in (p for p, _ in found):
                found.append(nested)
    return found


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from _strings(k)
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


def _variables(documents: list[dict], env) -> dict[str, str]:
    names = sorted({name for doc in documents for text in _strings(doc) for name in VARIABLE.findall(text)})
    missing = [n for n in names if n not in env]
    if missing:
        raise SpecError(f"Not set: {', '.join('${' + n + '}' for n in missing)}. "
                        "Add them to .env next to the test file, or to the environment (e.g. CI secrets)")
    return {n: env[n] for n in names}


def load(path: str | Path, env: dict[str, str]) -> Spec:
    """Load a test file, plus the library files it includes. `env` supplies ${NAME} values.
    Every problem in the file is reported at once, in file order."""
    path = Path(path).resolve()
    data = _read(path)
    if "settings" in data:
        raise SpecError("`settings` is not part of a test file: each step waits up to 10 seconds, or its own "
                        "`timeout:`; everything else is a fixed rule (see the docs' Test file page)")
    unknown = set(data) - {"app", "device", "tests", "include"}
    if unknown:
        raise SpecError(f"Unknown top-level keys: {', '.join(sorted(map(str, unknown)))} "
                        "(a test file has app, device, include and tests)")
    libraries = _included(data, path, (path,))
    variables = _variables([data] + [d for _, d in libraries], env)
    problems: list[str] = []

    def section(parse, *args):
        try:
            return parse(*args)
        except SpecError as e:
            problems.append(str(e))
            return None

    apps = section(_apps, _fill_all(data.get("app"), variables), path.parent)
    devices = section(_devices, data.get("device"), apps, variables) if apps else None
    tests = _tests(data.get("tests"), path.name, problems)
    shared = [t for lib, d in libraries for t in _tests(d.get("tests"), lib.name, problems)]
    names = [t.name for t in tests + shared]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        problems.append(f"Test names must be unique (across included files too): {', '.join(dupes)}")
    if not problems:
        section(_link_uses, tests + shared)
    if len(problems) == 1:
        raise SpecError(problems[0])
    if problems:
        raise SpecError(f"{path.name} has {len(problems)} problems:\n" + "\n".join(f"  - {p}" for p in problems))
    return Spec(path=path, apps=apps, tests=tests, devices=devices, variables=variables,
                includes=[lib for lib, _ in libraries])


def _fill_all(value, variables: dict[str, str]):
    if isinstance(value, str):
        return fill(value, variables)
    if isinstance(value, dict):
        return {k: _fill_all(v, variables) for k, v in value.items()}
    return value


def is_test_file(path: Path) -> bool:
    """A file with `app:` is a test file to run; one without is a library other files include."""
    try:
        return "app" in _read(path)
    except SpecError:
        return True  # run it, so load() says what is wrong with it
