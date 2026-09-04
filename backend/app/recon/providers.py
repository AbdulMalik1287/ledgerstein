"""Model backends for the tier 4 adjudicator.

The adjudicator's safety properties -- the candidate whitelist, the confidence
floor, the call budget, the audit entry -- all live *outside* the model call, in
``adjudicator.py``. That is deliberate, and it is what makes swapping backends
cheap: a provider here only has to turn a system prompt and a user prompt into a
dict. It cannot widen what the adjudicator is allowed to believe.

Three are supported. Anthropic is the default and uses the official SDK.
Gemini and Groq are reached over plain HTTP with ``httpx``, which is already a
dependency -- adding two more SDKs to the image to make two POST requests would
be a worse trade than writing the requests out.
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

HTTP_TIMEOUT_SECONDS = 45.0


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
    default_model = "gemini-2.0-flash"
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
    default_model = "llama-3.3-70b-versatile"
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


# ----------------------------------------------------------------- plumbing


def _post(url: str, headers: dict, body: dict, transport=None) -> dict:
    import httpx

    with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS, transport=transport) as client:
        response = client.post(
            url, headers={"Content-Type": "application/json", **headers}, json=body
        )
        if response.status_code >= 400:
            # Trimmed: provider error bodies can be long, and the useful part is
            # always at the front. The full text never reaches the audit trail.
            raise RuntimeError(
                "%s returned %d: %s"
                % (url.split("/")[2], response.status_code, response.text[:300])
            )
        return response.json()


PROVIDERS: dict[str, type] = {
    "anthropic": AnthropicProvider,
    "gemini": GeminiProvider,
    "groq": GroqProvider,
}

ENV_KEYS: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "groq": "GROQ_API_KEY",
}

# Anthropic first: it is the backend the prompt was written against and the one
# whose schema enforcement is strongest. The others are fallbacks that exist so
# the tier can run without a paid account.
AUTO_ORDER = ("anthropic", "gemini", "groq")


def available() -> list[str]:
    """Which backends have a key present, in preference order."""
    return [name for name in AUTO_ORDER if os.environ.get(ENV_KEYS[name])]


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
                "no model credentials are set (looked for %s)"
                % ", ".join(ENV_KEYS[n] for n in AUTO_ORDER)
            )
        provider = ready[0]

    if provider not in PROVIDERS:
        return None, "unknown provider %r, expected one of %s" % (
            provider,
            ", ".join(PROVIDERS),
        )

    env_key = ENV_KEYS[provider]
    if not os.environ.get(env_key):
        return None, "%s is not set" % env_key

    return PROVIDERS[provider](model=model), ""
