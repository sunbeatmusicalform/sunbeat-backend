from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

import httpx

from app.core.config import settings

DEEPSEEK_PROVIDER = "deepseek"
GEMINI_PROVIDER = "gemini"

DEFAULT_DEEPSEEK_MODEL = "deepseek-chat"
DEFAULT_GEMINI_MODEL = "gemini-3-flash-preview"


def ai_gateway_enabled() -> bool:
    return bool(getattr(settings, "AI_GATEWAY_ENABLED", False))


def ai_request_timeout_seconds() -> float:
    value = getattr(settings, "AI_REQUEST_TIMEOUT_SECONDS", 30)
    try:
        timeout = float(value)
    except (TypeError, ValueError):
        return 30.0

    return timeout if timeout > 0 else 30.0


def _require_ai_gateway_enabled() -> None:
    if not ai_gateway_enabled():
        raise RuntimeError("AI_GATEWAY_ENABLED is disabled")


def _deepseek_api_key() -> str:
    if not settings.DEEPSEEK_API_KEY:
        raise RuntimeError("DEEPSEEK_API_KEY is not configured")
    return settings.DEEPSEEK_API_KEY


def _deepseek_api_base_url() -> str:
    return str(getattr(settings, "DEEPSEEK_API_BASE_URL", "") or "https://api.deepseek.com").rstrip("/")


def _gemini_api_key() -> str:
    if not settings.GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is not configured")
    return settings.GEMINI_API_KEY


def _gemini_api_base_url() -> str:
    return str(
        getattr(settings, "GEMINI_API_BASE_URL", "") or "https://generativelanguage.googleapis.com"
    ).rstrip("/")


def provider_is_configured(provider: Any) -> bool:
    normalized_provider = str(provider or "").strip().lower()

    if normalized_provider == DEEPSEEK_PROVIDER:
        return bool(settings.DEEPSEEK_API_KEY)

    if normalized_provider == GEMINI_PROVIDER:
        return bool(settings.GEMINI_API_KEY)

    return False


def provider_configuration_snapshot() -> Dict[str, Dict[str, Any]]:
    return {
        DEEPSEEK_PROVIDER: {
            "configured": provider_is_configured(DEEPSEEK_PROVIDER),
            "default_model": DEFAULT_DEEPSEEK_MODEL,
        },
        GEMINI_PROVIDER: {
            "configured": provider_is_configured(GEMINI_PROVIDER),
            "default_model": DEFAULT_GEMINI_MODEL,
        },
    }


def _message_dict(message: Any) -> Dict[str, Any]:
    if hasattr(message, "model_dump"):
        data = message.model_dump()
    elif isinstance(message, dict):
        data = message
    else:
        data = dict(message)

    role = str(data.get("role") or "user").strip().lower()
    content = str(data.get("content") or "").strip()
    name = str(data.get("name") or "").strip() or None

    if not content:
        raise RuntimeError("AI provider messages require non-empty content")

    normalized = {
        "role": role,
        "content": content,
    }
    if name:
        normalized["name"] = name

    return normalized


def _normalize_messages(messages: Iterable[Any]) -> List[Dict[str, Any]]:
    normalized = [_message_dict(message) for message in messages]
    if not normalized:
        raise RuntimeError("At least one AI provider message is required")
    return normalized


def _http_json(
    *,
    method: str,
    url: str,
    headers: Dict[str, str],
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    with httpx.Client(timeout=ai_request_timeout_seconds()) as client:
        response = client.request(
            method=method.upper(),
            url=url,
            headers=headers,
            json=payload,
        )

    try:
        data = response.json()
    except Exception:
        data = {"raw": response.text}

    if response.status_code >= 400:
        raise RuntimeError(f"AI provider HTTP {response.status_code}: {data}")

    if not isinstance(data, dict):
        raise RuntimeError("AI provider response must be a JSON object")

    return data


def _extract_deepseek_text(data: Dict[str, Any]) -> str:
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("DeepSeek response did not include choices")

    message = choices[0].get("message") or {}
    content = message.get("content")

    if isinstance(content, str) and content.strip():
        return content.strip()

    if isinstance(content, list):
        text_parts = [
            str(part.get("text") or "").strip()
            for part in content
            if isinstance(part, dict) and str(part.get("text") or "").strip()
        ]
        if text_parts:
            return "\n".join(text_parts)

    raise RuntimeError("DeepSeek response did not include message content")


def _extract_gemini_text(data: Dict[str, Any]) -> str:
    candidates = data.get("candidates") or []
    if not candidates:
        raise RuntimeError("Gemini response did not include candidates")

    content = candidates[0].get("content") or {}
    parts = content.get("parts") or []
    text_parts = [
        str(part.get("text") or "").strip()
        for part in parts
        if isinstance(part, dict) and str(part.get("text") or "").strip()
    ]

    if text_parts:
        return "\n".join(text_parts)

    raise RuntimeError("Gemini response did not include text parts")


def call_deepseek_chat(
    *,
    messages: Iterable[Any],
    model: Optional[str] = None,
    temperature: Optional[float] = None,
) -> Dict[str, Any]:
    _require_ai_gateway_enabled()

    selected_model = str(model or DEFAULT_DEEPSEEK_MODEL).strip() or DEFAULT_DEEPSEEK_MODEL
    payload: Dict[str, Any] = {
        "model": selected_model,
        "messages": _normalize_messages(messages),
        "stream": False,
    }
    if temperature is not None:
        payload["temperature"] = temperature

    data = _http_json(
        method="POST",
        url=f"{_deepseek_api_base_url()}/chat/completions",
        headers={
            "Authorization": f"Bearer {_deepseek_api_key()}",
            "Content-Type": "application/json",
        },
        payload=payload,
    )

    return {
        "provider": DEEPSEEK_PROVIDER,
        "model": selected_model,
        "text": _extract_deepseek_text(data),
        "raw_response": data,
    }


def _build_gemini_payload(
    *,
    messages: Iterable[Any],
    temperature: Optional[float] = None,
) -> Dict[str, Any]:
    normalized_messages = _normalize_messages(messages)
    system_instructions: List[str] = []
    contents: List[Dict[str, Any]] = []

    for message in normalized_messages:
        role = message["role"]
        content = message["content"]

        if role == "system":
            system_instructions.append(content)
            continue

        gemini_role = "model" if role == "assistant" else "user"
        contents.append(
            {
                "role": gemini_role,
                "parts": [{"text": content}],
            }
        )

    if not contents:
        raise RuntimeError("Gemini payload requires at least one non-system message")

    payload: Dict[str, Any] = {
        "contents": contents,
    }
    if system_instructions:
        payload["system_instruction"] = {
            "parts": [{"text": "\n\n".join(system_instructions)}],
        }
    if temperature is not None:
        payload["generationConfig"] = {"temperature": temperature}

    return payload


def call_gemini_chat(
    *,
    messages: Iterable[Any],
    model: Optional[str] = None,
    temperature: Optional[float] = None,
) -> Dict[str, Any]:
    _require_ai_gateway_enabled()

    selected_model = str(model or DEFAULT_GEMINI_MODEL).strip() or DEFAULT_GEMINI_MODEL
    payload = _build_gemini_payload(
        messages=messages,
        temperature=temperature,
    )

    data = _http_json(
        method="POST",
        url=f"{_gemini_api_base_url()}/v1beta/models/{selected_model}:generateContent",
        headers={
            "x-goog-api-key": _gemini_api_key(),
            "Content-Type": "application/json",
        },
        payload=payload,
    )

    return {
        "provider": GEMINI_PROVIDER,
        "model": selected_model,
        "text": _extract_gemini_text(data),
        "raw_response": data,
    }


def call_provider_chat(
    *,
    provider: str,
    messages: Iterable[Any],
    model: Optional[str] = None,
    temperature: Optional[float] = None,
) -> Dict[str, Any]:
    normalized_provider = str(provider or "").strip().lower()

    if normalized_provider == DEEPSEEK_PROVIDER:
        return call_deepseek_chat(
            messages=messages,
            model=model,
            temperature=temperature,
        )

    if normalized_provider == GEMINI_PROVIDER:
        return call_gemini_chat(
            messages=messages,
            model=model,
            temperature=temperature,
        )

    raise RuntimeError(f"Unsupported AI provider: {provider}")
