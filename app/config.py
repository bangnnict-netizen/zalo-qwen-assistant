"""Application settings loaded from environment variables via pydantic-settings."""

from functools import lru_cache
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def _parse_csv_list(value: object) -> list[str]:
    """Parse comma-separated env values into a list of strings."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        if not value.strip():
            return []
        return [item.strip() for item in value.split(",") if item.strip()]
    return [str(value)]


class Settings(BaseSettings):
    """Central configuration. Never hardcode secrets — use .env / Space secrets."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    groq_api_key: str = ""
    llm_primary_model: str = "qwen/qwen3.6-27b"
    llm_fallback_model: str = "openai/gpt-oss-20b"
    bot_tags: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["@Byron", "@bot"]
    )
    supabase_url: str = ""
    supabase_key: str = ""
    supabase_db_url: str = ""
    admin_token: str = ""
    enable_zalo_real: bool = True
    airtable_api_key: str = ""
    airtable_base_id: str = ""
    allowed_internal_group_ids: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["group_internal_demo"]
    )
    allowed_customer_group_ids: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["group_customer_demo"]
    )
    admin_user_ids: Annotated[list[str], NoDecode] = []
    ttl_days: int = 3
    zalo_max_msg_per_min: int = 6
    zalo_min_delay_sec: int = 3
    zalo_max_delay_sec: int = 6

    @field_validator(
        "bot_tags",
        "allowed_internal_group_ids",
        "allowed_customer_group_ids",
        "admin_user_ids",
        mode="before",
    )
    @classmethod
    def parse_id_lists(cls, value: object) -> list[str]:
        return _parse_csv_list(value)


@lru_cache
def get_settings() -> Settings:
    """Cached settings singleton."""
    return Settings()
