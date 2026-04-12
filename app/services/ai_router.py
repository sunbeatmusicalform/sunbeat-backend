from __future__ import annotations

import math
from typing import Any, Dict, Iterable

from app.services.ai_providers import (
    DEFAULT_DEEPSEEK_MODEL,
    DEFAULT_GEMINI_MODEL,
    DEEPSEEK_PROVIDER,
    GEMINI_PROVIDER,
    ai_gateway_enabled,
    call_provider_chat,
)

DEFAULT_AI_TASK = "product"

STATIC_TASK_ROUTES: Dict[str, Dict[str, Dict[str, str]]] = {
    "product": {
        "primary": {"provider": DEEPSEEK_PROVIDER, "model": DEFAULT_DEEPSEEK_MODEL},
        "secondary": {"provider": GEMINI_PROVIDER, "model": DEFAULT_GEMINI_MODEL},
    },
    "onboarding": {
        "primary": {"provider": DEEPSEEK_PROVIDER, "model": DEFAULT_DEEPSEEK_MODEL},
        "secondary": {"provider": GEMINI_PROVIDER, "model": DEFAULT_GEMINI_MODEL},
    },
    "setup": {
        "primary": {"provider": DEEPSEEK_PROVIDER, "model": DEFAULT_DEEPSEEK_MODEL},
        "secondary": {"provider": GEMINI_PROVIDER, "model": DEFAULT_GEMINI_MODEL},
    },
    "release": {
        "primary": {"provider": DEEPSEEK_PROVIDER, "model": DEFAULT_DEEPSEEK_MODEL},
        "secondary": {"provider": GEMINI_PROVIDER, "model": DEFAULT_GEMINI_MODEL},
    },
    "summary": {
        "primary": {"provider": DEEPSEEK_PROVIDER, "model": DEFAULT_DEEPSEEK_MODEL},
        "secondary": {"provider": GEMINI_PROVIDER, "model": DEFAULT_GEMINI_MODEL},
    },
    "schema": {
        "primary": {"provider": GEMINI_PROVIDER, "model": DEFAULT_GEMINI_MODEL},
        "secondary": {"provider": DEEPSEEK_PROVIDER, "model": DEFAULT_DEEPSEEK_MODEL},
    },
    "lyrics": {
        "primary": {"provider": GEMINI_PROVIDER, "model": DEFAULT_GEMINI_MODEL},
        "secondary": {"provider": DEEPSEEK_PROVIDER, "model": DEFAULT_DEEPSEEK_MODEL},
    },
    "classification": {
        "primary": {"provider": DEEPSEEK_PROVIDER, "model": DEFAULT_DEEPSEEK_MODEL},
        "secondary": {"provider": GEMINI_PROVIDER, "model": DEFAULT_GEMINI_MODEL},
    },
    "operations": {
        "primary": {"provider": DEEPSEEK_PROVIDER, "model": DEFAULT_DEEPSEEK_MODEL},
        "secondary": {"provider": GEMINI_PROVIDER, "model": DEFAULT_GEMINI_MODEL},
    },
    "commercial": {
        "primary": {"provider": DEEPSEEK_PROVIDER, "model": DEFAULT_DEEPSEEK_MODEL},
        "secondary": {"provider": GEMINI_PROVIDER, "model": DEFAULT_GEMINI_MODEL},
    },
}

# Placeholder heuristics until pricing/telemetry gets its own layer.
STATIC_PROVIDER_COST_PROFILE: Dict[str, Dict[str, float]] = {
    DEEPSEEK_PROVIDER: {
        "input_per_1k_tokens_usd": 0.0010,
        "output_per_1k_tokens_usd": 0.0020,
    },
    GEMINI_PROVIDER: {
        "input_per_1k_tokens_usd": 0.0015,
        "output_per_1k_tokens_usd": 0.0030,
    },
}


def normalize_task(task: Any) -> str:
    normalized = str(task or "").strip().lower()
    return normalized if normalized in STATIC_TASK_ROUTES else DEFAULT_AI_TASK


def get_task_route(task: Any) -> Dict[str, Any]:
    normalized_task = normalize_task(task)
    route = STATIC_TASK_ROUTES[normalized_task]

    return {
        "task": normalized_task,
        "primary": dict(route["primary"]),
        "secondary": dict(route["secondary"]),
        "fallback_strategy": "static",
    }


def get_task_model(task: Any, *, use_fallback: bool = False) -> str:
    route = get_task_route(task)
    route_key = "secondary" if use_fallback else "primary"
    return str(route[route_key]["model"])


def _estimate_input_tokens(messages: Iterable[Any]) -> int:
    total_characters = 0

    for message in messages:
        if hasattr(message, "model_dump"):
            payload = message.model_dump()
        elif isinstance(message, dict):
            payload = message
        else:
            payload = dict(message)

        total_characters += len(str(payload.get("content") or ""))

    if total_characters <= 0:
        return 1

    return max(1, math.ceil(total_characters / 4))


def estimate_cost(
    *,
    task: Any,
    messages: Iterable[Any],
    expected_output_tokens: int = 400,
    use_fallback: bool = False,
) -> Dict[str, Any]:
    route = get_task_route(task)
    selected_route = route["secondary"] if use_fallback else route["primary"]
    provider = selected_route["provider"]
    pricing = STATIC_PROVIDER_COST_PROFILE[provider]

    estimated_input_tokens = _estimate_input_tokens(messages)
    estimated_cost_usd = (
        (estimated_input_tokens / 1000) * pricing["input_per_1k_tokens_usd"]
        + (max(1, expected_output_tokens) / 1000) * pricing["output_per_1k_tokens_usd"]
    )

    return {
        "task": route["task"],
        "provider": provider,
        "model": selected_route["model"],
        "used_fallback": use_fallback,
        "estimated_input_tokens": estimated_input_tokens,
        "expected_output_tokens": max(1, expected_output_tokens),
        "estimated_cost_usd": round(estimated_cost_usd, 6),
    }


def run_task_with_fallback(
    *,
    task: Any,
    messages: Iterable[Any],
    temperature: float | None = None,
) -> Dict[str, Any]:
    if not ai_gateway_enabled():
        raise RuntimeError("AI_GATEWAY_ENABLED is disabled")

    route = get_task_route(task)
    primary = route["primary"]
    secondary = route["secondary"]
    messages_payload = list(messages)

    try:
        result = call_provider_chat(
            provider=primary["provider"],
            model=primary["model"],
            messages=messages_payload,
            temperature=temperature,
        )
        return result | {
            "task": route["task"],
            "used_fallback": False,
            "selected_route": "primary",
            "primary": primary,
            "secondary": secondary,
        }
    except Exception as primary_exc:
        primary_error = str(primary_exc)

    try:
        result = call_provider_chat(
            provider=secondary["provider"],
            model=secondary["model"],
            messages=messages_payload,
            temperature=temperature,
        )
        return result | {
            "task": route["task"],
            "used_fallback": True,
            "selected_route": "secondary",
            "primary": primary,
            "secondary": secondary,
            "primary_error": primary_error,
        }
    except Exception as secondary_exc:
        raise RuntimeError(
            f"AI task failed on primary and secondary routes: {primary_error}; {secondary_exc}"
        ) from secondary_exc
