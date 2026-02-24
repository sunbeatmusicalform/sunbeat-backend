from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Lê do .env e também do ambiente
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Supabase
    SUPABASE_URL: str = Field(validation_alias="SUPABASE_URL")
    SUPABASE_ANON_KEY: str | None = Field(default=None, validation_alias="SUPABASE_ANON_KEY")

    # Seu .env atual usa isso:
    SUPABASE_SERVICE_ROLE_KEY: str | None = Field(default=None, validation_alias="SUPABASE_SERVICE_ROLE_KEY")

    # Compatibilidade com seu nome antigo (se existir em algum lugar)
    SUPABASE_KEY: str | None = Field(default=None, validation_alias="SUPABASE_KEY")


settings = Settings()