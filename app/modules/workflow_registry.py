from __future__ import annotations

from typing import Any, Dict, List

DEFAULT_WORKFLOW_TYPE = "release_intake"
RIGHTS_CLEARANCE_WORKFLOW_TYPE = "rights_clearance"
PEOPLE_REGISTRY_WORKFLOW_TYPE = "people_registry"
LEGACY_RELEASE_INTAKE_FORM_VERSION = "legacy_v1"
ACTIVE_WORKFLOW_FORM_VERSION = "v1"
PLANNED_WORKFLOW_FORM_VERSION = "draft_v1"

WORKFLOW_REGISTRY: Dict[str, Dict[str, str]] = {
    DEFAULT_WORKFLOW_TYPE: {
        "label": "Release intake",
        "description": "Fluxo legado operacional da Atabaque preservado como trilha principal.",
        "default_form_version": LEGACY_RELEASE_INTAKE_FORM_VERSION,
        "status": "active",
        "renderer": "release_intake",
        "template_factory": "release_intake",
        "payload_builder": "release_intake",
        "public_path_prefix": "/intake",
    },
    RIGHTS_CLEARANCE_WORKFLOW_TYPE: {
        "label": "Rights clearance",
        "description": "Workflow multi-step de clearance, licenciamento e referencias operacionais.",
        "default_form_version": ACTIVE_WORKFLOW_FORM_VERSION,
        "status": "active",
        "renderer": "rights_clearance",
        "template_factory": "rights_clearance",
        "payload_builder": "rights_clearance",
        "public_path_prefix": "/clearance",
    },
    PEOPLE_REGISTRY_WORKFLOW_TYPE: {
        "label": "People registry",
        "description": "Placeholder inicial para o workflow de cadastro de pessoas fisicas e juridicas.",
        "default_form_version": PLANNED_WORKFLOW_FORM_VERSION,
        "status": "planned",
        "renderer": "external",
        "template_factory": "external",
        "payload_builder": "external",
        "public_path_prefix": "/people",
    },
}


def build_workflow_source(workspace_slug: str, workflow_type: str, form_version: str) -> str:
    return f"sunbeat.{workspace_slug}.{workflow_type}.{form_version}"


def _humanize_workflow_type(workflow_type: str) -> str:
    return " ".join(part.capitalize() for part in workflow_type.split("_") if part)


def get_workflow_registry_entry(workflow_type: Any) -> Dict[str, str]:
    normalized = str(workflow_type or "").strip() or DEFAULT_WORKFLOW_TYPE
    entry = WORKFLOW_REGISTRY.get(normalized)
    if entry:
        return {
            "workflow_type": normalized,
            **entry,
        }

    return {
        "workflow_type": normalized,
        "label": _humanize_workflow_type(normalized),
        "description": "Workflow customizado ainda nao conectado a um renderer local.",
        "default_form_version": PLANNED_WORKFLOW_FORM_VERSION,
        "status": "custom",
        "renderer": "external",
        "template_factory": "external",
        "payload_builder": "external",
        "public_path_prefix": f"/{normalized}",
    }


def normalize_workflow_type(value: Any) -> str:
    return get_workflow_registry_entry(value)["workflow_type"]


def normalize_form_version(value: Any, workflow_type: Any = None) -> str:
    text = str(value or "").strip()
    if text:
        return text
    return get_workflow_registry_entry(workflow_type)["default_form_version"]


def resolve_workflow_identity(
    *,
    workspace_slug: str,
    workflow_type: Any = None,
    form_version: Any = None,
) -> Dict[str, str]:
    normalized_workspace_slug = str(workspace_slug or "").strip()
    workflow = get_workflow_registry_entry(workflow_type)
    resolved_form_version = normalize_form_version(
        form_version,
        workflow["workflow_type"],
    )

    return {
        "workspace_slug": normalized_workspace_slug,
        "workflow_type": workflow["workflow_type"],
        "form_version": resolved_form_version,
        "source": build_workflow_source(
            normalized_workspace_slug,
            workflow["workflow_type"],
            resolved_form_version,
        ),
        "status": workflow["status"],
        "label": workflow["label"],
        "description": workflow["description"],
        "renderer": workflow["renderer"],
        "template_factory": workflow["template_factory"],
        "payload_builder": workflow["payload_builder"],
        "public_path_prefix": workflow["public_path_prefix"],
    }


def build_frontend_workflow_path(*, workspace_slug: str, workflow_type: Any = None) -> str:
    workflow = get_workflow_registry_entry(workflow_type)
    normalized_workspace_slug = str(workspace_slug or "").strip()
    return f"{workflow['public_path_prefix']}/{normalized_workspace_slug}"


def list_registered_workflows() -> List[Dict[str, str]]:
    return [get_workflow_registry_entry(workflow_type) for workflow_type in WORKFLOW_REGISTRY]


# Para plugar novos workflows:
# 1. registrar a entrada em WORKFLOW_REGISTRY;
# 2. ligar o renderer correto no frontend;
# 3. conectar schema/rota especificos quando o formulario sair do placeholder.
