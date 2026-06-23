from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from voice_lead_agent.domain import (
    CALLABLE_CONSENT_STATUSES,
    ValidationFailure,
    canonical_source,
    normalize_email,
    normalize_phone,
    normalize_timezone,
    validate_consent_status,
)


class LeadIntakeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_system: str
    source_entity_type: str
    source_entity_id: str
    source_payload: dict[str, Any] = Field(default_factory=dict)
    source_payload_version: int
    full_name: str | None = None
    company_name: str | None = None
    phone_e164: str
    email: str | None = None
    timezone: str | None = None
    consent_status: str
    consent_source: str | None = None
    consent_captured_at: datetime | None = None
    consent_evidence_uri: str | None = None

    @field_validator("source_system", "source_entity_type", mode="after")
    @classmethod
    def validate_source(cls, value: str) -> str:
        return canonical_source(value)

    @field_validator("source_entity_id", mode="after")
    @classmethod
    def validate_source_entity_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("missing_source_entity_id")
        return normalized

    @field_validator("source_payload_version", mode="after")
    @classmethod
    def validate_source_payload_version(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("invalid_source_payload_version")
        return value

    @field_validator("phone_e164", mode="after")
    @classmethod
    def validate_phone(cls, value: str) -> str:
        try:
            return normalize_phone(value)
        except ValidationFailure as exc:
            raise ValueError(exc.code) from exc

    @field_validator("email", mode="after")
    @classmethod
    def validate_email(cls, value: str | None) -> str | None:
        try:
            return normalize_email(value)
        except ValidationFailure as exc:
            raise ValueError(exc.code) from exc

    @field_validator("timezone", mode="after")
    @classmethod
    def validate_timezone(cls, value: str | None) -> str | None:
        try:
            return normalize_timezone(value)
        except ValidationFailure as exc:
            raise ValueError(exc.code) from exc

    @field_validator("consent_status", mode="after")
    @classmethod
    def validate_consent(cls, value: str) -> str:
        try:
            return validate_consent_status(value)
        except ValidationFailure as exc:
            raise ValueError(exc.code) from exc

    @model_validator(mode="after")
    def validate_callable_consent(self) -> LeadIntakeRequest:
        if self.consent_status in CALLABLE_CONSENT_STATUSES:
            if not self.consent_source or not self.consent_source.strip():
                raise ValueError("invalid_consent_source")
            if self.consent_captured_at is None:
                raise ValueError("invalid_consent_captured_at")
            if self.consent_captured_at.tzinfo is None:
                raise ValueError("invalid_consent_captured_at")

            self.consent_captured_at = self.consent_captured_at.astimezone(UTC)
        return self


class LeadIntakeResponse(BaseModel):
    lead_id: str
    status: str
    callable: bool
    call_job_id: str | None
    attempt_number: int | None = None
    duplicate: bool
    blocked_reason: str | None = None


class LiveResponse(BaseModel):
    status: str
    service: str
    timestamp: str


class ReadyDatabase(BaseModel):
    reachable: bool
    migration_version: str


class ReadyConfiguration(BaseModel):
    required_environment_present: bool


class ReadyResponse(BaseModel):
    status: str
    service: str
    environment: str
    database: ReadyDatabase
    configuration: ReadyConfiguration
    calling_paused: bool
