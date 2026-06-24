from __future__ import annotations

from dataclasses import dataclass

from httpx import ASGITransport, AsyncClient

from tests.fakes import InMemoryLeadRepository
from voice_lead_agent.app import create_app
from voice_lead_agent.config import Settings
from voice_lead_agent.domain import ProviderEventResult


@dataclass
class FakeSignatureVerifier:
    valid: bool
    calls: int = 0

    def validate(self, *, url: str, params: dict[str, str], signature: str | None) -> bool:
        self.calls += 1
        assert url.startswith("http://testserver/webhooks/twilio/")
        assert signature is not None
        assert params
        return self.valid


class FakeTwilioWebhookRepository:
    def __init__(self) -> None:
        self.events: set[str] = set()
        self.side_effect_count = 0

    async def record_twilio_call_status(
        self, *, params: dict[str, str], payload_hash: str
    ) -> ProviderEventResult:
        key = f"{params['CallSid']}:{params.get('SequenceNumber')}:{params['CallStatus']}"
        if key in self.events:
            return ProviderEventResult(
                duplicate=True,
                call_attempt_id="attempt-1",
                status="ringing",
            )
        self.events.add(key)
        self.side_effect_count += 1
        return ProviderEventResult(
            duplicate=False,
            call_attempt_id="attempt-1",
            status="ringing",
        )


def make_settings() -> Settings:
    return Settings(
        APP_ENV="test",
        APP_PUBLIC_BASE_URL="http://testserver",
        CALLING_ENABLED=False,
        TRUSTED_SOURCE_TOKEN="test-token",
        DATABASE_URL="postgresql+asyncpg://user:pass@localhost/db",
        TWILIO_AUTH_TOKEN="twilio-test-token",
    )


async def test_valid_twilio_signature_accepted() -> None:
    repo = FakeTwilioWebhookRepository()
    app = create_app(
        settings=make_settings(),
        repository=InMemoryLeadRepository(),
        twilio_webhook_repository=repo,
        twilio_signature_verifier=FakeSignatureVerifier(valid=True),
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/webhooks/twilio/call-status",
            data={"CallSid": "CA123", "CallStatus": "ringing", "SequenceNumber": "1"},
            headers={"X-Twilio-Signature": "valid"},
        )

    assert response.status_code == 204
    assert repo.side_effect_count == 1


async def test_invalid_twilio_signature_rejected_before_state_changes() -> None:
    repo = FakeTwilioWebhookRepository()
    app = create_app(
        settings=make_settings(),
        repository=InMemoryLeadRepository(),
        twilio_webhook_repository=repo,
        twilio_signature_verifier=FakeSignatureVerifier(valid=False),
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/webhooks/twilio/call-status",
            data={"CallSid": "CA123", "CallStatus": "ringing", "SequenceNumber": "1"},
            headers={"X-Twilio-Signature": "invalid"},
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "invalid_signature"
    assert repo.side_effect_count == 0


async def test_duplicate_status_callback_performs_no_duplicate_side_effect() -> None:
    repo = FakeTwilioWebhookRepository()
    app = create_app(
        settings=make_settings(),
        repository=InMemoryLeadRepository(),
        twilio_webhook_repository=repo,
        twilio_signature_verifier=FakeSignatureVerifier(valid=True),
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        for _ in range(2):
            response = await client.post(
                "/webhooks/twilio/call-status",
                data={"CallSid": "CA123", "CallStatus": "ringing", "SequenceNumber": "1"},
                headers={"X-Twilio-Signature": "valid"},
            )
            assert response.status_code == 204

    assert repo.side_effect_count == 1


async def test_fixed_twiml_endpoint_returns_valid_xml() -> None:
    app = create_app(
        settings=make_settings(),
        repository=InMemoryLeadRepository(),
        twilio_webhook_repository=FakeTwilioWebhookRepository(),
        twilio_signature_verifier=FakeSignatureVerifier(valid=True),
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/webhooks/twilio/voice/start",
            data={"CallSid": "CA123", "CallStatus": "in-progress"},
            headers={"X-Twilio-Signature": "valid"},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/xml")
    assert "<Response>" in response.text
    assert "<Say>" in response.text
    assert "automated call" in response.text
    assert "technical Stage 3 test" in response.text
    assert "<Hangup />" in response.text
