"""Políticas e autorizações de edição pós-submissão por tenant."""
from __future__ import annotations

import copy
import hashlib
from typing import Any, Dict

from app.core.database import supabase
from app.modules.admin_config import _read_raw_row
from app.services.workspace_config import get_workflow_settings

EDIT_POLICIES = {"link_after_submit", "admin_authorized", "disabled"}
DEFAULT_EDIT_POLICIES = {
    "release_intake": "link_after_submit",
    "rights_clearance": "link_after_submit",
    "company_registry": "admin_authorized",
    "people_registry": "admin_authorized",
}


def get_edit_policy(workspace_slug: str, workflow_type: str) -> str:
    settings = get_workflow_settings(workspace_slug, workflow_type)
    editing = (settings.get("extra_settings") or {}).get("editing") or {}
    policy = str(editing.get("policy") or DEFAULT_EDIT_POLICIES.get(workflow_type, "disabled"))
    return policy if policy in EDIT_POLICIES else DEFAULT_EDIT_POLICIES.get(workflow_type, "disabled")


def _token_digest(edit_token: str) -> str:
    return hashlib.sha256(edit_token.encode("utf-8")).hexdigest()


def is_edit_token_authorized(
    workspace_slug: str,
    workflow_type: str,
    record_id: str,
    edit_token: str,
) -> bool:
    policy = get_edit_policy(workspace_slug, workflow_type)
    if policy == "link_after_submit":
        return True
    if policy == "disabled":
        return False
    settings = get_workflow_settings(workspace_slug, workflow_type)
    editing = (settings.get("extra_settings") or {}).get("editing") or {}
    authorized = editing.get("authorized_tokens") or {}
    return authorized.get(str(record_id)) == _token_digest(edit_token)


def save_edit_policy(workspace_slug: str, workflow_type: str, policy: str) -> Dict[str, Any]:
    if policy not in EDIT_POLICIES:
        raise ValueError("invalid edit policy")
    row = _read_raw_row(workspace_slug, workflow_type) or {}
    extra = copy.deepcopy(row.get("extra_settings") or {})
    editing = copy.deepcopy(extra.get("editing") or {})
    editing["policy"] = policy
    extra["editing"] = editing
    supabase.table("workspace_workflow_settings").upsert(
        {"workspace_slug": workspace_slug, "workflow_type": workflow_type, "extra_settings": extra},
        on_conflict="workspace_slug,workflow_type",
    ).execute()
    return editing


def authorize_edit_token(
    workspace_slug: str,
    workflow_type: str,
    record_id: str,
    edit_token: str,
) -> None:
    row = _read_raw_row(workspace_slug, workflow_type) or {}
    extra = copy.deepcopy(row.get("extra_settings") or {})
    editing = copy.deepcopy(extra.get("editing") or {})
    authorized = copy.deepcopy(editing.get("authorized_tokens") or {})
    authorized[str(record_id)] = _token_digest(edit_token)
    # Evita crescimento ilimitado sem introduzir uma nova tabela/migração.
    if len(authorized) > 500:
        authorized = dict(list(authorized.items())[-500:])
    editing["policy"] = str(editing.get("policy") or DEFAULT_EDIT_POLICIES.get(workflow_type, "disabled"))
    editing["authorized_tokens"] = authorized
    extra["editing"] = editing
    supabase.table("workspace_workflow_settings").upsert(
        {"workspace_slug": workspace_slug, "workflow_type": workflow_type, "extra_settings": extra},
        on_conflict="workspace_slug,workflow_type",
    ).execute()
