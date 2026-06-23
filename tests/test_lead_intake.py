from __future__ import annotations

import asyncio

import pytest
from httpx import AsyncClient

from tests.fakes import InMemoryLeadRepository


async def test_valid_callable_lead_creates_initial_job(
    client: AsyncClient,
    auth_headers: dict[str, str],
    valid_payload: dict[str, object],
    repository: InMemoryLeadRepository,
) -> None:
    response = await client.post("/api/leads", json=valid_payload, headers=auth_headers)

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "scheduled"
    assert body["callable"] is True
    assert body["call_job_id"] is not None
    assert body["attempt_number"] == 1
    assert body["duplicate"] is False
    assert len(repository.leads) == 1
    assert len(repository.jobs) == 1


async def test_valid_non_callable_lead_does_not_create_job(
    client: AsyncClient,
    auth_headers: dict[str, str],
    valid_payload: dict[str, object],
    repository: InMemoryLeadRepository,
) -> None:
    valid_payload["consent_status"] = "unknown"
    valid_payload["consent_source"] = None
    valid_payload["consent_captured_at"] = None

    response = await client.post("/api/leads", json=valid_payload, headers=auth_headers)

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "new"
    assert body["callable"] is False
    assert body["call_job_id"] is None
    assert body["blocked_reason"] == "consent_status_not_callable"
    assert len(repository.jobs) == 0


async def test_duplicate_submission_returns_existing_lead_and_job(
    client: AsyncClient,
    auth_headers: dict[str, str],
    valid_payload: dict[str, object],
    repository: InMemoryLeadRepository,
) -> None:
    first = await client.post("/api/leads", json=valid_payload, headers=auth_headers)
    second = await client.post("/api/leads", json=valid_payload, headers=auth_headers)

    assert first.status_code == 201
    assert second.status_code == 200
    first_body = first.json()
    second_body = second.json()
    assert second_body["duplicate"] is True
    assert second_body["lead_id"] == first_body["lead_id"]
    assert second_body["call_job_id"] == first_body["call_job_id"]
    assert len(repository.leads) == 1
    assert len(repository.jobs) == 1


async def test_missing_source_entity_id_is_rejected(
    client: AsyncClient,
    auth_headers: dict[str, str],
    valid_payload: dict[str, object],
    repository: InMemoryLeadRepository,
) -> None:
    valid_payload.pop("source_entity_id")

    response = await client.post("/api/leads", json=valid_payload, headers=auth_headers)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "missing_source_entity_id"
    assert len(repository.leads) == 0
    assert len(repository.jobs) == 0


async def test_invalid_token_is_rejected(
    client: AsyncClient,
    valid_payload: dict[str, object],
    repository: InMemoryLeadRepository,
) -> None:
    response = await client.post(
        "/api/leads",
        json=valid_payload,
        headers={"Authorization": "Bearer wrong-token"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "invalid_token"
    assert len(repository.leads) == 0
    assert len(repository.jobs) == 0


async def test_invalid_phone_is_rejected(
    client: AsyncClient,
    auth_headers: dict[str, str],
    valid_payload: dict[str, object],
    repository: InMemoryLeadRepository,
) -> None:
    valid_payload["phone_e164"] = "07700900123"

    response = await client.post("/api/leads", json=valid_payload, headers=auth_headers)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_phone"
    assert len(repository.leads) == 0
    assert len(repository.jobs) == 0


async def test_suppressed_phone_creates_suppressed_lead_without_job(
    client: AsyncClient,
    auth_headers: dict[str, str],
    valid_payload: dict[str, object],
    repository: InMemoryLeadRepository,
) -> None:
    repository.suppressed_phones.add("+447700900123")

    response = await client.post("/api/leads", json=valid_payload, headers=auth_headers)

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "suppressed"
    assert body["callable"] is False
    assert body["call_job_id"] is None
    assert body["blocked_reason"] == "suppressed_contact"
    assert len(repository.jobs) == 0


async def test_suppressed_email_creates_suppressed_lead_without_job(
    client: AsyncClient,
    auth_headers: dict[str, str],
    valid_payload: dict[str, object],
    repository: InMemoryLeadRepository,
) -> None:
    repository.suppressed_emails.add("person@example.com")

    response = await client.post("/api/leads", json=valid_payload, headers=auth_headers)

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "suppressed"
    assert body["callable"] is False
    assert body["call_job_id"] is None
    assert body["blocked_reason"] == "suppressed_contact"
    assert len(repository.jobs) == 0


async def test_atomic_rollback_when_job_creation_fails(
    client: AsyncClient,
    auth_headers: dict[str, str],
    valid_payload: dict[str, object],
    repository: InMemoryLeadRepository,
) -> None:
    repository.fail_job_creation = True

    with pytest.raises(RuntimeError, match="simulated job creation failure"):
        await client.post("/api/leads", json=valid_payload, headers=auth_headers)

    assert len(repository.leads) == 0
    assert len(repository.jobs) == 0


async def test_concurrent_submissions(
    client: AsyncClient,
    auth_headers: dict[str, str],
    valid_payload: dict[str, object],
    repository: InMemoryLeadRepository,
) -> None:
    repository.simulate_concurrency_delay = True

    # Note: Real PostgreSQL concurrency safety validation remains required.
    results = await asyncio.gather(
        client.post("/api/leads", json=valid_payload, headers=auth_headers),
        client.post("/api/leads", json=valid_payload, headers=auth_headers),
    )

    status_codes = {r.status_code for r in results}
    assert status_codes == {200, 201}
    assert len(repository.leads) == 1
    assert len(repository.jobs) == 1


async def test_duplicate_source_identity_conflict(
    client: AsyncClient,
    auth_headers: dict[str, str],
    valid_payload: dict[str, object],
    repository: InMemoryLeadRepository,
) -> None:
    first = await client.post("/api/leads", json=valid_payload, headers=auth_headers)
    assert first.status_code == 201

    conflict_payload = dict(valid_payload)
    conflict_payload["phone_e164"] = "+447700900999"

    second = await client.post("/api/leads", json=conflict_payload, headers=auth_headers)
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "source_identity_conflict"
    assert len(repository.leads) == 1
    assert len(repository.jobs) == 1


async def test_duplicate_opted_out_lead_returns_not_callable(
    client: AsyncClient,
    auth_headers: dict[str, str],
    valid_payload: dict[str, object],
    repository: InMemoryLeadRepository,
) -> None:
    valid_payload["consent_status"] = "do_not_call"
    valid_payload["consent_source"] = None
    valid_payload["consent_captured_at"] = None

    first = await client.post("/api/leads", json=valid_payload, headers=auth_headers)

    assert first.status_code == 201
    first_body = first.json()
    assert first_body["status"] == "opted_out"
    assert first_body["callable"] is False
    assert first_body["call_job_id"] is None

    second = await client.post("/api/leads", json=valid_payload, headers=auth_headers)

    assert second.status_code == 200
    second_body = second.json()
    assert second_body["duplicate"] is True
    assert second_body["status"] == "opted_out"
    assert second_body["callable"] is False
    assert second_body["blocked_reason"] == "consent_status_not_callable"


async def test_missing_authorization_is_rejected(
    client: AsyncClient,
    valid_payload: dict[str, object],
    repository: InMemoryLeadRepository,
) -> None:
    response = await client.post("/api/leads", json=valid_payload)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "missing_authorization"
    assert len(repository.leads) == 0
    assert len(repository.jobs) == 0


async def test_invalid_consent_source_is_rejected(
    client: AsyncClient,
    auth_headers: dict[str, str],
    valid_payload: dict[str, object],
) -> None:
    valid_payload["consent_status"] = "specific_automated_call_consent"
    valid_payload["consent_source"] = " "

    response = await client.post("/api/leads", json=valid_payload, headers=auth_headers)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_consent_source"


async def test_invalid_consent_captured_at_is_rejected(
    client: AsyncClient,
    auth_headers: dict[str, str],
    valid_payload: dict[str, object],
) -> None:
    valid_payload["consent_status"] = "specific_automated_call_consent"
    valid_payload["consent_captured_at"] = "2026-06-23T12:00:00"  # timezone-naive

    response = await client.post("/api/leads", json=valid_payload, headers=auth_headers)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_consent_captured_at"


async def test_request_id_generated_serverside_only(
    client: AsyncClient,
    auth_headers: dict[str, str],
    valid_payload: dict[str, object],
) -> None:
    headers = dict(auth_headers)
    headers["X-Request-Id"] = "client-requested-id-123"

    response = await client.post("/api/leads", json=valid_payload, headers=headers)
    assert response.status_code == 201

    server_request_id = response.headers.get("X-Request-Id")
    assert server_request_id is not None
    assert server_request_id != "client-requested-id-123"
