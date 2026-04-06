from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

YesNo = Literal["yes", "no"]
ReleaseType = Literal["single", "ep", "album"]
WorkflowType = str
ClearanceFormat = Literal[
    "music_release_clearance_intake",
    "music_project_track",
    "audiovisual_product_sync",
]


class UploadedFileRef(BaseModel):
    model_config = ConfigDict(extra="ignore")

    file_name: Optional[str] = None
    storage_bucket: Optional[str] = None
    storage_path: Optional[str] = None
    public_url: Optional[str] = None
    download_url: Optional[str] = None
    mime_type: Optional[str] = None
    size_bytes: Optional[int] = None


class SubmissionMetaPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    form_version: Optional[str | int] = "legacy_v1"
    source: Optional[str] = None
    submitted_at: Optional[str] = None


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


class ReleaseIntakeSubmissionPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    draft_token: str
    workspace_slug: str
    workflow_type: WorkflowType = "release_intake"
    identification: IdentificationPayload
    project: ProjectPayload
    tracks: List[TrackPayload]
    marketing: Optional[MarketingPayload] = None
    meta: Optional[SubmissionMetaPayload] = None


class RightsClearanceRequesterPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    requester_name: str = Field(..., min_length=1)
    requester_email: EmailStr
    requester_company: str = Field(..., min_length=1)
    requester_role: str = Field(..., min_length=1)


class RightsClearanceRequestTypePayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    clearance_format: ClearanceFormat


class RightsClearanceProjectContextPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    project_title: str = Field(..., min_length=1)
    responsible_company: str = Field(..., min_length=1)
    client_or_distributor: str = Field(..., min_length=1)
    release_or_start_date: str
    release_type: Optional[ReleaseType] = None
    project_synopsis: Optional[str] = None
    has_brand_association: Optional[YesNo] = None
    brand_context: Optional[str] = None
    general_clearance_notes: Optional[str] = None


class RightsClearanceTrackPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    local_id: str
    order_number: int
    title: str = Field(..., min_length=1)
    primary_artists: str = Field(..., min_length=1)
    authors: str = Field(..., min_length=1)
    publishers: Optional[str] = None
    phonogram_owner: str = Field(..., min_length=1)
    has_isrc: Optional[YesNo] = None
    isrc_code: Optional[str] = None
    notes_for_clearance: Optional[str] = None


class RightsClearanceScopePayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    music_title: str = Field(..., min_length=1)
    artist_name: str = Field(..., min_length=1)
    phonogram_owner: str = Field(..., min_length=1)
    territory: str = Field(..., min_length=1)
    licensing_period: str = Field(..., min_length=1)
    composer_author_info: Optional[str] = None
    publisher_info: Optional[str] = None
    material_type: Optional[str] = None
    intended_use: Optional[str] = None
    exclusivity: Optional[YesNo] = None
    audiovisual_type: Optional[str] = None
    director_name: Optional[str] = None
    product_or_campaign_name: Optional[str] = None
    scene_description: Optional[str] = None
    sync_duration: Optional[str] = None
    media_channels: Optional[str] = None


class RightsClearanceAssetsPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    supporting_files: Optional[List[UploadedFileRef]] = None
    reference_links: Optional[str] = None
    additional_notes: Optional[str] = None


class RightsClearanceSubmissionPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    draft_token: str
    workspace_slug: str
    workflow_type: WorkflowType = "rights_clearance"
    requester_identification: RightsClearanceRequesterPayload
    request_type: RightsClearanceRequestTypePayload
    project_context: RightsClearanceProjectContextPayload
    tracks: Optional[List[RightsClearanceTrackPayload]] = None
    clearance_scope: Optional[RightsClearanceScopePayload] = None
    assets_references: Optional[RightsClearanceAssetsPayload] = None
    meta: Optional[SubmissionMetaPayload] = None

    @model_validator(mode="after")
    def validate_rights_clearance(self) -> "RightsClearanceSubmissionPayload":
        if (
            self.project_context.has_brand_association == "yes"
            and not str(self.project_context.brand_context or "").strip()
        ):
            raise ValueError(
                "brand_context is required when has_brand_association is yes"
            )

        if self.request_type.clearance_format == "music_release_clearance_intake":
            if self.project_context.release_type is None:
                raise ValueError(
                    "release_type is required for music_release_clearance_intake"
                )

            if not str(self.project_context.general_clearance_notes or "").strip():
                raise ValueError(
                    "general_clearance_notes is required for music_release_clearance_intake"
                )

            tracks = self.tracks or []
            if not tracks:
                raise ValueError(
                    "At least one track is required for music_release_clearance_intake"
                )

            for index, track in enumerate(tracks, start=1):
                if track.has_isrc == "yes" and not str(track.isrc_code or "").strip():
                    raise ValueError(
                        f"isrc_code is required for track {index} when has_isrc is yes"
                    )

            return self

        if self.clearance_scope is None:
            raise ValueError(
                "clearance_scope is required for generic rights_clearance formats"
            )

        if self.request_type.clearance_format == "music_project_track":
            required_fields = {
                "composer_author_info": self.clearance_scope.composer_author_info,
                "publisher_info": self.clearance_scope.publisher_info,
                "material_type": self.clearance_scope.material_type,
                "intended_use": self.clearance_scope.intended_use,
                "exclusivity": self.clearance_scope.exclusivity,
            }
        else:
            required_fields = {
                "audiovisual_type": self.clearance_scope.audiovisual_type,
                "director_name": self.clearance_scope.director_name,
                "product_or_campaign_name": self.clearance_scope.product_or_campaign_name,
                "scene_description": self.clearance_scope.scene_description,
                "sync_duration": self.clearance_scope.sync_duration,
                "media_channels": self.clearance_scope.media_channels,
            }

        missing = [
            field_name
            for field_name, value in required_fields.items()
            if value in (None, "")
        ]
        if missing:
            raise ValueError(
                "Missing required rights_clearance fields for the selected "
                f"clearance_format: {', '.join(missing)}"
            )

        return self


WorkflowSubmissionPayload = (
    ReleaseIntakeSubmissionPayload | RightsClearanceSubmissionPayload
)
SubmissionPayload = ReleaseIntakeSubmissionPayload


def validate_submission_payload(payload: Dict[str, Any]) -> WorkflowSubmissionPayload:
    workflow_type = str(payload.get("workflow_type") or "release_intake").strip()

    if workflow_type == "rights_clearance":
        return RightsClearanceSubmissionPayload.model_validate(payload)

    return ReleaseIntakeSubmissionPayload.model_validate(payload)
