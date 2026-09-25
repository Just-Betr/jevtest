"""Loads and validates a jevtest YAML file."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

ANDROID_EXT = {".apk", ".aab"}
IOS_EXT = {".app", ".zip", ".ipa"}

# A step is: one action, then optional checks on the result.
#   - do: Sign in with email "a@b.c" and password "pw"     <- action (Jev works it out)
#     expect: The home screen greets the user              <- check (Jev judges)
#     see: Welcome                                         <- check (exact text)
# Actions that take no value can be a bare string ("- back") or a key ("- back:").
BARE = {"launch", "stop", "restart", "clear_data", "reinstall", "back", "home", "hide_keyboard"}
ACTIONS = {"do", "use", "wait", "key", "scroll", "swipe", "scroll_to", "tap", "double_tap",
           "long_press", "type", "clear", "rotate", "location", "open_url", "screenshot",
           "background", "dark_mode", "grant", "network"}
CHECKS = {"expect", "see", "not_see"}
OPTIONS = {"timeout", "max_actions", "max_scrolls", "target", "direction", "text", "into"}


class SpecError(ValueError):
    pass


@dataclass
class Settings:
    model: str = "~typesafe/jev-latest"
    max_actions: int = 8       # Jev actions allowed per goal step
    timeout: float = 10.0      # seconds to wait for expect/see/locate before failing
    settle: float = 1.0        # seconds to let the UI settle after each action
    threshold: float = 0.5     # noul above this = true


@dataclass
class Step:
    kind: str | None                 # the action, or None for a checks-only step
    value: object = None
    opts: dict = field(default_factory=dict)
    checks: list[tuple[str, str]] = field(default_factory=list)  # (expect|see|not_see, text)
    raw: object = None
    used: "Test | None" = None       # the test a `use:` step runs

    def title(self) -> str:
        if self.kind is None:
            return ""
        opts = " ".join(f"{k}={v!r}" for k, v in self.opts.items())
        if self.value is None or self.value is True:
            return f"{self.kind} {opts}".strip()
        return f"{self.kind}: {self.value} {opts}".strip()


@dataclass
class Test:
    name: str
    steps: list[Step]
    fresh: bool = True


@dataclass
class Spec:
    path: Path
    apps: dict[str, Path]
    tests: list[Test]
    settings: Settings

    def app_for(self, platform: str | None) -> tuple[str, Path]:
        if platform:
            if platform not in self.apps:
                raise SpecError(f"No {platform} app in {self.path.name} (have: {', '.join(self.apps)})")
            return platform, self.apps[platform]
        if len(self.apps) > 1:
            raise SpecError(f"{self.path.name} has apps for {', '.join(self.apps)}; pick one with --platform")
        return next(iter(self.apps.items()))


def platform_of(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in ANDROID_EXT:
        return "android"
    if ext in IOS_EXT:
        return "ios"
    raise SpecError(f"Unknown app type '{path.name}' (use .apk/.aab for Android, .app/.zip/.ipa for iOS)")


def parse_step(raw) -> Step:
    if isinstance(raw, str):
        word = raw.strip()
        return Step(word, raw=raw) if word in BARE else Step("do", word, raw=raw)
    if not isinstance(raw, dict):
        raise SpecError(f"Step must be a string or a mapping, got {raw!r}")

    unknown = set(raw) - BARE - ACTIONS - CHECKS - OPTIONS
    if unknown:
        raise SpecError(f"Step {raw!r} has unknown keys: {', '.join(sorted(map(str, unknown)))}")
    actions = [k for k in raw if k in BARE or k in ACTIONS]
    if len(actions) > 1:
        raise SpecError(f"Step {raw!r} has more than one action ({', '.join(actions)}); split it into two steps")

    checks = []
    for k in raw:
        if k in CHECKS:
            values = raw[k] if isinstance(raw[k], list) else [raw[k]]
            checks += [(k, str(v)) for v in values]
    if not actions:
        if not checks:
            raise SpecError(f"Step {raw!r} has no action or check")
        return Step(None, checks=checks, raw=raw, opts={k: raw[k] for k in raw if k in OPTIONS})

    kind = actions[0]
    opts = {k: v for k, v in raw.items() if k in OPTIONS}
    value = raw[kind]
    if isinstance(value, dict):  # `type: {text: .., into: ..}` form
        bad = set(value) - OPTIONS
        if bad:
            raise SpecError(f"Step {raw!r} has unknown keys: {', '.join(sorted(bad))}")
        opts.update(value)
        value = None
    if kind in ACTIONS and value is None and kind not in ("type", "swipe"):
        raise SpecError(f"Step '{kind}' needs a value")
    return Step(kind, value, opts, checks, raw)


def _link_uses(tests: list[Test]):
    """Point each `use:` step at the test it names, and refuse cycles."""
    by_name = {t.name: t for t in tests}
    for t in tests:
        for step in t.steps:
            if step.kind == "use":
                if str(step.value) not in by_name:
                    raise SpecError(f"Test '{t.name}' uses '{step.value}', but no test has that name")
                step.used = by_name[str(step.value)]

    def visit(t: Test, path: list[str]):
        if t.name in path:
            raise SpecError("Tests use each other in a loop: " + " -> ".join(path + [t.name]))
        for step in t.steps:
            if step.used:
                visit(step.used, path + [t.name])
    for t in tests:
        visit(t, [])


def load(path: str | Path) -> Spec:
    path = Path(path).resolve()
    try:
        data = yaml.safe_load(path.read_text())
    except FileNotFoundError:
        raise SpecError(f"Test file not found: {path}") from None
    if not isinstance(data, dict):
        raise SpecError(f"{path.name} must be a YAML mapping with `app` and `tests`")

    app = data.get("app")
    if app is None:
        raise SpecError("Missing `app:` (path to .apk/.aab/.app/.zip/.ipa, or {android: ..., ios: ...})")
    raw_apps = app if isinstance(app, dict) else {None: app}
    apps = {}
    for plat, p in raw_apps.items():
        p = (path.parent / str(p)).resolve()
        detected = platform_of(p)
        if plat and plat != detected:
            raise SpecError(f"app.{plat} points at a {detected} build: {p.name}")
        apps[detected] = p

    s = data.get("settings") or {}
    unknown = set(s) - set(Settings.__dataclass_fields__)
    if unknown:
        raise SpecError(f"Unknown settings: {', '.join(sorted(unknown))}")
    settings = Settings(**s)

    tests = []
    for i, t in enumerate(data.get("tests") or [], 1):
        if not isinstance(t, dict) or "steps" not in t:
            raise SpecError(f"Test #{i} needs `name` and `steps`")
        tests.append(Test(name=str(t.get("name", f"test {i}")),
                          steps=[parse_step(s) for s in t["steps"]],
                          fresh=bool(t.get("fresh", True))))
    if not tests:
        raise SpecError("No tests found under `tests:`")
    names = [t.name for t in tests]
    dupes = {n for n in names if names.count(n) > 1}
    if dupes:
        raise SpecError(f"Test names must be unique: {', '.join(sorted(dupes))}")
    _link_uses(tests)
    return Spec(path=path, apps=apps, tests=tests, settings=settings)
