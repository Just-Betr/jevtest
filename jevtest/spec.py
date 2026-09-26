"""Loads and validates a jevtest YAML file.

Everything is checked here, before any device work: a bad test file fails
immediately with the step and the reason, never halfway through a run.

A step is one action, then optional checks on the result:

    - do: Sign in with email "a@b.c" and password "pw"     <- action (Jev works it out)
      expect: The home screen greets the user              <- check (Jev judges)
      see: Welcome                                         <- check (exact text)
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, fields
from pathlib import Path

import yaml

ANDROID_EXT = {".apk", ".aab"}
IOS_EXT = {".app", ".zip", ".ipa"}

# Actions with no value: a bare string ("- back") or a key ("- back:").
BARE = {"launch", "stop", "restart", "clear_data", "reinstall", "back", "home", "hide_keyboard"}
TEXT_ACTIONS = {"do", "use", "tap", "double_tap", "long_press", "clear", "scroll_to", "key",
                "open_url", "grant", "screenshot"}
ACTIONS = TEXT_ACTIONS | {"wait", "background", "scroll", "swipe", "type", "rotate", "location",
                          "dark_mode", "network"}
CHECKS = {"expect", "see", "not_see"}
OPTIONS = {"timeout", "max_actions", "max_scrolls", "target", "direction", "text", "into"}

DIRECTIONS = ("up", "down", "left", "right")
ORIENTATIONS = ("portrait", "landscape", "landscape_right", "portrait_upside_down")
ON = {"on", "true", "yes", "enable", "enabled", "dark"}
OFF = {"off", "false", "no", "disable", "disabled", "light"}


VARIABLE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class SpecError(ValueError):
    pass


def fill(text: str, variables: dict[str, str]) -> str:
    """Put variable values into `text` (for typing, comparing, locating): never into logs or Jev's goals."""
    return VARIABLE.sub(lambda m: variables[m.group(1)], text)


@dataclass
class Settings:
    model: str = "typesafe/jev-1.13"
    max_actions: int = 8       # Jev actions allowed per `do:` step
    timeout: float = 10.0      # seconds a check / element lookup keeps retrying
    settle: float = 3.0        # most seconds to wait for the UI to go idle after an action
    threshold: float = 0.5     # Jev yes-probability an `expect:` needs to pass
    ios_team: str = ""         # Apple team to sign with on a real iPhone; only needed with several teams


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
    fresh: bool = True


@dataclass
class Spec:
    path: Path
    apps: dict[str, Path]      # platform -> build, in the order the file lists them
    tests: list[Test]
    settings: Settings
    devices: dict[str, list[str]] = field(default_factory=dict)  # platform -> device names; none = the running one
    variables: dict[str, str] = field(default_factory=dict)      # ${NAME} -> value, from the environment / .env


def platform_of(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in ANDROID_EXT:
        return "android"
    if ext in IOS_EXT:
        return "ios"
    raise SpecError(f"Unknown app type '{path.name}' (use .apk/.aab for Android, .app/.zip/.ipa for iOS)")


# --- value parsing -------------------------------------------------------------

def _number(value, what: str, minimum: float = 0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        try:
            value = float(str(value).strip())
        except ValueError:
            raise SpecError(f"{what} must be a number, got {value!r}") from None
    if value != value or value in (float("inf"), float("-inf")):
        raise SpecError(f"{what} must be a finite number, got {value}")
    if value < minimum:
        raise SpecError(f"{what} must be at least {minimum:g}, got {value:g}")
    return float(value)


def _count(value, what: str) -> int:
    n = _number(value, what, minimum=1)
    if n != int(n):
        raise SpecError(f"{what} must be a whole number, got {n:g}")
    return int(n)


def _text(value, what: str) -> str:
    if value is None or isinstance(value, (bool, dict, list)) or str(value).strip() == "":
        raise SpecError(f"{what} needs a text value")
    return str(value).strip()


def _choice(value, allowed: tuple, what: str) -> str:
    v = str(value).strip().lower()
    if v not in allowed:
        raise SpecError(f"{what} must be one of {', '.join(allowed)}; got {value!r}")
    return v


def _on_off(value, what: str) -> bool:
    if isinstance(value, bool):
        return value
    v = str(value).strip().lower()
    if v in ON:
        return True
    if v in OFF:
        return False
    raise SpecError(f"{what} must be on or off, got {value!r}")


def _location(value) -> tuple[float, float]:
    parts = value if isinstance(value, (list, tuple)) else str(value).split(",")
    if len(parts) != 2:
        raise SpecError(f"location must be 'lat,lon', got {value!r}")
    lat, lon = (_number(p, "location", minimum=-180) for p in parts)
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise SpecError(f"location {lat:g},{lon:g} is out of range")
    return lat, lon


def _value(kind: str, value, opts: dict):
    """Validate and normalize one action's value (and its options)."""
    what = f"'{kind}'"
    if kind in BARE:
        return None
    if kind in TEXT_ACTIONS:
        return _text(value, what)
    if kind in ("wait", "background"):
        return _number(value, what)
    if kind == "scroll":
        return _choice(value, DIRECTIONS, what)
    if kind == "swipe":
        direction = value if value is not None else opts.pop("direction", None)
        return _choice(direction, DIRECTIONS, what)
    if kind == "type":
        text = opts.pop("text", value)
        if text is None or isinstance(text, (bool, dict, list)):
            raise SpecError("'type' needs text: `type: hello` or `type: {text: hello, into: Email}`")
        return str(text)
    if kind == "rotate":
        return _choice(value, ORIENTATIONS, what)
    if kind == "location":
        return _location(value)
    return _on_off(value, what)  # dark_mode, network


def _options(opts: dict) -> dict:
    out = dict(opts)
    if "timeout" in out:
        out["timeout"] = _number(out["timeout"], "timeout")
    for k in ("max_actions", "max_scrolls"):
        if k in out:
            out[k] = _count(out[k], k)
    if "direction" in out:
        out["direction"] = _choice(out["direction"], DIRECTIONS, "direction")
    for k in ("target", "into", "text"):
        if k in out:
            out[k] = _text(out[k], k)
    return out


def parse_step(raw) -> Step:
    if isinstance(raw, str):
        word = raw.strip()
        if not word:
            raise SpecError("Empty step")
        return Step(word, raw=raw) if word in BARE else Step("do", word, raw=raw)
    if not isinstance(raw, dict):
        raise SpecError(f"Step must be a string or a mapping, got {raw!r}")

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
    if not actions:
        if not checks:
            raise SpecError(f"Step {raw!r} has no action or check")
        return Step(None, opts=_options(opts), checks=checks, raw=raw)

    kind = actions[0]
    value = raw[kind]
    if isinstance(value, dict):  # `type: {text: .., into: ..}` form
        bad = set(value) - OPTIONS
        if bad:
            raise SpecError(f"Step {raw!r} has unknown keys: {', '.join(sorted(map(str, bad)))}")
        opts.update(value)
        value = None
    value = _value(kind, value, opts)
    return Step(kind, value, _options(opts), checks, raw)


# --- file ----------------------------------------------------------------------

def _settings(raw) -> Settings:
    if raw is None:
        return Settings()
    if not isinstance(raw, dict):
        raise SpecError("`settings` must be a mapping")
    unknown = set(raw) - {f.name for f in fields(Settings)}
    if unknown:
        raise SpecError(f"Unknown settings: {', '.join(sorted(map(str, unknown)))}")
    s = Settings()
    if "model" in raw:
        s.model = _text(raw["model"], "settings.model")
    if "ios_team" in raw:
        s.ios_team = _text(raw["ios_team"], "settings.ios_team")
    if "max_actions" in raw:
        s.max_actions = _count(raw["max_actions"], "settings.max_actions")
    for name in ("timeout", "settle"):
        if name in raw:
            setattr(s, name, _number(raw[name], f"settings.{name}"))
    if "threshold" in raw:
        s.threshold = _number(raw["threshold"], "settings.threshold")
        if not 0 < s.threshold < 1:
            raise SpecError("settings.threshold must be between 0 and 1")
    return s


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
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise SpecError("`device` must name a device per platform, e.g. {android: Pixel 4a, ios: iPhone 17 Pro}")
    unknown = set(raw) - {"android", "ios"}
    if unknown:
        raise SpecError(f"`device` keys must be android and/or ios, got {', '.join(sorted(map(str, unknown)))}")
    devices = {}
    for plat, names in raw.items():
        if plat not in apps:
            raise SpecError(f"`device` names a {plat} device, but `app` has no {plat} build")
        names = names if isinstance(names, list) else [names]
        if not names:
            raise SpecError(f"device.{plat} needs at least one device")
        devices[plat] = [fill(_text(n, f"device.{plat}"), variables) for n in names]
        if len(set(devices[plat])) != len(devices[plat]):
            raise SpecError(f"device.{plat} lists a device twice")
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


def _tests(raw, where: str) -> list[Test]:
    if not isinstance(raw, list) or not raw:
        raise SpecError(f"No tests found under `tests:` in {where}")
    tests = []
    for i, t in enumerate(raw, 1):
        if not isinstance(t, dict) or not t.get("name") or "steps" not in t:
            raise SpecError(f"Test #{i} in {where} needs a `name` and `steps`")
        name = str(t["name"])
        unknown = set(t) - {"name", "steps", "fresh"}
        if unknown:
            raise SpecError(f"Test '{name}' has unknown keys: {', '.join(sorted(map(str, unknown)))}")
        if not isinstance(t["steps"], list) or not t["steps"]:
            raise SpecError(f"Test '{name}' needs at least one step")
        try:
            steps = [parse_step(s) for s in t["steps"]]
        except SpecError as e:
            raise SpecError(f"Test '{name}': {e}") from None
        tests.append(Test(name=name, steps=steps, fresh=_on_off(t.get("fresh", True), "fresh")))
    return tests


def _read(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text())
    except FileNotFoundError:
        raise SpecError(f"Test file not found: {path}") from None
    except yaml.YAMLError as e:
        raise SpecError(f"{path.name} is not valid YAML: {e}") from None
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


def load(path: str | Path, env=None) -> Spec:
    """Load a test file, plus the library files it includes. `env` supplies ${NAME} values."""
    path = Path(path).resolve()
    data = _read(path)
    unknown = set(data) - {"app", "device", "settings", "tests", "include"}
    if unknown:
        raise SpecError(f"Unknown top-level keys: {', '.join(sorted(map(str, unknown)))}")
    libraries = _included(data, path, (path,))
    variables = _variables([data] + [d for _, d in libraries], os.environ if env is None else env)
    apps = _apps(_fill_all(data.get("app"), variables), path.parent)
    tests = _tests(data.get("tests"), path.name)
    shared = [t for lib, d in libraries for t in _tests(d.get("tests"), lib.name)]
    names = [t.name for t in tests + shared]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        raise SpecError(f"Test names must be unique (across included files too): {', '.join(dupes)}")
    _link_uses(tests + shared)
    return Spec(path=path, apps=apps, tests=tests, settings=_settings(_fill_all(data.get("settings"), variables)),
                devices=_devices(data.get("device"), apps, variables), variables=variables)


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
