"""Test files: YAML into a `Suite`, checked completely before any device work.

A bad test file fails at once with every problem and its fix, never halfway through a run. Nothing is assumed:
every value a run uses is written in the file (or the .env next to it), or is a documented default setting;
anything missing, misspelled or of the wrong type is an error.
"""

from __future__ import annotations

import re
from collections.abc import Generator, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path

import yaml

from jevtest.adapters.shapes import USER_TEXT, is_list, is_mapping
from jevtest.domain.failures import TestFileError
from jevtest.domain.kinds import Platform
from jevtest.domain.settings import DEFAULTS, REMOVED, STEP_SETTINGS, Settings
from jevtest.domain.steps import Step, Suite, Test, Use
from jevtest.domain.variables import VARIABLE, fill

from .steps import parse_step
from .values import coherent, model, on_off, setting, text

Document = Mapping[object, object]
"""A YAML file's top-level mapping. YAML keys can be numbers or dates too, so they're checked, not assumed."""

ANDROID_EXT = frozenset({".apk", ".aab"})
IOS_EXT = frozenset({".app", ".zip", ".ipa"})
TOP_LEVEL = ("app", "device", "settings", "include", "tests")
LIBRARY_TOP_LEVEL = frozenset({"include", "tests"})
TEST_KEYS = frozenset({"name", "fresh", "steps"})
PLATFORMS = frozenset(p.value for p in Platform)
DEVICE_EXAMPLE = "{android: Pixel 4a, ios: iPhone 17 Pro}"


class Problems:
    """Every problem found in a file, in file order, so they're all reported at once."""

    def __init__(self) -> None:
        self._found: list[str] = []

    def add(self, problem: str) -> None:
        """Note a problem."""
        self._found.append(problem)

    @contextmanager
    def collect(self, prefix: str = "") -> Generator[None]:
        """Note a `TestFileError` raised inside the block, instead of stopping at it."""
        try:
            yield
        except TestFileError as e:
            self.add(f"{prefix}{e}")

    def __len__(self) -> int:
        return len(self._found)

    def check(self, file: str) -> None:
        """Raise every problem noted so far, if there are any.

        Raises:
            TestFileError: With the one problem, or a numbered list of them.
        """
        if len(self._found) == 1:
            raise TestFileError(self._found[0])
        if self._found:
            listed = "\n".join(f"  - {p}" for p in self._found)
            raise TestFileError(f"{file} has {len(self._found)} problems:\n{listed}")


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


def _bare_word_hint(path: Path, error: yaml.YAMLError) -> str:
    """A hint for `- launch` with checks under it: YAML needs `- launch:` there."""
    if not isinstance(error, yaml.MarkedYAMLError) or error.problem != "mapping values are not allowed here":
        return ""
    line: int = error.problem_mark.line if error.problem_mark else 0
    if line < 1:
        return ""
    word = re.fullmatch(r"-\s+([a-z_]+)", path.read_text(encoding=USER_TEXT).splitlines()[line - 1].strip())
    if word is None:
        return ""
    return f"\nOn line {line}, a step with checks under it needs a colon after its action: `- {word[1]}:`"


def read_yaml(path: Path) -> Document:
    """A YAML file that must be a mapping.

    Raises:
        TestFileError: It's missing, isn't valid YAML, or isn't a mapping.
    """
    try:
        data: object = yaml.safe_load(path.read_text(encoding=USER_TEXT))
    except FileNotFoundError:
        raise TestFileError(f"Test file not found: {path}") from None
    except UnicodeDecodeError:
        raise TestFileError(f"{path.name} isn't UTF-8 text: save it as UTF-8") from None
    except yaml.YAMLError as e:
        hint = _bare_word_hint(path, e)
        if not hint and re.search(r"[{\[][^\n]*\$\{", str(e)):
            hint = (
                '\nA value starting with ${ must be quoted inside { } or [ ]: {android: "${PHONE}"}, '
                "not {android: ${PHONE}}"
            )
        raise TestFileError(f"{path.name} is not valid YAML: {e}{hint}") from None
    if not is_mapping(data):
        raise TestFileError(f"{path.name} must be a YAML mapping")
    return data


def is_test_file(path: Path) -> bool:
    """A file with `app:` is a test file to run; one without is a library other files include."""
    try:
        return "app" in read_yaml(path)
    except TestFileError:
        return True  # run it, so load() says what is wrong with it


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
    unknown = data.keys() - set(TOP_LEVEL)
    if unknown:
        raise TestFileError(f"Unknown top-level keys: {_names(unknown)} (a test file has {', '.join(TOP_LEVEL)})")
    libraries = _included(data, path, (path,))
    variables = _variables(path, [data, *libraries.values()], env)
    problems = Problems()
    apps: dict[Platform, Path] = {}
    devices: dict[Platform, tuple[str, ...]] = {}
    with problems.collect():
        apps = _apps(_fill_all(data.get("app"), variables), path.parent)
        devices = _devices(data.get("device"), apps, variables)
    settings = _settings(data.get("settings"), problems)
    tests = _tests(data.get("tests"), path.name, settings, problems)
    shared = [t for lib, doc in libraries.items() for t in _tests(doc.get("tests"), lib.name, settings, problems)]
    _check_names(tests + shared, problems)
    problems.check(path.name)
    return Suite(
        path, apps, devices, tuple(tests), {t.name: t for t in tests + shared}, variables, tuple(libraries), settings
    )


def _names(keys: Iterable[object]) -> str:
    return ", ".join(sorted(str(k) for k in keys))


# --- app, device, settings ---------------------------------------------------------------------------------------


def _apps(raw: object, base: Path) -> dict[Platform, Path]:
    if raw is None:
        raise TestFileError("Missing `app:` (path to .apk/.aab/.app/.zip/.ipa, or {android: ..., ios: ...})")
    pairs = raw.items() if is_mapping(raw) else [(None, raw)]
    apps: dict[Platform, Path] = {}
    for named, value in pairs:
        if named is not None and named not in PLATFORMS:
            raise TestFileError(f"`app` keys must be android and/or ios, got {named!r}")
        path = (base / text(value, "app")).resolve()
        detected = platform_of(path)
        if named is not None and named != detected:
            build = "an Android" if detected is Platform.ANDROID else "an iOS"
            raise TestFileError(f"app.{named} points at {path.name}, which is {build} build")
        apps[detected] = path
    return apps


def _devices(
    raw: object, apps: Mapping[Platform, Path], variables: Mapping[str, str]
) -> dict[Platform, tuple[str, ...]]:
    """Which device(s) each platform runs on. Several devices share the tests and run at the same time."""
    if raw is None:
        raise TestFileError(f"Missing `device:`. Name the device for each platform in `app:`, e.g. {DEVICE_EXAMPLE}")
    if not is_mapping(raw):
        raise TestFileError(f"`device` must name a device per platform, e.g. {DEVICE_EXAMPLE}")
    unknown = raw.keys() - PLATFORMS
    if unknown:
        raise TestFileError(f"`device` keys must be android and/or ios, got {_names(unknown)}")
    named = {key: names for key, names in raw.items() if isinstance(key, str)}  # every key, now known to be text
    devices = {Platform(key): _device_names(key, names, apps, variables) for key, names in named.items()}
    missing = [p.value for p in apps if p not in devices]
    if missing:
        raise TestFileError(
            f"`device` has no {' or '.join(missing)} device, but `app` has an {' and '.join(missing)} build"
        )
    return devices


def _device_names(
    key: str, names: object, apps: Mapping[Platform, Path], variables: Mapping[str, str]
) -> tuple[str, ...]:
    if Platform(key) not in apps:
        raise TestFileError(f"`device` names an {key} device, but `app` has no {key} build")
    listed = names if is_list(names) else [names]
    if not listed:
        raise TestFileError(f"device.{key} needs at least one device")
    found = tuple(fill(text(n, f"device.{key}"), variables) for n in listed)
    if len(set(found)) != len(found):
        raise TestFileError(f"device.{key} lists a device twice")
    return found


def _settings(raw: object, problems: Problems) -> Settings:
    """The file's `settings:` block; anything it leaves out keeps its default. Each bad setting is a problem."""
    if raw is None:
        return DEFAULTS
    allowed = ["model", *sorted(STEP_SETTINGS)]
    if not is_mapping(raw) or not raw:
        problems.add(
            f"`settings` must be a mapping of {', '.join(allowed)}; got {raw!r} (leave it out to use the defaults)"
        )
        return DEFAULTS
    settings, changes = DEFAULTS, dict[str, float]()
    for name, value in raw.items():
        with problems.collect():
            if isinstance(name, str) and name in REMOVED:
                raise TestFileError(REMOVED[name])
            if not isinstance(name, str) or name not in allowed:
                raise TestFileError(f"`settings` has an unknown key: {name} (it takes {', '.join(allowed)})")
            if name == "model":
                settings = Settings(model=model(value))
            else:
                changes[name] = setting(name, value)
    return coherent(settings.changed(changes))


# --- tests -------------------------------------------------------------------------------------------------------


def _tests(raw: object, where: str, settings: Settings, problems: Problems) -> list[Test]:
    """The tests under `tests:`. Every bad test and bad step is a problem, so all are reported."""
    if not is_list(raw) or not raw:
        problems.add(f"No tests found under `tests:` in {where}")
        return []
    tests: list[Test] = []
    for i, entry in enumerate(raw, 1):
        with problems.collect():
            test = _test(entry, f"Test #{i} in {where}", settings, problems)
            if test is not None:
                tests.append(test)
    return tests


def _test(raw: object, where: str, settings: Settings, problems: Problems) -> Test | None:
    """One test, or None when a step is bad (each bad step is a problem)."""
    if not is_mapping(raw) or not raw.keys() >= TEST_KEYS:
        raise TestFileError(
            f"{where} needs `name`, `fresh` (true: start from a clean install, "
            "false: carry on from the previous test) and `steps`"
        )
    name = text(raw["name"], f"{where}: name")
    unknown = raw.keys() - TEST_KEYS
    if unknown:
        raise TestFileError(f"Test '{name}' has unknown keys: {_names(unknown)}")
    written = raw["steps"]
    if not is_list(written) or not written:
        raise TestFileError(f"Test '{name}' needs at least one step")
    before = len(problems)
    steps: list[Step] = []
    for n, step in enumerate(written, 1):
        with problems.collect(f"Test '{name}', step {n}: "):
            steps.append(parse_step(step, settings))
    if len(problems) > before:
        return None
    return Test(name, on_off(raw["fresh"], f"Test '{name}': fresh"), tuple(steps))


def _check_names(tests: Sequence[Test], problems: Problems) -> None:
    """Test names are unique; every `use:` names a test; tests don't use each other in a loop."""
    names = [t.name for t in tests]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        problems.add(f"Test names must be unique (across included files too): {', '.join(dupes)}")
        return
    if not problems:
        with problems.collect():
            _check_uses({t.name: t for t in tests})


def _check_uses(tests: Mapping[str, Test]) -> None:
    for t in tests.values():
        for used in _uses(t):
            if used not in tests:
                raise TestFileError(f"Test '{t.name}' uses '{used}', but no test has that name")

    def visit(t: Test, path: tuple[str, ...]) -> None:
        if t.name in path:
            raise TestFileError("Tests use each other in a loop: " + " -> ".join((*path, t.name)))
        for used in _uses(t):
            visit(tests[used], (*path, t.name))

    for t in tests.values():
        visit(t, ())


def _uses(test: Test) -> list[str]:
    return [step.action.test for step in test.steps if isinstance(step.action, Use)]


# --- include and ${NAME} -----------------------------------------------------------------------------------------


def _included(data: Document, path: Path, chain: tuple[Path, ...]) -> dict[Path, Document]:
    """The library files a test file includes (and those include), in order, each once."""
    raw = data.get("include")
    entries: list[object] = raw if is_list(raw) else ([] if raw is None else [raw])
    found: dict[Path, Document] = {}
    for entry in entries:
        lib = (path.parent / text(entry, f"include in {path.name}")).resolve()
        if lib in chain:
            raise TestFileError("Files include each other in a loop: " + " -> ".join(p.name for p in (*chain, lib)))
        if not lib.is_file():
            raise TestFileError(f"{path.name} includes {entry}, which isn't there: {lib}")
        doc = read_yaml(lib)
        unknown = doc.keys() - LIBRARY_TOP_LEVEL
        if unknown:
            raise TestFileError(
                f"{lib.name} is included, so it can only have `include` and `tests` "
                f"(found {_names(unknown)}); its tests run with the settings of the file being run"
            )
        for nested, nested_doc in [*_included(doc, lib, (*chain, lib)).items(), (lib, doc)]:
            found.setdefault(nested, nested_doc)
    return found


def _strings(value: object) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif is_mapping(value):
        for k, v in value.items():
            yield from _strings(k)
            yield from _strings(v)
    elif is_list(value):
        for v in value:
            yield from _strings(v)


def _variables(path: Path, documents: Sequence[Document], env: Mapping[str, str]) -> dict[str, str]:
    names = sorted({name for doc in documents for s in _strings(doc) for name in VARIABLE.findall(s)})
    missing = [n for n in names if n not in env]
    if missing:
        one = len(missing) == 1
        raise TestFileError(
            f"{path.name} uses {', '.join('${' + n + '}' for n in missing)}, which {'is' if one else 'are'} not set. "
            f"Add {'it' if one else 'them'} to {path.parent / '.env'} (the .env next to this test file), "
            "or to the environment (e.g. CI secrets)"
        )
    return {n: env[n] for n in names}


def _fill_all(value: object, variables: Mapping[str, str]) -> object:
    if isinstance(value, str):
        return fill(value, variables)
    if is_mapping(value):
        return {k: _fill_all(v, variables) for k, v in value.items()}
    return value
