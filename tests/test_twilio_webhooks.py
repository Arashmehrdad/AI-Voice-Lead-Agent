from __future__ import annotations

from dataclasses import dataclass

from httpx import ASGITransport, AsyncClient

from tests.fakes import InMemoryLeadRepository
from voice_lead_agent.app import create_app
from voice_lead_agent.config import Settings
from voice_lead_agent.domain import ProviderEventResult
from voice_lead_agent.twiml import conversation_relay_twiml


@dataclass
class FakeSignatureVerifier:
    valid: bool
    calls: int = 0
    last_url: str = ""

    def validate(self, *, url: str, params: dict[str, str], signature: str | None) -> bool:
        self.calls += 1
        self.last_url = url
        if signature is None:
            return False
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
    settings = make_settings().model_copy(
        update={
            "app_public_base_url": "https://voice.example.com",
            "gemini_api_key": "gemini-secret-key",
        }
    )
    app = create_app(
        settings=settings,
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
    assert "<Connect>" in response.text
    assert "<ConversationRelay " in response.text
    assert 'url="wss://voice.example.com/ws/twilio/conversationrelay"' in response.text
    assert 'language="en-GB"' in response.text
    assert "automated AI assistant" in response.text
    assert "qualification questions" in response.text
    assert 'dtmfDetection="true"' in response.text
    assert "twilio-test-token" not in response.text
    assert "gemini-secret-key" not in response.text


async def test_invalid_voice_start_signature_behaviour_remains_unchanged() -> None:
    app = create_app(
        settings=make_settings(),
        repository=InMemoryLeadRepository(),
        twilio_webhook_repository=FakeTwilioWebhookRepository(),
        twilio_signature_verifier=FakeSignatureVerifier(valid=False),
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/webhooks/twilio/voice/start",
            data={"CallSid": "CA123", "CallStatus": "in-progress"},
            headers={"X-Twilio-Signature": "invalid"},
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "invalid_signature"


def test_conversation_relay_twiml_includes_configured_language() -> None:
    xml = conversation_relay_twiml(
        websocket_url="wss://voice.example.com/ws/twilio/conversationrelay",
        welcome_greeting="Hello.",
        language="en-GB",
    )
    assert 'language="en-GB"' in xml


def test_conversation_relay_twiml_quoteattr_encodes_special_characters() -> None:
    xml = conversation_relay_twiml(
        websocket_url='wss://voice.example.com/ws?token="abc"&mode=<relay>',
        welcome_greeting='He said "hello" & <goodbye>',
        language='en-"GB"',
        custom_parameters={
            'call_"id"': "value&with<tags>",
        },
    )
    assert "url='wss://voice.example.com/ws?token=\"abc\"&amp;mode=&lt;relay&gt;'" in xml
    assert "welcomeGreeting='He said \"hello\" &amp; &lt;goodbye&gt;'" in xml
    assert "language='en-\"GB\"'" in xml
    assert "name='call_\"id\"'" in xml
    assert 'value="value&amp;with&lt;tags&gt;"' in xml
    assert "<relay>" not in xml
    assert "<goodbye>" not in xml
    assert "&mode=" not in xml
