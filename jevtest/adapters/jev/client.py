"""HTTP client for TypeSafe's Jev, through TypeSafe's own API (https://docs.typesafe.ai/api).

Jev reads a text state and answers typed questions (choice or yes/no) with probabilities. It doesn't generate
text and doesn't see images. This module speaks the ``/v1/systemone`` wire format only; `wire` maps it to and
from jevtest's own types.
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
from jevtest.domain.failures import ModelError, SetupRefused
from jevtest.domain.settings import DEFAULTS
from jevtest.domain.words import plural

from .wire import RawAnswers

API_URL = "https://api.typesafe.ai/v1/systemone"
RETRY_STATUSES = frozenset({408, 429, *range(500, 600)})
"""Temporary failures, as TypeSafe's docs list them: a timeout, rate limits (429), overload (529), server errors."""
MAX_RETRY_AFTER = 60
"""Seconds: the longest `retry-after` jevtest waits for. A longer one falls back to its own backoff."""
TIMEOUT = 15
"""Seconds to wait for one answer before asking again. Measured 2026-09-28 against api.typesafe.ai with 74 real
requests from a run: half answered within 0.2 s, but a quarter took 8 to 29 s, and in runs some took over 30 s."""
UNAUTHORIZED = 401

UNKNOWN_MODEL = "Unknown model"
"""How TypeSafe's message starts for a model it doesn't serve (measured: HTTP 400, "Unknown model: jev-1.12.0")."""
KEY_HELP = "Create a key at https://console.typesafe.ai/keys"
PRICE_PER_INPUT_TOKEN: Mapping[str, float] = {"jev-1.13.0": 0.042 / 1_000_000}
"""US dollars, from https://docs.typesafe.ai/models: Jev charges per input token; output tokens are free."""


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
        cost: What the request cost in US dollars; None for a model whose price jevtest doesn't know.
    """

    answers: RawAnswers
    ms: int
    served_by: str | None
    cost: float | None


class JevClient:
    """Sends questions to Jev.

    Transient failures (HTTP 429/5xx, network) are retried with backoff, and every retry is reported to `log`,
    so a slow run always says why.

    Args:
        model: The Jev model to ask.
        api_key: The TypeSafe API key.
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
        timeout: float = TIMEOUT,
        retries: int = 4,
        sleep: Callable[[float], None] = time.sleep,
        urlopen: UrlOpen = urllib.request.urlopen,
    ) -> None:
        if not api_key:
            raise ModelError(
                "TYPESAFE_API_KEY is not set: put it in the .env next to the test file, or in the "
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
            SetupRefused: Jev refused the API key, or doesn't know the model.
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
        served_by = data.get("model")
        served_by = served_by if isinstance(served_by, str) else None
        return Reply(
            answers, round((time.monotonic() - started) * 1000), served_by, self._cost(served_by, data.get("usage"))
        )

    def _cost(self, served_by: str | None, usage: object) -> float | None:
        """The price of the input tokens `usage` reports, for the model that answered; None when unknown."""
        price = PRICE_PER_INPUT_TOKEN.get(served_by or self.model)
        tokens = usage.get("input_tokens") if is_json_object(usage) else None
        if price is None or isinstance(tokens, bool) or not isinstance(tokens, int) or tokens < 0:
            return None
        return tokens * price

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
                    self._retry(f"HTTP {e.code}", attempt, _retry_after(e.headers.get("retry-after")))
                    attempt += 1
                    continue
                said = _message(detail)
                if e.code == UNAUTHORIZED:
                    raise SetupRefused(f"Jev HTTP {e.code}: {said} (check the key; {KEY_HELP})") from None
                if said.startswith(UNKNOWN_MODEL):
                    raise SetupRefused(
                        f"Jev HTTP {e.code}: {said} (settings: model names a Jev version TypeSafe serves; this jevtest "
                        f"is tested with {DEFAULTS.model})"
                    ) from None
                raise ModelError(f"Jev HTTP {e.code}: {said}") from None
            except (urllib.error.URLError, TimeoutError) as e:
                if not last:
                    self._retry(f"unreachable ({e})", attempt)
                    attempt += 1
                    continue
                raise ModelError(
                    f"Jev unreachable after {plural(self.retries, 'retry', 'retries')} ({e}): check the network; "
                    "--lock frozen needs none, if every decision is recorded"
                ) from None
            try:
                data: object = json.loads(raw)
            except json.JSONDecodeError:
                raise ModelError(f"Jev returned invalid JSON: {raw[:200]!r}") from None
            if not is_json_object(data):
                raise ModelError(f"Jev returned {type(data).__name__}, expected a JSON object")
            return data

    def _retry(self, why: str, attempt: int, asked: float | None = None) -> None:
        """Wait before retrying: as long as the server asked (`retry-after`), or an exponential backoff."""
        wait = asked if asked is not None else 0.5 * 2**attempt
        self._log(f"Jev {why}: trying again in {wait:g}s (retry {attempt + 1} of {self.retries})")
        self._sleep(wait)


def _retry_after(header: str | None) -> float | None:
    """A `retry-after` header in seconds, when it is one jevtest will wait for (0 to `MAX_RETRY_AFTER`).

    The header may also be an HTTP date; jevtest uses its own backoff then.
    """
    try:
        seconds = float(header or "")
    except ValueError:
        return None
    return seconds if 0 <= seconds <= MAX_RETRY_AFTER else None


def _message(detail: str) -> str:
    """TypeSafe's own words from an error body (``{"detail": {"message": ...}}``); else the body as it came."""
    try:
        body: object = json.loads(detail)
    except json.JSONDecodeError:
        return detail
    inner = body.get("detail") if is_json_object(body) else None
    message = inner.get("message") if is_json_object(inner) else inner
    return message if isinstance(message, str) and message else detail
