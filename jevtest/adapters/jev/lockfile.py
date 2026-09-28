"""The decision lockfile: makes runs deterministic even though Jev's answers wobble.

Jev's probabilities vary slightly between identical calls, so a close call can flip. The lockfile stores Jev's
answer for every exact (model, state, questions) request: the same screen and question always get the same
answer, with no network call. A changed screen is a new request and is asked fresh.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Callable, Mapping, Sequence
from enum import StrEnum
from pathlib import Path
from typing import Protocol, TypedDict

from jevtest.adapters.shapes import is_json_object, objects_by_key
from jevtest.domain.failures import ModelError, NotRecorded
from jevtest.domain.model import Answer, ModelCall, Question, State

from .client import Reply
from .wire import RawAnswers, answers_from_wire, questions_to_wire

VERSION = 1
"""The lockfile format version."""


class JevAsker(Protocol):
    """What the lockfile needs of Jev: one request, its reply (a `client.JevClient`)."""

    def ask(self, state: object, questions: Mapping[str, object]) -> Reply:
        """Ask `questions` about `state`."""
        ...


class LockMode(StrEnum):
    """How a run uses the lockfile."""

    RECORD = "record"
    """Use recorded answers; ask Jev about anything new and record it."""
    FROZEN = "frozen"
    """Use recorded answers only; anything new fails (no key or network needed)."""
    REFRESH = "refresh"
    """Ask Jev about everything again and re-record it."""
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


def _entries(data: object, name: str) -> dict[str, Entry]:
    """The recorded decisions of a parsed lockfile, checked.

    Raises:
        ModelError: It isn't a jevtest lockfile of this version.
    """
    bad = ModelError(f"{name} is not a jevtest v{VERSION} lockfile; delete it to re-record")
    if not is_json_object(data) or data.get("version") != VERSION:
        raise bad
    decisions = data.get("decisions", {})
    if not is_json_object(decisions):
        raise bad
    entries: dict[str, Entry] = {}
    for key, entry in decisions.items():
        answers = objects_by_key(entry.get("answers")) if is_json_object(entry) else None
        if answers is None or not is_json_object(entry):
            raise bad
        served_by = entry.get("served_by")
        entries[key] = {"served_by": served_by if isinstance(served_by, str) else None, "answers": answers}
    return entries


class _Store:
    """The recorded decisions of one lockfile, shared by every device testing that file at once."""

    def __init__(self, path: Path, mode: LockMode) -> None:
        self.path = path
        self.lock = threading.Lock()
        self.entries: dict[str, Entry] = {}
        self.used: set[str] = set()
        self.dirty = False
        if mode is not LockMode.OFF and path.exists():
            try:
                data: object = json.loads(path.read_text())
            except json.JSONDecodeError as e:
                raise ModelError(f"{path.name} is not valid JSON ({e}); delete it to re-record") from None
            self.entries = _entries(data, path.name)


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
            store.dirty = store.dirty or bool(stale)
        return len(stale)

    def save(self) -> None:
        """Write the lockfile, if anything was recorded or pruned."""
        store = self._store
        with store.lock:
            if not store.dirty:
                return
            data = {"version": VERSION, "model": self.model, "decisions": store.entries}
            store.path.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")
            store.dirty = False
