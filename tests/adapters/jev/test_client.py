import io
import json
import re
import urllib.error
import urllib.request
from email.message import Message
from typing import Self

import pytest

from jevtest.adapters.jev.client import JevClient
from jevtest.domain.failures import ModelError, SetupRefused

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
        SetupRefused,
        match=r"HTTP 401: bad key \(check TYPESAFE_API_KEY; Create a key at https://console.typesafe.ai/keys\)",
    ):
        j.ask("s", Q)
    assert not slept


@pytest.mark.parametrize(
    ("body", "shown"),
    [
        (
            b'{"detail":{"error_type":"api_usage_error","message":"Too many questions"}}',
            "Too many questions",
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


def test_an_unknown_model_stops_the_run_saying_which_version_jevtest_is_tested_with():
    body = b'{"detail":{"error_type":"api_usage_error","message":"Unknown model: jev-1.12.0"}}'  # as measured
    j, slept = client(http_error(400, body))
    with pytest.raises(
        SetupRefused,
        match=r"^Jev HTTP 400: Unknown model: jev-1\.12\.0 \(settings: model names a model typesafe serves; "
        r"this jevtest is tested with jev-1\.13\.0\)$",
    ):
        j.ask("s", Q)
    assert not slept


def openrouter(*outcomes, **kw):
    slept: list[float] = []
    j = JevClient(
        "typesafe/jev-1.13",
        "or-key",
        LOGGED.append,
        provider="openrouter",
        sleep=slept.append,
        urlopen=Opener(*outcomes),
        **kw,
    )
    return j, slept


OR_GOOD = {
    "model": "typesafe/jev-1.13-20260917",
    "answers": {"q": {"type": "choice", "choice": "a"}},
    "usage": {"input_tokens": 476, "output_tokens": 70, "cost": 0.000019992},
}


def test_openrouter_is_asked_at_its_decisions_api_with_its_own_model_name_and_cost():
    j, _ = openrouter(OR_GOOD)
    reply = j.ask({"s": 1}, Q)
    req, _ = opener_of(j).seen[0]
    assert req.full_url == "https://openrouter.ai/api/alpha/decisions"
    assert req.get_header("Authorization") == "Bearer or-key"
    assert isinstance(req.data, bytes) and json.loads(req.data)["model"] == "typesafe/jev-1.13"
    assert (reply.served_by, reply.cost) == ("typesafe/jev-1.13-20260917", 0.000019992)


@pytest.mark.parametrize("usage", [{"input_tokens": 5}, {"cost": True}, {"cost": "0.1"}, "free"])
def test_openrouter_without_a_cost_has_none(usage):
    j, _ = openrouter({**OR_GOOD, "usage": usage})
    assert j.ask("s", Q).cost is None


def test_openrouter_cost_may_be_a_whole_number():
    j, _ = openrouter({**OR_GOOD, "usage": {"cost": 0}})
    assert j.ask("s", Q).cost == 0.0


def test_a_custom_url_is_used():
    j, _ = openrouter(OR_GOOD, url="http://localhost:8080/decisions")
    j.ask("s", Q)
    assert opener_of(j).seen[0][0].full_url == "http://localhost:8080/decisions"


def test_a_custom_url_must_be_http():
    with pytest.raises(ModelError, match=r"^JEVTEST_API_URL must be an http\(s\) URL, got 'ftp://x'$"):
        JevClient(MODEL, "k", LOGGED.append, url="ftp://x")


def test_openrouter_missing_key_names_its_variable():
    with pytest.raises(ModelError, match=r"OPENROUTER_API_KEY is not set: .* https://openrouter.ai/settings/keys$"):
        JevClient("typesafe/jev-1.13", "", LOGGED.append, provider="openrouter")


def test_openrouter_refusing_the_key_names_its_variable():
    j, _ = openrouter(http_error(401, b'{"error":{"code":401,"message":"No auth credentials found"}}'))
    with pytest.raises(
        SetupRefused,
        match=r"^Jev HTTP 401: No auth credentials found \(check OPENROUTER_API_KEY; Create a key at "
        r"https://openrouter.ai/settings/keys\)$",
    ):
        j.ask("s", Q)


@pytest.mark.parametrize(
    "message",
    [
        "Model typesafe/jev-9.9 does not exist",
        "typesafe/jev-9 is not a valid model ID",
        "No endpoints found for typesafe/jev-9.",
        "Model not found",
    ],
)
def test_openrouter_not_serving_the_model_stops_the_run(message):
    j, slept = openrouter(http_error(400, json.dumps({"error": {"code": 400, "message": message}}).encode()))
    with pytest.raises(
        SetupRefused,
        match=r"\(settings: model names a model openrouter serves; this jevtest is tested with typesafe/jev-1\.13\)$",
    ):
        j.ask("s", Q)
    assert not slept


def test_openrouter_in_flight_budget_is_retried_as_asked():
    busy = {
        "error": {
            "code": 402,
            "message": "Too many in flight",
            "metadata": {"limit_source": "openrouter_in_flight_budget"},
        }
    }
    j, slept = openrouter(http_error(402, json.dumps(busy).encode(), retry_after="3"), OR_GOOD)
    assert j.ask("s", Q).answers == OR_GOOD["answers"]
    assert slept == [3.0]


@pytest.mark.parametrize(
    "error",
    [
        {"code": 402, "message": "Insufficient credits"},
        {"code": 402, "message": "Insufficient credits", "metadata": {"limit_source": "key"}},
    ],
)
def test_openrouter_out_of_credits_is_not_retried(error):
    j, slept = openrouter(http_error(402, json.dumps({"error": error}).encode()))
    with pytest.raises(ModelError, match=r"^Jev HTTP 402: Insufficient credits$"):
        j.ask("s", Q)
    assert not slept
