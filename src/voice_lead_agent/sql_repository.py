from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from voice_lead_agent.domain import (
    LeadIntakeCommand,
    LeadIntakeResult,
    consent_to_lead_status,
    is_callable_consent,
)
from voice_lead_agent.errors import ApiError


class SqlLeadRepository:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def readiness(self) -> bool:
        try:
            async with self._sessionmaker() as session:
                result = await session.execute(text("select 1"))
                return bool(result.scalar_one() == 1)
        except SQLAlchemyError:
            return False

    async def create_or_get_initial_lead(self, command: LeadIntakeCommand) -> LeadIntakeResult:
        async with self._sessionmaker() as session, session.begin():
            suppressed = await session.execute(
                text(
                    """
                        select id
                        from public.suppression_list
                        where phone_e164 = :phone_e164
                           or (:email is not null and lower(email) = :email)
                        limit 1
                        """
                ),
                {"phone_e164": command.phone_e164, "email": command.email},
            )
            is_suppressed = suppressed.first() is not None
            callable_lead = is_callable_consent(command.consent_status) and not is_suppressed
            lead_status = consent_to_lead_status(command.consent_status, suppressed=is_suppressed)
            blocked_reason = None
            if is_suppressed:
                blocked_reason = "suppressed_contact"
            elif not callable_lead:
                blocked_reason = "consent_status_not_callable"

            lead_id = await self._insert_lead(session, command, lead_status)
            if lead_id is None:
                # Duplicate exists
                existing = await session.execute(
                    text(
                        """
                            select id::text, status, source_entity_type, phone_e164
                            from public.leads
                            where source_system = :source_system
                              and source_entity_id = :source_entity_id
                            """
                    ),
                    {
                        "source_system": command.source_system,
                        "source_entity_id": command.source_entity_id,
                    },
                )
                existing_row = existing.mappings().first()
                if existing_row is None:
                    raise RuntimeError("Lead not found after insert conflict.")

                if (
                    existing_row["source_entity_type"] != command.source_entity_type
                    or existing_row["phone_e164"] != command.phone_e164
                ):
                    raise ApiError(
                        code="source_identity_conflict",
                        message="Lead already exists with a different identity or phone number.",
                        status_code=409,
                    )

                return await self._duplicate_result(
                    session,
                    lead_id=existing_row["id"],
                    status=existing_row["status"],
                )

            await self._insert_lead_event(
                session,
                lead_id=lead_id,
                event_type="lead_created",
                summary="Lead created through trusted intake.",
                metadata={"source_system": command.source_system},
            )
            if not callable_lead:
                return LeadIntakeResult(
                    lead_id=lead_id,
                    status=lead_status,
                    callable=False,
                    call_job_id=None,
                    attempt_number=None,
                    duplicate=False,
                    blocked_reason=blocked_reason,
                )

            job_id = await self._insert_initial_job(session, lead_id)
            await self._insert_lead_event(
                session,
                lead_id=lead_id,
                event_type="call_scheduled",
                summary="Initial call job created.",
                metadata={"job_id": job_id, "attempt_number": 1},
            )
            return LeadIntakeResult(
                lead_id=lead_id,
                status=lead_status,
                callable=True,
                call_job_id=job_id,
                attempt_number=1,
                duplicate=False,
                blocked_reason=None,
            )

    async def _duplicate_result(
        self, session: AsyncSession, lead_id: str, status: str
    ) -> LeadIntakeResult:
        job = await session.execute(
            text(
                """
                select id::text
                from public.jobs
                where job_type = 'initiate_call'
                  and entity_type = 'lead'
                  and entity_id = :lead_id
                  and attempt_number = 1
                order by created_at asc
                limit 1
                """
            ),
            {"lead_id": lead_id},
        )
        job_row = job.mappings().first()

        callable_statuses = {"eligible", "scheduled", "calling", "qualified"}
        is_callable = status in callable_statuses and job_row is not None

        blocked_reason = None
        if not is_callable:
            if status == "suppressed":
                blocked_reason = "suppressed_contact"
            elif status in ("opted_out", "new"):
                blocked_reason = "consent_status_not_callable"
            else:
                blocked_reason = "lead_not_callable"

        return LeadIntakeResult(
            lead_id=lead_id,
            status=status,
            callable=is_callable,
            call_job_id=job_row["id"] if job_row is not None else None,
            attempt_number=1 if job_row is not None else None,
            duplicate=True,
            blocked_reason=blocked_reason,
        )

    async def _insert_lead(
        self,
        session: AsyncSession,
        command: LeadIntakeCommand,
        status: str,
    ) -> str | None:
        result = await session.execute(
            text(
                """
                insert into public.leads (
                  source_system,
                  source_entity_type,
                  source_entity_id,
                  source_payload,
                  source_payload_version,
                  full_name,
                  company_name,
                  phone_e164,
                  email,
                  timezone,
                  status,
                  consent_status,
                  consent_source,
                  consent_captured_at,
                  consent_evidence_uri
                )
                values (
                  :source_system,
                  :source_entity_type,
                  :source_entity_id,
                  cast(:source_payload as jsonb),
                  :source_payload_version,
                  :full_name,
                  :company_name,
                  :phone_e164,
                  :email,
                  :timezone,
                  :status,
                  :consent_status,
                  :consent_source,
                  :consent_captured_at,
                  :consent_evidence_uri
                )
                on conflict (source_system, source_entity_id) do nothing
                returning id::text
                """
            ),
            {
                "source_system": command.source_system,
                "source_entity_type": command.source_entity_type,
                "source_entity_id": command.source_entity_id,
                "source_payload": json.dumps(command.source_payload),
                "source_payload_version": command.source_payload_version,
                "full_name": command.full_name,
                "company_name": command.company_name,
                "phone_e164": command.phone_e164,
                "email": command.email,
                "timezone": command.timezone,
                "status": status,
                "consent_status": command.consent_status,
                "consent_source": command.consent_source,
                "consent_captured_at": command.consent_captured_at,
                "consent_evidence_uri": command.consent_evidence_uri,
            },
        )
        row = result.scalar_one_or_none()
        return str(row) if row is not None else None

    async def _insert_initial_job(self, session: AsyncSession, lead_id: str) -> str:
        result = await session.execute(
            text(
                """
                insert into public.jobs (
                  job_type,
                  status,
                  idempotency_key,
                  entity_type,
                  entity_id,
                  attempt_number,
                  payload,
                  run_after
                )
                values (
                  'initiate_call',
                  'pending',
                  :idempotency_key,
                  'lead',
                  :lead_id,
                  1,
                  '{}'::jsonb,
                  now()
                )
                returning id::text
                """
            ),
            {
                "idempotency_key": f"job:initiate_call:{lead_id}:1",
                "lead_id": lead_id,
            },
        )
        return str(result.scalar_one())

    async def _insert_lead_event(
        self,
        session: AsyncSession,
        lead_id: str,
        event_type: str,
        summary: str,
        metadata: dict[str, Any],
    ) -> None:
        await session.execute(
            text(
                """
                insert into public.lead_events (
                  lead_id,
                  event_type,
                  actor_type,
                  summary,
                  metadata
                )
                values (
                  :lead_id,
                  :event_type,
                  'api',
                  :summary,
                  cast(:metadata as jsonb)
                )
                """
            ),
            {
                "lead_id": lead_id,
                "event_type": event_type,
                "summary": summary,
                "metadata": json.dumps(metadata),
            },
        )
