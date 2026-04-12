from __future__ import annotations

from typing import Any, Dict, Optional

from app.schemas.ai_gateway import AIContextPayload

PUBLIC_SURFACE = "public"
LOGGED_SURFACE = "logged"
INTERNAL_SURFACE = "internal"

SUPPORTED_AI_SURFACES = {
    PUBLIC_SURFACE,
    LOGGED_SURFACE,
    INTERNAL_SURFACE,
}
PROTECTED_WORKSPACES = {"atabaque"}
ATABAQUE_GUARDRAIL_REASON = (
    "Atabaque remains protected until AI rollout is explicitly enabled for that workspace."
)


def _context_dict(context: Any) -> Dict[str, Any]:
    if isinstance(context, AIContextPayload):
        return context.model_dump()
    if hasattr(context, "model_dump"):
        return context.model_dump()
    if isinstance(context, dict):
        return dict(context)
    return dict(context)


def _normalized_text(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


def normalize_surface(value: Any) -> str:
    normalized = _normalized_text(value)
    if not normalized:
        raise RuntimeError("AI context surface is required")

    surface = normalized.lower()
    if surface not in SUPPORTED_AI_SURFACES:
        raise RuntimeError(f"Unsupported AI surface: {value}")

    return surface


def is_protected_workspace(workspace_slug: Any) -> bool:
    normalized_workspace_slug = _normalized_text(workspace_slug)
    if not normalized_workspace_slug:
        return False

    return normalized_workspace_slug.lower() in PROTECTED_WORKSPACES


def _context_guardrails(*, surface: str, protected_workspace_detected: bool) -> Dict[str, Any]:
    return {
        "surface_blocks_operational_data": surface == PUBLIC_SURFACE,
        "protected_workspace_detected": protected_workspace_detected,
        "protected_workspace_reason": ATABAQUE_GUARDRAIL_REASON,
        "content_logging_redacted": True,
    }


def _base_context_payload(
    *,
    surface: str,
    locale: Optional[str],
    domain: Optional[str],
    session_id: Optional[str],
    workspace_slug: Optional[str],
    workflow_type: Optional[str],
    form_version: Optional[str],
    user_id: Optional[str],
    allow_operational_data: bool,
    allow_workspace_context: bool,
    allow_user_context: bool,
    protected_workspace_detected: bool,
) -> Dict[str, Any]:
    return {
        "surface": surface,
        "locale": locale,
        "domain": domain,
        "session_id": session_id,
        "workspace_slug": workspace_slug if allow_workspace_context else None,
        "workflow_type": workflow_type if allow_workspace_context else None,
        "form_version": form_version if allow_workspace_context else None,
        "user_id": user_id if allow_user_context else None,
        "access_policy": {
            "allow_operational_data": allow_operational_data,
            "allow_workspace_context": allow_workspace_context,
            "allow_user_context": allow_user_context,
        },
        "guardrails": _context_guardrails(
            surface=surface,
            protected_workspace_detected=protected_workspace_detected,
        ),
    }


def _build_public_context(context: Dict[str, Any]) -> Dict[str, Any]:
    return _base_context_payload(
        surface=PUBLIC_SURFACE,
        locale=_normalized_text(context.get("locale")),
        domain=_normalized_text(context.get("domain")),
        session_id=_normalized_text(context.get("session_id")),
        workspace_slug=None,
        workflow_type=None,
        form_version=None,
        user_id=None,
        allow_operational_data=False,
        allow_workspace_context=False,
        allow_user_context=False,
        protected_workspace_detected=is_protected_workspace(context.get("workspace_slug")),
    )


def _build_logged_context(context: Dict[str, Any]) -> Dict[str, Any]:
    workspace_slug = _normalized_text(context.get("workspace_slug"))
    if is_protected_workspace(workspace_slug):
        raise RuntimeError(ATABAQUE_GUARDRAIL_REASON)

    form_version = _normalized_text(context.get("form_version"))

    return _base_context_payload(
        surface=LOGGED_SURFACE,
        locale=_normalized_text(context.get("locale")),
        domain=_normalized_text(context.get("domain")),
        session_id=_normalized_text(context.get("session_id")),
        workspace_slug=workspace_slug,
        workflow_type=_normalized_text(context.get("workflow_type")),
        form_version=form_version,
        user_id=_normalized_text(context.get("user_id")),
        allow_operational_data=True,
        allow_workspace_context=True,
        allow_user_context=True,
        protected_workspace_detected=False,
    )


def _build_internal_context(context: Dict[str, Any]) -> Dict[str, Any]:
    workspace_slug = _normalized_text(context.get("workspace_slug"))
    if is_protected_workspace(workspace_slug):
        raise RuntimeError(ATABAQUE_GUARDRAIL_REASON)

    form_version = _normalized_text(context.get("form_version"))

    return _base_context_payload(
        surface=INTERNAL_SURFACE,
        locale=_normalized_text(context.get("locale")),
        domain=_normalized_text(context.get("domain")),
        session_id=_normalized_text(context.get("session_id")),
        workspace_slug=workspace_slug,
        workflow_type=_normalized_text(context.get("workflow_type")),
        form_version=form_version,
        user_id=_normalized_text(context.get("user_id")),
        allow_operational_data=True,
        allow_workspace_context=True,
        allow_user_context=True,
        protected_workspace_detected=False,
    )


def build_ai_context(context: Any) -> Dict[str, Any]:
    payload = _context_dict(context)
    surface = normalize_surface(payload.get("surface"))

    if surface == PUBLIC_SURFACE:
        return _build_public_context(payload)
    if surface == LOGGED_SURFACE:
        return _build_logged_context(payload)
    if surface == INTERNAL_SURFACE:
        return _build_internal_context(payload)

    raise RuntimeError(f"Unsupported AI surface: {surface}")


def context_allows_operational_data(context: Any) -> bool:
    normalized_context = build_ai_context(context)
    return bool(normalized_context["access_policy"]["allow_operational_data"])
