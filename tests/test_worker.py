from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from voice_lead_agent.domain import (
    BusinessCallingSettings,
    CallAttemptRecord,
    JobRecord,
    LeadCallContext,
    PermanentProviderError,
    RetryableProviderError,
)
from voice_lead_agent.worker import (
    DeterministicWorker,
    WorkerConfig,
    within_calling_hours,
)

DEFAULT_CALLING_HOURS: dict[str, Any] = {
    "timezone": "UTC",
    "windows": [{"days": [0, 1, 2, 3, 4, 5, 6], "start": "00:00", "end": "23:59"}],
}


@dataclass
class WorkerJobState:
    record: JobRecord
    status: str = "pending"
    locked_by: str | None = None
    lease_expired: bool = False
    last_reason: str | None = None


class InMemoryWorkerRepository:
    def __init__(self) -> None:
        self.jobs: dict[str, WorkerJobState] = {}
        self.contexts: dict[str, LeadCallContext] = {}
        self.call_attempts: dict[tuple[str, int], CallAttemptRecord] = {}
        self.stored_sids: dict[str, str] = {}
        self.dialing_marked: set[str] = set()
        self.reconciliation_jobs: dict[str, str] = {}
        self.fail_dialing_for: set[str] = set()
        self.fail_store_sid_for: set[str] = set()

    def add_job(
        self,
        *,
        lead_id: str,
        attempt_number: int = 1,
        attempt_count: int = 0,
        max_attempts: int = 3,
        claimed_by: str | None = None,
    ) -> JobRecord:
        job = JobRecord(
            id=str(uuid4()),
            entity_id=lead_id,
            attempt_number=attempt_number,
            attempt_count=attempt_count,
            max_attempts=max_attempts,
        )
        self.jobs[job.id] = WorkerJobState(record=job)
        if claimed_by is not None:
            state = self.jobs[job.id]
            state.status = "claimed"
            state.locked_by = claimed_by
        return job

    async def claim_due_initiate_call_jobs(
        self, *, worker_id: str, lease_seconds: int, limit: int
    ) -> list[JobRecord]:
        del lease_seconds
        claimed: list[JobRecord] = []
        for state in self.jobs.values():
            if len(claimed) >= limit:
                break
            if state.status in {"pending", "retry_scheduled"}:
                state.status = "claimed"
                state.locked_by = worker_id
                claimed.append(state.record)
        return claimed

    async def release_expired_leases(self) -> int:
        count = 0
        for state in self.jobs.values():
            if state.status in {"claimed", "running"} and state.lease_expired:
                state.status = "pending"
                state.locked_by = None
                state.lease_expired = False
                count += 1
        return count

    async def mark_job_running(self, *, job_id: str, worker_id: str) -> bool:
        state = self.jobs[job_id]
        if state.status != "claimed":
            return False
        if state.locked_by != worker_id:
            return False
        if state.lease_expired:
            return False
        state.status = "running"
        return True

    async def get_lead_call_context(self, *, lead_id: str) -> LeadCallContext | None:
        return self.contexts.get(lead_id)

    async def cancel_job(self, *, job_id: str, reason: str) -> None:
        self.jobs[job_id].status = "cancelled"
        self.jobs[job_id].last_reason = reason

    async def schedule_job_retry(
        self, *, job_id: str, reason: str, retry_delay_seconds: int
    ) -> None:
        del retry_delay_seconds
        state = self.jobs[job_id]
        state.status = "retry_scheduled"
        state.last_reason = reason
        record = state.record
        state.record = JobRecord(
            id=record.id,
            entity_id=record.entity_id,
            attempt_number=record.attempt_number,
            attempt_count=record.attempt_count + 1,
            max_attempts=record.max_attempts,
        )

    async def defer_job(self, *, job_id: str, reason: str, retry_delay_seconds: int) -> None:
        del retry_delay_seconds
        state = self.jobs[job_id]
        state.status = "retry_scheduled"
        state.last_reason = reason

    async def fail_job(self, *, job_id: str, reason: str) -> None:
        self.jobs[job_id].status = "failed"
        self.jobs[job_id].last_reason = reason

    async def dead_letter_job(self, *, job_id: str, reason: str) -> None:
        self.jobs[job_id].status = "dead_lettered"
        self.jobs[job_id].last_reason = reason

    async def succeed_job(self, *, job_id: str) -> None:
        self.jobs[job_id].status = "succeeded"

    async def create_or_get_call_attempt(
        self, *, lead_id: str, attempt_number: int
    ) -> CallAttemptRecord:
        key = (lead_id, attempt_number)
        attempt = self.call_attempts.get(key)
        if attempt is None:
            attempt = CallAttemptRecord(
                id=str(uuid4()),
                lead_id=lead_id,
                attempt_number=attempt_number,
                status="scheduled",
                twilio_call_sid=None,
            )
            self.call_attempts[key] = attempt
        return attempt

    async def store_twilio_call_sid(self, *, call_attempt_id: str, call_sid: str) -> bool:
        if call_attempt_id in self.fail_store_sid_for:
            return False
        attempt = self._find_attempt_by_id(call_attempt_id)
        if attempt is None:
            return False
        if attempt.status != "dialing":
            return False
        if attempt.twilio_call_sid is not None:
            return False
        self.stored_sids[call_attempt_id] = call_sid
        for key, a in self.call_attempts.items():
            if a.id == call_attempt_id:
                self.call_attempts[key] = CallAttemptRecord(
                    id=a.id,
                    lead_id=a.lead_id,
                    attempt_number=a.attempt_number,
                    status="dialing",
                    twilio_call_sid=call_sid,
                )
        return True

    async def mark_call_attempt_dialing(self, *, call_attempt_id: str) -> bool:
        if call_attempt_id in self.fail_dialing_for:
            return False
        attempt = self._find_attempt_by_id(call_attempt_id)
        if attempt is None:
            return False
        if attempt.status != "scheduled":
            return False
        if attempt.twilio_call_sid is not None:
            return False
        self.dialing_marked.add(call_attempt_id)
        for key, a in self.call_attempts.items():
            if a.id == call_attempt_id:
                self.call_attempts[key] = CallAttemptRecord(
                    id=a.id,
                    lead_id=a.lead_id,
                    attempt_number=a.attempt_number,
                    status="dialing",
                    twilio_call_sid=a.twilio_call_sid,
                )
        return True

    def _find_attempt_by_id(self, call_attempt_id: str) -> CallAttemptRecord | None:
        for attempt in self.call_attempts.values():
            if attempt.id == call_attempt_id:
                return attempt
        return None

    async def mark_call_provider_state_uncertain(
        self, *, initiate_job_id: str, call_attempt_id: str, reason: str
    ) -> str:
        self.jobs[initiate_job_id].status = "cancelled"
        self.jobs[initiate_job_id].last_reason = reason
        if call_attempt_id not in self.reconciliation_jobs:
            self.reconciliation_jobs[call_attempt_id] = f"recon-{call_attempt_id}"
        return self.reconciliation_jobs[call_attempt_id]


@dataclass
class MockTwilioClient:
    call_sid: str = "CA123"
    error: Exception | None = None
    calls: list[dict[str, str]] = field(default_factory=list)

    async def initiate_outbound_call(
        self,
        *,
        to_phone: str,
        from_phone: str,
        voice_url: str,
        status_callback_url: str,
    ) -> str:
        self.calls.append(
            {
                "to_phone": to_phone,
                "from_phone": from_phone,
                "voice_url": voice_url,
                "status_callback_url": status_callback_url,
            }
        )
        if self.error is not None:
            raise self.error
        return self.call_sid


def make_context(
    lead_id: str,
    *,
    paused: bool = False,
    consent_status: str = "specific_automated_call_consent",
    suppressed: bool = False,
    status: str = "scheduled",
    attempt_count: int = 0,
    max_call_attempts: int = 3,
    calling_hours: dict[str, Any] | None = None,
) -> LeadCallContext:
    return LeadCallContext(
        lead_id=lead_id,
        phone_e164="+447700900123",
        email="person@example.com",
        status=status,
        consent_status=consent_status,
        attempt_count=attempt_count,
        business=BusinessCallingSettings(
            calling_paused=paused,
            calling_hours=calling_hours if calling_hours is not None else DEFAULT_CALLING_HOURS,
            max_call_attempts=max_call_attempts,
        ),
        suppressed=suppressed,
    )


def make_worker(
    repository: InMemoryWorkerRepository,
    twilio_client: MockTwilioClient,
    *,
    worker_id: str = "worker-1",
) -> DeterministicWorker:
    return DeterministicWorker(
        repository=repository,
        twilio_client=twilio_client,
        config=WorkerConfig(
            worker_id=worker_id,
            lease_seconds=300,
            retry_delay_seconds=60,
            public_base_url="https://voice.example.com",
            caller_id="+441234567890",
        ),
    )


async def test_one_worker_claims_one_due_job() -> None:
    repository = InMemoryWorkerRepository()
    lead_id = str(uuid4())
    job = repository.add_job(lead_id=lead_id)
    worker = make_worker(repository, MockTwilioClient())

    claimed = await worker.claim_once(limit=1)

    assert claimed == [job]
    assert repository.jobs[job.id].status == "claimed"


async def test_two_workers_cannot_claim_same_job() -> None:
    repository = InMemoryWorkerRepository()
    lead_id = str(uuid4())
    repository.add_job(lead_id=lead_id)
    worker_one = make_worker(repository, MockTwilioClient(), worker_id="worker-1")
    worker_two = make_worker(repository, MockTwilioClient(), worker_id="worker-2")

    first = await worker_one.claim_once(limit=1)
    second = await worker_two.claim_once(limit=1)

    assert len(first) == 1
    assert second == []


async def test_expired_lease_recovery() -> None:
    repository = InMemoryWorkerRepository()
    lead_id = str(uuid4())
    job = repository.add_job(lead_id=lead_id)
    repository.jobs[job.id].status = "claimed"
    repository.jobs[job.id].locked_by = "dead-worker"
    repository.jobs[job.id].lease_expired = True
    worker = make_worker(repository, MockTwilioClient())

    recovered = await worker.recover_expired_leases()

    assert recovered == 1
    assert repository.jobs[job.id].status == "pending"


async def test_paused_calling_safely_defers_initiation() -> None:
    repository = InMemoryWorkerRepository()
    twilio = MockTwilioClient()
    lead_id = str(uuid4())
    job = repository.add_job(lead_id=lead_id, claimed_by="worker-1")
    repository.contexts[lead_id] = make_context(lead_id, paused=True)
    worker = make_worker(repository, twilio)

    outcome = await worker.process_job(job)

    assert outcome.status == "retry_scheduled"
    assert outcome.reason == "calling_paused"
    assert twilio.calls == []


async def test_non_callable_consent_prevents_calling() -> None:
    repository = InMemoryWorkerRepository()
    twilio = MockTwilioClient()
    lead_id = str(uuid4())
    job = repository.add_job(lead_id=lead_id, claimed_by="worker-1")
    repository.contexts[lead_id] = make_context(lead_id, consent_status="unknown")
    worker = make_worker(repository, twilio)

    outcome = await worker.process_job(job)

    assert outcome.status == "cancelled"
    assert outcome.reason == "consent_not_callable"
    assert twilio.calls == []


async def test_suppression_prevents_calling() -> None:
    repository = InMemoryWorkerRepository()
    twilio = MockTwilioClient()
    lead_id = str(uuid4())
    job = repository.add_job(lead_id=lead_id, claimed_by="worker-1")
    repository.contexts[lead_id] = make_context(lead_id, suppressed=True)
    worker = make_worker(repository, twilio)

    outcome = await worker.process_job(job)

    assert outcome.status == "cancelled"
    assert outcome.reason == "suppressed_contact"
    assert twilio.calls == []


async def test_terminal_lead_prevents_calling() -> None:
    repository = InMemoryWorkerRepository()
    twilio = MockTwilioClient()
    lead_id = str(uuid4())
    job = repository.add_job(lead_id=lead_id, claimed_by="worker-1")
    repository.contexts[lead_id] = make_context(lead_id, status="booked")
    worker = make_worker(repository, twilio)

    outcome = await worker.process_job(job)

    assert outcome.status == "cancelled"
    assert outcome.reason == "terminal_lead"
    assert twilio.calls == []


async def test_attempt_limit_enforced() -> None:
    repository = InMemoryWorkerRepository()
    twilio = MockTwilioClient()
    lead_id = str(uuid4())
    job = repository.add_job(lead_id=lead_id, attempt_number=4, claimed_by="worker-1")
    repository.contexts[lead_id] = make_context(lead_id, attempt_count=3, max_call_attempts=3)
    worker = make_worker(repository, twilio)

    outcome = await worker.process_job(job)

    assert outcome.status == "cancelled"
    assert outcome.reason == "attempt_limit_exceeded"
    assert twilio.calls == []


async def test_deterministic_call_attempt_reuse() -> None:
    repository = InMemoryWorkerRepository()
    twilio = MockTwilioClient()
    lead_id = str(uuid4())
    job = repository.add_job(lead_id=lead_id, claimed_by="worker-1")
    repository.contexts[lead_id] = make_context(lead_id)
    repository.call_attempts[(lead_id, 1)] = CallAttemptRecord(
        id=str(uuid4()),
        lead_id=lead_id,
        attempt_number=1,
        status="dialing",
        twilio_call_sid="CAEXISTING",
    )
    worker = make_worker(repository, twilio)

    outcome = await worker.process_job(job)

    assert outcome.status == "succeeded"
    assert outcome.reason == "attempt_reused"
    assert twilio.calls == []


async def test_twilio_success_stores_call_sid_and_succeeds_job() -> None:
    repository = InMemoryWorkerRepository()
    twilio = MockTwilioClient(call_sid="CAOK")
    lead_id = str(uuid4())
    job = repository.add_job(lead_id=lead_id, claimed_by="worker-1")
    repository.contexts[lead_id] = make_context(lead_id)
    worker = make_worker(repository, twilio)

    outcome = await worker.process_job(job)

    assert outcome.status == "succeeded"
    assert list(repository.stored_sids.values()) == ["CAOK"]
    assert repository.jobs[job.id].status == "succeeded"
    assert twilio.calls[0]["voice_url"] == "https://voice.example.com/webhooks/twilio/voice/start"
    assert (
        twilio.calls[0]["status_callback_url"]
        == "https://voice.example.com/webhooks/twilio/call-status"
    )


async def test_retryable_twilio_failure_after_dialing_is_uncertain() -> None:
    repository = InMemoryWorkerRepository()
    twilio = MockTwilioClient(error=RetryableProviderError("twilio_timeout"))
    lead_id = str(uuid4())
    job = repository.add_job(
        lead_id=lead_id, attempt_count=0, max_attempts=3, claimed_by="worker-1"
    )
    repository.contexts[lead_id] = make_context(lead_id)
    worker = make_worker(repository, twilio)

    outcome = await worker.process_job(job)

    assert outcome.status == "cancelled"
    assert outcome.reason == "provider_state_uncertain"


async def test_permanent_twilio_failure_after_dialing_is_uncertain() -> None:
    repository = InMemoryWorkerRepository()
    twilio = MockTwilioClient(error=PermanentProviderError("invalid_to_number"))
    lead_id = str(uuid4())
    job = repository.add_job(lead_id=lead_id, claimed_by="worker-1")
    repository.contexts[lead_id] = make_context(lead_id)
    worker = make_worker(repository, twilio)

    outcome = await worker.process_job(job)

    assert outcome.status == "cancelled"
    assert outcome.reason == "provider_state_uncertain"


async def test_exhausted_retry_budget_after_dialing_is_uncertain() -> None:
    repository = InMemoryWorkerRepository()
    twilio = MockTwilioClient(error=RetryableProviderError("twilio_timeout"))
    lead_id = str(uuid4())
    job = repository.add_job(
        lead_id=lead_id, attempt_count=2, max_attempts=3, claimed_by="worker-1"
    )
    repository.contexts[lead_id] = make_context(lead_id)
    worker = make_worker(repository, twilio)

    outcome = await worker.process_job(job)

    assert outcome.status == "cancelled"
    assert outcome.reason == "provider_state_uncertain"


# --- calling-hours safety tests ---


async def test_empty_calling_hours_is_invalid() -> None:
    result = within_calling_hours({}, now=datetime(2026, 1, 5, 12, 0, tzinfo=ZoneInfo("UTC")))
    assert result == "invalid_calling_hours"


async def test_missing_timezone_is_invalid() -> None:
    config = {"windows": [{"days": [0, 1, 2, 3, 4, 5, 6], "start": "09:00", "end": "17:00"}]}
    result = within_calling_hours(config, now=datetime(2026, 1, 5, 12, 0, tzinfo=ZoneInfo("UTC")))
    assert result == "invalid_calling_hours"


async def test_blank_timezone_is_invalid() -> None:
    config = {"timezone": "", "windows": [{"days": [0], "start": "09:00", "end": "17:00"}]}
    result = within_calling_hours(config, now=datetime(2026, 1, 5, 12, 0, tzinfo=ZoneInfo("UTC")))
    assert result == "invalid_calling_hours"


async def test_invalid_timezone_name_is_invalid() -> None:
    config = {
        "timezone": "Not/A_Real/Timezone",
        "windows": [{"days": [0, 1, 2, 3, 4, 5, 6], "start": "09:00", "end": "17:00"}],
    }
    result = within_calling_hours(config, now=datetime(2026, 1, 5, 12, 0, tzinfo=ZoneInfo("UTC")))
    assert result == "invalid_calling_hours"


async def test_missing_windows_is_invalid() -> None:
    config = {"timezone": "UTC"}
    result = within_calling_hours(config, now=datetime(2026, 1, 5, 12, 0, tzinfo=ZoneInfo("UTC")))
    assert result == "invalid_calling_hours"


async def test_empty_windows_list_is_invalid() -> None:
    config = {"timezone": "UTC", "windows": []}
    result = within_calling_hours(config, now=datetime(2026, 1, 5, 12, 0, tzinfo=ZoneInfo("UTC")))
    assert result == "invalid_calling_hours"


async def test_non_list_windows_is_invalid() -> None:
    config = {"timezone": "UTC", "windows": "not_a_list"}
    result = within_calling_hours(config, now=datetime(2026, 1, 5, 12, 0, tzinfo=ZoneInfo("UTC")))
    assert result == "invalid_calling_hours"


async def test_non_dict_window_is_invalid() -> None:
    config = {"timezone": "UTC", "windows": ["not_a_dict"]}
    result = within_calling_hours(config, now=datetime(2026, 1, 5, 12, 0, tzinfo=ZoneInfo("UTC")))
    assert result == "invalid_calling_hours"


async def test_missing_days_in_window_is_invalid() -> None:
    config = {"timezone": "UTC", "windows": [{"start": "09:00", "end": "17:00"}]}
    result = within_calling_hours(config, now=datetime(2026, 1, 5, 12, 0, tzinfo=ZoneInfo("UTC")))
    assert result == "invalid_calling_hours"


async def test_empty_days_list_is_invalid() -> None:
    config = {
        "timezone": "UTC",
        "windows": [{"days": [], "start": "09:00", "end": "17:00"}],
    }
    result = within_calling_hours(config, now=datetime(2026, 1, 5, 12, 0, tzinfo=ZoneInfo("UTC")))
    assert result == "invalid_calling_hours"


async def test_non_int_day_in_days_is_invalid() -> None:
    config = {
        "timezone": "UTC",
        "windows": [{"days": ["monday"], "start": "09:00", "end": "17:00"}],
    }
    result = within_calling_hours(config, now=datetime(2026, 1, 5, 12, 0, tzinfo=ZoneInfo("UTC")))
    assert result == "invalid_calling_hours"


async def test_out_of_range_day_is_invalid() -> None:
    config = {
        "timezone": "UTC",
        "windows": [{"days": [7], "start": "09:00", "end": "17:00"}],
    }
    result = within_calling_hours(config, now=datetime(2026, 1, 5, 12, 0, tzinfo=ZoneInfo("UTC")))
    assert result == "invalid_calling_hours"


async def test_negative_day_is_invalid() -> None:
    config = {
        "timezone": "UTC",
        "windows": [{"days": [-1], "start": "09:00", "end": "17:00"}],
    }
    result = within_calling_hours(config, now=datetime(2026, 1, 5, 12, 0, tzinfo=ZoneInfo("UTC")))
    assert result == "invalid_calling_hours"


async def test_invalid_start_format_is_invalid() -> None:
    config = {
        "timezone": "UTC",
        "windows": [{"days": [0], "start": "nine", "end": "17:00"}],
    }
    result = within_calling_hours(config, now=datetime(2026, 1, 5, 12, 0, tzinfo=ZoneInfo("UTC")))
    assert result == "invalid_calling_hours"


async def test_invalid_end_format_is_invalid() -> None:
    config = {
        "timezone": "UTC",
        "windows": [{"days": [0], "start": "09:00", "end": "five"}],
    }
    result = within_calling_hours(config, now=datetime(2026, 1, 5, 12, 0, tzinfo=ZoneInfo("UTC")))
    assert result == "invalid_calling_hours"


async def test_start_equals_end_is_invalid() -> None:
    config = {
        "timezone": "UTC",
        "windows": [{"days": [0, 1, 2, 3, 4, 5, 6], "start": "12:00", "end": "12:00"}],
    }
    result = within_calling_hours(config, now=datetime(2026, 1, 5, 12, 0, tzinfo=ZoneInfo("UTC")))
    assert result == "invalid_calling_hours"


async def test_outside_calling_hours_returns_reason() -> None:
    config = {
        "timezone": "America/New_York",
        "windows": [{"days": [0, 1, 2, 3, 4, 5, 6], "start": "09:00", "end": "17:00"}],
    }
    now = datetime(2026, 1, 5, 8, 0, tzinfo=ZoneInfo("America/New_York"))
    result = within_calling_hours(config, now=now)
    assert result == "outside_calling_hours"


async def test_inside_calling_hours_returns_none() -> None:
    config = {
        "timezone": "America/New_York",
        "windows": [{"days": [0, 1, 2, 3, 4, 5, 6], "start": "09:00", "end": "17:00"}],
    }
    now = datetime(2026, 1, 5, 12, 0, tzinfo=ZoneInfo("America/New_York"))
    result = within_calling_hours(config, now=now)
    assert result is None


async def test_outside_calling_hours_defers_job() -> None:
    repository = InMemoryWorkerRepository()
    twilio = MockTwilioClient()
    lead_id = str(uuid4())
    job = repository.add_job(lead_id=lead_id, claimed_by="worker-1")
    calling_hours = {
        "timezone": "America/New_York",
        "windows": [{"days": [0, 1, 2, 3, 4, 5, 6], "start": "23:00", "end": "23:59"}],
    }
    repository.contexts[lead_id] = make_context(lead_id, calling_hours=calling_hours)
    worker = make_worker(repository, twilio)

    outcome = await worker.process_job(job)

    assert outcome.status == "retry_scheduled"
    assert outcome.reason == "outside_calling_hours"
    assert twilio.calls == []


async def test_invalid_calling_hours_cancels_job() -> None:
    repository = InMemoryWorkerRepository()
    twilio = MockTwilioClient()
    lead_id = str(uuid4())
    job = repository.add_job(lead_id=lead_id, claimed_by="worker-1")
    repository.contexts[lead_id] = make_context(lead_id, calling_hours={})
    worker = make_worker(repository, twilio)

    outcome = await worker.process_job(job)

    assert outcome.status == "cancelled"
    assert outcome.reason == "invalid_calling_hours"
    assert twilio.calls == []


# --- attempt_count safety tests ---


async def test_paused_calling_does_not_increment_attempt_count() -> None:
    repository = InMemoryWorkerRepository()
    twilio = MockTwilioClient()
    lead_id = str(uuid4())
    job = repository.add_job(lead_id=lead_id, attempt_count=0, claimed_by="worker-1")
    repository.contexts[lead_id] = make_context(lead_id, paused=True)
    worker = make_worker(repository, twilio)

    outcome = await worker.process_job(job)

    assert outcome.status == "retry_scheduled"
    assert outcome.reason == "calling_paused"
    assert repository.jobs[job.id].record.attempt_count == 0


async def test_outside_calling_hours_does_not_increment_attempt_count() -> None:
    repository = InMemoryWorkerRepository()
    twilio = MockTwilioClient()
    lead_id = str(uuid4())
    job = repository.add_job(lead_id=lead_id, attempt_count=0, claimed_by="worker-1")
    calling_hours = {
        "timezone": "America/New_York",
        "windows": [{"days": [0, 1, 2, 3, 4, 5, 6], "start": "23:00", "end": "23:59"}],
    }
    repository.contexts[lead_id] = make_context(lead_id, calling_hours=calling_hours)
    worker = make_worker(repository, twilio)

    outcome = await worker.process_job(job)

    assert outcome.status == "retry_scheduled"
    assert outcome.reason == "outside_calling_hours"
    assert repository.jobs[job.id].record.attempt_count == 0


async def test_twilio_failure_after_dialing_does_not_increment_attempt_count() -> None:
    repository = InMemoryWorkerRepository()
    twilio = MockTwilioClient(error=RetryableProviderError("twilio_timeout"))
    lead_id = str(uuid4())
    job = repository.add_job(
        lead_id=lead_id, attempt_count=0, max_attempts=3, claimed_by="worker-1"
    )
    repository.contexts[lead_id] = make_context(lead_id)
    worker = make_worker(repository, twilio)

    outcome = await worker.process_job(job)

    assert outcome.status == "cancelled"
    assert outcome.reason == "provider_state_uncertain"
    assert repository.jobs[job.id].record.attempt_count == 0


# --- duplicate outbound call prevention tests ---


async def test_normal_path_marks_dialing_before_calling_twilio() -> None:
    repository = InMemoryWorkerRepository()
    twilio = MockTwilioClient(call_sid="CAOK")
    lead_id = str(uuid4())
    job = repository.add_job(lead_id=lead_id, claimed_by="worker-1")
    repository.contexts[lead_id] = make_context(lead_id)
    worker = make_worker(repository, twilio)

    outcome = await worker.process_job(job)

    assert outcome.status == "succeeded"
    attempt_key = (lead_id, 1)
    attempt = repository.call_attempts[attempt_key]
    assert attempt.id in repository.dialing_marked
    assert attempt.twilio_call_sid == "CAOK"
    assert len(twilio.calls) == 1


async def test_existing_sid_does_not_call_twilio() -> None:
    repository = InMemoryWorkerRepository()
    twilio = MockTwilioClient()
    lead_id = str(uuid4())
    job = repository.add_job(lead_id=lead_id, claimed_by="worker-1")
    repository.contexts[lead_id] = make_context(lead_id)
    repository.call_attempts[(lead_id, 1)] = CallAttemptRecord(
        id=str(uuid4()),
        lead_id=lead_id,
        attempt_number=1,
        status="dialing",
        twilio_call_sid="CAEXISTING",
    )
    worker = make_worker(repository, twilio)

    outcome = await worker.process_job(job)

    assert outcome.status == "succeeded"
    assert outcome.reason == "attempt_reused"
    assert twilio.calls == []


async def test_existing_dialing_without_sid_does_not_call_twilio() -> None:
    repository = InMemoryWorkerRepository()
    twilio = MockTwilioClient()
    lead_id = str(uuid4())
    job = repository.add_job(lead_id=lead_id, claimed_by="worker-1")
    repository.contexts[lead_id] = make_context(lead_id)
    repository.call_attempts[(lead_id, 1)] = CallAttemptRecord(
        id=str(uuid4()),
        lead_id=lead_id,
        attempt_number=1,
        status="dialing",
        twilio_call_sid=None,
    )
    worker = make_worker(repository, twilio)

    outcome = await worker.process_job(job)

    assert outcome.status == "cancelled"
    assert outcome.reason == "provider_state_uncertain"
    assert twilio.calls == []


async def test_uncertain_state_creates_reconciliation_job() -> None:
    repository = InMemoryWorkerRepository()
    twilio = MockTwilioClient()
    lead_id = str(uuid4())
    job = repository.add_job(lead_id=lead_id, claimed_by="worker-1")
    attempt_id = str(uuid4())
    repository.contexts[lead_id] = make_context(lead_id)
    repository.call_attempts[(lead_id, 1)] = CallAttemptRecord(
        id=attempt_id,
        lead_id=lead_id,
        attempt_number=1,
        status="dialing",
        twilio_call_sid=None,
    )
    worker = make_worker(repository, twilio)

    await worker.process_job(job)

    assert attempt_id in repository.reconciliation_jobs
    assert len(repository.reconciliation_jobs) == 1


async def test_uncertain_state_twice_creates_one_reconciliation_job() -> None:
    repository = InMemoryWorkerRepository()
    twilio = MockTwilioClient()
    lead_id = str(uuid4())
    job = repository.add_job(lead_id=lead_id, claimed_by="worker-1")
    attempt_id = str(uuid4())
    repository.contexts[lead_id] = make_context(lead_id)
    repository.call_attempts[(lead_id, 1)] = CallAttemptRecord(
        id=attempt_id,
        lead_id=lead_id,
        attempt_number=1,
        status="dialing",
        twilio_call_sid=None,
    )
    worker = make_worker(repository, twilio)

    await worker.process_job(job)
    await worker.process_job(job)

    assert len(repository.reconciliation_jobs) == 1


async def test_normal_success_stores_sid_and_succeeds() -> None:
    repository = InMemoryWorkerRepository()
    twilio = MockTwilioClient(call_sid="CAOK")
    lead_id = str(uuid4())
    job = repository.add_job(lead_id=lead_id, claimed_by="worker-1")
    repository.contexts[lead_id] = make_context(lead_id)
    worker = make_worker(repository, twilio)

    outcome = await worker.process_job(job)

    assert outcome.status == "succeeded"
    assert list(repository.stored_sids.values()) == ["CAOK"]
    assert repository.jobs[job.id].status == "succeeded"
    assert twilio.calls[0]["voice_url"] == "https://voice.example.com/webhooks/twilio/voice/start"
    assert (
        twilio.calls[0]["status_callback_url"]
        == "https://voice.example.com/webhooks/twilio/call-status"
    )


async def test_retryable_error_after_dialing_creates_reconciliation() -> None:
    repository = InMemoryWorkerRepository()
    twilio = MockTwilioClient(error=RetryableProviderError("twilio_timeout"))
    lead_id = str(uuid4())
    job = repository.add_job(lead_id=lead_id, claimed_by="worker-1")
    repository.contexts[lead_id] = make_context(lead_id)
    worker = make_worker(repository, twilio)

    await worker.process_job(job)

    attempt_key = (lead_id, 1)
    attempt = repository.call_attempts[attempt_key]
    assert attempt.id in repository.reconciliation_jobs
    assert len(repository.reconciliation_jobs) == 1


async def test_permanent_error_after_dialing_creates_reconciliation() -> None:
    repository = InMemoryWorkerRepository()
    twilio = MockTwilioClient(error=PermanentProviderError("invalid_to_number"))
    lead_id = str(uuid4())
    job = repository.add_job(lead_id=lead_id, claimed_by="worker-1")
    repository.contexts[lead_id] = make_context(lead_id)
    worker = make_worker(repository, twilio)

    await worker.process_job(job)

    attempt_key = (lead_id, 1)
    attempt = repository.call_attempts[attempt_key]
    assert attempt.id in repository.reconciliation_jobs
    assert len(repository.reconciliation_jobs) == 1


async def test_error_after_dialing_does_not_schedule_retry() -> None:
    repository = InMemoryWorkerRepository()
    twilio = MockTwilioClient(error=RetryableProviderError("twilio_timeout"))
    lead_id = str(uuid4())
    job = repository.add_job(
        lead_id=lead_id, attempt_count=0, max_attempts=3, claimed_by="worker-1"
    )
    repository.contexts[lead_id] = make_context(lead_id)
    worker = make_worker(repository, twilio)

    await worker.process_job(job)

    assert repository.jobs[job.id].status == "cancelled"
    assert repository.jobs[job.id].record.attempt_count == 0


async def test_error_after_dialing_does_not_call_twilio_again() -> None:
    repository = InMemoryWorkerRepository()
    twilio = MockTwilioClient(error=RetryableProviderError("twilio_timeout"))
    lead_id = str(uuid4())
    job = repository.add_job(lead_id=lead_id, claimed_by="worker-1")
    repository.contexts[lead_id] = make_context(lead_id)
    worker = make_worker(repository, twilio)

    await worker.process_job(job)

    assert len(twilio.calls) == 1


# --- ownership, lease, dialing transition, SID guard tests ---


async def test_ownership_change_skips_job() -> None:
    repository = InMemoryWorkerRepository()
    twilio = MockTwilioClient()
    lead_id = str(uuid4())
    job = repository.add_job(lead_id=lead_id)
    repository.contexts[lead_id] = make_context(lead_id)
    repository.jobs[job.id].status = "claimed"
    repository.jobs[job.id].locked_by = "worker-1"
    worker = make_worker(repository, twilio, worker_id="worker-2")

    outcome = await worker.process_job(job)

    assert outcome.status == "skipped"
    assert outcome.reason == "job_not_owned"
    assert twilio.calls == []
    assert repository.jobs[job.id].status == "claimed"


async def test_expired_lease_skips_job() -> None:
    repository = InMemoryWorkerRepository()
    twilio = MockTwilioClient()
    lead_id = str(uuid4())
    job = repository.add_job(lead_id=lead_id)
    repository.contexts[lead_id] = make_context(lead_id)
    repository.jobs[job.id].status = "claimed"
    repository.jobs[job.id].locked_by = "worker-1"
    repository.jobs[job.id].lease_expired = True
    worker = make_worker(repository, twilio, worker_id="worker-1")

    outcome = await worker.process_job(job)

    assert outcome.status == "skipped"
    assert outcome.reason == "job_not_owned"
    assert twilio.calls == []


async def test_failed_dialing_transition_marks_uncertain() -> None:
    repository = InMemoryWorkerRepository()
    twilio = MockTwilioClient()
    lead_id = str(uuid4())
    job = repository.add_job(lead_id=lead_id, claimed_by="worker-1")
    repository.contexts[lead_id] = make_context(lead_id)
    attempt_id = str(uuid4())
    repository.call_attempts[(lead_id, 1)] = CallAttemptRecord(
        id=attempt_id,
        lead_id=lead_id,
        attempt_number=1,
        status="scheduled",
        twilio_call_sid=None,
    )
    repository.fail_dialing_for.add(attempt_id)
    worker = make_worker(repository, twilio)

    outcome = await worker.process_job(job)

    assert outcome.status == "cancelled"
    assert outcome.reason == "provider_state_uncertain"
    assert twilio.calls == []
    assert attempt_id in repository.reconciliation_jobs


async def test_sid_overwrite_marks_uncertain() -> None:
    repository = InMemoryWorkerRepository()
    twilio = MockTwilioClient(call_sid="CAOK")
    lead_id = str(uuid4())
    job = repository.add_job(lead_id=lead_id, claimed_by="worker-1")
    repository.contexts[lead_id] = make_context(lead_id)
    attempt_id = str(uuid4())
    repository.call_attempts[(lead_id, 1)] = CallAttemptRecord(
        id=attempt_id,
        lead_id=lead_id,
        attempt_number=1,
        status="scheduled",
        twilio_call_sid=None,
    )
    repository.fail_store_sid_for.add(attempt_id)
    worker = make_worker(repository, twilio)

    outcome = await worker.process_job(job)

    assert outcome.status == "cancelled"
    assert outcome.reason == "provider_state_uncertain"
    assert len(twilio.calls) == 1
    assert attempt_id in repository.reconciliation_jobs


async def test_repeated_uncertain_returns_same_recon_id() -> None:
    repo = InMemoryWorkerRepository()
    attempt_id = str(uuid4())
    job_id = str(uuid4())
    repo.jobs[job_id] = WorkerJobState(
        record=JobRecord(
            id=job_id,
            entity_id="lead-1",
            attempt_number=1,
            attempt_count=0,
            max_attempts=3,
        ),
        status="claimed",
        locked_by="worker-1",
    )
    id1 = await repo.mark_call_provider_state_uncertain(
        initiate_job_id=job_id,
        call_attempt_id=attempt_id,
        reason="provider_state_uncertain",
    )
    id2 = await repo.mark_call_provider_state_uncertain(
        initiate_job_id=job_id,
        call_attempt_id=attempt_id,
        reason="provider_state_uncertain",
    )
    assert id1 == id2
    assert id1 == f"recon-{attempt_id}"
