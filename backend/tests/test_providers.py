"""Backend wiring, checked against a mock transport rather than a live API.

These tests cannot prove Gemini or Groq accept the request -- only their servers
can do that. What they do prove is the half that is ours: the request is shaped
the way each API documents, the schema and JSON-mode switches are actually set,
the response is read from the right place, and an error status becomes a raised
exception rather than a silently malformed verdict.

That matters because the adjudicator treats a raised exception as "log it and
leave the row in the queue". A parsing mistake that returned a half-empty dict
instead would slip past into a match.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.recon import providers
from app.recon.providers import GeminiProvider, GroqProvider, OllamaProvider

VERDICT = {
    "decision": "match",
    "invoice_no": "INV-2026-0001",
    "reason": "Older of the two, and the ERP still shows it open.",
    "confidence": 0.71,
}


def capture(payload: dict, status: int = 200):
    """A transport that records the request and replays a canned response."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        seen["body"] = json.loads(request.content)
        return httpx.Response(status, json=payload)

    return httpx.MockTransport(handler), seen


# ------------------------------------------------------------------ gemini


def _gemini_response(text: str) -> dict:
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


def test_gemini_sends_the_documented_shape():
    transport, seen = capture(_gemini_response(json.dumps(VERDICT)))
    provider = GeminiProvider(api_key="k", transport=transport)

    assert provider.complete("SYSTEM", "PROMPT") == VERDICT

    assert "generativelanguage.googleapis.com" in seen["url"]
    assert provider.model in seen["url"]
    assert seen["url"].endswith(":generateContent")
    assert seen["headers"]["x-goog-api-key"] == "k"

    body = seen["body"]
    assert body["system_instruction"]["parts"][0]["text"] == "SYSTEM"
    assert body["contents"][0]["parts"][0]["text"] == "PROMPT"

    # Structured output and determinism are the two settings that make a
    # verdict parseable and repeatable; both must actually be on the wire.
    config = body["generationConfig"]
    assert config["responseMimeType"] == "application/json"
    assert config["temperature"] == 0
    schema = config["responseSchema"]
    assert schema["type"] == "OBJECT"
    assert set(schema["required"]) == {
        "decision",
        "invoice_no",
        "reason",
        "confidence",
    }


def test_gemini_surfaces_an_error_status():
    transport, _ = capture({"error": {"message": "bad request"}}, status=400)
    with pytest.raises(RuntimeError, match="400"):
        GeminiProvider(api_key="k", transport=transport).complete("s", "p")


# -------------------------------------------------------------------- groq


def _groq_response(text: str) -> dict:
    return {"choices": [{"message": {"content": text}}]}


def test_groq_sends_the_documented_shape():
    transport, seen = capture(_groq_response(json.dumps(VERDICT)))
    provider = GroqProvider(api_key="k", transport=transport)

    assert provider.complete("SYSTEM", "PROMPT") == VERDICT

    assert seen["url"] == GroqProvider.endpoint
    assert seen["headers"]["authorization"] == "Bearer k"

    body = seen["body"]
    assert body["model"] == provider.model
    assert body["response_format"] == {"type": "json_object"}
    assert body["temperature"] == 0
    assert body["messages"][0] == {"role": "system", "content": "SYSTEM"}

    # This endpoint rejects JSON mode unless the word appears in the
    # conversation, so the shape is restated in the user turn on purpose.
    user = body["messages"][1]["content"]
    assert user.startswith("PROMPT")
    assert "JSON" in user


def test_groq_surfaces_an_error_status():
    transport, _ = capture({"error": "bad key"}, status=401)
    with pytest.raises(RuntimeError, match="401"):
        GroqProvider(api_key="k", transport=transport).complete("s", "p")


# --------------------------------------------------------------- selection


def test_auto_prefers_anthropic_then_gemini_then_groq(monkeypatch):
    for key in providers.ENV_KEYS.values():
        monkeypatch.delenv(key, raising=False)

    monkeypatch.setenv("GROQ_API_KEY", "g")
    assert providers.build("auto")[0].name == "groq"

    monkeypatch.setenv("GEMINI_API_KEY", "g")
    assert providers.build("auto")[0].name == "gemini"

    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    assert providers.build("auto")[0].name == "anthropic"


def test_no_credentials_explains_what_it_looked_for(monkeypatch):
    for key in providers.ENV_KEYS.values():
        monkeypatch.delenv(key, raising=False)

    backend, reason = providers.build("auto")
    assert backend is None
    for key in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY"):
        assert key in reason


def test_an_explicitly_named_provider_without_its_key_is_refused(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "g")

    # Naming a provider must not silently fall through to a different one --
    # a run's audit trail has to name the model that actually answered.
    backend, reason = providers.build("gemini")
    assert backend is None
    assert "GEMINI_API_KEY" in reason


def test_an_unknown_provider_is_refused(monkeypatch):
    backend, reason = providers.build("gpt5")
    assert backend is None
    assert "unknown provider" in reason


def test_a_model_override_reaches_the_provider(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    backend, _ = providers.build("gemini", model="gemini-2.5-flash")
    assert backend.model == "gemini-2.5-flash"


# ----------------------------------------------------------------- retries


def counting_transport(statuses: list[int], payload: dict):
    """Replays a sequence of statuses, recording how many attempts were made."""
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        status = statuses[min(attempts["n"], len(statuses) - 1)]
        attempts["n"] += 1
        return httpx.Response(status, json=payload)

    return httpx.MockTransport(handler), attempts


def test_a_transient_503_is_retried_and_can_succeed(monkeypatch):
    """Free tiers return 503 under load. One attempt loses the row."""
    monkeypatch.setattr("time.sleep", lambda _: None)
    transport, attempts = counting_transport(
        [503, 200], _gemini_response(json.dumps(VERDICT))
    )
    provider = GeminiProvider(api_key="k", transport=transport)

    assert provider.complete("s", "p") == VERDICT
    assert attempts["n"] == 2


def test_retries_are_bounded(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    transport, attempts = counting_transport([503], {"error": "down"})

    with pytest.raises(RuntimeError, match="503"):
        GeminiProvider(api_key="k", transport=transport).complete("s", "p")
    assert attempts["n"] == providers.MAX_ATTEMPTS


def test_a_client_error_is_not_retried(monkeypatch):
    """A 401 is a fact, not a blip. Retrying it only burns the clock."""
    monkeypatch.setattr("time.sleep", lambda _: None)
    transport, attempts = counting_transport([401], {"error": "bad key"})

    with pytest.raises(RuntimeError, match="401"):
        GroqProvider(api_key="k", transport=transport).complete("s", "p")
    assert attempts["n"] == 1


# ------------------------------------------------------------------ ollama


def _ollama_response(text: str) -> dict:
    return {"message": {"content": text}}


def test_ollama_sends_the_documented_shape():
    transport, seen = capture(_ollama_response(json.dumps(VERDICT)))
    provider = OllamaProvider(host="http://localhost:11434", transport=transport)

    assert provider.complete("SYSTEM", "PROMPT") == VERDICT

    assert seen["url"] == "http://localhost:11434/api/chat"
    body = seen["body"]
    assert body["model"] == provider.model
    assert body["stream"] is False
    assert body["options"]["temperature"] == 0
    assert body["messages"][0] == {"role": "system", "content": "SYSTEM"}
    assert body["messages"][1]["content"].startswith("PROMPT")

    # Ollama takes a JSON schema directly, so structured output is enforced by
    # the server rather than hoped for in the prompt.
    schema = body["format"]
    assert schema["type"] == "object"
    assert set(schema["required"]) == {
        "decision",
        "invoice_no",
        "reason",
        "confidence",
    }


def test_ollama_honours_host_and_model_env(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "http://box:9999/")
    monkeypatch.setenv("OLLAMA_MODEL", "llama3.2:3b")

    provider = OllamaProvider()
    assert provider.host == "http://box:9999"
    assert provider.model == "llama3.2:3b"


def test_ollama_is_selected_only_when_it_answers(monkeypatch):
    for key in providers.ENV_KEYS.values():
        monkeypatch.delenv(key, raising=False)

    # A local backend has no key to check, so reachability is the only honest
    # test of whether it can be used.
    monkeypatch.setattr(OllamaProvider, "is_reachable", classmethod(lambda cls, host="": False))
    backend, reason = providers.build("auto")
    assert backend is None
    assert "Ollama" in reason

    monkeypatch.setattr(OllamaProvider, "is_reachable", classmethod(lambda cls, host="": True))
    backend, _ = providers.build("auto")
    assert backend is not None and backend.name == "ollama"
    assert providers.available() == ["ollama"]


def test_a_hosted_key_outranks_a_running_ollama(monkeypatch):
    """Ollama is the fallback, not the preference -- it is usually the weakest
    model on offer, so it only wins when nothing else is configured."""
    for key in providers.ENV_KEYS.values():
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(OllamaProvider, "is_reachable", classmethod(lambda cls, host="": True))
    monkeypatch.setenv("GROQ_API_KEY", "g")

    assert providers.build("auto")[0].name == "groq"
