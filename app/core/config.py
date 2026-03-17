from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ------------------------------------------------
    # SUPABASE
    # ------------------------------------------------

    SUPABASE_URL: str = Field(validation_alias="SUPABASE_URL")

    SUPABASE_ANON_KEY: str | None = Field(
        default=None,
        validation_alias="SUPABASE_ANON_KEY",
    )

    SUPABASE_SERVICE_ROLE_KEY: str | None = Field(
        default=None,
        validation_alias="SUPABASE_SERVICE_ROLE_KEY",
    )

    # fallback legado
    SUPABASE_KEY: str | None = Field(default=None)

    # ------------------------------------------------
    # FRONTEND
    # ------------------------------------------------

    FRONTEND_BASE_URL: str = Field(default="https://sunbeat.pro")

    # ------------------------------------------------
    # EMAIL / RESEND
    # ------------------------------------------------

    RESEND_API_KEY: str | None = Field(default=None)

    RESEND_FROM_EMAIL: str | None = Field(default="noreply@sunbeat.pro")

    RESEND_FROM_NAME: str = Field(default="Sunbeat")

    # ------------------------------------------------
    # AIRTABLE
    # ------------------------------------------------

    AIRTABLE_API_KEY: str | None = Field(default=None)
    AIRTABLE_BASE_ID: str | None = Field(default=None)

    AIRTABLE_PROJECTS_TABLE: str = Field(default="[V2] Projetos Musicais")
    AIRTABLE_TRACKS_TABLE: str = Field(default="[V2] Faixas Musicais")

    # campo de link entre track e projeto
    AIRTABLE_TRACK_PROJECT_LINK_FIELD: str = Field(default="Projeto")


settings = Settings()
