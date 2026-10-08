"""Thin OpenAI-compatible client for the shared AI gateway.

Standard library only, so enabling this adds no runtime dependency. Same request
shape, timeout and error wording as the other services in this workspace. The API
key is read from Django settings and never logged.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

from django.conf import settings

ALLOWED_MODELS = ("minimax-m3", "glm-5.3-flash", "kimi-k3")


class AiGatewayError(RuntimeError):
    """Raised when the gateway cannot be reached or returns an error."""


def is_configured() -> bool:
    return bool(getattr(settings, "AI_API_KEY", ""))


def narrative_enabled() -> bool:
    return bool(getattr(settings, "AI_NARRATIVE_SCORES", False)) and is_configured()


def resolve_model(feature: str) -> str:
    """Per-feature override wins, then AI_MODEL_CHAT, then AI_MODEL_DEFAULT."""
    override = getattr(settings, f"AI_MODEL_{feature.upper()}", "")
    if override:
        return override
    chat = getattr(settings, "AI_MODEL_CHAT", "")
    if chat:
        return chat
    return getattr(settings, "AI_MODEL_DEFAULT", "minimax-m3")


def _redact(value: str) -> str:
    return re.sub(r"(sk-[A-Za-z0-9_-]{6})[A-Za-z0-9_-]+", r"\1...", str(value))


def chat_json(
    messages: list[dict[str, str]],
    *,
    feature: str = "CHAT",
    temperature: float = 0.2,
    max_tokens: int = 700,
) -> dict:
    """POST /chat/completions with JSON output. Returns the decoded object."""
    api_key = getattr(settings, "AI_API_KEY", "")
    if not api_key:
        raise AiGatewayError("AI_API_KEY is not configured")

    base_url = str(getattr(settings, "AI_BASE_URL", "")).rstrip("/")
    timeout = float(getattr(settings, "AI_TIMEOUT_SECONDS", 60))
    model = resolve_model(feature)

    payload = json.dumps(
        {
            "model": model,
            "stream": False,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
            "messages": messages,
        },
        ensure_ascii=False,
    ).encode("utf-8")

    request = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=payload,
        headers={
            "content-type": "application/json",
            "authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        # The upstream body is not forwarded: it can echo request headers.
        raise AiGatewayError(f"AI gateway returned {error.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise AiGatewayError(f"AI gateway request failed: {_redact(error)}") from None

    content = body.get("choices", [{}])[0].get("message", {}).get("content") or "{}"
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        raise AiGatewayError("AI gateway returned invalid JSON") from None
