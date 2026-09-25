"""Minimal client for TypeSafe's Jev (POST /v1/systemone).

Jev reads text state and answers typed questions (choice / noul / score)
with probabilities. It does not generate text and does not see images.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

# Jev is reached through OpenRouter's TypeSafe-compatible endpoint (same
# request/response shape as api.typesafe.ai/v1/systemone).
API_URL = "https://openrouter.ai/api/v1/systemone"
DEFAULT_MODEL = "~typesafe/jev-latest"
RETRY_STATUSES = {429, 500, 502, 503, 504, 529}


class JevError(RuntimeError):
    pass


def choice(instructions, options: dict) -> dict:
    return {"type": "choice", "instructions": instructions, "criteria": options}


def noul(instructions, true: str | None = None, false: str | None = None) -> dict:
    q = {"type": "noul", "instructions": instructions}
    if true or false:
        q["criteria"] = {"true": true or "Yes", "false": false or "No"}
    return q


class Jev:
    def __init__(self, model: str = DEFAULT_MODEL, api_key: str | None = None,
                 url: str = API_URL, timeout: float = 30, retries: int = 4):
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
        if not self.api_key:
            raise JevError("OPENROUTER_API_KEY is not set. Create a key at https://openrouter.ai/keys")
        self.model = model
        self.url = url
        self.timeout = timeout
        self.retries = retries
        self.calls: list[dict] = []  # every request/response, for the report

    def ask(self, state, questions: dict) -> dict:
        body = json.dumps({"model": self.model, "state": state, "questions": questions}).encode()
        started = time.monotonic()
        for attempt in range(self.retries + 1):
            req = urllib.request.Request(self.url, data=body, method="POST", headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            })
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = json.loads(resp.read())
                break
            except urllib.error.HTTPError as e:
                detail = e.read().decode(errors="replace")[:500]
                if e.code in RETRY_STATUSES and attempt < self.retries:
                    time.sleep(0.5 * 2 ** attempt)
                    continue
                raise JevError(f"Jev HTTP {e.code}: {detail}") from None
            except (urllib.error.URLError, TimeoutError) as e:
                if attempt < self.retries:
                    time.sleep(0.5 * 2 ** attempt)
                    continue
                raise JevError(f"Jev unreachable: {e}") from None
        self.calls.append({
            "ms": round((time.monotonic() - started) * 1000),
            "model": data.get("model"),
            "state": state,
            "questions": questions,
            "answers": data.get("answers"),
            "usage": data.get("usage"),
        })
        return data["answers"]
