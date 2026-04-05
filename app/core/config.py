
from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    SUPABASE_URL: str = Field(validation_alias="SUPABASE_URL")
    SUPABASE_ANON_KEY: str | None = Field(default=None, validation_alias="SUPABASE_ANON_KEY")
    SUPABASE_SERVICE_ROLE_KEY: str | None = Field(default=None, validation_alias="SUPABASE_SERVICE_ROLE_KEY")
    SUPABASE_KEY: str | None = Field(default=None)

    FRONTEND_BASE_URL: str = Field(default="https://sunbeat.pro")

    RESEND_API_KEY: str | None = Field(default=None)
    RESEND_FROM_EMAIL: str | None = Field(default="noreply@sunbeat.pro")
    RESEND_FROM_NAME: str = Field(default="Sunbeat")

    AIRTABLE_API_KEY: str | None = Field(default=None)
    AIRTABLE_BASE_ID: str | None = Field(default=None)
    AIRTABLE_PROJECTS_TABLE: str = Field(default="[V2] Projetos Musicais")
    AIRTABLE_TRACKS_TABLE: str = Field(default="[V2] Faixas Musicais")
    AIRTABLE_TRACK_PROJECT_LINK_FIELD: str = Field(default="Projeto")

    AIRTABLE_CLIENTS_TABLE: str = Field(default="[V2] Clientes")
    AIRTABLE_CLIENT_NAME_FIELD: str = Field(default="Clientes")
    AIRTABLE_CLIENT_LABEL_FIELD: str = Field(default="Label")
    AIRTABLE_CLIENT_STATUS_FIELD: str = Field(default="Status - Cliente")
    AIRTABLE_CLIENT_DRIVE_LINK_FIELD: str = Field(default="Pasta do Drive")
    AIRTABLE_CLIENT_LABEL_EMAIL_FIELD: str = Field(default="Email do Label")
    AIRTABLE_CLIENT_ARTIST_FOLDER_ID_FIELD: str = Field(default="folder_id_artista")
    AIRTABLE_CLIENT_PROJECTS_FOLDER_ID_FIELD: str = Field(default="folder_id_projetos")

    GOOGLE_DRIVE_ENABLED: bool = Field(default=False)
    GOOGLE_DRIVE_ROOT_FOLDER_ID: str | None = Field(default=None)
    GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON: str | None = Field(default=None)


settings = Settings()
