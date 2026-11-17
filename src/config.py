"""Environment-backed application settings."""

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    telegram_bot_token: SecretStr
    allowed_telegram_user_ids: set[int] = Field(min_length=1)
    openai_api_key: SecretStr
    openai_base_url: str | None = None
    openai_model: str = "gpt-5.4-nano"
    home_assistant_url: str
    home_assistant_token: SecretStr
    dougal_indicator_label: str = ""

    @field_validator("home_assistant_url")
    @classmethod
    def normalize_home_assistant_url(cls, value: str) -> str:
        return value.rstrip("/")
