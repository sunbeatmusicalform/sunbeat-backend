from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field

PartyKind = Literal["pf", "pj"]
PeopleRegistryWorkflowType = Literal["people_registry"]
PeopleRegistryAirtableSyncStatus = Literal["pending", "blocked", "failed", "synced"]
PeopleRegistryResponseStatus = Literal["validated", "invalid", "created", "error"]


class PeopleRegistryPartyPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    party_kind: PartyKind
    display_name: str = Field(..., min_length=1)
    legal_name: str = Field(..., min_length=1)
    stage_name: Optional[str] = None
    trade_name: Optional[str] = None
    document_id: Optional[str] = None
    roles: List[str] = Field(default_factory=list)


class PeopleRegistryContactPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    email_primary: Optional[EmailStr] = None
    phone_primary: Optional[str] = None
    website: Optional[str] = None
    instagram: Optional[str] = None


class PeopleRegistryAddressPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    country: Optional[str] = None
    state_region: Optional[str] = None
    city: Optional[str] = None
    postal_code: Optional[str] = None
    address_line_1: Optional[str] = None
    address_line_2: Optional[str] = None


class PeopleRegistryBankingPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    pix_key: Optional[str] = None
    bank_name: Optional[str] = None
    account_holder_name: Optional[str] = None
    account_holder_document_id: Optional[str] = None


class PeopleRegistryAdditionalInfoPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    manager_name: Optional[str] = None
    label_name: Optional[str] = None
    notes_internal: Optional[str] = None
    external_refs: Dict[str, Any] = Field(default_factory=dict)


class PeopleRegistryMetaPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    form_version: Optional[str | int] = "draft_v1"
    source: Optional[str] = None
    submitted_at: Optional[str] = None


class PeopleRegistryPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    workspace_slug: str = Field(..., min_length=1)
    workflow_type: PeopleRegistryWorkflowType = "people_registry"
    profile: str = Field(..., min_length=1)
    party: PeopleRegistryPartyPayload
    contact: PeopleRegistryContactPayload = Field(
        default_factory=PeopleRegistryContactPayload
    )
    address: PeopleRegistryAddressPayload = Field(
        default_factory=PeopleRegistryAddressPayload
    )
    banking: PeopleRegistryBankingPayload = Field(
        default_factory=PeopleRegistryBankingPayload
    )
    additional_info: PeopleRegistryAdditionalInfoPayload = Field(
        default_factory=PeopleRegistryAdditionalInfoPayload
    )
    meta: PeopleRegistryMetaPayload = Field(default_factory=PeopleRegistryMetaPayload)


class PeopleRegistryValidationIssuePayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    field: str
    message: str


class PeopleRegistryErrorDetailPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    code: str
    message: str
    stage: str
    issues: List[PeopleRegistryValidationIssuePayload] = Field(default_factory=list)


class PeopleRegistryPreparedPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    workspace_slug: str
    workflow_type: str
    profile: str
    party: PeopleRegistryPartyPayload
    contact: PeopleRegistryContactPayload
    address: PeopleRegistryAddressPayload
    banking: PeopleRegistryBankingPayload
    additional_info: PeopleRegistryAdditionalInfoPayload
    meta: PeopleRegistryMetaPayload
    normalized: Dict[str, Any] = Field(default_factory=dict)
    validation_issues: List[PeopleRegistryValidationIssuePayload] = Field(
        default_factory=list
    )


class PeopleRegistryRecordPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    record_id: str
    airtable_sync_status: PeopleRegistryAirtableSyncStatus = "pending"
    created_at: str
    updated_at: str


class PeopleRegistryResponsePayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ok: bool = True
    status: PeopleRegistryResponseStatus = "validated"
    data: Optional[PeopleRegistryPreparedPayload] = None
    record: Optional[PeopleRegistryRecordPayload] = None
    error: Optional[PeopleRegistryErrorDetailPayload] = None
