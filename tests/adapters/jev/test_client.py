import io
import json
import urllib.error

import pytest

from jevtest.adapters.jev.client import JevClient
from jevtest.domain.failures import ModelError

MODEL = "typesafe/jev-1.13"

Q = {"q": {"type": "choice", "instructions": "pick", "criteria": {"a": None, "b": None}}}
GOOD = {
    "model": "typesafe/jev-1.13-x",
    "answers": {"q": {"type": "choice", "choice": "a"}},
    "usage": {"input_tokens": 3, "cost": 0.1},
}


class Resp:
    def __init__(self, body: bytes):
        self.body = body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def opener(*outcomes):
    """urlopen stand-in: each outcome is a dict (JSON reply), bytes, or an exception."""
    seen = []

    def urlopen(req, timeout):
        seen.append((req, timeout))
        o = outcomes[len(seen) - 1]
        if isinstance(o, Exception):
            raise o
        return Resp(o if isinstance(o, bytes) else json.dumps(o).encode())

    urlopen.seen = seen
    return urlopen


def http_error(code, body=b"nope"):
    return urllib.error.HTTPError("u", code, "msg", {}, io.BytesIO(body))


def client(*outcomes, **kw):
    slept = []
    j = JevClient(MODEL, "k", LOGGED.append, sleep=slept.append, urlopen=opener(*outcomes), **kw)
    return j, slept


LOGGED: list[str] = []


def test_success_returns_the_reply():
    j, slept = client(GOOD)
    reply = j.ask({"s": 1}, Q)
    assert reply.answers == GOOD["answers"]
    req, timeout = j._urlopen.seen[0]
    assert req.full_url.endswith("/api/v1/systemone")
    assert req.get_header("Authorization") == "Bearer k"
    assert json.loads(req.data) == {"model": MODEL, "state": {"s": 1}, "questions": Q}
    assert timeout == 30 and not slept
    assert reply.served_by == "typesafe/jev-1.13-x" and reply.cost == 0.1 and reply.ms >= 0


def test_missing_usage_costs_nothing():
    j, _ = client({"answers": GOOD["answers"]})
    assert j.ask("s", Q).cost == 0


def test_retries_rate_limits_then_succeeds():
    LOGGED.clear()
    j, slept = client(http_error(429), http_error(529), GOOD)
    assert j.ask("s", Q).answers == GOOD["answers"]
    assert slept == [0.5, 1.0]
    assert LOGGED == [
        "Jev HTTP 429: trying again in 0.5s (retry 1 of 4)",
        "Jev HTTP 529: trying again in 1s (retry 2 of 4)",
    ]


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
    with pytest.raises(ModelError, match="unreachable"):
        j.ask("s", Q)


def test_client_errors_are_not_retried():
    j, slept = client(http_error(401, b"bad key"))
    with pytest.raises(
        ModelError, match=r"HTTP 401: bad key \(check the key; Create a key at https://openrouter.ai/keys\)"
    ):
        j.ask("s", Q)
    assert not slept


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
    monkeypatch.setenv("OPENROUTER_API_KEY", "env-key")  # only the key it is given counts
    with pytest.raises(ModelError, match="OPENROUTER_API_KEY is not set: put it in the .env next to the test file"):
        JevClient(MODEL, None, LOGGED.append)


def test_an_answer_that_is_not_an_object():
    j, _ = client({"answers": {"q": "a"}})
    with pytest.raises(ModelError, match="an answer that isn't a JSON object"):
        j.ask("s", Q)


@pytest.mark.parametrize(
    ("extra", "served_by", "cost"),
    [
        ({"model": 5, "usage": "free"}, None, 0.0),
        ({"usage": {"cost": "lots"}}, None, 0.0),
    ],
)
def test_odd_metadata_is_ignored_not_trusted(extra, served_by, cost):
    j, _ = client({"answers": {"q": {"type": "choice", "choice": "a"}}, **extra})
    reply = j.ask("s", Q)
    assert (reply.served_by, reply.cost) == (served_by, cost)
