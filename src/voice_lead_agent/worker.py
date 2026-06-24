from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from voice_lead_agent.domain import (
    CALLABLE_CONSENT_STATUSES,
    TERMINAL_LEAD_STATUSES,
    JobRecord,
    LeadCallContext,
    PermanentProviderError,
    RetryableProviderError,
)
from voice_lead_agent.repositories import WorkerRepository
from voice_lead_agent.twilio_adapter import TwilioVoiceClient


@dataclass(frozen=True)
class WorkerConfig:
    worker_id: str
    lease_seconds: int
    retry_delay_seconds: int
    public_base_url: str
    caller_id: str


@dataclass(frozen=True)
class WorkerJobOutcome:
    job_id: str
    status: str
    reason: str | None = None


class DeterministicWorker:
    def __init__(
        self,
        *,
        repository: WorkerRepository,
        twilio_client: TwilioVoiceClient,
        config: WorkerConfig,
    ) -> None:
        self._repository = repository
        self._twilio_client = twilio_client
        self._config = config

    async def claim_once(self, *, limit: int = 10) -> list[JobRecord]:
        return await self._repository.claim_due_initiate_call_jobs(
            worker_id=self._config.worker_id,
            lease_seconds=self._config.lease_seconds,
            limit=limit,
        )

    async def recover_expired_leases(self) -> int:
        return await self._repository.release_expired_leases()

    async def run_once(self, *, limit: int = 10) -> list[WorkerJobOutcome]:
        jobs = await self.claim_once(limit=limit)
        outcomes: list[WorkerJobOutcome] = []
        for job in jobs:
            outcomes.append(await self.process_job(job))
        return outcomes

    async def process_job(self, job: JobRecord) -> WorkerJobOutcome:
        if not await self._repository.mark_job_running(
            job_id=job.id,
            worker_id=self._config.worker_id,
        ):
            return WorkerJobOutcome(
                job_id=job.id,
                status="skipped",
                reason="job_not_owned",
            )
        context = await self._repository.get_lead_call_context(lead_id=job.entity_id)
        if context is None:
            await self._repository.fail_job(job_id=job.id, reason="lead_not_found")
            return WorkerJobOutcome(job_id=job.id, status="failed", reason="lead_not_found")

        safety_reason = safety_block_reason(context, attempt_number=job.attempt_number)
        if safety_reason is not None:
            if safety_reason in ("calling_paused", "outside_calling_hours"):
                await self._repository.defer_job(
                    job_id=job.id,
                    reason=safety_reason,
                    retry_delay_seconds=self._config.retry_delay_seconds,
                )
                return WorkerJobOutcome(
                    job_id=job.id,
                    status="retry_scheduled",
                    reason=safety_reason,
                )
            await self._repository.cancel_job(job_id=job.id, reason=safety_reason)
            return WorkerJobOutcome(job_id=job.id, status="cancelled", reason=safety_reason)

        attempt = await self._repository.create_or_get_call_attempt(
            lead_id=context.lead_id,
            attempt_number=job.attempt_number,
        )
        if attempt.twilio_call_sid is not None:
            await self._repository.succeed_job(job_id=job.id)
            return WorkerJobOutcome(job_id=job.id, status="succeeded", reason="attempt_reused")

        if attempt.status == "dialing":
            await self._repository.mark_call_provider_state_uncertain(
                initiate_job_id=job.id,
                call_attempt_id=attempt.id,
                reason="provider_state_uncertain",
            )
            return WorkerJobOutcome(
                job_id=job.id,
                status="cancelled",
                reason="provider_state_uncertain",
            )

        if not await self._repository.mark_call_attempt_dialing(call_attempt_id=attempt.id):
            await self._repository.mark_call_provider_state_uncertain(
                initiate_job_id=job.id,
                call_attempt_id=attempt.id,
                reason="provider_state_uncertain",
            )
            return WorkerJobOutcome(
                job_id=job.id,
                status="cancelled",
                reason="provider_state_uncertain",
            )

        try:
            call_sid = await self._twilio_client.initiate_outbound_call(
                to_phone=context.phone_e164,
                from_phone=self._config.caller_id,
                voice_url=f"{self._config.public_base_url}/webhooks/twilio/voice/start",
                status_callback_url=f"{self._config.public_base_url}/webhooks/twilio/call-status",
            )
        except (RetryableProviderError, PermanentProviderError):
            await self._repository.mark_call_provider_state_uncertain(
                initiate_job_id=job.id,
                call_attempt_id=attempt.id,
                reason="provider_state_uncertain",
            )
            return WorkerJobOutcome(
                job_id=job.id,
                status="cancelled",
                reason="provider_state_uncertain",
            )

        if not await self._repository.store_twilio_call_sid(
            call_attempt_id=attempt.id,
            call_sid=call_sid,
        ):
            await self._repository.mark_call_provider_state_uncertain(
                initiate_job_id=job.id,
                call_attempt_id=attempt.id,
                reason="provider_state_uncertain",
            )
            return WorkerJobOutcome(
                job_id=job.id,
                status="cancelled",
                reason="provider_state_uncertain",
            )
        await self._repository.succeed_job(job_id=job.id)
        return WorkerJobOutcome(job_id=job.id, status="succeeded")

    async def _handle_retryable_failure(self, job: JobRecord, reason: str) -> WorkerJobOutcome:
        if job.attempt_count + 1 >= job.max_attempts:
            await self._repository.dead_letter_job(job_id=job.id, reason=reason)
            return WorkerJobOutcome(job_id=job.id, status="dead_lettered", reason=reason)
        await self._repository.schedule_job_retry(
            job_id=job.id,
            reason=reason,
            retry_delay_seconds=self._config.retry_delay_seconds,
        )
        return WorkerJobOutcome(job_id=job.id, status="retry_scheduled", reason=reason)


def safety_block_reason(context: LeadCallContext, *, attempt_number: int) -> str | None:
    if context.business.calling_paused:
        return "calling_paused"
    if context.consent_status not in CALLABLE_CONSENT_STATUSES:
        return "consent_not_callable"
    if context.suppressed:
        return "suppressed_contact"
    if context.status in TERMINAL_LEAD_STATUSES:
        return "terminal_lead"
    if attempt_number > context.business.max_call_attempts:
        return "attempt_limit_exceeded"
    if context.attempt_count >= context.business.max_call_attempts:
        return "attempt_limit_exceeded"
    hours_reason = within_calling_hours(context.business.calling_hours)
    if hours_reason is not None:
        return hours_reason
    return None


def within_calling_hours(
    calling_hours: dict[str, Any], *, now: datetime | None = None
) -> str | None:
    if not calling_hours:
        return "invalid_calling_hours"

    timezone_name = calling_hours.get("timezone")
    if not timezone_name or not isinstance(timezone_name, str) or not timezone_name.strip():
        return "invalid_calling_hours"
    try:
        timezone = ZoneInfo(str(timezone_name))
    except (ZoneInfoNotFoundError, TypeError):
        return "invalid_calling_hours"

    windows = calling_hours.get("windows")
    if not isinstance(windows, list) or not windows:
        return "invalid_calling_hours"

    for window in windows:
        if not isinstance(window, dict):
            return "invalid_calling_hours"
        days = window.get("days")
        if not isinstance(days, list) or not days:
            return "invalid_calling_hours"
        for day in days:
            if not isinstance(day, int) or day < 0 or day > 6:
                return "invalid_calling_hours"
        start = _parse_hhmm(window.get("start"))
        end = _parse_hhmm(window.get("end"))
        if start is None or end is None:
            return "invalid_calling_hours"
        if start == end:
            return "invalid_calling_hours"

    local_now = (now or datetime.now(tz=timezone)).astimezone(timezone)
    weekday = local_now.weekday()
    current_time = local_now.time()
    for window in windows:
        if weekday not in window["days"]:
            continue
        start = _parse_hhmm(window["start"])
        end = _parse_hhmm(window["end"])
        assert start is not None and end is not None
        if start <= current_time <= end:
            return None
    return "outside_calling_hours"


def _parse_hhmm(value: object) -> time | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, "%H:%M").time()
    except ValueError:
        return None
