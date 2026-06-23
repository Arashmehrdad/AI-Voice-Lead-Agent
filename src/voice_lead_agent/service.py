from __future__ import annotations

from fastapi import status

from voice_lead_agent.domain import (
    LeadIntakeCommand,
    LeadIntakeResult,
    is_callable_consent,
)
from voice_lead_agent.errors import ApiError
from voice_lead_agent.repositories import LeadRepository
from voice_lead_agent.schemas import LeadIntakeRequest


def _requires_consent_evidence(request: LeadIntakeRequest) -> bool:
    return is_callable_consent(request.consent_status)


def build_command(request: LeadIntakeRequest) -> LeadIntakeCommand:
    if _requires_consent_evidence(request) and (
        request.consent_source is None or request.consent_captured_at is None
    ):
        raise ApiError(
            code="missing_consent_evidence",
            message="Callable consent requires source and capture timestamp.",
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    return LeadIntakeCommand(
        source_system=request.source_system,
        source_entity_type=request.source_entity_type,
        source_entity_id=request.source_entity_id,
        source_payload=request.source_payload,
        source_payload_version=request.source_payload_version,
        full_name=request.full_name,
        company_name=request.company_name,
        phone_e164=request.phone_e164,
        email=request.email,
        timezone=request.timezone,
        consent_status=request.consent_status,
        consent_source=request.consent_source,
        consent_captured_at=request.consent_captured_at,
        consent_evidence_uri=request.consent_evidence_uri,
    )


async def intake_lead(
    request: LeadIntakeRequest,
    repository: LeadRepository,
) -> tuple[int, LeadIntakeResult]:
    command = build_command(request)
    result = await repository.create_or_get_initial_lead(command)
    status_code = status.HTTP_200_OK if result.duplicate else status.HTTP_201_CREATED
    return status_code, result
