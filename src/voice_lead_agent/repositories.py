from __future__ import annotations

from typing import Protocol

from voice_lead_agent.domain import (
    CallAttemptRecord,
    JobRecord,
    LeadCallContext,
    LeadIntakeCommand,
    LeadIntakeResult,
    ProviderEventResult,
)


class LeadRepository(Protocol):
    async def create_or_get_initial_lead(self, command: LeadIntakeCommand) -> LeadIntakeResult:
        """Create or return a lead and its initial initiate_call job atomically."""

    async def readiness(self) -> bool:
        """Return whether the backing database is reachable."""


class WorkerRepository(Protocol):
    async def claim_due_initiate_call_jobs(
        self, *, worker_id: str, lease_seconds: int, limit: int
    ) -> list[JobRecord]:
        """Claim due initiate_call jobs using a durable lease."""

    async def release_expired_leases(self) -> int:
        """Release expired claimed/running jobs back to pending."""

    async def mark_job_running(self, *, job_id: str, worker_id: str) -> bool:
        """Mark a claimed job as running. Returns False if not owned or lease expired."""

    async def get_lead_call_context(self, *, lead_id: str) -> LeadCallContext | None:
        """Load all safety-check state needed before calling."""

    async def cancel_job(self, *, job_id: str, reason: str) -> None:
        """Cancel a job that is no longer safe or necessary."""

    async def schedule_job_retry(
        self, *, job_id: str, reason: str, retry_delay_seconds: int
    ) -> None:
        """Move a job to retry_scheduled with a database-relative run_after."""

    async def defer_job(
        self,
        *,
        job_id: str,
        reason: str,
        retry_delay_seconds: int,
    ) -> None:
        """Defer a job without incrementing the provider failure attempt counter."""

    async def fail_job(self, *, job_id: str, reason: str) -> None:
        """Mark a job as permanently failed."""

    async def dead_letter_job(self, *, job_id: str, reason: str) -> None:
        """Mark a job as dead-lettered."""

    async def succeed_job(self, *, job_id: str) -> None:
        """Mark a job as succeeded."""

    async def create_or_get_call_attempt(
        self, *, lead_id: str, attempt_number: int
    ) -> CallAttemptRecord:
        """Create or reuse the deterministic call attempt."""

    async def mark_call_attempt_dialing(
        self,
        *,
        call_attempt_id: str,
    ) -> bool:
        """Persist provider-request-started state. Returns False if already dialing or has SID."""

    async def store_twilio_call_sid(self, *, call_attempt_id: str, call_sid: str) -> bool:
        """Store the Twilio Call SID. Returns False if not dialing or SID already set."""

    async def mark_call_provider_state_uncertain(
        self,
        *,
        initiate_job_id: str,
        call_attempt_id: str,
        reason: str,
    ) -> str:
        """Atomically cancel the initiation job and create or reuse reconciliation."""


class TwilioWebhookRepository(Protocol):
    async def record_twilio_call_status(
        self, *, params: dict[str, str], payload_hash: str
    ) -> ProviderEventResult:
        """Deduplicate and apply a Twilio call-status callback."""
