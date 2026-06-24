from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

CALLABLE_CONSENT_STATUSES = {
    "specific_automated_call_consent",
    "service_follow_up_approved",
}

CONSENT_STATUSES = CALLABLE_CONSENT_STATUSES | {
    "unknown",
    "withdrawn",
    "do_not_call",
    "blocked",
}

LEAD_STATUSES = {
    "new",
    "eligible",
    "scheduled",
    "calling",
    "qualified",
    "not_qualified",
    "booked",
    "needs_human",
    "opted_out",
    "unreachable",
    "invalid_phone",
    "failed",
    "suppressed",
}

TERMINAL_LEAD_STATUSES = {
    "not_qualified",
    "booked",
    "needs_human",
    "opted_out",
    "unreachable",
    "invalid_phone",
    "failed",
    "suppressed",
}

STRONGER_CALL_ATTEMPT_OUTCOMES = {
    "completed",
    "timed_out",
    "failed",
    "escalated",
}

PHONE_RE = re.compile(r"^\+[1-9][0-9]{7,14}$")


class ValidationFailure(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class LeadIntakeCommand:
    source_system: str
    source_entity_type: str
    source_entity_id: str
    source_payload: dict[str, Any]
    source_payload_version: int
    full_name: str | None
    company_name: str | None
    phone_e164: str
    email: str | None
    timezone: str | None
    consent_status: str
    consent_source: str | None
    consent_captured_at: datetime | None
    consent_evidence_uri: str | None


@dataclass(frozen=True)
class LeadIntakeResult:
    lead_id: str
    status: str
    callable: bool
    call_job_id: str | None
    attempt_number: int | None
    duplicate: bool
    blocked_reason: str | None = None


@dataclass(frozen=True)
class JobRecord:
    id: str
    entity_id: str
    attempt_number: int
    attempt_count: int
    max_attempts: int


@dataclass(frozen=True)
class BusinessCallingSettings:
    calling_paused: bool
    calling_hours: dict[str, Any]
    max_call_attempts: int


@dataclass(frozen=True)
class LeadCallContext:
    lead_id: str
    phone_e164: str
    email: str | None
    status: str
    consent_status: str
    attempt_count: int
    business: BusinessCallingSettings
    suppressed: bool


@dataclass(frozen=True)
class CallAttemptRecord:
    id: str
    lead_id: str
    attempt_number: int
    status: str
    twilio_call_sid: str | None


@dataclass(frozen=True)
class ProviderEventResult:
    duplicate: bool
    call_attempt_id: str | None
    status: str | None


class RetryableProviderError(Exception):
    """External provider failure that is safe to retry with the same job."""


class PermanentProviderError(Exception):
    """External provider failure that should not be retried automatically."""


def canonical_source(value: str) -> str:
    normalized = value.strip().lower()
    if not normalized:
        raise ValidationFailure("invalid_request", "Source fields must not be blank.")
    return normalized


def normalize_phone(value: str) -> str:
    phone = value.strip()
    if not PHONE_RE.fullmatch(phone):
        raise ValidationFailure("invalid_phone", "Phone number must be valid E.164.")
    return phone


def normalize_email(value: str | None) -> str | None:
    if value is None:
        return None
    email = value.strip().lower()
    if not email:
        return None
    if "@" not in email or email.startswith("@") or email.endswith("@"):
        raise ValidationFailure("invalid_email", "Email address is invalid.")
    return email


def normalize_timezone(value: str | None) -> str | None:
    if value is None or value.strip() == "":
        return None
    timezone = value.strip()
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValidationFailure("invalid_timezone", "Timezone is invalid.") from exc
    return timezone


def is_callable_consent(consent_status: str) -> bool:
    return consent_status in CALLABLE_CONSENT_STATUSES


def consent_to_lead_status(consent_status: str, *, suppressed: bool) -> str:
    if suppressed:
        return "suppressed"
    if consent_status in CALLABLE_CONSENT_STATUSES:
        return "scheduled"
    if consent_status in ("withdrawn", "do_not_call"):
        return "opted_out"
    if consent_status == "blocked":
        return "suppressed"
    return "new"


def validate_consent_status(consent_status: str) -> str:
    normalized = consent_status.strip()
    if normalized not in CONSENT_STATUSES:
        raise ValidationFailure("invalid_consent_status", "Consent status is not allowed.")
    return normalized
