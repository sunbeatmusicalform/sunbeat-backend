from __future__ import annotations

from typing import Literal, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class ReleaseIntakeHistoryLookupItemPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    value: str
    field: str
    source: Literal["submitter_history"] = "submitter_history"
    count: int = Field(..., ge=1)
    lastUsedAt: Optional[str] = None


class ReleaseIntakeHistoryLookupResponsePayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ok: bool = True
    items: List[ReleaseIntakeHistoryLookupItemPayload] = Field(default_factory=list)
