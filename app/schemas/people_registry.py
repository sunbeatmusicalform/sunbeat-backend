from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field

PartyKind = Literal["pf", "pj"]
PeopleRegistryWorkflowType = Literal["people_registry"]
PeopleRegistryAirtableSyncStatus = Literal["pending", "blocked", "failed", "synced"]
PeopleRegistryResponseStatus = Literal[
    "validated", "invalid", "created", "fetched", "conflict", "error"
]
PeopleRegistryLookupConfidence = Literal["exact", "partial"]
PeopleRegistryVerifyVerdict = Literal[
    "ambas",
    "so_v2",
    "so_legado",
    "nao_encontrado",
]
PeopleRegistryVerifyMatchBy = Literal["email", "documento", "nome"]
PeopleRegistryInviteStatus = Literal[
    "pending",
    "sent",
    "opened",
    "submitted",
    "submitted_pending_airtable",
    "failed",
    "expired",
    "discontinued",
]


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
    bank_agency: Optional[str] = None
    account_number: Optional[str] = None
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
    edit_token: Optional[str] = None


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
    edit_token: Optional[str] = None
    created_at: str
    updated_at: str


class PeopleRegistryResponsePayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ok: bool = True
    status: PeopleRegistryResponseStatus = "validated"
    data: Optional[PeopleRegistryPreparedPayload] = None
    record: Optional[PeopleRegistryRecordPayload] = None
    error: Optional[PeopleRegistryErrorDetailPayload] = None


class PeopleRegistryLookupItemPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    displayName: str
    roles: List[str] = Field(default_factory=list)
    source: Literal["people_registry"] = "people_registry"
    confidence: PeopleRegistryLookupConfidence


class PeopleRegistryLookupResponsePayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ok: bool = True
    items: List[PeopleRegistryLookupItemPayload] = Field(default_factory=list)


class PeopleRegistryVerifyMatchPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    record_id: str
    display_name: Optional[str] = None
    match_by: PeopleRegistryVerifyMatchBy


class PeopleRegistryVerifyResponsePayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ok: bool = True
    query: str
    verdict: PeopleRegistryVerifyVerdict
    dados_cadastrais: Optional[PeopleRegistryVerifyMatchPayload] = None
    v2_pessoas: Optional[PeopleRegistryVerifyMatchPayload] = None
    acao: str


class PeopleRegistryInviteCreatePayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    workspace_slug: str = Field(..., min_length=1)
    profile: str = "atabaque_people_v1"
    airtable_clearance_part_id: Optional[str] = None
    context: Dict[str, Any] = Field(default_factory=dict)
    expires_at: Optional[str] = None
    expires_in_days: Optional[int] = Field(default=None, ge=1, le=120)


class PeopleRegistryInvitePayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    token: str
    status: PeopleRegistryInviteStatus = "pending"
    workspace_slug: str
    profile: str
    airtable_clearance_part_id: str
    invite_url: str
    context: Dict[str, Any] = Field(default_factory=dict)
    people_registry_record_id: Optional[str] = None
    people_airtable_record_id: Optional[str] = None
    last_error: Optional[str] = None
    expires_at: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    opened_at: Optional[str] = None
    submitted_at: Optional[str] = None


class PeopleRegistryInviteListResponsePayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ok: bool = True
    items: List[PeopleRegistryInvitePayload] = Field(default_factory=list)
    total: int = 0


class PeopleRegistryInviteEmailPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    workspace_slug: str = Field(..., min_length=1)
    to_email: Optional[EmailStr] = None
    recipient_name: Optional[str] = None
    message: Optional[str] = None


class PeopleRegistryInviteErrorPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    code: str
    message: str
    stage: str


class PeopleRegistryInviteEmailResponsePayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ok: bool = True
    invite: Optional[PeopleRegistryInvitePayload] = None
    provider_message_id: Optional[str] = None
    error: Optional[PeopleRegistryInviteErrorPayload] = None


class PeopleRegistryInviteResponsePayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ok: bool = True
    status: PeopleRegistryInviteStatus = "pending"
    invite: Optional[PeopleRegistryInvitePayload] = None
    error: Optional[PeopleRegistryInviteErrorPayload] = None


class PeopleRegistryInviteParticipationPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    confirmation_status: Optional[str] = "confirmado"
    musical_role: Optional[str] = None
    remuneration_type: Optional[str] = None
    participation_percent: Optional[float] = None
    fixed_amount: Optional[float] = None
    notes: Optional[str] = None


class PeopleRegistryInviteSubmitPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    person: PeopleRegistryPayload
    participation: PeopleRegistryInviteParticipationPayload = Field(
        default_factory=PeopleRegistryInviteParticipationPayload
    )


class PeopleRegistryInviteSubmitResponsePayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ok: bool = True
    status: PeopleRegistryInviteStatus = "submitted"
    invite: Optional[PeopleRegistryInvitePayload] = None
    people: Optional[PeopleRegistryResponsePayload] = None
    error: Optional[PeopleRegistryInviteErrorPayload] = None
