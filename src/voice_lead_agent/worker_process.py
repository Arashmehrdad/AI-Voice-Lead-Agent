from __future__ import annotations

import asyncio

from voice_lead_agent.config import Settings, get_settings
from voice_lead_agent.db import create_engine, create_sessionmaker
from voice_lead_agent.errors import ApiError
from voice_lead_agent.sql_repository import SqlLeadRepository
from voice_lead_agent.twilio_adapter import TwilioSdkVoiceClient
from voice_lead_agent.worker import DeterministicWorker, WorkerConfig, WorkerJobOutcome


def build_worker(settings: Settings) -> DeterministicWorker:
    if settings.twilio_account_sid is None:
        raise ApiError(
            code="missing_twilio_account_sid",
            message="TWILIO_ACCOUNT_SID is required for the worker.",
            status_code=500,
        )
    if settings.twilio_auth_token is None:
        raise ApiError(
            code="missing_twilio_auth_token",
            message="TWILIO_AUTH_TOKEN is required for the worker.",
            status_code=500,
        )
    if settings.twilio_caller_id is None:
        raise ApiError(
            code="missing_twilio_caller_id",
            message="TWILIO_CALLER_ID is required for the worker.",
            status_code=500,
        )

    engine = create_engine(settings.database_url)
    repository = SqlLeadRepository(create_sessionmaker(engine))
    twilio_client = TwilioSdkVoiceClient(
        account_sid=settings.twilio_account_sid,
        auth_token=settings.twilio_auth_token,
    )
    return DeterministicWorker(
        repository=repository,
        twilio_client=twilio_client,
        config=WorkerConfig(
            worker_id=settings.worker_id,
            lease_seconds=settings.job_lease_seconds,
            retry_delay_seconds=settings.job_retry_base_seconds,
            public_base_url=settings.app_public_base_url.rstrip("/"),
            caller_id=settings.twilio_caller_id,
        ),
    )


async def run_once(settings: Settings | None = None, *, limit: int = 10) -> list[WorkerJobOutcome]:
    worker = build_worker(settings or get_settings())
    await worker.recover_expired_leases()
    return await worker.run_once(limit=limit)


def main() -> None:
    asyncio.run(run_once())


if __name__ == "__main__":
    main()
