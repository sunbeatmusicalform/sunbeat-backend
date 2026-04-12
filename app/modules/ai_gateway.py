from __future__ import annotations

import time
from typing import Any, Dict
from uuid import uuid4

from fastapi import APIRouter, HTTPException

from app.core.config import settings
from app.schemas.ai_gateway import (
    AIChatRequestPayload,
    AIChatResponsePayload,
    AIMessagePayload,
    AIRequestMetaPayload,
    AIResponseMetaPayload,
)
from app.services.ai_context import build_ai_context
from app.services.ai_governance import (
    build_workspace_governance_snapshot,
    is_public_domain_ai_enabled,
    is_surface_ai_enabled,
    is_surface_phase_eligible,
    is_workspace_ai_enabled,
    surface_allows_task,
)
from app.services.ai_log_sink import sink_ai_log_payload
from app.services.ai_logger import (
    build_ai_error_log_payload,
    build_ai_request_log_payload,
    build_ai_response_log_payload,
)
from app.services.ai_readiness import build_ai_readiness_snapshot
from app.services.ai_context import is_protected_workspace
from app.services.ai_providers import ai_gateway_enabled, provider_is_configured
from app.services.ai_router import (
    estimate_cost,
    get_task_route,
    normalize_task,
    run_task_with_fallback,
)

router = APIRouter(prefix="/ai", tags=["ai_gateway"])
MAX_AI_MESSAGES = 20
MAX_AI_MESSAGE_CHARACTERS = 4000
MAX_AI_TOTAL_CHARACTERS = 12000
MAX_SYSTEM_MESSAGES = 1


def _error_detail(
    *,
    code: str,
    message: str,
    stage: str,
    request_id: str | None = None,
) -> Dict[str, Any]:
    return {
        "code": code,
        "message": message,
        "stage": stage,
        "request_id": request_id,
    }


def _meta_payload(meta: AIRequestMetaPayload | None) -> Dict[str, Any]:
    if meta is None:
        return {}
    return meta.model_dump()


def _request_meta(payload: AIChatRequestPayload) -> Dict[str, Any]:
    meta = _meta_payload(payload.meta)
    request_id = str(meta.get("request_id") or uuid4())

    return {
        "request_id": request_id,
        "source": meta.get("source"),
        "requested_at": meta.get("requested_at"),
    }


def _context_payload(context: Any) -> Dict[str, Any]:
    if hasattr(context, "model_dump"):
        return context.model_dump()
    if isinstance(context, dict):
        return dict(context)
    return dict(context)


def _safe_context_for_error_logging(context: Any) -> Dict[str, Any]:
    payload = _context_payload(context)
    workspace_slug = str(payload.get("workspace_slug") or "").strip().lower()

    if workspace_slug == "atabaque":
        payload["workspace_slug"] = None
        payload["user_id"] = None

    return payload


def _governance_error_payload(
    *,
    task: str,
    context: Any,
    meta: Dict[str, Any],
    messages: Any,
    route: Dict[str, Any] | None,
    stage: str,
    reason: str,
    status_code: int,
) -> Dict[str, Any]:
    raw_context_payload = _context_payload(context)
    context_payload = _safe_context_for_error_logging(context)
    governance_snapshot = build_workspace_governance_snapshot(
        surface=raw_context_payload.get("surface"),
        task=task,
        workspace_slug=raw_context_payload.get("workspace_slug"),
        domain=raw_context_payload.get("domain"),
    )

    payload = build_ai_error_log_payload(
        task=task,
        context=context_payload,
        error=RuntimeError(reason),
        stage=stage,
        messages=messages,
        meta=meta,
        route=route,
    )
    payload["governance"] = {
        "blocked": True,
        "reason": reason,
        "status_code": status_code,
        "workspace": governance_snapshot,
    }
    return payload


def _message_payloads(messages: Any) -> list[Dict[str, Any]]:
    payloads: list[Dict[str, Any]] = []

    for message in messages:
        if hasattr(message, "model_dump"):
            payload = message.model_dump()
        elif isinstance(message, dict):
            payload = dict(message)
        else:
            payload = dict(message)
        payloads.append(payload)

    return payloads


def _validate_messages(
    *,
    messages: Any,
    task: str,
    context: Any,
    meta: Dict[str, Any],
    route: Dict[str, Any],
) -> None:
    payloads = _message_payloads(messages)

    if not payloads:
        _raise_governance_error(
            status_code=400,
            detail="At least one AI message is required.",
            stage="missing_messages",
            code="missing_messages",
            task=task,
            context=context,
            meta=meta,
            messages=messages,
            route=route,
        )

    if len(payloads) > MAX_AI_MESSAGES:
        _raise_governance_error(
            status_code=400,
            detail=f"AI request exceeds the maximum of {MAX_AI_MESSAGES} messages.",
            stage="message_count_limit",
            code="message_count_limit",
            task=task,
            context=context,
            meta=meta,
            messages=messages,
            route=route,
        )

    total_characters = 0
    system_messages = 0
    user_messages = 0

    for payload in payloads:
        role = str(payload.get("role") or "").strip().lower()
        content = str(payload.get("content") or "")
        total_characters += len(content)

        if len(content) > MAX_AI_MESSAGE_CHARACTERS:
            _raise_governance_error(
                status_code=400,
                detail=(
                    "AI request contains a message that exceeds the maximum "
                    f"of {MAX_AI_MESSAGE_CHARACTERS} characters."
                ),
                stage="message_size_limit",
                code="message_size_limit",
                task=task,
                context=context,
                meta=meta,
                messages=messages,
                route=route,
            )

        if role == "system":
            system_messages += 1
        if role == "user":
            user_messages += 1

    if total_characters > MAX_AI_TOTAL_CHARACTERS:
        _raise_governance_error(
            status_code=400,
            detail=(
                "AI request exceeds the maximum total message size of "
                f"{MAX_AI_TOTAL_CHARACTERS} characters."
            ),
            stage="total_message_size_limit",
            code="total_message_size_limit",
            task=task,
            context=context,
            meta=meta,
            messages=messages,
            route=route,
        )

    if system_messages > MAX_SYSTEM_MESSAGES:
        _raise_governance_error(
            status_code=400,
            detail="AI request supports at most one system message.",
            stage="system_message_limit",
            code="system_message_limit",
            task=task,
            context=context,
            meta=meta,
            messages=messages,
            route=route,
        )

    if user_messages == 0:
        _raise_governance_error(
            status_code=400,
            detail="AI request requires at least one user message.",
            stage="missing_user_message",
            code="missing_user_message",
            task=task,
            context=context,
            meta=meta,
            messages=messages,
            route=route,
        )


def _route_has_available_provider(route: Dict[str, Any]) -> bool:
    primary = route.get("primary") or {}
    secondary = route.get("secondary") or {}

    return provider_is_configured(primary.get("provider")) or provider_is_configured(
        secondary.get("provider")
    )


def _raise_governance_error(
    *,
    status_code: int,
    detail: str,
    stage: str,
    code: str,
    task: str,
    context: Any,
    meta: Dict[str, Any],
    messages: Any,
    route: Dict[str, Any] | None = None,
) -> None:
    sink_ai_log_payload(
        _governance_error_payload(
            task=task,
            context=context,
            meta=meta,
            messages=messages,
            route=route,
            stage=stage,
            reason=detail,
            status_code=status_code,
        ),
        level="warning" if status_code < 500 else "error",
    )
    raise HTTPException(
        status_code=status_code,
        detail=_error_detail(
            code=code,
            message=detail,
            stage=stage,
            request_id=str(meta.get("request_id") or "") or None,
        ),
    )


def _enforce_request_governance(
    *,
    payload: AIChatRequestPayload,
    normalized_context: Dict[str, Any],
    task: str,
    meta: Dict[str, Any],
    route: Dict[str, Any],
) -> None:
    if not ai_gateway_enabled():
        _raise_governance_error(
            status_code=503,
            detail="AI gateway is disabled.",
            stage="gateway_disabled",
            code="gateway_disabled",
            task=task,
            context=payload.context,
            meta=meta,
            messages=payload.messages,
            route=route,
        )

    if not is_surface_ai_enabled(normalized_context["surface"]):
        _raise_governance_error(
            status_code=403,
            detail=f"AI surface '{normalized_context['surface']}' is not enabled.",
            stage="surface_not_enabled",
            code="surface_not_enabled",
            task=task,
            context=payload.context,
            meta=meta,
            messages=payload.messages,
            route=route,
        )

    if not is_surface_phase_eligible(normalized_context["surface"]):
        _raise_governance_error(
            status_code=403,
            detail=(
                f"AI surface '{normalized_context['surface']}' is not eligible "
                "for this rollout phase."
            ),
            stage="surface_phase_block",
            code="surface_phase_block",
            task=task,
            context=payload.context,
            meta=meta,
            messages=payload.messages,
            route=route,
        )

    if not _route_has_available_provider(route):
        _raise_governance_error(
            status_code=503,
            detail="No configured AI provider is available for this task route.",
            stage="route_provider_unavailable",
            code="route_provider_unavailable",
            task=task,
            context=payload.context,
            meta=meta,
            messages=payload.messages,
            route=route,
        )

    _validate_messages(
        messages=payload.messages,
        task=task,
        context=payload.context,
        meta=meta,
        route=route,
    )

    surface = normalized_context["surface"]
    raw_context = _context_payload(payload.context)
    raw_workspace_slug = raw_context.get("workspace_slug")

    if is_protected_workspace(raw_workspace_slug):
        _raise_governance_error(
            status_code=403,
            detail="Protected workspaces are blocked from AI access in this phase.",
            stage="protected_workspace_block",
            code="protected_workspace_block",
            task=task,
            context=payload.context,
            meta=meta,
            messages=payload.messages,
            route=route,
        )

    if surface == "public":
        if any(
            raw_context.get(field) not in (None, "")
            for field in ("workspace_slug", "workflow_type", "form_version", "user_id")
        ):
            _raise_governance_error(
                status_code=400,
                detail=(
                    "Public AI surface does not accept workspace or user operational context."
                ),
                stage="public_operational_context_block",
                code="public_operational_context_block",
                task=task,
                context=payload.context,
                meta=meta,
                messages=payload.messages,
                route=route,
            )

        if not normalized_context.get("domain"):
            _raise_governance_error(
                status_code=400,
                detail="Public AI surface requires context.domain for controlled testing.",
                stage="missing_public_domain",
                code="missing_public_domain",
                task=task,
                context=payload.context,
                meta=meta,
                messages=payload.messages,
                route=route,
            )

        if not is_public_domain_ai_enabled(normalized_context.get("domain")):
            _raise_governance_error(
                status_code=403,
                detail="AI is not enabled for this public domain.",
                stage="public_domain_not_enabled",
                code="public_domain_not_enabled",
                task=task,
                context=payload.context,
                meta=meta,
                messages=payload.messages,
                route=route,
            )

    if not surface_allows_task(surface, task):
        _raise_governance_error(
            status_code=403,
            detail=f"Task '{task}' is not allowed for AI surface '{surface}'.",
            stage="surface_task_block",
            code="surface_task_block",
            task=task,
            context=payload.context,
            meta=meta,
            messages=payload.messages,
            route=route,
        )

    if surface in {"logged", "internal"}:
        if not normalized_context.get("workspace_slug"):
            _raise_governance_error(
                status_code=400,
                detail=f"{surface.capitalize()} AI surface requires workspace_slug.",
                stage="missing_workspace_slug",
                code="missing_workspace_slug",
                task=task,
                context=payload.context,
                meta=meta,
                messages=payload.messages,
                route=route,
            )

        if not normalized_context.get("user_id"):
            _raise_governance_error(
                status_code=400,
                detail=f"{surface.capitalize()} AI surface requires user_id.",
                stage="missing_user_id",
                code="missing_user_id",
                task=task,
                context=payload.context,
                meta=meta,
                messages=payload.messages,
                route=route,
            )

        if not is_workspace_ai_enabled(normalized_context.get("workspace_slug")):
            _raise_governance_error(
                status_code=403,
                detail="AI is not enabled for this workspace.",
                stage="workspace_not_enabled",
                code="workspace_not_enabled",
                task=task,
                context=payload.context,
                meta=meta,
                messages=payload.messages,
                route=route,
            )


@router.get("/readiness")
async def readiness(
    surface: str = "public",
    task: str | None = None,
    workspace_slug: str | None = None,
    domain: str | None = None,
) -> Dict[str, Any]:
    if not bool(getattr(settings, "AI_READINESS_ENABLED", False)):
        raise HTTPException(
            status_code=404,
            detail=_error_detail(
                code="readiness_disabled",
                message="AI readiness route is disabled.",
                stage="readiness_disabled",
            ),
        )

    try:
        return {
            "ok": True,
            "data": build_ai_readiness_snapshot(
                surface=surface,
                task=task,
                workspace_slug=workspace_slug,
                domain=domain,
            ),
        }
    except RuntimeError as exc:
        raise HTTPException(
            status_code=400,
            detail=_error_detail(
                code="invalid_readiness_request",
                message=str(exc),
                stage="readiness_validation",
            ),
        ) from exc


@router.post("/chat", response_model=AIChatResponsePayload)
async def chat(payload: AIChatRequestPayload) -> AIChatResponsePayload:
    task = normalize_task(payload.task)
    meta = _request_meta(payload)
    route = get_task_route(task)

    try:
        normalized_context = build_ai_context(payload.context)
    except RuntimeError as exc:
        _raise_governance_error(
            status_code=403,
            detail=str(exc),
            stage="context_guardrail",
            code="context_guardrail",
            task=task,
            context=payload.context,
            meta=meta,
            messages=payload.messages,
            route=route,
        )

    _enforce_request_governance(
        payload=payload,
        normalized_context=normalized_context,
        task=task,
        meta=meta,
        route=route,
    )

    request_log_payload = build_ai_request_log_payload(
        task=task,
        context=normalized_context,
        messages=payload.messages,
        meta=meta,
        route=route,
    )
    sink_ai_log_payload(request_log_payload)

    estimated_cost = estimate_cost(
        task=task,
        messages=payload.messages,
    )

    started_at = time.perf_counter()

    try:
        result = run_task_with_fallback(
            task=task,
            messages=payload.messages,
        )
    except Exception as exc:
        error_log_payload = build_ai_error_log_payload(
            task=task,
            context=normalized_context,
            error=exc,
            stage="provider_execution",
            messages=payload.messages,
            meta=meta,
            route=route,
        )
        sink_ai_log_payload(error_log_payload, level="error")
        raise HTTPException(
            status_code=502,
            detail=_error_detail(
                code="provider_execution_failed",
                message="AI provider request failed.",
                stage="provider_execution",
                request_id=meta["request_id"],
            ),
        ) from exc

    latency_ms = max(1, int((time.perf_counter() - started_at) * 1000))
    result_with_metrics = result | {
        "latency_ms": latency_ms,
        "estimated_cost_usd": estimated_cost["estimated_cost_usd"],
    }

    response_log_payload = build_ai_response_log_payload(
        task=task,
        context=normalized_context,
        messages=payload.messages,
        meta=meta,
        route=result,
        result=result_with_metrics,
    )
    sink_ai_log_payload(response_log_payload)

    return AIChatResponsePayload(
        status="ok",
        message=AIMessagePayload(
            role="assistant",
            content=result["text"],
        ),
        task=task,
        meta=AIResponseMetaPayload(
            request_id=meta["request_id"],
            route=f'{result["provider"]}:{result["model"]}',
            used_fallback=bool(result.get("used_fallback", False)),
            latency_ms=latency_ms,
            estimated_cost_usd=estimated_cost["estimated_cost_usd"],
            handoff_required=False,
        ),
    )
