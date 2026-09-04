"""Model backends for the tier 4 adjudicator.

The adjudicator's safety properties -- the candidate whitelist, the confidence
floor, the call budget, the audit entry -- all live *outside* the model call, in
``adjudicator.py``. That is deliberate, and it is what makes swapping backends
cheap: a provider here only has to turn a system prompt and a user prompt into a
dict. It cannot widen what the adjudicator is allowed to believe.

Four are supported. Anthropic is the default and uses the official SDK. Gemini
and Groq are reached over plain HTTP with ``httpx``, which is already a
dependency -- adding SDKs to the image to make one POST request each would be a
worse trade than writing the requests out. Ollama is the same shape, pointed at
a model running on the machine.

Ollama exists here because every hosted free tier eventually says no. Keys
expire mid-session, quotas exhaust after a couple of dozen calls, and a demo
that depends on somebody else's rate limiter is a demo that can fail while it is
being watched. A local model has no key, no quota and no network.
"""

from __future__ import annotations

import json
import os
from typing import Protocol

# The JSON contract every backend must return. Anthropic enforces it with a
# Pydantic schema; the others are handed it as a schema or as prose, and the
# result is validated on the way back regardless.
VERDICT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "decision": {
            "type": "string",
            "enum": ["match", "decline"],
            "description": "Either 'match' or 'decline'.",
        },
        "invoice_no": {
            "type": "string",
            "description": (
                "The chosen invoice_no, copied exactly from the candidate list. "
                "Empty string when declining."
            ),
        },
        "reason": {
            "type": "string",
            "description": "One sentence a finance controller can act on.",
        },
        "confidence": {
            "type": "number",
            "description": "0 to 1, reflecting how much the evidence separates candidates.",
        },
    },
    "required": ["decision", "invoice_no", "reason", "confidence"],
    "additionalProperties": False,
}

JSON_ONLY_SUFFIX = """

Reply with JSON only, no prose and no code fence, in exactly this shape:
{"decision": "match" | "decline", "invoice_no": "<exact id from the list, or empty string>", "reason": "<one sentence>", "confidence": <number between 0 and 1>}"""

HTTP_TIMEOUT_SECONDS = 120.0
"""Generous on purpose. Current reasoning models spend real time on a judgement
call, and a timeout here is not a free failure -- it drops a row back into the
queue that the tier was asked to decide."""


class Provider(Protocol):
    """Turns two prompts into a dict. Nothing more is trusted of it."""

    name: str
    model: str

    def complete(self, system: str, prompt: str) -> dict: ...


# --------------------------------------------------------------- anthropic


class AnthropicProvider:
    """The default. Uses the official SDK's schema-validated parse helper."""

    name = "anthropic"
    default_model = "claude-opus-5"

    def __init__(self, model: str = "", client=None) -> None:
        self.model = model or self.default_model
        self._client = client

    def complete(self, system: str, prompt: str) -> dict:
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic()

        from .adjudicator import Verdict

        response = self._client.messages.parse(
            model=self.model,
            max_tokens=2000,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_format=Verdict,
        )
        return response.parsed_output.model_dump()


# ------------------------------------------------------------------ gemini


class GeminiProvider:
    """Google's free tier. No card required, which is why it is here."""

    name = "gemini"
    default_model = "gemini-3.6-flash"
    endpoint = "https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent"

    def __init__(self, model: str = "", api_key: str = "", transport=None) -> None:
        self.model = model or self.default_model
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY", "")
        self._transport = transport

    def complete(self, system: str, prompt: str) -> dict:
        body = {
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": _gemini_schema(),
                # A reconciliation verdict should not vary between identical
                # runs, so nothing here is left to sampling.
                "temperature": 0,
            },
        }
        payload = _post(
            self.endpoint % self.model,
            headers={"x-goog-api-key": self.api_key},
            body=body,
            transport=self._transport,
        )
        text = payload["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(text)


def _gemini_schema() -> dict:
    """The verdict schema in the OpenAPI dialect Gemini expects."""
    return {
        "type": "OBJECT",
        "properties": {
            "decision": {"type": "STRING", "enum": ["match", "decline"]},
            "invoice_no": {"type": "STRING"},
            "reason": {"type": "STRING"},
            "confidence": {"type": "NUMBER"},
        },
        "required": ["decision", "invoice_no", "reason", "confidence"],
    }


# -------------------------------------------------------------------- groq


class GroqProvider:
    """Groq's free tier, over the OpenAI-compatible endpoint."""

    name = "groq"
    default_model = "openai/gpt-oss-120b"
    endpoint = "https://api.groq.com/openai/v1/chat/completions"

    def __init__(self, model: str = "", api_key: str = "", transport=None) -> None:
        self.model = model or self.default_model
        self.api_key = api_key or os.environ.get("GROQ_API_KEY", "")
        self._transport = transport

    def complete(self, system: str, prompt: str) -> dict:
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                # JSON mode on this endpoint requires the word JSON in the
                # conversation, so the shape is restated rather than assumed.
                {"role": "user", "content": prompt + JSON_ONLY_SUFFIX},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0,
        }
        payload = _post(
            self.endpoint,
            headers={"Authorization": "Bearer " + self.api_key},
            body=body,
            transport=self._transport,
        )
        return json.loads(payload["choices"][0]["message"]["content"])


# ------------------------------------------------------------------ ollama


class OllamaProvider:
    """A model running on this machine. No key, no quota, no network.

    Slower than a hosted model and usually less capable, but it cannot rate-limit
    you halfway through a demo, which on the day is often the property that
    matters more.
    """

    name = "ollama"
    default_model = "qwen3:4b"
    default_host = "http://localhost:11434"

    def __init__(self, model: str = "", host: str = "", transport=None) -> None:
        self.model = model or os.environ.get("OLLAMA_MODEL", "") or self.default_model
        self.host = (
            host or os.environ.get("OLLAMA_HOST", "") or self.default_host
        ).rstrip("/")
        self._transport = transport

    def complete(self, system: str, prompt: str) -> dict:
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt + JSON_ONLY_SUFFIX},
            ],
            # Ollama takes a JSON schema directly as the response format.
            "format": _ollama_schema(),
            "stream": False,
            "options": {"temperature": 0},
        }
        payload = _post(
            self.host + "/api/chat",
            headers={},
            body=body,
            transport=self._transport,
        )
        return json.loads(payload["message"]["content"])

    @classmethod
    def is_reachable(cls, host: str = "") -> bool:
        """Cheap local probe. A hosted backend is checked by key; this one has
        no key to check, so reachability is the only honest test."""
        import httpx

        target = (
            host or os.environ.get("OLLAMA_HOST", "") or cls.default_host
        ).rstrip("/")
        try:
            return httpx.get(target + "/api/tags", timeout=1.5).status_code == 200
        except Exception:  # noqa: BLE001 - unreachable is the answer, not an error
            return False


def _ollama_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "decision": {"type": "string", "enum": ["match", "decline"]},
            "invoice_no": {"type": "string"},
            "reason": {"type": "string"},
            "confidence": {"type": "number"},
        },
        "required": ["decision", "invoice_no", "reason", "confidence"],
    }


# ----------------------------------------------------------------- plumbing


RETRYABLE_STATUS = (429, 500, 502, 503, 504)
MAX_ATTEMPTS = 3


def _post(url: str, headers: dict, body: dict, transport=None) -> dict:
    """POST with a short bounded retry on transient failures.

    Free tiers return 503 under load often enough that a single attempt loses
    rows the tier was asked to decide. Retries are capped at three and only
    cover statuses that mean "try again" -- a 400 or a 401 is a fact, not a
    blip, and retrying it just burns the clock.
    """
    import time

    import httpx

    host = url.split("/")[2]
    last = ""
    with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS, transport=transport) as client:
        for attempt in range(MAX_ATTEMPTS):
            try:
                response = client.post(
                    url,
                    headers={"Content-Type": "application/json", **headers},
                    json=body,
                )
            except httpx.TimeoutException as error:
                last = "timed out (%s)" % type(error).__name__
                if attempt == MAX_ATTEMPTS - 1:
                    break
                time.sleep(2**attempt)
                continue

            if response.status_code < 400:
                return response.json()

            # Trimmed: provider error bodies can be long and the useful part is
            # always at the front. The full text never reaches the audit trail.
            last = "returned %d: %s" % (
                response.status_code,
                " ".join(response.text[:200].split()),
            )
            if response.status_code not in RETRYABLE_STATUS:
                break
            if attempt < MAX_ATTEMPTS - 1:
                time.sleep(2**attempt)

    raise RuntimeError("%s %s" % (host, last))


PROVIDERS: dict[str, type] = {
    "anthropic": AnthropicProvider,
    "gemini": GeminiProvider,
    "groq": GroqProvider,
    "ollama": OllamaProvider,
}

ENV_KEYS: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "groq": "GROQ_API_KEY",
}
"""Ollama is absent on purpose: it has no key, and is detected by reachability."""

# Anthropic first: it is the backend the prompt was written against and the one
# whose schema enforcement is strongest. The others are fallbacks that exist so
# the tier can run without a paid account.
AUTO_ORDER = ("anthropic", "gemini", "groq", "ollama")


def available() -> list[str]:
    """Which backends are usable right now, in preference order.

    Hosted backends are usable if their key is set; Ollama if it answers. The
    local one is checked last so a probe only runs when nothing else is
    configured.
    """
    ready = [
        name for name in AUTO_ORDER if name in ENV_KEYS and os.environ.get(ENV_KEYS[name])
    ]
    if OllamaProvider.is_reachable():
        ready.append("ollama")
    return ready


def build(provider: str = "auto", model: str = "") -> tuple[Provider | None, str]:
    """Resolve a backend, or explain why there isn't one.

    Returns ``(provider, reason)``. A ``None`` provider is not an error -- the
    adjudicator treats it as "skip the tier and log why", which keeps a run
    without credentials honest rather than crashing it.
    """
    if provider == "auto":
        ready = available()
        if not ready:
            return None, (
                "no model backend is available (looked for %s, and for a local "
                "Ollama server on %s)"
                % (
                    ", ".join(ENV_KEYS[n] for n in AUTO_ORDER if n in ENV_KEYS),
                    OllamaProvider.default_host,
                )
            )
        provider = ready[0]

    if provider not in PROVIDERS:
        return None, "unknown provider %r, expected one of %s" % (
            provider,
            ", ".join(PROVIDERS),
        )

    if provider == "ollama":
        if not OllamaProvider.is_reachable():
            return None, (
                "no Ollama server is answering on %s"
                % OllamaProvider(model=model).host
            )
        return OllamaProvider(model=model), ""

    env_key = ENV_KEYS[provider]
    if not os.environ.get(env_key):
        return None, "%s is not set" % env_key

    return PROVIDERS[provider](model=model), ""
