from __future__ import annotations

from dataclasses import dataclass, field
from unittest.mock import MagicMock

import pytest
from fastapi import WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from tests.fakes import InMemoryLeadRepository
from tests.test_twilio_webhooks import (
    FakeSignatureVerifier,
    FakeTwilioWebhookRepository,
    make_settings,
)
from voice_lead_agent.app import create_app, public_ws_url_for_request


@dataclass
class ParamsTrackingVerifier:
    valid: bool
    calls: int = 0
    last_url: str = ""
    last_params: dict[str, str] = field(default_factory=dict)

    def validate(self, *, url: str, params: dict[str, str], signature: str | None) -> bool:
        self.calls += 1
        self.last_url = url
        self.last_params = params
        if signature is None:
            return False
        return self.valid


def test_missing_signature_rejected_before_accept() -> None:
    app = create_app(
        settings=make_settings(),
        repository=InMemoryLeadRepository(),
        twilio_webhook_repository=FakeTwilioWebhookRepository(),
        twilio_signature_verifier=FakeSignatureVerifier(valid=True),
    )
    client = TestClient(app)
    with (
        pytest.raises(WebSocketDisconnect) as exc_info,
        client.websocket_connect("/ws/twilio/conversationrelay"),
    ):
        pass
    assert exc_info.value.code == 1008


def test_invalid_signature_rejected_before_accept() -> None:
    app = create_app(
        settings=make_settings(),
        repository=InMemoryLeadRepository(),
        twilio_webhook_repository=FakeTwilioWebhookRepository(),
        twilio_signature_verifier=FakeSignatureVerifier(valid=False),
    )
    client = TestClient(app)
    with (
        pytest.raises(WebSocketDisconnect) as exc_info,
        client.websocket_connect(
            "/ws/twilio/conversationrelay",
            headers={"X-Twilio-Signature": "invalid"},
        ),
    ):
        pass
    assert exc_info.value.code == 1008


def test_valid_signature_accepted() -> None:
    app = create_app(
        settings=make_settings(),
        repository=InMemoryLeadRepository(),
        twilio_webhook_repository=FakeTwilioWebhookRepository(),
        twilio_signature_verifier=FakeSignatureVerifier(valid=True),
    )
    client = TestClient(app)
    with client.websocket_connect(
        "/ws/twilio/conversationrelay",
        headers={"X-Twilio-Signature": "valid"},
    ):
        pass


def test_public_ws_url_uses_ws_scheme() -> None:
    settings = make_settings()
    assert settings.app_public_base_url == "http://testserver"
    mock_ws = MagicMock(spec=WebSocket)
    mock_ws.url.path = "/ws/twilio/conversationrelay"
    mock_ws.url.query = ""
    mock_ws.scope = {"query_string": b""}
    url = public_ws_url_for_request(mock_ws, settings)
    assert url == "ws://testserver/ws/twilio/conversationrelay"


def test_public_ws_url_uses_wss_when_https() -> None:
    settings = make_settings()
    https_settings = settings.model_copy(
        update={"app_public_base_url": "https://voice.example.com"}
    )
    mock_ws = MagicMock(spec=WebSocket)
    mock_ws.url.path = "/ws/twilio/conversationrelay"
    mock_ws.url.query = "call_attempt_id=abc"
    mock_ws.scope = {"query_string": b"call_attempt_id=abc"}
    url = public_ws_url_for_request(mock_ws, https_settings)
    assert url == "wss://voice.example.com/ws/twilio/conversationrelay?call_attempt_id=abc"


def test_public_ws_url_preserves_raw_query_string() -> None:
    settings = make_settings()
    mock_ws = MagicMock(spec=WebSocket)
    mock_ws.url.path = "/ws/twilio/conversationrelay"
    mock_ws.url.query = "name=hello world&type=a/b"
    mock_ws.scope = {"query_string": b"name=hello%20world&type=a%2Fb"}
    url = public_ws_url_for_request(mock_ws, settings)
    assert url == "ws://testserver/ws/twilio/conversationrelay?name=hello%20world&type=a%2Fb"


def test_query_params_in_url_not_separately() -> None:
    verifier = ParamsTrackingVerifier(valid=True)
    app = create_app(
        settings=make_settings(),
        repository=InMemoryLeadRepository(),
        twilio_webhook_repository=FakeTwilioWebhookRepository(),
        twilio_signature_verifier=verifier,
    )
    client = TestClient(app)
    with client.websocket_connect(
        "/ws/twilio/conversationrelay?call_attempt_id=abc123",
        headers={"X-Twilio-Signature": "valid"},
    ):
        pass
    assert verifier.calls == 1
    assert "call_attempt_id=abc123" in verifier.last_url
    assert verifier.last_url.count("call_attempt_id=abc123") == 1
    assert verifier.last_params == {}


def test_percent_encoded_query_remains_encoded() -> None:
    verifier = ParamsTrackingVerifier(valid=True)
    app = create_app(
        settings=make_settings(),
        repository=InMemoryLeadRepository(),
        twilio_webhook_repository=FakeTwilioWebhookRepository(),
        twilio_signature_verifier=verifier,
    )
    client = TestClient(app)
    with client.websocket_connect(
        "/ws/twilio/conversationrelay?name=hello%20world&type=a%2Fb",
        headers={"X-Twilio-Signature": "valid"},
    ):
        pass
    assert verifier.calls == 1
    assert "%20" in verifier.last_url
    assert "%2F" in verifier.last_url


def test_internal_asgi_not_used() -> None:
    settings = make_settings()
    external_settings = settings.model_copy(
        update={"app_public_base_url": "https://voice.example.com"}
    )
    verifier = ParamsTrackingVerifier(valid=True)
    app = create_app(
        settings=external_settings,
        repository=InMemoryLeadRepository(),
        twilio_webhook_repository=FakeTwilioWebhookRepository(),
        twilio_signature_verifier=verifier,
    )
    client = TestClient(app)
    with client.websocket_connect(
        "/ws/twilio/conversationrelay",
        headers={"X-Twilio-Signature": "valid"},
    ):
        pass
    assert verifier.last_url == "wss://voice.example.com/ws/twilio/conversationrelay"


def test_spoofed_forwarded_headers_ignored() -> None:
    verifier = ParamsTrackingVerifier(valid=True)
    app = create_app(
        settings=make_settings(),
        repository=InMemoryLeadRepository(),
        twilio_webhook_repository=FakeTwilioWebhookRepository(),
        twilio_signature_verifier=verifier,
    )
    client = TestClient(app)
    with client.websocket_connect(
        "/ws/twilio/conversationrelay",
        headers={
            "X-Twilio-Signature": "valid",
            "X-Forwarded-Host": "evil.example.com",
            "X-Forwarded-Proto": "https",
        },
    ):
        pass
    assert verifier.last_url.startswith("ws://testserver/ws/twilio/conversationrelay")


def test_signature_verifier_receives_ws_url() -> None:
    verifier = ParamsTrackingVerifier(valid=True)
    app = create_app(
        settings=make_settings(),
        repository=InMemoryLeadRepository(),
        twilio_webhook_repository=FakeTwilioWebhookRepository(),
        twilio_signature_verifier=verifier,
    )
    client = TestClient(app)
    with client.websocket_connect(
        "/ws/twilio/conversationrelay",
        headers={"X-Twilio-Signature": "valid"},
    ):
        pass
    assert verifier.calls == 1
    assert verifier.last_url.startswith("ws://testserver/ws/twilio/conversationrelay")


def test_disconnect_handled_gracefully() -> None:
    app = create_app(
        settings=make_settings(),
        repository=InMemoryLeadRepository(),
        twilio_webhook_repository=FakeTwilioWebhookRepository(),
        twilio_signature_verifier=FakeSignatureVerifier(valid=True),
    )
    client = TestClient(app)
    with client.websocket_connect(
        "/ws/twilio/conversationrelay",
        headers={"X-Twilio-Signature": "valid"},
    ) as ws:
        ws.close()
