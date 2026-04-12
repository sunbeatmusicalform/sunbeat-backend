from __future__ import annotations

from typing import Any, Dict, Optional, Set
from urllib.parse import urlparse

from app.core.config import settings
from app.services.ai_context import (
    INTERNAL_SURFACE,
    LOGGED_SURFACE,
    PUBLIC_SURFACE,
    SUPPORTED_AI_SURFACES,
    is_protected_workspace,
)

PUBLIC_ALLOWED_TASKS = {
    "product",
    "onboarding",
    "setup",
    "commercial",
}
LOGGED_ALLOWED_TASKS = {
    "product",
    "onboarding",
    "setup",
    "release",
    "summary",
    "classification",
    "operations",
    "commercial",
}
INTERNAL_ALLOWED_TASKS = set(LOGGED_ALLOWED_TASKS)
PHASE_ELIGIBLE_SURFACES = {PUBLIC_SURFACE}


def _normalized_text(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


def _normalized_domain(value: Any) -> Optional[str]:
    text = _normalized_text(value)
    if not text:
        return None

    candidate = text if "://" in text else f"https://{text}"
    parsed = urlparse(candidate)
    host = (parsed.netloc or parsed.path).strip().lower()
    if not host:
        return None

    return host.split("/")[0].split(":")[0] or None


def enabled_ai_workspace_slugs() -> Set[str]:
    raw_value = _normalized_text(getattr(settings, "AI_ENABLED_WORKSPACE_SLUGS", ""))
    if not raw_value:
        return set()

    normalized = {
        item.strip().lower()
        for item in raw_value.split(",")
        if item.strip()
    }

    return {
        workspace_slug
        for workspace_slug in normalized
        if not is_protected_workspace(workspace_slug)
    }


def enabled_ai_surfaces() -> Set[str]:
    raw_value = _normalized_text(getattr(settings, "AI_ENABLED_SURFACES", ""))
    if not raw_value:
        return set()

    normalized = {
        item.strip().lower()
        for item in raw_value.split(",")
        if item.strip()
    }

    return {
        surface
        for surface in normalized
        if surface in SUPPORTED_AI_SURFACES
    }


def enabled_ai_public_domains() -> Set[str]:
    raw_value = _normalized_text(getattr(settings, "AI_PUBLIC_ENABLED_DOMAINS", ""))
    if not raw_value:
        return set()

    normalized = {
        _normalized_domain(item)
        for item in raw_value.split(",")
        if item.strip()
    }

    return {
        domain
        for domain in normalized
        if domain
    }


def is_surface_ai_enabled(surface: Any) -> bool:
    normalized_surface = _normalized_text(surface)
    if not normalized_surface:
        return False

    return normalized_surface.lower() in enabled_ai_surfaces()


def is_surface_phase_eligible(surface: Any) -> bool:
    normalized_surface = _normalized_text(surface)
    if not normalized_surface:
        return False

    return normalized_surface.lower() in PHASE_ELIGIBLE_SURFACES


def is_public_domain_ai_enabled(domain: Any) -> bool:
    normalized_domain = _normalized_domain(domain)
    if not normalized_domain:
        return False

    return normalized_domain in enabled_ai_public_domains()


def is_workspace_ai_enabled(workspace_slug: Any) -> bool:
    normalized_workspace_slug = _normalized_text(workspace_slug)
    if not normalized_workspace_slug:
        return False

    if is_protected_workspace(normalized_workspace_slug):
        return False

    return normalized_workspace_slug.lower() in enabled_ai_workspace_slugs()


def surface_allowed_tasks(surface: Any) -> Set[str]:
    normalized_surface = str(surface or "").strip().lower()

    if normalized_surface == PUBLIC_SURFACE:
        return set(PUBLIC_ALLOWED_TASKS)
    if normalized_surface == LOGGED_SURFACE:
        return set(LOGGED_ALLOWED_TASKS)
    if normalized_surface == INTERNAL_SURFACE:
        return set(INTERNAL_ALLOWED_TASKS)

    return set()


def surface_allows_task(surface: Any, task: Any) -> bool:
    normalized_task = str(task or "").strip().lower()
    if not normalized_task:
        return False

    return normalized_task in surface_allowed_tasks(surface)


def build_workspace_governance_snapshot(
    *,
    surface: Any,
    task: Any,
    workspace_slug: Any = None,
    domain: Any = None,
) -> Dict[str, Any]:
    normalized_surface = _normalized_text(surface)
    normalized_workspace_slug = _normalized_text(workspace_slug)
    normalized_domain = _normalized_domain(domain)
    protected_workspace = is_protected_workspace(normalized_workspace_slug)
    allowlist = enabled_ai_workspace_slugs()
    public_surface = normalized_surface == PUBLIC_SURFACE

    return {
        "surface": normalized_surface,
        "task": _normalized_text(task),
        "surface_phase_eligible": is_surface_phase_eligible(normalized_surface),
        "workspace_slug": (
            None
            if public_surface or protected_workspace
            else normalized_workspace_slug
        ),
        "surface_governance_enabled": bool(enabled_ai_surfaces()),
        "surface_ai_enabled": is_surface_ai_enabled(normalized_surface),
        "public_domain": normalized_domain if public_surface else None,
        "public_domain_governance_enabled": bool(enabled_ai_public_domains()),
        "public_domain_ai_enabled": (
            is_public_domain_ai_enabled(normalized_domain)
            if public_surface
            else False
        ),
        "workspace_governance_enabled": bool(allowlist),
        "workspace_ai_enabled": False if public_surface else is_workspace_ai_enabled(normalized_workspace_slug),
        "protected_workspace": protected_workspace,
        "allowed_tasks": sorted(surface_allowed_tasks(surface)),
    }
