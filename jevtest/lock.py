"""Decision lockfile: makes runs deterministic even though Jev's answers wobble.

Jev's probabilities vary slightly between identical calls, so a close call can
flip. The lockfile stores Jev's answer for every exact (model, state,
questions) request. The same screen and question always get the same answer,
with no network call. A changed screen is a new request and is asked fresh.

Modes:
  record   use stored answers, ask Jev on a miss and store it (default)
  frozen   use stored answers, fail on a miss (CI: fully reproducible, offline)
  refresh  always ask Jev and overwrite the stored answers
  off      no lockfile at all
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .jev import JevError

MODES = ("record", "frozen", "refresh", "off")
VERSION = 1


def request_key(model: str, state, questions: dict) -> str:
    canonical = json.dumps({"model": model, "state": state, "questions": questions},
                           sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


class LockedJev:
    """Same `ask` / `calls` / `model` interface as Jev, backed by a lockfile.

    `make_jev` is only called on the first lookup the lockfile can't answer, so a
    fully recorded run needs no API key and no network.
    """

    def __init__(self, model: str, path: Path, mode: str, make_jev):
        if mode not in MODES:
            raise ValueError(f"lock mode must be one of {', '.join(MODES)}")
        self.model = model
        self.path = path
        self.mode = mode
        self._make_jev = make_jev
        self._jev = None
        self.calls: list[dict] = []
        self.hits = 0
        self.entries: dict[str, dict] = {}
        self.dirty = False
        if mode != "off" and path.exists():
            try:
                data = json.loads(path.read_text())
            except json.JSONDecodeError as e:
                raise JevError(f"{path.name} is not valid JSON ({e}); delete it to re-record") from None
            if not isinstance(data, dict) or data.get("version") != VERSION:
                raise JevError(f"{path.name} is not a jevtest v{VERSION} lockfile; delete it to re-record")
            self.entries = data.get("decisions", {})

    def _ask_jev(self, state, questions: dict) -> tuple[dict, dict]:
        if self._jev is None:
            self._jev = self._make_jev()
        answers = self._jev.ask(state, questions)
        return answers, self._jev.calls[-1]

    def ask(self, state, questions: dict) -> dict:
        key = request_key(self.model, state, questions)
        if self.mode in ("record", "frozen") and key in self.entries:
            self.hits += 1
            entry = self.entries[key]
            self.calls.append({"ms": 0, "cached": True, "model": entry.get("served_by"), "state": state,
                               "questions": questions, "answers": entry["answers"], "usage": {}})
            return entry["answers"]
        if self.mode == "frozen":
            raise JevError(f"A screen/question is not in {self.path.name} and --frozen is set. "
                           "Run once without --frozen to record it.")
        answers, call = self._ask_jev(state, questions)
        self.calls.append(call)
        if self.mode != "off":
            self.entries[key] = {"served_by": call.get("model"), "answers": answers}
            self.dirty = True
        return answers

    @property
    def misses(self) -> int:
        return len(self.calls) - self.hits

    def save(self):
        if not self.dirty:
            return
        data = {"version": VERSION, "model": self.model, "decisions": self.entries}
        self.path.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")
        self.dirty = False
