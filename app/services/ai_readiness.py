from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.services.ai_context import is_protected_workspace, normalize_surface
from app.services.ai_governance import (
    build_workspace_governance_snapshot,
    is_public_domain_ai_enabled,
    is_surface_ai_enabled,
    is_surface_phase_eligible,
    is_workspace_ai_enabled,
    surface_allows_task,
)
from app.core.config import settings
from app.services.ai_providers import (
    ai_gateway_enabled,
    ai_request_timeout_seconds,
    provider_configuration_snapshot,
    provider_is_configured,
)
from app.services.ai_router import DEFAULT_AI_TASK, get_task_route, normalize_task


def _normalized_text(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


def _route_provider_readiness(route: Dict[str, Any]) -> Dict[str, Any]:
    primary = route.get("primary") or {}
    secondary = route.get("secondary") or {}

    primary_provider = _normalized_text(primary.get("provider"))
    secondary_provider = _normalized_text(secondary.get("provider"))
    primary_configured = provider_is_configured(primary_provider)
    secondary_configured = provider_is_configured(secondary_provider)

    return {
        "primary_provider": primary_provider,
        "primary_model": _normalized_text(primary.get("model")),
        "primary_configured": primary_configured,
        "secondary_provider": secondary_provider,
        "secondary_model": _normalized_text(secondary.get("model")),
        "secondary_configured": secondary_configured,
        "at_least_one_provider_configured": primary_configured or secondary_configured,
    }


def _readiness_configuration_status(
    *,
    surface: str,
    task: str,
    workspace_governance: Dict[str, Any],
    route_readiness: Dict[str, Any],
    domain: Optional[str],
) -> Dict[str, Any]:
    return {
        "mode": "public_technical_only",
        "gateway_enabled": ai_gateway_enabled(),
        "readiness_enabled": bool(getattr(settings, "AI_READINESS_ENABLED", False)),
        "surface_enabled": is_surface_ai_enabled(surface),
        "surface_phase_eligible": is_surface_phase_eligible(surface),
        "task_allowed_for_surface": surface_allows_task(surface, task),
        "route_has_provider": route_readiness["at_least_one_provider_configured"],
        "public_domain_provided": bool(domain) if surface == "public" else False,
        "public_domain_enabled": bool(
            workspace_governance.get("public_domain_ai_enabled", False)
        ),
        "protected_workspace_blocked": bool(
            workspace_governance.get("protected_workspace", False)
        ),
    }


def _enable_requirements(
    *,
    surface: str,
    workspace_slug: Optional[str],
    domain: Optional[str],
    task: str,
    route_readiness: Dict[str, Any],
) -> List[str]:
    requirements: List[str] = []

    if not ai_gateway_enabled():
        requirements.append("Set AI_GATEWAY_ENABLED=true in the target environment.")

    if not bool(getattr(settings, "AI_READINESS_ENABLED", False)):
        requirements.append("Set AI_READINESS_ENABLED=true to expose readiness in a controlled environment.")

    if not is_surface_ai_enabled(surface):
        requirements.append(
            "Add the target surface to AI_ENABLED_SURFACES for controlled enable."
        )

    if not is_surface_phase_eligible(surface):
        requirements.append(
            "Only the public AI surface is eligible in this phase; keep logged/internal disabled."
        )

    if not route_readiness["at_least_one_provider_configured"]:
        requirements.append("Configure at least one provider API key for the selected task route.")

    if surface in {"logged", "internal"}:
        requirements.append("Logged/internal surfaces remain blocked in this phase.")

    if surface == "public":
        requirements.append("Keep the request limited to public-safe tasks and no operational data.")

        if not domain:
            requirements.append("Send context.domain for controlled public testing.")
        elif not is_public_domain_ai_enabled(domain):
            requirements.append(
                "Add the public test domain to AI_PUBLIC_ENABLED_DOMAINS for controlled enable."
            )

    if not surface_allows_task(surface, task):
        requirements.append(f"Choose a task allowed for AI surface '{surface}'.")

    return requirements


def _controlled_enable_checklist(
    *,
    task: str,
    domain: Optional[str],
) -> List[str]:
    selected_domain = domain or "sunbeat.com.br"

    return [
        "Configure at least one provider API key for the selected task route.",
        "Set AI_READINESS_ENABLED=true in the target environment.",
        "Set AI_ENABLED_SURFACES=public.",
        f"Set AI_PUBLIC_ENABLED_DOMAINS={selected_domain}.",
        "Set AI_GATEWAY_ENABLED=true only after the items above are in place.",
        (
            "Validate GET /ai/readiness?surface=public"
            f"&task={task}&domain={selected_domain} before calling POST /ai/chat."
        ),
    ]


def _controlled_rollback_checklist() -> List[str]:
    return [
        "Set AI_GATEWAY_ENABLED=false for the fastest stop.",
        "Clear AI_ENABLED_SURFACES.",
        "Clear AI_PUBLIC_ENABLED_DOMAINS.",
        "Set AI_READINESS_ENABLED=false after the technical checks are complete.",
    ]


def _controlled_validation_flow(
    *,
    task: str,
    domain: Optional[str],
) -> List[str]:
    selected_domain = domain or "sunbeat.com.br"

    return [
        (
            "Call GET /ai/readiness?surface=public"
            f"&task={task}&domain={selected_domain} and confirm can_test_controlled=true."
        ),
        "Confirm the route shows at least one configured provider.",
        "Confirm product_blockers still indicate technical-only public access and Atabaque block.",
        "Call POST /ai/chat with the minimal public payload example below.",
        "If any unexpected result appears, rollback by disabling AI_GATEWAY_ENABLED immediately.",
    ]


def _public_test_request_example(
    *,
    task: str,
    domain: Optional[str],
) -> Dict[str, Any]:
    return {
        "task": task,
        "messages": [
            {
                "role": "user",
                "content": "Teste tecnico controlado do AI Gateway publico.",
            }
        ],
        "context": {
            "surface": "public",
            "domain": domain or "sunbeat.com.br",
        },
        "meta": {
            "source": "controlled_public_test",
        },
    }


def _product_blockers(surface: str) -> List[str]:
    blockers = [
        "No frontend or visible product surface is connected yet.",
        "Governance persistence still uses logger sink only; there is no database sink yet.",
        "Feature flags remain minimal and environment-based, not product-grade.",
        "Atabaque remains explicitly blocked.",
    ]

    if surface == "public":
        blockers.append("Public surface remains technical-only and is not released as a product entrypoint.")
        blockers.append("Public testing still requires an explicitly allowlisted domain.")

    if surface in {"logged", "internal"}:
        blockers.append("Logged/internal surfaces remain blocked in this rollout phase.")

    return blockers


def build_ai_readiness_snapshot(
    *,
    surface: Any = "public",
    task: Any = None,
    workspace_slug: Any = None,
    domain: Any = None,
) -> Dict[str, Any]:
    normalized_surface = normalize_surface(surface)
    normalized_task = normalize_task(task or DEFAULT_AI_TASK)
    normalized_workspace_slug = _normalized_text(workspace_slug)
    normalized_domain = _normalized_text(domain)
    route = get_task_route(normalized_task)
    route_readiness = _route_provider_readiness(route)
    workspace_governance = build_workspace_governance_snapshot(
        surface=normalized_surface,
        task=normalized_task,
        workspace_slug=normalized_workspace_slug,
        domain=normalized_domain,
    )
    enable_requirements = _enable_requirements(
        surface=normalized_surface,
        workspace_slug=normalized_workspace_slug,
        domain=normalized_domain,
        task=normalized_task,
        route_readiness=route_readiness,
    )
    configuration_status = _readiness_configuration_status(
        surface=normalized_surface,
        task=normalized_task,
        workspace_governance=workspace_governance,
        route_readiness=route_readiness,
        domain=normalized_domain,
    )

    can_test_controlled = (
        configuration_status["gateway_enabled"]
        and configuration_status["surface_enabled"]
        and configuration_status["surface_phase_eligible"]
        and configuration_status["route_has_provider"]
        and configuration_status["task_allowed_for_surface"]
        and not configuration_status["protected_workspace_blocked"]
        and (
            (
                normalized_surface == "public"
                and configuration_status["public_domain_provided"]
                and configuration_status["public_domain_enabled"]
            )
            or (
                bool(normalized_workspace_slug)
                and is_workspace_ai_enabled(normalized_workspace_slug)
            )
        )
    )

    return {
        "gateway": {
            "enabled": ai_gateway_enabled(),
            "readiness_enabled": bool(getattr(settings, "AI_READINESS_ENABLED", False)),
            "request_timeout_seconds": ai_request_timeout_seconds(),
        },
        "surface": normalized_surface,
        "task": normalized_task,
        "domain": normalized_domain,
        "configuration_status": configuration_status,
        "workspace": workspace_governance,
        "providers": provider_configuration_snapshot(),
        "route": route | route_readiness,
        "can_test_controlled": can_test_controlled,
        "enable_requirements": enable_requirements,
        "product_blockers": _product_blockers(normalized_surface),
        "controlled_test": {
            "enable_checklist": _controlled_enable_checklist(
                task=normalized_task,
                domain=normalized_domain,
            ),
            "rollback_checklist": _controlled_rollback_checklist(),
            "validation_flow": _controlled_validation_flow(
                task=normalized_task,
                domain=normalized_domain,
            ),
            "request_example": _public_test_request_example(
                task=normalized_task,
                domain=normalized_domain,
            )
            if normalized_surface == "public"
            else None,
        },
    }
