from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

AIMessageRole = Literal["system", "user", "assistant"]
AISurface = Literal["public", "logged", "internal"]
AITask = Literal[
    "product",
    "onboarding",
    "setup",
    "release",
    "summary",
    "schema",
    "lyrics",
    "classification",
    "operations",
    "commercial",
]
AIResponseStatus = Literal["ok", "handoff", "blocked", "error"]


class AIMessagePayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    role: AIMessageRole
    content: str = Field(..., min_length=1)
    name: Optional[str] = None


class AIContextPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    surface: AISurface
    workspace_slug: Optional[str] = None
    workflow_type: Optional[str] = None
    form_version: Optional[str | int] = None
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    locale: Optional[str] = None
    domain: Optional[str] = None


class AIRequestMetaPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    request_id: Optional[str] = None
    source: Optional[str] = None
    requested_at: Optional[str] = None


class AIChatRequestPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    messages: List[AIMessagePayload] = Field(..., min_length=1)
    context: AIContextPayload
    task: Optional[AITask] = None
    meta: Optional[AIRequestMetaPayload] = None


class AIResponseMetaPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    request_id: Optional[str] = None
    route: Optional[str] = None
    used_fallback: Optional[bool] = False
    latency_ms: Optional[int] = None
    estimated_cost_usd: Optional[float] = None
    handoff_required: Optional[bool] = False


class AIChatResponsePayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: AIResponseStatus = "ok"
    message: AIMessagePayload
    task: Optional[AITask] = None
    meta: Optional[AIResponseMetaPayload] = None
