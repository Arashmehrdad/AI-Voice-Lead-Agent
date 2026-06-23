from __future__ import annotations

import asyncio
from dataclasses import dataclass
from uuid import uuid4

from voice_lead_agent.domain import (
    LeadIntakeCommand,
    LeadIntakeResult,
    consent_to_lead_status,
    is_callable_consent,
)
from voice_lead_agent.errors import ApiError


@dataclass
class StoredLead:
    lead_id: str
    status: str
    callable: bool
    call_job_id: str | None
    attempt_number: int | None
    blocked_reason: str | None
    source_entity_type: str
    phone_e164: str


class InMemoryLeadRepository:
    def __init__(self) -> None:
        self.leads: dict[tuple[str, str], StoredLead] = {}
        self.jobs: dict[str, str] = {}
        self.suppressed_phones: set[str] = set()
        self.suppressed_emails: set[str] = set()
        self.fail_job_creation = False
        self.ready = True
        self.simulate_concurrency_delay = False

    async def readiness(self) -> bool:
        return self.ready

    async def create_or_get_initial_lead(self, command: LeadIntakeCommand) -> LeadIntakeResult:
        key = (command.source_system, command.source_entity_id)
        existing = self.leads.get(key)

        if self.simulate_concurrency_delay:
            await asyncio.sleep(0.05)

        if existing is None:
            existing = self.leads.get(key)

        if existing is not None:
            if (
                existing.source_entity_type != command.source_entity_type
                or existing.phone_e164 != command.phone_e164
            ):
                raise ApiError(
                    code="source_identity_conflict",
                    message="Lead already exists with a different identity or phone number.",
                    status_code=409,
                )

            callable_statuses = {"eligible", "scheduled", "calling", "qualified"}
            is_callable = existing.status in callable_statuses and existing.call_job_id is not None

            blocked_reason = None
            if not is_callable:
                if existing.status == "suppressed":
                    blocked_reason = "suppressed_contact"
                elif existing.status in ("opted_out", "new"):
                    blocked_reason = "consent_status_not_callable"
                else:
                    blocked_reason = "lead_not_callable"

            return LeadIntakeResult(
                lead_id=existing.lead_id,
                status=existing.status,
                callable=is_callable,
                call_job_id=existing.call_job_id,
                attempt_number=existing.attempt_number,
                duplicate=True,
                blocked_reason=blocked_reason,
            )

        suppressed = command.phone_e164 in self.suppressed_phones or (
            command.email is not None and command.email in self.suppressed_emails
        )
        callable_lead = is_callable_consent(command.consent_status) and not suppressed
        lead_id = str(uuid4())
        status = consent_to_lead_status(command.consent_status, suppressed=suppressed)
        blocked_reason = None
        if suppressed:
            blocked_reason = "suppressed_contact"
        elif not callable_lead:
            blocked_reason = "consent_status_not_callable"

        staged = StoredLead(
            lead_id=lead_id,
            status=status,
            callable=callable_lead,
            call_job_id=None,
            attempt_number=None,
            blocked_reason=blocked_reason,
            source_entity_type=command.source_entity_type,
            phone_e164=command.phone_e164,
        )
        if callable_lead:
            if self.fail_job_creation:
                raise RuntimeError("simulated job creation failure")
            job_id = str(uuid4())
            job_key = f"job:initiate_call:{lead_id}:1"
            self.jobs[job_key] = job_id
            staged.call_job_id = job_id
            staged.attempt_number = 1

        self.leads[key] = staged
        return LeadIntakeResult(
            lead_id=staged.lead_id,
            status=staged.status,
            callable=staged.callable,
            call_job_id=staged.call_job_id,
            attempt_number=staged.attempt_number,
            duplicate=False,
            blocked_reason=staged.blocked_reason,
        )
