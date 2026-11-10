"""TypeSafe's hosted JEV as a drop-in for the local Laya model, so the guard can run against either.

Same state and question as the MLX server (policy.py); only the forward pass moves to
`https://api.typesafe.ai/v1/systemone`. Used to compare the two on latency and accuracy.

Environment:
  TYPESAFE_API_KEY   bearer token for the TypeSafe API (required)
  LAYA_GUARD_JEV_MODEL  JEV model id (default jev-latest)
"""

import json
import os
import time
import urllib.error
import urllib.request

ENDPOINT = "https://api.typesafe.ai/v1/systemone"
RETRYABLE = {429, 500, 502, 503, 504, 529}


class JevModel:
    def __init__(self, model: str | None = None, api_key: str | None = None, timeout: float = 30.0):
        self.model = model or os.environ.get("LAYA_GUARD_JEV_MODEL", "jev-latest")
        self.api_key = api_key or os.environ.get("TYPESAFE_API_KEY")
        if not self.api_key:
            raise RuntimeError("TYPESAFE_API_KEY is not set")
        self.timeout = timeout
        self.resolved = None  # the version `jev-latest` points to, from the last response

    @property
    def name(self) -> str:
        return f"typesafe:{self.resolved or self.model}"

    def predict(self, state, questions: dict) -> dict:
        # Laya calls yes/no questions `noul`; JEV calls them `bool`.
        questions = {name: {**q, "type": "bool"} if q.get("type") == "noul" else q for name, q in questions.items()}
        body = json.dumps({"model": self.model, "state": state, "questions": questions}).encode()
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "laya-guard-jev/0.1",
        }
        for attempt in range(4):
            if attempt:
                time.sleep(0.5 * 2**attempt)
            try:
                request = urllib.request.Request(ENDPOINT, body, headers)
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    data = json.load(response)
            except urllib.error.HTTPError as error:
                if error.code in RETRYABLE:
                    continue
                raise ValueError(f"JEV HTTP {error.code}: {error.read()[:300]!r}") from None
            except (urllib.error.URLError, TimeoutError):
                continue
            self.resolved = data.get("model") or self.resolved
            return {**data, "answers": {name: _with_probabilities(a, questions.get(name, {})) for name, a in data.get("answers", {}).items()}}
        raise ValueError("JEV retries exhausted")


def _with_probabilities(answer: dict, question: dict) -> dict:
    """policy.verdict reads `probabilities`; fill them from `choice` + `confidence` when JEV leaves them out."""
    if answer.get("type") != "choice" or "probabilities" in answer:
        return answer
    options = list(question.get("criteria") or [])
    choice, confidence = answer.get("choice"), float(answer.get("confidence") or 1.0)
    if choice not in options:
        return answer
    rest = (1.0 - confidence) / max(1, len(options) - 1)
    return {**answer, "probabilities": {o: confidence if o == choice else rest for o in options}}
