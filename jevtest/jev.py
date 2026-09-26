"""Minimal client for TypeSafe's Jev, reached through OpenRouter.

Jev reads text state and answers typed questions (choice / noul) with
probabilities. It does not generate text and does not see images.
OpenRouter's /v1/systemone endpoint takes the same request shape as
TypeSafe's own API.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

API_URL = "https://openrouter.ai/api/v1/systemone"
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504, 529})


class JevError(RuntimeError):
    pass


def choice(instructions, options: dict) -> dict:
    return {"type": "choice", "instructions": instructions, "criteria": options}


def noul(instructions) -> dict:
    return {"type": "noul", "instructions": instructions}


class Jev:
    """Transient failures (HTTP 429/5xx, network) are retried with backoff, and every retry is
    printed, so a slow run always says why."""

    def __init__(self, model: str, api_key: str | None, url: str = API_URL, timeout: float = 30,
                 retries: int = 4, sleep=time.sleep, urlopen=urllib.request.urlopen, log=None):
        self.api_key = api_key
        if not self.api_key:
            raise JevError("OPENROUTER_API_KEY is not set: put it in the .env next to the test file, or in the "
                           "environment. Create a key at https://openrouter.ai/keys")
        self._log = log or (lambda msg: print(msg, file=sys.stderr, flush=True))
        self.model = model
        self.url = url
        self.timeout = timeout
        self.retries = retries
        self._sleep = sleep
        self._urlopen = urlopen
        self.calls: list[dict] = []  # every request/response, for the report

    def ask(self, state, questions: dict) -> dict:
        body = json.dumps({"model": self.model, "state": state, "questions": questions}).encode()
        started = time.monotonic()
        data = self._post(body)
        answers = data.get("answers")
        if not isinstance(answers, dict) or set(answers) != set(questions):
            raise JevError(f"Jev returned answers for {sorted(answers or {})}, expected {sorted(questions)}")
        self.calls.append({
            "ms": round((time.monotonic() - started) * 1000),
            "model": data.get("model"),
            "state": state,
            "questions": questions,
            "answers": answers,
            "usage": data.get("usage") or {},
        })
        return answers

    def _post(self, body: bytes) -> dict:
        for attempt in range(self.retries + 1):
            last = attempt == self.retries
            req = urllib.request.Request(self.url, data=body, method="POST", headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            })
            try:
                with self._urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read()
            except urllib.error.HTTPError as e:
                if e.code in RETRY_STATUSES and not last:
                    self._retry(f"HTTP {e.code}", attempt)
                    continue
                detail = e.read().decode(errors="replace")[:500]
                raise JevError(f"Jev HTTP {e.code}: {detail}") from None
            except (urllib.error.URLError, TimeoutError) as e:
                if not last:
                    self._retry(f"unreachable ({e})", attempt)
                    continue
                raise JevError(f"Jev unreachable: {e}") from None
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                raise JevError(f"Jev returned invalid JSON: {raw[:200]!r}") from None
        raise AssertionError("unreachable")  # pragma: no cover

    def _retry(self, why: str, attempt: int):
        wait = 0.5 * 2 ** attempt
        self._log(f"Jev {why}: trying again in {wait:g}s (retry {attempt + 1} of {self.retries})")
        self._sleep(wait)
