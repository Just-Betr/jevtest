import io
import json
import urllib.error

import pytest

from jevtest.jev import Jev, JevError, choice, noul

MODEL = "typesafe/jev-1.13"

Q = {"q": choice("pick", {"a": None, "b": None})}
GOOD = {"model": "typesafe/jev-1.13-x", "answers": {"q": {"type": "choice", "choice": "a"}},
        "usage": {"input_tokens": 3, "cost": 0.1}}


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
    j = Jev(MODEL, "k", sleep=slept.append, urlopen=opener(*outcomes), log=LOGGED.append, **kw)
    return j, slept


LOGGED: list[str] = []


def test_question_builders():
    assert choice("i", {"a": None}) == {"type": "choice", "instructions": "i", "criteria": {"a": None}}
    assert noul("i") == {"type": "noul", "instructions": "i"}


def test_success_records_call():
    j, slept = client(GOOD)
    assert j.ask({"s": 1}, Q) == GOOD["answers"]
    req, timeout = j._urlopen.seen[0]
    assert req.full_url.endswith("/api/v1/systemone")
    assert req.get_header("Authorization") == "Bearer k"
    assert json.loads(req.data) == {"model": MODEL, "state": {"s": 1}, "questions": Q}
    assert timeout == 30 and not slept
    call = j.calls[0]
    assert call["model"] == "typesafe/jev-1.13-x" and call["usage"]["cost"] == 0.1 and call["ms"] >= 0


def test_missing_usage_is_empty_dict():
    j, _ = client({"answers": GOOD["answers"]})
    j.ask("s", Q)
    assert j.calls[0]["usage"] == {}


def test_retries_rate_limits_then_succeeds():
    LOGGED.clear()
    j, slept = client(http_error(429), http_error(529), GOOD)
    assert j.ask("s", Q) == GOOD["answers"]
    assert slept == [0.5, 1.0]
    assert LOGGED == ["Jev HTTP 429: trying again in 0.5s (retry 1 of 4)",
                      "Jev HTTP 529: trying again in 1s (retry 2 of 4)"]


def test_retries_network_errors_then_succeeds():
    j, slept = client(urllib.error.URLError("down"), TimeoutError(), GOOD)
    j.ask("s", Q)
    assert slept == [0.5, 1.0]


def test_gives_up_after_retries():
    j, slept = client(*[http_error(503)] * 3, retries=2)
    with pytest.raises(JevError, match="HTTP 503"):
        j.ask("s", Q)
    assert slept == [0.5, 1.0]


def test_unreachable_after_retries():
    j, _ = client(urllib.error.URLError("dns"), urllib.error.URLError("dns"), retries=1)
    with pytest.raises(JevError, match="unreachable"):
        j.ask("s", Q)


def test_client_errors_are_not_retried():
    j, slept = client(http_error(401, b"bad key"))
    with pytest.raises(JevError, match="HTTP 401: bad key"):
        j.ask("s", Q)
    assert not slept


def test_invalid_json():
    j, _ = client(b"<html>")
    with pytest.raises(JevError, match="invalid JSON"):
        j.ask("s", Q)


@pytest.mark.parametrize("reply", [{}, {"answers": None}, {"answers": {"other": {}}}])
def test_answers_must_match_questions(reply):
    j, _ = client(reply)
    with pytest.raises(JevError, match="expected"):
        j.ask("s", Q)
    assert not j.calls


def test_missing_key_is_not_looked_up_elsewhere(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "env-key")  # only the key it is given counts
    with pytest.raises(JevError, match="OPENROUTER_API_KEY is not set: put it in the .env next to the test file"):
        Jev(MODEL, None)


def test_retries_are_printed(capsys):
    j = Jev(MODEL, "k", sleep=lambda s: None, urlopen=opener(http_error(503), GOOD))
    j.ask("s", Q)
    assert "Jev HTTP 503: trying again in 0.5s (retry 1 of 4)" in capsys.readouterr().err
