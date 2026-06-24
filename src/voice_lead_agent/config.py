from __future__ import annotations

import os
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    app_env: str = Field(default="development", alias="APP_ENV")
    app_public_base_url: str = Field(default="http://localhost:8000", alias="APP_PUBLIC_BASE_URL")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    calling_enabled: bool = Field(default=False, alias="CALLING_ENABLED")

    trusted_source_token: str = Field(alias="TRUSTED_SOURCE_TOKEN")
    database_url: str = Field(alias="DATABASE_URL")

    supabase_url: str | None = Field(default=None, alias="SUPABASE_URL")
    supabase_anon_key: str | None = Field(default=None, alias="SUPABASE_ANON_KEY")
    supabase_service_role_key: str | None = Field(default=None, alias="SUPABASE_SERVICE_ROLE_KEY")

    worker_id: str = Field(default="worker-local", alias="WORKER_ID")
    job_lease_seconds: int = Field(default=300, alias="JOB_LEASE_SECONDS")
    job_retry_base_seconds: int = Field(default=300, alias="JOB_RETRY_BASE_SECONDS")

    twilio_account_sid: str | None = Field(default=None, alias="TWILIO_ACCOUNT_SID")
    twilio_auth_token: str | None = Field(default=None, alias="TWILIO_AUTH_TOKEN")
    twilio_caller_id: str | None = Field(default=None, alias="TWILIO_CALLER_ID")

    # Gemini conversation loop (Stage 4). No default API key: callers must provide one.
    gemini_api_key: str | None = Field(default=None, alias="GEMINI_API_KEY")
    gemini_model: str = Field(default="gemini-2.0-flash", alias="GEMINI_MODEL")
    gemini_timeout_seconds: float = Field(default=15.0, alias="GEMINI_TIMEOUT_SECONDS")
    gemini_max_output_tokens: int = Field(default=256, alias="GEMINI_MAX_OUTPUT_TOKENS")
    gemini_temperature: float = Field(default=0.2, alias="GEMINI_TEMPERATURE")
    conversation_max_turns: int = Field(default=10, alias="CONVERSATION_MAX_TURNS")

    # Conversation disclosure content. Loaded from environment, never hard-coded secrets.
    business_name: str = Field(default="AI Voice Lead Agent", alias="BUSINESS_NAME")
    ai_disclosure_text: str = Field(
        default=(
            "This is an automated AI assistant calling on behalf of the business. "
            "You can ask to speak with a human or ask me to stop calling at any time."
        ),
        alias="AI_DISCLOSURE_TEXT",
    )
    human_help_text: str = Field(
        default=(
            "I'll arrange for a member of our team to follow up with you. Thank you for your time."
        ),
        alias="HUMAN_HELP_TEXT",
    )


@lru_cache
def get_settings() -> Settings:
    env_file = os.getenv("VOICE_LEAD_AGENT_ENV_FILE", ".env")
    return Settings(_env_file=env_file)  # type: ignore[call-arg]
