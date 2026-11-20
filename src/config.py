"""Environment-backed application settings."""

from pathlib import Path

from pydantic import Field, HttpUrl, SecretStr, field_validator
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
    dougal_voice_transcription_url: HttpUrl | None = None
    dougal_voice_transcription_model: str = "whisper-1"
    dougal_voice_transcription_api_key: SecretStr | None = None
    dougal_history_path: Path = Path("data/history.sqlite3")

    @field_validator("dougal_voice_transcription_url", mode="before")
    @classmethod
    def blank_voice_url_is_disabled(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("home_assistant_url")
    @classmethod
    def normalize_home_assistant_url(cls, value: str) -> str:
        return value.rstrip("/")
