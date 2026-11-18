"""Bounded, cached JSON requests to an operator-configured AI provider."""
from __future__ import annotations

import hashlib
import json
import threading
import time
import urllib.error
import urllib.request
from collections import OrderedDict

from django.conf import settings

_CACHE: OrderedDict[str, tuple[float, str | None]] = OrderedDict()
_LOCK = threading.Lock()
_MAX_CACHE_ENTRIES = 256


class AiGatewayError(RuntimeError):
    """A safe error message, without credentials or upstream response bodies."""


def _setting(name, default=None):
    return getattr(settings, name, default)


def is_configured() -> bool:
    return bool(
        str(_setting("AI_BASE_URL", "") or "").strip()
        and str(_setting("AI_API_KEY", "") or "").strip()
    )


def narrative_enabled() -> bool:
    return bool(_setting("AI_NARRATIVE_SCORES", False)) and is_configured()


def resolve_model(feature: str) -> str:
    for name in (f"AI_MODEL_{feature.upper()}", "AI_MODEL_CHAT", "AI_MODEL_DEFAULT"):
        value = str(_setting(name, "") or "").strip()
        if value:
            return value
    raise AiGatewayError("No AI model configured. Set AI_MODEL_DEFAULT.")


def _remember(key: str, value: dict | None, seconds: float):
    with _LOCK:
        _CACHE[key] = (time.monotonic() + seconds, json.dumps(value) if value else None)
        _CACHE.move_to_end(key)
        while len(_CACHE) > _MAX_CACHE_ENTRIES:
            _CACHE.popitem(last=False)


def _request_json(request, timeout: float, retries: int, required_key: str | None) -> dict:
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read(65537)
            if len(raw) > 65536:
                raise AiGatewayError("AI response exceeded the size limit.")
            body = json.loads(raw)
            choice = body["choices"][0]
            if choice.get("finish_reason") == "length":
                raise AiGatewayError("AI response was incomplete.")
            data = json.loads(choice["message"]["content"])
            if not isinstance(data, dict) or not data:
                raise AiGatewayError("AI response must be a JSON object.")
            if required_key and (not isinstance(data.get(required_key), str) or not data[required_key].strip()):
                raise AiGatewayError("AI response is missing the requested text field.")
            return data
        except urllib.error.HTTPError as error:
            status = error.code
            # Never echo the provider's body, headers, URL, or credential.
            retry_after = error.headers.get("Retry-After", "0") if error.headers else "0"
            error.close()
            can_retry = status in {408, 429, 500, 502, 503, 504}
            if not can_retry or attempt == retries or (retry_after.isdigit() and int(retry_after) > 1):
                raise AiGatewayError(f"AI provider returned HTTP {status}.") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            if attempt == retries:
                raise AiGatewayError("AI provider could not be reached.") from None
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise AiGatewayError("AI provider returned an invalid response.") from None
        time.sleep(0.25 * (2 ** attempt))
    raise AiGatewayError("AI provider could not be reached.")  # pragma: no cover


def chat_json(
    messages: list[dict[str, str]],
    *,
    feature: str = "CHAT",
    temperature: float = 0.2,
    max_tokens: int = 1200,
    required_key: str | None = None,
) -> dict:
    """Request validated JSON; cache successes and briefly back off after failures."""
    if not is_configured():
        raise AiGatewayError("AI_BASE_URL and AI_API_KEY must both be configured.")
    api_key = str(_setting("AI_API_KEY")).strip()
    base_url = str(_setting("AI_BASE_URL")).strip().rstrip("/")
    model = resolve_model(feature)
    payload = {
        "model": model,
        "stream": False,
        "temperature": temperature,
        "response_format": {"type": "json_object"},
        "messages": messages,
    }
    if base_url.startswith("https://api.groq.com/"):
        payload["max_completion_tokens"] = max_tokens
        if model.startswith("openai/gpt-oss"):
            payload.update(reasoning_effort="low", include_reasoning=False)
    else:
        payload["max_tokens"] = max_tokens
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    # Key changes, model changes, and input edits all invalidate the cache.
    key = hashlib.sha256(base_url.encode() + api_key.encode() + (required_key or "").encode() + encoded).hexdigest()
    with _LOCK:
        cached = _CACHE.get(key)
        if cached and cached[0] > time.monotonic():
            if cached[1] is None:
                raise AiGatewayError("AI is temporarily unavailable. Try again shortly.")
            return json.loads(cached[1])
        _CACHE.pop(key, None)
    request = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=encoded,
        headers={
            "content-type": "application/json",
            "authorization": f"Bearer {api_key}",
            "user-agent": "PortfolioAI/1.0",
        },
        method="POST",
    )
    timeout = min(30, max(1, float(_setting("AI_TIMEOUT_SECONDS", 12))))
    retries = min(2, max(0, int(_setting("AI_MAX_RETRIES", 1))))
    try:
        data = _request_json(request, timeout, retries, required_key)
    except AiGatewayError:
        _remember(key, None, 30)
        raise
    _remember(key, data, max(0, float(_setting("AI_CACHE_SECONDS", 900))))
    return data
