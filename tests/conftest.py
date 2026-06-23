from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient

from tests.fakes import InMemoryLeadRepository
from voice_lead_agent.app import create_app
from voice_lead_agent.config import Settings


@pytest.fixture
def settings() -> Settings:
    return Settings(
        APP_ENV="test",
        APP_PUBLIC_BASE_URL="http://testserver",
        CALLING_ENABLED=False,
        TRUSTED_SOURCE_TOKEN="test-token",
        DATABASE_URL="postgresql+asyncpg://user:pass@localhost/db",
    )


@pytest.fixture
def repository() -> InMemoryLeadRepository:
    return InMemoryLeadRepository()


@pytest.fixture
async def client(
    settings: Settings,
    repository: InMemoryLeadRepository,
) -> AsyncGenerator[AsyncClient]:
    app = create_app(settings=settings, repository=repository)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as async_client:
        yield async_client


@pytest.fixture
def valid_payload() -> dict[str, object]:
    return {
        "source_system": "web_form",
        "source_entity_type": "lead",
        "source_entity_id": "external-123",
        "source_payload": {},
        "source_payload_version": 1,
        "full_name": "Example Person",
        "company_name": "Example Company",
        "phone_e164": "+447700900123",
        "email": "person@example.com",
        "timezone": "Europe/London",
        "consent_status": "specific_automated_call_consent",
        "consent_source": "website_enquiry_form",
        "consent_captured_at": "2026-06-23T12:00:00Z",
        "consent_evidence_uri": "internal://consent/external-123",
    }


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer test-token"}
