import io
import json
import re
import urllib.error
import urllib.request
from email.message import Message
from typing import Self

import pytest

from jevtest.adapters.jev.client import JevClient
from jevtest.domain.failures import KeyRejected, ModelError

MODEL = "jev-1.13.0"

Q = {"q": {"type": "choice", "instructions": "pick", "criteria": {"a": None, "b": None}}}
GOOD = {
    "model": "jev-1.13.0",
    "answers": {"q": {"type": "choice", "choice": "a"}},
    "usage": {"input_tokens": 1_000_000, "output_tokens": 20},
}


class Resp:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def read(self) -> bytes:
        return self.body

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class Opener:
    """urlopen stand-in: each outcome is a dict (JSON reply), bytes, or an exception. `seen` keeps every request."""

    def __init__(self, *outcomes):
        self.outcomes = outcomes
        self.seen: list[tuple[urllib.request.Request, float]] = []

    def __call__(self, req: urllib.request.Request, timeout: float) -> Resp:
        self.seen.append((req, timeout))
        o = self.outcomes[len(self.seen) - 1]
        if isinstance(o, Exception):
            raise o
        return Resp(o if isinstance(o, bytes) else json.dumps(o).encode())


def http_error(code, body=b"nope", retry_after=None):
    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError("u", code, "msg", headers, io.BytesIO(body))


def opener_of(j: JevClient) -> Opener:
    """The fake urlopen a client was made with."""
    assert isinstance(j._urlopen, Opener)
    return j._urlopen


def client(*outcomes, **kw):
    slept: list[float] = []
    j = JevClient(MODEL, "k", LOGGED.append, sleep=slept.append, urlopen=Opener(*outcomes), **kw)
    return j, slept


LOGGED: list[str] = []


def test_success_returns_the_reply():
    j, slept = client(GOOD)
    reply = j.ask({"s": 1}, Q)
    assert reply.answers == GOOD["answers"]
    req, timeout = opener_of(j).seen[0]
    assert req.full_url == "https://api.typesafe.ai/v1/systemone"
    assert req.get_header("Authorization") == "Bearer k"
    assert isinstance(req.data, bytes)
    assert json.loads(req.data) == {"model": MODEL, "state": {"s": 1}, "questions": Q}
    assert timeout == 15 and not slept
    assert reply.served_by == "jev-1.13.0" and reply.ms >= 0
    assert reply.cost == pytest.approx(0.042)  # $0.042 per million input tokens; output tokens are free


def test_the_cost_is_unknown_without_a_price_or_a_token_count():
    j, _ = client({**GOOD, "model": "jev-9.0.0"}, {"answers": GOOD["answers"]})
    assert j.ask("s", Q).cost is None  # a version jevtest has no price for
    assert j.ask("s", Q).cost is None  # no usage: the price of the model asked, but nothing to count


def test_retries_rate_limits_then_succeeds():
    LOGGED.clear()
    j, slept = client(http_error(429), http_error(529), GOOD)
    assert j.ask("s", Q).answers == GOOD["answers"]
    assert slept == [0.5, 1.0]
    assert LOGGED == [
        "Jev HTTP 429: trying again in 0.5s (retry 1 of 4)",
        "Jev HTTP 529: trying again in 1s (retry 2 of 4)",
    ]


@pytest.mark.parametrize("code", [408, 500, 502, 503, 504, 520, 524, 599])
def test_timeouts_and_server_errors_are_retried(code):
    j, slept = client(http_error(code), GOOD)
    assert j.ask("s", Q).answers == GOOD["answers"] and slept == [0.5]


@pytest.mark.parametrize(
    ("header", "waited"), [("2", 2.0), ("0", 0.0), ("60", 60.0), ("61", 0.5), ("-1", 0.5), ("soon", 0.5), ("nan", 0.5)]
)
def test_retry_after_is_honored_up_to_a_minute(header, waited):
    j, slept = client(http_error(429, retry_after=header), GOOD)
    j.ask("s", Q)
    assert slept == [waited]  # otherwise jevtest's own backoff


def test_the_timeout_it_is_given_is_the_one_used():
    j, _ = client(GOOD, timeout=3)
    j.ask("s", Q)
    assert opener_of(j).seen[0][1] == 3


def test_retries_network_errors_then_succeeds():
    j, slept = client(urllib.error.URLError("down"), TimeoutError(), GOOD)
    j.ask("s", Q)
    assert slept == [0.5, 1.0]


def test_gives_up_after_retries():
    j, slept = client(*[http_error(503) for _ in range(3)], retries=2)
    with pytest.raises(ModelError, match="HTTP 503"):
        j.ask("s", Q)
    assert slept == [0.5, 1.0]


def test_unreachable_after_retries():
    j, _ = client(urllib.error.URLError("dns"), urllib.error.URLError("dns"), retries=1)
    with pytest.raises(
        ModelError,
        match=r"^Jev unreachable after 1 retry \(<urlopen error dns>\): check the network; --lock frozen needs none",
    ):
        j.ask("s", Q)


def test_client_errors_are_not_retried():
    j, slept = client(http_error(401, b"bad key"))
    with pytest.raises(
        KeyRejected, match=r"HTTP 401: bad key \(check the key; Create a key at https://console.typesafe.ai/keys\)"
    ):
        j.ask("s", Q)
    assert not slept


@pytest.mark.parametrize(
    ("body", "shown"),
    [
        (
            b'{"detail":{"error_type":"api_usage_error","message":"Unknown model: jev-9.9.9"}}',
            "Unknown model: jev-9.9.9",
        ),
        (b'{"detail":"Not authenticated"}', "Not authenticated"),
        (b'{"detail":{"message":""}}', '{"detail":{"message":""}}'),
        (b"[1]", "[1]"),
    ],
)
def test_an_error_shows_typesafes_own_message(body, shown):
    j, _ = client(http_error(400, body))
    with pytest.raises(ModelError, match=rf"^Jev HTTP 400: {re.escape(shown)}$"):
        j.ask("s", Q)


def test_invalid_json():
    j, _ = client(b"<html>")
    with pytest.raises(ModelError, match="invalid JSON"):
        j.ask("s", Q)


@pytest.mark.parametrize("reply", [{}, {"answers": None}, {"answers": {"other": {}}}])
def test_answers_must_match_questions(reply):
    j, _ = client(reply)
    with pytest.raises(ModelError, match="expected"):
        j.ask("s", Q)


def test_a_reply_that_is_not_an_object():
    j, _ = client([1, 2])
    with pytest.raises(ModelError, match="Jev returned list, expected a JSON object"):
        j.ask("s", Q)


def test_missing_key_is_not_looked_up_elsewhere(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "env-key")  # only the key it is given counts
    with pytest.raises(ModelError, match="TYPESAFE_API_KEY is not set: put it in the .env next to the test file"):
        JevClient(MODEL, None, LOGGED.append)


def test_an_answer_that_is_not_an_object():
    j, _ = client({"answers": {"q": "a"}})
    with pytest.raises(ModelError, match="an answer that isn't a JSON object"):
        j.ask("s", Q)


@pytest.mark.parametrize(
    ("extra", "served_by", "cost"),
    [
        ({"model": 5, "usage": "free"}, None, None),
        ({"usage": {"input_tokens": "lots"}}, None, None),
        ({"usage": {"input_tokens": True}}, None, None),
        ({"usage": {"input_tokens": -1}}, None, None),
        ({"usage": {"input_tokens": 0}}, None, 0.0),
    ],
)
def test_odd_metadata_is_ignored_not_trusted(extra, served_by, cost):
    j, _ = client({"answers": {"q": {"type": "choice", "choice": "a"}}, **extra})
    reply = j.ask("s", Q)
    assert (reply.served_by, reply.cost) == (served_by, cost)
