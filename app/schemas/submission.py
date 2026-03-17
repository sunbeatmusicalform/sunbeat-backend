from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field

YesNo = Literal["yes", "no"]
ReleaseType = Literal["single", "ep", "album"]


class UploadedFileRef(BaseModel):
    model_config = ConfigDict(extra="ignore")

    file_name: Optional[str] = None
    storage_bucket: Optional[str] = None
    storage_path: Optional[str] = None
    public_url: Optional[str] = None
    download_url: Optional[str] = None
    mime_type: Optional[str] = None
    size_bytes: Optional[int] = None


class IdentificationPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    submitter_name: str = Field(..., min_length=1)
    submitter_email: EmailStr
    project_title: str = Field(..., min_length=1)
    release_type: ReleaseType


class ProjectPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    release_date: str
    genre: Optional[str] = None
    explicit_content: Optional[YesNo] = None
    tiktok_snippet: Optional[str] = None
    cover_link: Optional[str] = None
    promo_assets_link: Optional[str] = None
    presskit_link: Optional[str] = None
    has_video_asset: Optional[YesNo] = None
    video_link: Optional[str] = None
    video_release_date: Optional[str] = None
    cover_file: Optional[UploadedFileRef] = None


class TrackPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    local_id: str
    order_number: int
    title: str = Field(..., min_length=1)
    is_focus_track: Optional[bool] = False
    primary_artists: str = Field(..., min_length=1)
    featured_artists: Optional[str] = None
    interpreters: Optional[str] = None
    authors: str = Field(..., min_length=1)
    publishers: Optional[str] = None
    producers_musicians: Optional[str] = None
    phonographic_producer: Optional[str] = None
    artist_profiles_status: Optional[str] = None
    artist_profile_names_to_create: Optional[str] = None
    existing_profile_links: Optional[str] = None
    has_isrc: Optional[YesNo] = None
    isrc_code: Optional[str] = None
    explicit_content: Optional[YesNo] = None
    tiktok_snippet: Optional[str] = None
    audio_file: Optional[UploadedFileRef] = None
    lyrics: Optional[str] = None
    track_status: Optional[str] = "draft"


class MarketingPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    marketing_numbers: Optional[str] = None
    marketing_focus: Optional[str] = None
    marketing_objectives: Optional[str] = None
    has_marketing_budget: Optional[YesNo] = None
    marketing_budget: Optional[str] = None
    focus_track_name: Optional[str] = None
    date_flexibility: Optional[str] = None
    has_special_guests: Optional[YesNo] = None
    special_guests_bio: Optional[str] = None
    feat_will_promote: Optional[YesNo] = None
    promotion_participants: Optional[str] = None
    influencers_brands_partners: Optional[str] = None
    general_notes: Optional[str] = None
    additional_files: Optional[List[UploadedFileRef]] = None


class SubmissionMetaPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    form_version: Optional[int] = 1
    source: Optional[str] = None
    submitted_at: Optional[str] = None


class SubmissionPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    draft_token: str
    workspace_slug: str
    identification: IdentificationPayload
    project: ProjectPayload
    tracks: List[TrackPayload]
    marketing: Optional[MarketingPayload] = None
    meta: Optional[SubmissionMetaPayload] = None
