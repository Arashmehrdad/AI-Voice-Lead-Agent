from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, cast

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from voice_lead_agent.domain import (
    STRONGER_CALL_ATTEMPT_OUTCOMES,
    BusinessCallingSettings,
    CallAttemptRecord,
    JobRecord,
    LeadCallContext,
    LeadIntakeCommand,
    LeadIntakeResult,
    ProviderEventResult,
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

    async def claim_due_initiate_call_jobs(
        self, *, worker_id: str, lease_seconds: int, limit: int
    ) -> list[JobRecord]:
        async with self._sessionmaker() as session, session.begin():
            result = await session.execute(
                text(
                    """
                    with candidates as (
                      select id
                      from public.jobs
                      where job_type = 'initiate_call'
                        and status in ('pending', 'retry_scheduled')
                        and run_after <= now()
                      order by run_after, created_at, id
                      for update skip locked
                      limit :limit
                    )
                    update public.jobs j
                    set status = 'claimed',
                        locked_by = :worker_id,
                        locked_at = now(),
                        lease_expires_at = now() + (:lease_seconds || ' seconds')::interval
                    from candidates
                    where j.id = candidates.id
                    returning
                      j.id::text,
                      j.entity_id::text,
                      j.attempt_number,
                      j.attempt_count,
                      j.max_attempts
                    """
                ),
                {
                    "worker_id": worker_id,
                    "lease_seconds": lease_seconds,
                    "limit": limit,
                },
            )
            return [
                JobRecord(
                    id=row["id"],
                    entity_id=row["entity_id"],
                    attempt_number=int(row["attempt_number"]),
                    attempt_count=int(row["attempt_count"]),
                    max_attempts=int(row["max_attempts"]),
                )
                for row in result.mappings()
            ]

    async def release_expired_leases(self) -> int:
        async with self._sessionmaker() as session, session.begin():
            result = await session.execute(
                text(
                    """
                    update public.jobs
                    set status = 'pending',
                        locked_by = null,
                        locked_at = null,
                        lease_expires_at = null,
                        last_error_category = 'lease_expired',
                        last_error_redacted = 'Worker lease expired before completion.'
                    where status in ('claimed', 'running')
                      and lease_expires_at < now()
                    """
                )
            )
            rowcount = getattr(result, "rowcount", 0)
            return int(rowcount or 0)

    async def mark_job_running(self, *, job_id: str, worker_id: str) -> bool:
        async with self._sessionmaker() as session, session.begin():
            result = await session.execute(
                text(
                    """
                    update public.jobs
                    set status = 'running'
                    where id = :job_id
                      and status = 'claimed'
                      and locked_by = :worker_id
                      and lease_expires_at > now()
                    returning 1
                    """
                ),
                {"job_id": job_id, "worker_id": worker_id},
            )
            return result.scalar_one_or_none() is not None

    async def get_lead_call_context(self, *, lead_id: str) -> LeadCallContext | None:
        async with self._sessionmaker() as session:
            result = await session.execute(
                text(
                    """
                    select
                      l.id::text as lead_id,
                      l.phone_e164,
                      l.email,
                      l.status,
                      l.consent_status,
                      l.attempt_count,
                      coalesce(bs.calling_paused, true) as calling_paused,
                      coalesce(bs.calling_hours, '{}'::jsonb) as calling_hours,
                      coalesce(bs.max_call_attempts, 3) as max_call_attempts,
                      exists (
                        select 1
                        from public.suppression_list s
                        where s.phone_e164 = l.phone_e164
                           or (l.email is not null and lower(s.email) = lower(l.email))
                      ) as suppressed
                    from public.leads l
                    left join public.business_settings bs on true
                    where l.id = :lead_id
                    limit 1
                    """
                ),
                {"lead_id": lead_id},
            )
            row = result.mappings().first()
            if row is None:
                return None
            calling_hours_raw = row["calling_hours"]
            calling_hours = (
                calling_hours_raw
                if isinstance(calling_hours_raw, dict)
                else json.loads(str(calling_hours_raw))
            )
            return LeadCallContext(
                lead_id=row["lead_id"],
                phone_e164=row["phone_e164"],
                email=row["email"],
                status=row["status"],
                consent_status=row["consent_status"],
                attempt_count=int(row["attempt_count"]),
                business=BusinessCallingSettings(
                    calling_paused=bool(row["calling_paused"]),
                    calling_hours=calling_hours,
                    max_call_attempts=int(row["max_call_attempts"]),
                ),
                suppressed=bool(row["suppressed"]),
            )

    async def cancel_job(self, *, job_id: str, reason: str) -> None:
        await self._set_terminal_job_status(job_id=job_id, status="cancelled", reason=reason)

    async def fail_job(self, *, job_id: str, reason: str) -> None:
        await self._set_terminal_job_status(job_id=job_id, status="failed", reason=reason)

    async def dead_letter_job(self, *, job_id: str, reason: str) -> None:
        await self._set_terminal_job_status(job_id=job_id, status="dead_lettered", reason=reason)

    async def succeed_job(self, *, job_id: str) -> None:
        async with self._sessionmaker() as session, session.begin():
            await session.execute(
                text(
                    """
                    update public.jobs
                    set status = 'succeeded',
                        locked_by = null,
                        locked_at = null,
                        lease_expires_at = null,
                        last_error_category = null,
                        last_error_redacted = null
                    where id = :job_id
                    """
                ),
                {"job_id": job_id},
            )

    async def schedule_job_retry(
        self, *, job_id: str, reason: str, retry_delay_seconds: int
    ) -> None:
        async with self._sessionmaker() as session, session.begin():
            await session.execute(
                text(
                    """
                    update public.jobs
                    set status = 'retry_scheduled',
                        run_after = now() + (:retry_delay_seconds || ' seconds')::interval,
                        attempt_count = attempt_count + 1,
                        locked_by = null,
                        locked_at = null,
                        lease_expires_at = null,
                        last_error_category = :reason,
                        last_error_redacted = :reason
                    where id = :job_id
                    """
                ),
                {"job_id": job_id, "reason": reason, "retry_delay_seconds": retry_delay_seconds},
            )

    async def defer_job(self, *, job_id: str, reason: str, retry_delay_seconds: int) -> None:
        async with self._sessionmaker() as session, session.begin():
            await session.execute(
                text(
                    """
                    update public.jobs
                    set status = 'retry_scheduled',
                        run_after = now() + (:retry_delay_seconds || ' seconds')::interval,
                        locked_by = null,
                        locked_at = null,
                        lease_expires_at = null,
                        last_error_category = :reason,
                        last_error_redacted = :reason
                    where id = :job_id
                    """
                ),
                {"job_id": job_id, "reason": reason, "retry_delay_seconds": retry_delay_seconds},
            )

    async def create_or_get_call_attempt(
        self, *, lead_id: str, attempt_number: int
    ) -> CallAttemptRecord:
        async with self._sessionmaker() as session, session.begin():
            result = await session.execute(
                text(
                    """
                    insert into public.call_attempts (
                      lead_id,
                      attempt_number,
                      idempotency_key,
                      status,
                      started_at
                    )
                    values (
                      :lead_id,
                      :attempt_number,
                      :idempotency_key,
                      'scheduled',
                      now()
                    )
                    on conflict (lead_id, attempt_number) do update
                    set lead_id = excluded.lead_id
                    returning
                      id::text,
                      lead_id::text,
                      attempt_number,
                      status,
                      twilio_call_sid
                    """
                ),
                {
                    "lead_id": lead_id,
                    "attempt_number": attempt_number,
                    "idempotency_key": f"call:{lead_id}:{attempt_number}",
                },
            )
            row = result.mappings().one()
            return self._call_attempt_from_row(cast(Mapping[str, Any], row))

    async def store_twilio_call_sid(self, *, call_attempt_id: str, call_sid: str) -> bool:
        async with self._sessionmaker() as session, session.begin():
            result = await session.execute(
                text(
                    """
                    update public.call_attempts
                    set twilio_call_sid = :call_sid
                    where id = :call_attempt_id
                      and status = 'dialing'
                      and twilio_call_sid is null
                    returning 1
                    """
                ),
                {"call_attempt_id": call_attempt_id, "call_sid": call_sid},
            )
            return result.scalar_one_or_none() is not None

    async def mark_call_attempt_dialing(self, *, call_attempt_id: str) -> bool:
        async with self._sessionmaker() as session, session.begin():
            result = await session.execute(
                text(
                    """
                    update public.call_attempts
                    set status = 'dialing'
                    where id = :call_attempt_id
                      and status = 'scheduled'
                      and twilio_call_sid is null
                    returning 1
                    """
                ),
                {"call_attempt_id": call_attempt_id},
            )
            return result.scalar_one_or_none() is not None

    async def mark_call_provider_state_uncertain(
        self, *, initiate_job_id: str, call_attempt_id: str, reason: str
    ) -> str:
        idempotency_key = f"job:reconcile_twilio_call:{call_attempt_id}"
        async with self._sessionmaker() as session, session.begin():
            await session.execute(
                text(
                    """
                    update public.jobs
                    set status = 'cancelled',
                        locked_by = null,
                        locked_at = null,
                        lease_expires_at = null,
                        last_error_category = :reason,
                        last_error_redacted = :reason
                    where id = :initiate_job_id
                    """
                ),
                {"initiate_job_id": initiate_job_id, "reason": reason},
            )
            result = await session.execute(
                text(
                    """
                    insert into public.jobs (
                        job_type, status, idempotency_key,
                        entity_type, entity_id, attempt_number,
                        payload, run_after
                    )
                    values (
                        'reconcile_twilio_call', 'pending',
                        :idempotency_key,
                        'call_attempt', :call_attempt_id, 1,
                        '{}'::jsonb, now()
                    )
                    on conflict (idempotency_key) do nothing
                    returning id::text
                    """
                ),
                {
                    "idempotency_key": idempotency_key,
                    "call_attempt_id": call_attempt_id,
                },
            )
            row = result.scalar_one_or_none()
            if row is not None:
                return str(row)
            existing = await session.execute(
                text(
                    """
                    select id::text
                    from public.jobs
                    where idempotency_key = :idempotency_key
                    limit 1
                    """
                ),
                {"idempotency_key": idempotency_key},
            )
            existing_row = existing.scalar_one_or_none()
            if existing_row is None:
                raise RuntimeError("Reconciliation job was not found after idempotency conflict.")
            return str(existing_row)

    async def record_twilio_call_status(
        self, *, params: dict[str, str], payload_hash: str
    ) -> ProviderEventResult:
        call_sid = params.get("CallSid") or ""
        call_status = params.get("CallStatus") or "unknown"
        sequence_number = params.get("SequenceNumber")
        if sequence_number:
            idempotency_key = f"twilio:call-status:{call_sid}:{sequence_number}:{call_status}"
        else:
            idempotency_key = f"twilio:call-status:{call_sid}:{call_status}:{payload_hash}"

        async with self._sessionmaker() as session, session.begin():
            inserted = await session.execute(
                text(
                    """
                    insert into public.provider_events (
                      provider,
                      event_type,
                      provider_object_id,
                      idempotency_key,
                      payload_hash,
                      signature_valid,
                      processing_status
                    )
                    values (
                      'twilio',
                      'call-status',
                      :call_sid,
                      :idempotency_key,
                      :payload_hash,
                      true,
                      'received'
                    )
                    on conflict (provider, idempotency_key) do nothing
                    returning id::text
                    """
                ),
                {
                    "call_sid": call_sid,
                    "idempotency_key": idempotency_key,
                    "payload_hash": payload_hash,
                },
            )
            if inserted.scalar_one_or_none() is None:
                await session.execute(
                    text(
                        """
                        update public.provider_events
                        set delivery_count = delivery_count + 1,
                            last_seen_at = now()
                        where provider = 'twilio'
                          and idempotency_key = :idempotency_key
                        """
                    ),
                    {"idempotency_key": idempotency_key},
                )
                existing_attempt = await self._find_call_attempt_by_sid(session, call_sid)
                return ProviderEventResult(
                    duplicate=True,
                    call_attempt_id=existing_attempt.id if existing_attempt else None,
                    status=existing_attempt.status if existing_attempt else None,
                )

            mapped_status = map_twilio_call_status(call_status)
            attempt = await self._find_call_attempt_by_sid(session, call_sid)
            if (
                attempt is not None
                and mapped_status is not None
                and should_update_call_attempt_status(attempt.status, mapped_status)
            ):
                await session.execute(
                    text(
                        """
                            update public.call_attempts
                            set status = :status,
                                twilio_status = :twilio_status,
                                ended_at = case
                                  when :status in (
                                    'completed',
                                    'busy',
                                    'no_answer',
                                    'failed',
                                    'cancelled'
                                  ) then coalesce(ended_at, now())
                                  else ended_at
                                end,
                                answered_at = case
                                  when :status = 'in_progress' then coalesce(answered_at, now())
                                  else answered_at
                                end
                            where id = :call_attempt_id
                            """
                    ),
                    {
                        "call_attempt_id": attempt.id,
                        "status": mapped_status,
                        "twilio_status": call_status,
                    },
                )
                attempt = CallAttemptRecord(
                    id=attempt.id,
                    lead_id=attempt.lead_id,
                    attempt_number=attempt.attempt_number,
                    status=mapped_status,
                    twilio_call_sid=attempt.twilio_call_sid,
                )

            await session.execute(
                text(
                    """
                    update public.provider_events
                    set processing_status = 'processed',
                        processed_at = now()
                    where provider = 'twilio'
                      and idempotency_key = :idempotency_key
                    """
                ),
                {"idempotency_key": idempotency_key},
            )
            return ProviderEventResult(
                duplicate=False,
                call_attempt_id=attempt.id if attempt else None,
                status=attempt.status if attempt else None,
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

    async def _set_terminal_job_status(self, *, job_id: str, status: str, reason: str) -> None:
        async with self._sessionmaker() as session, session.begin():
            await session.execute(
                text(
                    """
                    update public.jobs
                    set status = :status,
                        locked_by = null,
                        locked_at = null,
                        lease_expires_at = null,
                        last_error_category = :reason,
                        last_error_redacted = :reason
                    where id = :job_id
                    """
                ),
                {"job_id": job_id, "status": status, "reason": reason},
            )

    async def _find_call_attempt_by_sid(
        self, session: AsyncSession, call_sid: str
    ) -> CallAttemptRecord | None:
        result = await session.execute(
            text(
                """
                select
                  id::text,
                  lead_id::text,
                  attempt_number,
                  status,
                  twilio_call_sid
                from public.call_attempts
                where twilio_call_sid = :call_sid
                limit 1
                """
            ),
            {"call_sid": call_sid},
        )
        row = result.mappings().first()
        return (
            self._call_attempt_from_row(cast(Mapping[str, Any], row)) if row is not None else None
        )

    @staticmethod
    def _call_attempt_from_row(row: Mapping[str, Any]) -> CallAttemptRecord:
        return CallAttemptRecord(
            id=str(row["id"]),
            lead_id=str(row["lead_id"]),
            attempt_number=int(row["attempt_number"]),
            status=str(row["status"]),
            twilio_call_sid=row["twilio_call_sid"],
        )


def map_twilio_call_status(status: str) -> str | None:
    return {
        "queued": "dialing",
        "initiated": "dialing",
        "ringing": "ringing",
        "in-progress": "in_progress",
        "completed": "completed",
        "busy": "busy",
        "failed": "failed",
        "no-answer": "no_answer",
        "canceled": "cancelled",
    }.get(status)


def should_update_call_attempt_status(current: str, new: str) -> bool:
    if current in STRONGER_CALL_ATTEMPT_OUTCOMES:
        return False
    terminal_statuses = {
        "completed",
        "no_answer",
        "busy",
        "voicemail",
        "failed",
        "timed_out",
        "cancelled",
        "escalated",
    }
    if current in terminal_statuses and new == "completed":
        return False
    return current != new
