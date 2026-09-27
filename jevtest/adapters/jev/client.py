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
from typing import Protocol, Self

from jevtest.adapters.shapes import is_json_object, objects_by_key
from jevtest.domain.failures import ModelError

from .wire import RawAnswers

API_URL = "https://openrouter.ai/api/v1/systemone"
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504, 529})
UNAUTHORIZED = 401
KEY_HELP = "Create a key at https://openrouter.ai/keys"


class _Response(Protocol):
    def read(self) -> bytes: ...
    def __enter__(self) -> Self: ...
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

    answers: RawAnswers
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

    def __init__(
        self,
        model: str,
        api_key: str | None,
        log: Callable[[str], None],
        *,
        url: str = API_URL,
        timeout: float = 30,
        retries: int = 4,
        sleep: Callable[[float], None] = time.sleep,
        urlopen: UrlOpen = urllib.request.urlopen,
    ) -> None:
        if not api_key:
            raise ModelError(
                "OPENROUTER_API_KEY is not set: put it in the .env next to the test file, or in the "
                f"environment. {KEY_HELP}"
            )
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
        raw_answers = data.get("answers")
        if not is_json_object(raw_answers) or set(raw_answers) != set(questions):
            got = sorted(raw_answers) if is_json_object(raw_answers) else raw_answers
            raise ModelError(f"Jev returned answers for {got}, expected {sorted(questions)}")
        answers = objects_by_key(raw_answers)
        if answers is None:
            raise ModelError(f"Jev returned an answer that isn't a JSON object: {raw_answers!r}")
        served_by, usage = data.get("model"), data.get("usage")
        cost = usage.get("cost", 0) if is_json_object(usage) else 0
        return Reply(
            answers,
            round((time.monotonic() - started) * 1000),
            served_by if isinstance(served_by, str) else None,
            float(cost) if isinstance(cost, int | float) else 0.0,
        )

    def _post(self, body: bytes) -> dict[str, object]:
        attempt = 0
        while True:
            last = attempt == self.retries
            req = urllib.request.Request(
                self.url,
                data=body,
                method="POST",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
            )
            try:
                with self._urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read()
            except urllib.error.HTTPError as e:
                with e:  # an HTTP error carries the open response
                    detail = e.read().decode(errors="replace")[:500]
                if e.code in RETRY_STATUSES and not last:
                    self._retry(f"HTTP {e.code}", attempt)
                    attempt += 1
                    continue
                hint = f" (check the key; {KEY_HELP})" if e.code == UNAUTHORIZED else ""
                raise ModelError(f"Jev HTTP {e.code}: {detail}{hint}") from None
            except (urllib.error.URLError, TimeoutError) as e:
                if not last:
                    self._retry(f"unreachable ({e})", attempt)
                    attempt += 1
                    continue
                raise ModelError(f"Jev unreachable: {e}") from None
            try:
                data: object = json.loads(raw)
            except json.JSONDecodeError:
                raise ModelError(f"Jev returned invalid JSON: {raw[:200]!r}") from None
            if not is_json_object(data):
                raise ModelError(f"Jev returned {type(data).__name__}, expected a JSON object")
            return data

    def _retry(self, why: str, attempt: int) -> None:
        wait = 0.5 * 2**attempt
        self._log(f"Jev {why}: trying again in {wait:g}s (retry {attempt + 1} of {self.retries})")
        self._sleep(wait)
