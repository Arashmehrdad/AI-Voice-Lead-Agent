from __future__ import annotations

from httpx import AsyncClient

from tests.fakes import InMemoryLeadRepository


async def test_live_health(client: AsyncClient) -> None:
    response = await client.get("/health/live")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "live"
    assert body["service"] == "api"
    assert response.headers["x-request-id"]


async def test_ready_health(
    client: AsyncClient,
    repository: InMemoryLeadRepository,
) -> None:
    repository.ready = True

    response = await client.get("/health/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["database"] == {
        "reachable": True,
        "migration_version": "0001_initial_schema",
    }
    assert body["calling_paused"] is True


async def test_ready_health_failure(
    client: AsyncClient,
    repository: InMemoryLeadRepository,
) -> None:
    repository.ready = False

    response = await client.get("/health/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["database"] == {
        "reachable": False,
        "migration_version": "0001_initial_schema",
    }
