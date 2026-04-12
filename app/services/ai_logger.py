from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional

from app.services.ai_context import build_ai_context

AI_LOG_SCHEMA_VERSION = 1


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _payload_dict(value: Any) -> Dict[str, Any]:
    if value is None:
        return {}
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, dict):
        return dict(value)
    return dict(value)


def _normalized_text(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


def _message_summary(messages: Iterable[Any] | None) -> Dict[str, Any]:
    summary = {
        "message_count": 0,
        "total_characters": 0,
        "role_counts": {
            "system": 0,
            "user": 0,
            "assistant": 0,
            "other": 0,
        },
    }

    if messages is None:
        return summary

    for message in messages:
        payload = _payload_dict(message)
        role = str(payload.get("role") or "").strip().lower()
        content = str(payload.get("content") or "")

        summary["message_count"] += 1
        summary["total_characters"] += len(content)

        if role in summary["role_counts"]:
            summary["role_counts"][role] += 1
        else:
            summary["role_counts"]["other"] += 1

    return summary


def _route_summary(route: Any) -> Dict[str, Any]:
    payload = _payload_dict(route)
    primary = payload.get("primary") or {}
    secondary = payload.get("secondary") or {}

    return {
        "task": _normalized_text(payload.get("task")),
        "fallback_strategy": _normalized_text(payload.get("fallback_strategy")),
        "selected_route": _normalized_text(payload.get("selected_route")),
        "used_fallback": bool(payload.get("used_fallback", False)),
        "primary_provider": _normalized_text(primary.get("provider")),
        "primary_model": _normalized_text(primary.get("model")),
        "secondary_provider": _normalized_text(secondary.get("provider")),
        "secondary_model": _normalized_text(secondary.get("model")),
    }


def _result_summary(result: Any) -> Dict[str, Any]:
    payload = _payload_dict(result)

    return {
        "provider": _normalized_text(payload.get("provider")),
        "model": _normalized_text(payload.get("model")),
        "selected_route": _normalized_text(payload.get("selected_route")),
        "used_fallback": bool(payload.get("used_fallback", False)),
        "estimated_cost_usd": payload.get("estimated_cost_usd"),
        "latency_ms": payload.get("latency_ms"),
        "response_characters": len(str(payload.get("text") or "")),
        "has_raw_response": payload.get("raw_response") is not None,
    }


def _meta_summary(meta: Any) -> Dict[str, Any]:
    payload = _payload_dict(meta)

    return {
        "request_id": _normalized_text(payload.get("request_id")),
        "source": _normalized_text(payload.get("source")),
        "requested_at": _normalized_text(payload.get("requested_at")),
    }


def _error_summary(error: Exception | None, *, stage: Optional[str] = None) -> Dict[str, Any] | None:
    if error is None:
        return None

    return {
        "type": type(error).__name__,
        "stage": _normalized_text(stage),
    }


def _base_log_payload(
    *,
    event: str,
    task: Any,
    context: Any,
    meta: Any = None,
) -> Dict[str, Any]:
    if (
        isinstance(context, dict)
        and isinstance(context.get("access_policy"), dict)
        and isinstance(context.get("guardrails"), dict)
        and context.get("surface")
    ):
        normalized_context = dict(context)
    else:
        normalized_context = build_ai_context(context)

    return {
        "event": event,
        "logged_at": _utc_now_iso(),
        "schema_version": AI_LOG_SCHEMA_VERSION,
        "task": _normalized_text(task),
        "context": normalized_context,
        "meta": _meta_summary(meta),
        "content_redacted": True,
    }


def build_ai_request_log_payload(
    *,
    task: Any,
    context: Any,
    messages: Iterable[Any],
    meta: Any = None,
    route: Any = None,
) -> Dict[str, Any]:
    payload = _base_log_payload(
        event="ai_request",
        task=task,
        context=context,
        meta=meta,
    )
    payload["messages"] = _message_summary(messages)
    payload["route"] = _route_summary(route)
    return payload


def build_ai_response_log_payload(
    *,
    task: Any,
    context: Any,
    messages: Iterable[Any] | None = None,
    meta: Any = None,
    route: Any = None,
    result: Any = None,
) -> Dict[str, Any]:
    payload = _base_log_payload(
        event="ai_response",
        task=task,
        context=context,
        meta=meta,
    )
    payload["messages"] = _message_summary(messages)
    payload["route"] = _route_summary(route)
    payload["result"] = _result_summary(result)
    return payload


def build_ai_error_log_payload(
    *,
    task: Any,
    context: Any,
    error: Exception,
    stage: Optional[str] = None,
    messages: Iterable[Any] | None = None,
    meta: Any = None,
    route: Any = None,
) -> Dict[str, Any]:
    payload = _base_log_payload(
        event="ai_error",
        task=task,
        context=context,
        meta=meta,
    )
    payload["messages"] = _message_summary(messages)
    payload["route"] = _route_summary(route)
    payload["error"] = _error_summary(
        error,
        stage=stage,
    )
    return payload
