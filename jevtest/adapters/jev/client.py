"""HTTP client for TypeSafe's Jev, reached through OpenRouter.

Jev reads a text state and answers typed questions (choice or yes/no) with probabilities. It doesn't generate
text and doesn't see images. OpenRouter's ``/v1/systemone`` endpoint takes the same request shape as TypeSafe's
own API. This module speaks that wire format only; `wire` maps it to and from jevtest's own types.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from jevtest.domain.failures import ModelError

API_URL = "https://openrouter.ai/api/v1/systemone"
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504, 529})
UNAUTHORIZED = 401
KEY_HELP = "Create a key at https://openrouter.ai/keys"


class _Response(Protocol):
    def read(self) -> bytes: ...
    def __enter__(self) -> _Response: ...
    def __exit__(self, *exc: object) -> None: ...


UrlOpen = Callable[..., _Response]


@dataclass(frozen=True)
class Reply:
    """What Jev answered, still in its wire format.

    Attributes:
        answers: One answer per question id, as Jev sent it.
        ms: How long the request took.
        served_by: The model version that answered.
        cost: What the request cost in US dollars.
    """

    answers: Mapping[str, Mapping[str, Any]]
    ms: int
    served_by: str | None
    cost: float


class JevClient:
    """Sends questions to Jev.

    Transient failures (HTTP 429/5xx, network) are retried with backoff, and every retry is reported to `log`,
    so a slow run always says why.

    Args:
        model: The Jev model to ask.
        api_key: The OpenRouter key.
        log: Told about each retry.
        url: The endpoint.
        timeout: Seconds to wait for one response.
        retries: How many times to retry a transient failure.
        sleep: Waits between retries (tests pass a fake).
        urlopen: Sends the request (tests pass a fake).

    Raises:
        ModelError: `api_key` is empty.
    """

    def __init__(self, model: str, api_key: str | None, log: Callable[[str], None], *, url: str = API_URL,
                 timeout: float = 30, retries: int = 4, sleep: Callable[[float], None] = time.sleep,
                 urlopen: UrlOpen = urllib.request.urlopen) -> None:
        if not api_key:
            raise ModelError("OPENROUTER_API_KEY is not set: put it in the .env next to the test file, or in the "
                             f"environment. {KEY_HELP}")
        self.api_key = api_key
        self.model = model
        self.url = url
        self.timeout = timeout
        self.retries = retries
        self._log = log
        self._sleep = sleep
        self._urlopen = urlopen

    def ask(self, state: object, questions: Mapping[str, object]) -> Reply:
        """Ask Jev `questions` (wire format) about `state`.

        Raises:
            ModelError: Jev couldn't be reached, refused the request, or answered a different set of questions.
        """
        body = json.dumps({"model": self.model, "state": state, "questions": questions}).encode()
        started = time.monotonic()
        data = self._post(body)
        answers = data.get("answers")
        if not isinstance(answers, dict) or set(answers) != set(questions):
            got = sorted(answers) if isinstance(answers, dict) else answers
            raise ModelError(f"Jev returned answers for {got}, expected {sorted(questions)}")
        usage = data.get("usage") or {}
        return Reply(answers, round((time.monotonic() - started) * 1000), data.get("model"),
                     float(usage.get("cost", 0)))

    def _post(self, body: bytes) -> dict[str, Any]:
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
                hint = f" (check the key; {KEY_HELP})" if e.code == UNAUTHORIZED else ""
                raise ModelError(f"Jev HTTP {e.code}: {detail}{hint}") from None
            except (urllib.error.URLError, TimeoutError) as e:
                if not last:
                    self._retry(f"unreachable ({e})", attempt)
                    continue
                raise ModelError(f"Jev unreachable: {e}") from None
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                raise ModelError(f"Jev returned invalid JSON: {raw[:200]!r}") from None
            if not isinstance(data, dict):
                raise ModelError(f"Jev returned {type(data).__name__}, expected a JSON object")
            return data
        raise AssertionError("unreachable")  # pragma: no cover

    def _retry(self, why: str, attempt: int) -> None:
        wait = 0.5 * 2 ** attempt
        self._log(f"Jev {why}: trying again in {wait:g}s (retry {attempt + 1} of {self.retries})")
        self._sleep(wait)
