"""The lockfile: makes runs repeat exactly, even though Jev's answers wobble.

It keeps two things:

- The steps each `do:` goal took the first time (Jev worked them out), so later runs repeat those steps.
- Jev's answer to every other question (an `expect:`, which of several exact matches a step means), keyed by
  the exact (model, state, questions) request: the same screen and question always get the same answer.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Callable, Mapping, Sequence
from enum import StrEnum
from pathlib import Path
from typing import Protocol, TypedDict, TypeGuard

from jevtest.adapters.shapes import is_json_object, is_list, objects_by_key
from jevtest.domain.decisions import STEP_ACTIONS, SavedStep, Target
from jevtest.domain.failures import ModelError, NotRecorded
from jevtest.domain.model import Answer, ModelCall, Question, State

from .client import Reply
from .wire import RawAnswers, answers_from_wire, questions_to_wire

VERSION = 2
"""The lockfile format version. Version 1 had no saved steps."""


class JevAsker(Protocol):
    """What the lockfile needs of Jev: one request, its reply (a `client.JevClient`)."""

    def ask(self, state: object, questions: Mapping[str, object]) -> Reply:
        """Ask `questions` about `state`."""
        ...


class LockMode(StrEnum):
    """How a run uses the lockfile."""

    RECORD = "record"
    """Use saved steps and recorded answers; work out anything new with Jev and record it."""
    FROZEN = "frozen"
    """Use saved steps and recorded answers only; anything new fails (no key or network needed)."""
    REFRESH = "refresh"
    """Work out everything with Jev again and re-record it."""
    OFF = "off"
    """Don't read or write a lockfile."""


def request_key(model: str, state: object, questions: Mapping[str, object]) -> str:
    """The lockfile key for one request: a hash of exactly what Jev would be sent."""
    canonical = json.dumps(
        {"model": model, "state": state, "questions": questions},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


class Entry(TypedDict):
    """One recorded decision: Jev's answers, as it sent them, and the model version that gave them."""

    served_by: str | None
    answers: RawAnswers


def _bad(name: str) -> ModelError:
    return ModelError(f"{name} is not a jevtest v{VERSION} lockfile; delete it to re-record")


def _entries(data: object, name: str) -> dict[str, Entry]:
    """The recorded decisions of a parsed lockfile, checked.

    Raises:
        ModelError: It isn't a jevtest lockfile of this version.
    """
    if not is_json_object(data) or data.get("version") != VERSION:
        raise _bad(name)
    decisions = data.get("decisions", {})
    if not is_json_object(decisions):
        raise _bad(name)
    entries: dict[str, Entry] = {}
    for key, entry in decisions.items():
        answers = objects_by_key(entry.get("answers")) if is_json_object(entry) else None
        if answers is None or not is_json_object(entry):
            raise _bad(name)
        served_by = entry.get("served_by")
        entries[key] = {"served_by": served_by if isinstance(served_by, str) else None, "answers": answers}
    return entries


def _saved(data: object, name: str) -> dict[str, tuple[SavedStep, ...]]:
    """The saved steps of a parsed lockfile (already checked to be this version), each checked.

    Raises:
        ModelError: A saved step isn't one jevtest writes.
    """
    raw = data.get("steps", {}) if is_json_object(data) else None
    if not is_json_object(raw):
        raise _bad(name)
    saved: dict[str, tuple[SavedStep, ...]] = {}
    for key, steps in raw.items():
        if not is_list(steps):
            raise _bad(name)
        saved[key] = tuple(_step(step, name) for step in steps)
    return saved


def _step(raw: object, name: str) -> SavedStep:
    if not is_json_object(raw) or raw.get("action") not in STEP_ACTIONS:
        raise _bad(name)
    action = str(raw["action"])
    text, target = raw.get("text"), raw.get("target")
    if text is not None and not isinstance(text, str):
        raise _bad(name)
    if target is None:
        return SavedStep(action, None, text)
    if not is_json_object(target):
        raise _bad(name)
    kind, label, nth, count = (target.get(k) for k in ("kind", "name", "nth", "count"))
    if not (isinstance(kind, str) and isinstance(label, str) and _whole(nth) and _whole(count)):
        raise _bad(name)
    if not 1 <= nth <= count:
        raise _bad(name)
    return SavedStep(action, Target(kind, label, nth, count), text)


def _whole(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _step_to_wire(step: SavedStep) -> dict[str, object]:
    t = step.target
    target = None if t is None else {"kind": t.kind, "name": t.name, "nth": t.nth, "count": t.count}
    return {"action": step.action, "target": target, "text": step.text}


class _Store:
    """The recorded decisions of one lockfile, shared by every device testing that file at once."""

    def __init__(self, path: Path, mode: LockMode) -> None:
        self.path = path
        self.lock = threading.Lock()
        self.entries: dict[str, Entry] = {}
        self.steps: dict[str, tuple[SavedStep, ...]] = {}
        self.used: set[str] = set()
        self.used_steps: set[str] = set()
        self.dirty = False
        if mode is not LockMode.OFF and path.exists():
            try:
                data: object = json.loads(path.read_text())
            except json.JSONDecodeError as e:
                raise ModelError(f"{path.name} is not valid JSON ({e}); delete it to re-record") from None
            self.entries = _entries(data, path.name)
            self.steps = _saved(data, path.name)


class LockedModel:
    """Jev behind a lockfile: the `DecisionModel` the runner uses.

    Args:
        model: The Jev model, part of every lockfile key.
        path: The lockfile.
        mode: How to use it.
        connect: Makes the Jev client. Called only on the first request the lockfile can't answer, so a fully
            recorded run needs no API key and no network.

    Devices testing the same file at once each get a `fork()`, with its own `calls` for its report and the
    same recorded decisions, guarded by one lock.
    """

    def __init__(
        self, model: str, path: Path, mode: LockMode, connect: Callable[[], JevAsker], *, _store: _Store | None = None
    ) -> None:
        self.model = model
        self.mode = mode
        self._connect = connect
        self._client: JevAsker | None = None
        self._store = _store or _Store(path, mode)
        self._calls: list[ModelCall] = []

    @property
    def path(self) -> Path:
        """The lockfile."""
        return self._store.path

    @property
    def calls(self) -> Sequence[ModelCall]:
        """Every request this model (not its forks) made, in order."""
        return self._calls

    @property
    def replays_only(self) -> bool:
        """Whether only saved steps and recorded answers may be used (``--lock frozen``)."""
        return self.mode is LockMode.FROZEN

    def saved_steps(self, key: str) -> tuple[SavedStep, ...] | None:
        """The steps saved for the `do:` goal `key` (record, frozen); None to work it out with Jev.

        Raises:
            NotRecorded: Frozen, and no steps are saved for it.
        """
        store = self._store
        with store.lock:
            store.used_steps.add(key)
            steps = store.steps.get(key) if self.mode in (LockMode.RECORD, LockMode.FROZEN) else None
        if steps is None and self.mode is LockMode.FROZEN:
            raise NotRecorded(
                f"No steps are saved for this do: in {store.path.name}, and --lock frozen only repeats saved "
                "steps. Run with --lock record to work them out, then commit the lockfile."
            )
        return steps

    def save_steps(self, key: str, steps: Sequence[SavedStep]) -> None:
        """Save the steps the `do:` goal `key` took (not with ``--lock off``)."""
        if self.mode is LockMode.OFF:
            return
        store = self._store
        with store.lock:
            store.used_steps.add(key)
            store.steps[key] = tuple(steps)
            store.dirty = True

    def fork(self) -> LockedModel:
        """A model for another device: its own calls, the same recorded decisions."""
        return LockedModel(self.model, self.path, self.mode, self._connect, _store=self._store)

    def ask(self, state: State, questions: Mapping[str, Question]) -> Mapping[str, Answer]:
        """The recorded answer for this exact request, or Jev's (recorded for next time), per the mode.

        Raises:
            ModelError: The request isn't recorded and the mode is frozen, or Jev failed.
        """
        wire = questions_to_wire(questions)
        key = request_key(self.model, state, wire)
        store = self._store
        with store.lock:
            store.used.add(key)
            entry = store.entries.get(key) if self.mode in (LockMode.RECORD, LockMode.FROZEN) else None
        if entry is not None:
            answers = answers_from_wire(entry["answers"], questions)
            self._calls.append(ModelCall(state, questions, answers, recorded=True, served_by=entry.get("served_by")))
            return answers
        if self.mode is LockMode.FROZEN:
            raise NotRecorded(
                f"This screen and question are not in {store.path.name}, and --lock frozen only "
                "replays recorded decisions. Run with --lock record to record it, then commit the "
                "lockfile."
            )
        reply = self._ask_jev(state, wire)
        answers = answers_from_wire(reply.answers, questions)
        self._calls.append(
            ModelCall(
                state, questions, answers, recorded=False, ms=reply.ms, cost=reply.cost, served_by=reply.served_by
            )
        )
        if self.mode is not LockMode.OFF:
            with store.lock:
                store.entries[key] = {"served_by": reply.served_by, "answers": dict(reply.answers)}
                store.dirty = True
        return answers

    def _ask_jev(self, state: State, wire: Mapping[str, object]) -> Reply:
        if self._client is None:
            self._client = self._connect()
        return self._client.ask(state, wire)

    def prune(self) -> int:
        """Drop recorded decisions this run (and its forks) didn't use; return how many.

        Only right after a run of every test in which every test passed: a failed test stops early and skips
        screens that are still real.
        """
        store = self._store
        with store.lock:
            stale = [k for k in store.entries if k not in store.used]
            for k in stale:
                del store.entries[k]
            stale_steps = [k for k in store.steps if k not in store.used_steps]
            for k in stale_steps:
                del store.steps[k]
            store.dirty = store.dirty or bool(stale) or bool(stale_steps)
        return len(stale) + len(stale_steps)

    def save(self) -> None:
        """Write the lockfile, if anything was recorded or pruned."""
        store = self._store
        with store.lock:
            if not store.dirty:
                return
            data = {
                "version": VERSION,
                "model": self.model,
                "steps": {key: [_step_to_wire(step) for step in steps] for key, steps in store.steps.items()},
                "decisions": store.entries,
            }
            store.path.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")
            store.dirty = False
