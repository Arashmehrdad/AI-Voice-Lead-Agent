from __future__ import annotations

import json
import logging
from collections.abc import Mapping
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
from voice_lead_agent.conversation_models import ConversationContext
from voice_lead_agent.conversation_relay_protocol import (
    HANDOFF_REASON_END_CALL,
    HANDOFF_REASON_MAX_TURNS,
    HANDOFF_REASON_NEEDS_HUMAN,
    HANDOFF_REASON_OPTED_OUT,
)
from voice_lead_agent.conversation_service import DEFAULT_FALLBACK_SPOKEN_TEXT, OPT_OUT_SPOKEN_TEXT
from voice_lead_agent.gemini_client import GeminiConversationClient, GeminiError


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


@dataclass
class RecordingGeminiClient:
    outcomes: list[str | Exception]
    contexts: list[ConversationContext] = field(default_factory=list)

    async def generate_decision(self, context: ConversationContext) -> str:
        self.contexts.append(context)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@dataclass
class RecordingGeminiFactory:
    client: GeminiConversationClient | None
    calls: int = 0

    def __call__(self, settings: object) -> GeminiConversationClient | None:
        self.calls += 1
        return self.client


def _make_setup_payload() -> str:
    return json.dumps(
        {
            "type": "setup",
            "sessionId": "VX123",
            "callSid": "CA123",
            "accountSid": "AC123",
            "from": "+15551234567",
            "to": "+15559876543",
        }
    )


def _make_prompt_payload(*, voice_prompt: str, lang: str = "en-GB", last: bool) -> str:
    return json.dumps(
        {
            "type": "prompt",
            "voicePrompt": voice_prompt,
            "lang": lang,
            "last": last,
        }
    )


def _assert_end_message(*, payload: dict[str, object], expected_reason: str) -> None:
    assert payload["type"] == "end"
    handoff_raw = payload.get("handoffData")
    assert isinstance(handoff_raw, str)
    handoff = json.loads(handoff_raw)
    assert handoff == {"reasonCode": expected_reason}
    assert "voicePrompt" not in handoff_raw
    assert "@" not in handoff_raw
    assert "+" not in handoff_raw


def _receive_end_message(ws: object, *, expected_reason: str) -> None:
    payload = json.loads(ws.receive_text())  # type: ignore[attr-defined]
    _assert_end_message(payload=payload, expected_reason=expected_reason)


def _make_test_client(
    *,
    verifier: ParamsTrackingVerifier | FakeSignatureVerifier | None = None,
    gemini_factory: RecordingGeminiFactory | None = None,
    settings_override: Mapping[str, object] | None = None,
) -> TestClient:
    settings = make_settings()
    if settings_override:
        settings = settings.model_copy(update=dict(settings_override))
    app = create_app(
        settings=settings,
        repository=InMemoryLeadRepository(),
        twilio_webhook_repository=FakeTwilioWebhookRepository(),
        twilio_signature_verifier=verifier or FakeSignatureVerifier(valid=True),
        gemini_client_factory=gemini_factory,
    )
    return TestClient(app)


def test_missing_signature_rejected_before_accept() -> None:
    factory = RecordingGeminiFactory(client=None)
    client = _make_test_client(gemini_factory=factory)
    with (
        pytest.raises(WebSocketDisconnect) as exc_info,
        client.websocket_connect("/ws/twilio/conversationrelay"),
    ):
        pass
    assert exc_info.value.code == 1008
    assert factory.calls == 0


def test_invalid_signature_rejected_before_accept() -> None:
    factory = RecordingGeminiFactory(client=None)
    client = _make_test_client(
        verifier=FakeSignatureVerifier(valid=False),
        gemini_factory=factory,
    )
    with (
        pytest.raises(WebSocketDisconnect) as exc_info,
        client.websocket_connect(
            "/ws/twilio/conversationrelay",
            headers={"X-Twilio-Signature": "invalid"},
        ),
    ):
        pass
    assert exc_info.value.code == 1008
    assert factory.calls == 0


def test_valid_signature_accepted() -> None:
    factory = RecordingGeminiFactory(client=None)
    client = _make_test_client(gemini_factory=factory)
    with client.websocket_connect(
        "/ws/twilio/conversationrelay",
        headers={"X-Twilio-Signature": "valid"},
    ):
        pass
    assert factory.calls == 0


def test_public_ws_url_uses_ws_scheme() -> None:
    settings = make_settings()
    assert settings.app_public_base_url == "http://testserver"
    mock_ws = MagicMock(spec=WebSocket)
    mock_ws.url.path = "/ws/twilio/conversationrelay"
    mock_ws.url.query = ""
    mock_ws.scope = {"query_string": b""}
    assert (
        public_ws_url_for_request(mock_ws, settings)
        == "ws://testserver/ws/twilio/conversationrelay"
    )


def test_public_ws_url_uses_wss_when_https() -> None:
    settings = make_settings().model_copy(
        update={"app_public_base_url": "https://voice.example.com"}
    )
    mock_ws = MagicMock(spec=WebSocket)
    mock_ws.url.path = "/ws/twilio/conversationrelay"
    mock_ws.url.query = "call_attempt_id=abc"
    mock_ws.scope = {"query_string": b"call_attempt_id=abc"}
    assert (
        public_ws_url_for_request(mock_ws, settings)
        == "wss://voice.example.com/ws/twilio/conversationrelay?call_attempt_id=abc"
    )


def test_query_params_in_url_not_separately() -> None:
    verifier = ParamsTrackingVerifier(valid=True)
    client = _make_test_client(verifier=verifier)
    with client.websocket_connect(
        "/ws/twilio/conversationrelay?call_attempt_id=abc123",
        headers={"X-Twilio-Signature": "valid"},
    ):
        pass
    assert verifier.calls == 1
    assert "call_attempt_id=abc123" in verifier.last_url
    assert verifier.last_params == {}


def test_valid_setup_accepted() -> None:
    client = _make_test_client()
    with client.websocket_connect(
        "/ws/twilio/conversationrelay",
        headers={"X-Twilio-Signature": "valid"},
    ) as ws:
        ws.send_text(_make_setup_payload())


def test_setup_must_be_first() -> None:
    client = _make_test_client()
    with (
        pytest.raises(WebSocketDisconnect) as exc_info,
        client.websocket_connect(
            "/ws/twilio/conversationrelay",
            headers={"X-Twilio-Signature": "valid"},
        ) as ws,
    ):
        ws.send_text(_make_prompt_payload(voice_prompt="Hello", last=True))
        ws.receive_text()
    assert exc_info.value.code == 1008


def test_duplicate_setup_rejected() -> None:
    client = _make_test_client()
    with (
        pytest.raises(WebSocketDisconnect) as exc_info,
        client.websocket_connect(
            "/ws/twilio/conversationrelay",
            headers={"X-Twilio-Signature": "valid"},
        ) as ws,
    ):
        ws.send_text(_make_setup_payload())
        ws.send_text(_make_setup_payload())
        ws.receive_text()
    assert exc_info.value.code == 1008


def test_malformed_messages_close_with_1007() -> None:
    client = _make_test_client()
    with (
        pytest.raises(WebSocketDisconnect) as exc_info,
        client.websocket_connect(
            "/ws/twilio/conversationrelay",
            headers={"X-Twilio-Signature": "valid"},
        ) as ws,
    ):
        ws.send_text("not-json{{{")
        ws.receive_text()
    assert exc_info.value.code == 1007


def test_policy_ordering_failures_close_with_1008() -> None:
    client = _make_test_client()
    with (
        pytest.raises(WebSocketDisconnect) as exc_info,
        client.websocket_connect(
            "/ws/twilio/conversationrelay",
            headers={"X-Twilio-Signature": "valid"},
        ) as ws,
    ):
        ws.send_text(json.dumps({"type": "dtmf", "digit": "1"}))
        ws.receive_text()
    assert exc_info.value.code == 1008


def test_valid_setup_and_completed_prompt_produce_exact_text_token_response() -> None:
    gemini = RecordingGeminiClient(
        outcomes=[
            json.dumps({"action": "continue", "spoken_response": "Thanks for explaining that."})
        ]
    )
    factory = RecordingGeminiFactory(client=gemini)
    client = _make_test_client(gemini_factory=factory)
    with client.websocket_connect(
        "/ws/twilio/conversationrelay",
        headers={"X-Twilio-Signature": "valid"},
    ) as ws:
        ws.send_text(_make_setup_payload())
        ws.send_text(
            _make_prompt_payload(voice_prompt="I have a leaking roof.", lang="en-US", last=True)
        )
        payload = json.loads(ws.receive_text())

    assert payload == {
        "type": "text",
        "token": "Thanks for explaining that.",
        "last": True,
        "lang": "en-US",
        "interruptible": True,
        "preemptible": False,
    }
    assert factory.calls == 1
    assert len(gemini.contexts) == 1
    context = gemini.contexts[0]
    assert "automated assistant" in context.system_instruction
    assert "Return JSON only." in context.system_instruction
    assert "legal, medical, financial" in context.system_instruction


def test_partial_prompts_do_not_call_gemini() -> None:
    gemini = RecordingGeminiClient(
        outcomes=[json.dumps({"action": "continue", "spoken_response": "Please go on."})]
    )
    factory = RecordingGeminiFactory(client=gemini)
    client = _make_test_client(gemini_factory=factory)
    with client.websocket_connect(
        "/ws/twilio/conversationrelay",
        headers={"X-Twilio-Signature": "valid"},
    ) as ws:
        ws.send_text(_make_setup_payload())
        ws.send_text(_make_prompt_payload(voice_prompt="My roof is", last=False))
        assert len(gemini.contexts) == 0
        ws.send_text(_make_prompt_payload(voice_prompt="My roof is leaking.", last=True))
        assert json.loads(ws.receive_text())["token"] == "Please go on."

    assert factory.calls == 1
    assert len(gemini.contexts) == 1


def test_interrupt_event_is_accepted_and_connection_remains_usable() -> None:
    gemini = RecordingGeminiClient(
        outcomes=[json.dumps({"action": "continue", "spoken_response": "Please continue."})]
    )
    client = _make_test_client(gemini_factory=RecordingGeminiFactory(client=gemini))
    with client.websocket_connect(
        "/ws/twilio/conversationrelay",
        headers={"X-Twilio-Signature": "valid"},
    ) as ws:
        ws.send_text(_make_setup_payload())
        ws.send_text(
            json.dumps(
                {
                    "type": "interrupt",
                    "utteranceUntilInterrupt": "stop",
                    "durationUntilInterruptMs": 500,
                }
            )
        )
        ws.send_text(_make_prompt_payload(voice_prompt="The roof is leaking.", last=True))
        assert json.loads(ws.receive_text())["token"] == "Please continue."


def test_dtmf_event_is_accepted_and_connection_remains_usable() -> None:
    gemini = RecordingGeminiClient(
        outcomes=[json.dumps({"action": "continue", "spoken_response": "I heard that."})]
    )
    client = _make_test_client(gemini_factory=RecordingGeminiFactory(client=gemini))
    with client.websocket_connect(
        "/ws/twilio/conversationrelay",
        headers={"X-Twilio-Signature": "valid"},
    ) as ws:
        ws.send_text(_make_setup_payload())
        ws.send_text(json.dumps({"type": "dtmf", "digit": "5"}))
        ws.send_text(_make_prompt_payload(voice_prompt="The leak is near the chimney.", last=True))
        assert json.loads(ws.receive_text())["token"] == "I heard that."


def test_multiple_turns_include_previous_redacted_history_only() -> None:
    gemini = RecordingGeminiClient(
        outcomes=[
            json.dumps({"action": "continue", "spoken_response": "Thanks for that."}),
            json.dumps({"action": "continue", "spoken_response": "I have enough detail now."}),
        ]
    )
    client = _make_test_client(gemini_factory=RecordingGeminiFactory(client=gemini))
    with client.websocket_connect(
        "/ws/twilio/conversationrelay",
        headers={"X-Twilio-Signature": "valid"},
    ) as ws:
        ws.send_text(_make_setup_payload())
        ws.send_text(
            _make_prompt_payload(
                voice_prompt="Email me at person@example.com and call +447700900123.",
                last=True,
            )
        )
        ws.receive_text()
        ws.send_text(
            _make_prompt_payload(voice_prompt="The roof leaks near the chimney.", last=True)
        )
        ws.receive_text()

    assert len(gemini.contexts) == 2
    second_context = gemini.contexts[1]
    assert "[redacted:email]" in second_context.conversation_text
    assert "[redacted:phone]" in second_context.conversation_text
    assert "person@example.com" not in second_context.conversation_text
    assert "+447700900123" not in second_context.conversation_text
    assert "Thanks for that." in second_context.conversation_text


def test_sensitive_raw_values_never_reach_gemini() -> None:
    gemini = RecordingGeminiClient(
        outcomes=[json.dumps({"action": "continue", "spoken_response": "I can help with that."})]
    )
    client = _make_test_client(gemini_factory=RecordingGeminiFactory(client=gemini))
    with client.websocket_connect(
        "/ws/twilio/conversationrelay",
        headers={"X-Twilio-Signature": "valid"},
    ) as ws:
        ws.send_text(_make_setup_payload())
        ws.send_text(
            _make_prompt_payload(
                voice_prompt=(
                    "Call +447700900123, email person@example.com, postcode SW1A 1AA, "
                    "card 4111 1111 1111 1111, bearer abc123token"
                ),
                last=True,
            )
        )
        ws.receive_text()

    transcript = gemini.contexts[0].conversation_text
    for raw_value in (
        "+447700900123",
        "person@example.com",
        "SW1A 1AA",
        "4111 1111 1111 1111",
        "abc123token",
    ):
        assert raw_value not in transcript


def test_valid_qualified_response_is_spoken_without_closing_connection() -> None:
    gemini = RecordingGeminiClient(
        outcomes=[
            json.dumps({"action": "qualified", "spoken_response": "You sound like a good fit."}),
            json.dumps({"action": "continue", "spoken_response": "Tell me a bit more."}),
        ]
    )
    client = _make_test_client(gemini_factory=RecordingGeminiFactory(client=gemini))
    with client.websocket_connect(
        "/ws/twilio/conversationrelay",
        headers={"X-Twilio-Signature": "valid"},
    ) as ws:
        ws.send_text(_make_setup_payload())
        ws.send_text(_make_prompt_payload(voice_prompt="Yes, I own the property.", last=True))
        assert json.loads(ws.receive_text())["token"] == "You sound like a good fit."
        ws.send_text(_make_prompt_payload(voice_prompt="It is a detached house.", last=True))
        assert json.loads(ws.receive_text())["token"] == "Tell me a bit more."


def test_valid_not_qualified_response_is_sent() -> None:
    gemini = RecordingGeminiClient(
        outcomes=[
            json.dumps(
                {
                    "action": "not_qualified",
                    "spoken_response": "This service is not suitable for that request.",
                }
            )
        ]
    )
    client = _make_test_client(gemini_factory=RecordingGeminiFactory(client=gemini))
    with client.websocket_connect(
        "/ws/twilio/conversationrelay",
        headers={"X-Twilio-Signature": "valid"},
    ) as ws:
        ws.send_text(_make_setup_payload())
        ws.send_text(_make_prompt_payload(voice_prompt="I only need a repaint.", last=True))
        assert (
            json.loads(ws.receive_text())["token"]
            == "This service is not suitable for that request."
        )


def test_valid_end_call_response_sends_text_then_end_message() -> None:
    gemini = RecordingGeminiClient(
        outcomes=[
            json.dumps(
                {"action": "end_call", "spoken_response": "Thank you for your time. Goodbye."}
            )
        ]
    )
    client = _make_test_client(gemini_factory=RecordingGeminiFactory(client=gemini))
    with client.websocket_connect(
        "/ws/twilio/conversationrelay",
        headers={"X-Twilio-Signature": "valid"},
    ) as ws:
        ws.send_text(_make_setup_payload())
        ws.send_text(_make_prompt_payload(voice_prompt="No, that is everything.", last=True))
        assert json.loads(ws.receive_text())["token"] == "Thank you for your time. Goodbye."
        _receive_end_message(ws, expected_reason=HANDOFF_REASON_END_CALL)


def test_deterministic_opt_out_bypasses_gemini_and_sends_bounded_handoff() -> None:
    factory = RecordingGeminiFactory(
        client=RecordingGeminiClient(
            outcomes=[json.dumps({"action": "continue", "spoken_response": "unused"})]
        )
    )
    client = _make_test_client(gemini_factory=factory)
    with client.websocket_connect(
        "/ws/twilio/conversationrelay",
        headers={"X-Twilio-Signature": "valid"},
    ) as ws:
        ws.send_text(_make_setup_payload())
        ws.send_text(
            _make_prompt_payload(
                voice_prompt="Please stop calling me at person@example.com or +447700900123.",
                last=True,
            )
        )
        assert json.loads(ws.receive_text())["token"] == OPT_OUT_SPOKEN_TEXT
        _receive_end_message(ws, expected_reason=HANDOFF_REASON_OPTED_OUT)
    assert factory.calls == 0


def test_deterministic_human_escalation_bypasses_gemini_and_sends_bounded_handoff() -> None:
    factory = RecordingGeminiFactory(
        client=RecordingGeminiClient(
            outcomes=[json.dumps({"action": "continue", "spoken_response": "unused"})]
        )
    )
    settings = {
        "human_help_text": "I understand that you want human help. This call will end now. Goodbye."
    }
    client = _make_test_client(gemini_factory=factory, settings_override=settings)
    with client.websocket_connect(
        "/ws/twilio/conversationrelay",
        headers={"X-Twilio-Signature": "valid"},
    ) as ws:
        ws.send_text(_make_setup_payload())
        ws.send_text(
            _make_prompt_payload(voice_prompt="I want to speak to a real person.", last=True)
        )
        assert (
            json.loads(ws.receive_text())["token"]
            == "I understand that you want human help. This call will end now. Goodbye."
        )
        _receive_end_message(ws, expected_reason=HANDOFF_REASON_NEEDS_HUMAN)
    assert factory.calls == 0


@pytest.mark.parametrize("action", ["opt_out", "human_escalation"])
def test_model_generated_deterministic_actions_are_rejected(action: str) -> None:
    gemini = RecordingGeminiClient(
        outcomes=[json.dumps({"action": action, "spoken_response": "unsafe"})]
    )
    client = _make_test_client(gemini_factory=RecordingGeminiFactory(client=gemini))
    with client.websocket_connect(
        "/ws/twilio/conversationrelay",
        headers={"X-Twilio-Signature": "valid"},
    ) as ws:
        ws.send_text(_make_setup_payload())
        ws.send_text(_make_prompt_payload(voice_prompt="My roof leaks in heavy rain.", last=True))
        assert json.loads(ws.receive_text())["token"] == DEFAULT_FALLBACK_SPOKEN_TEXT


def test_missing_gemini_key_or_client_returns_safe_response() -> None:
    client = _make_test_client()
    with client.websocket_connect(
        "/ws/twilio/conversationrelay",
        headers={"X-Twilio-Signature": "valid"},
    ) as ws:
        ws.send_text(_make_setup_payload())
        ws.send_text(_make_prompt_payload(voice_prompt="I have a leaking roof.", last=True))
        assert json.loads(ws.receive_text())["token"] == DEFAULT_FALLBACK_SPOKEN_TEXT


def test_timeout_returns_safe_response() -> None:
    gemini = RecordingGeminiClient(
        outcomes=[GeminiError("gemini_timeout", "provider timeout raw-secret")]
    )
    client = _make_test_client(gemini_factory=RecordingGeminiFactory(client=gemini))
    with client.websocket_connect(
        "/ws/twilio/conversationrelay",
        headers={"X-Twilio-Signature": "valid"},
    ) as ws:
        ws.send_text(_make_setup_payload())
        ws.send_text(_make_prompt_payload(voice_prompt="I have a leaking roof.", last=True))
        assert json.loads(ws.receive_text())["token"] == DEFAULT_FALLBACK_SPOKEN_TEXT


def test_malformed_model_output_returns_safe_response_and_logs_no_raw_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    gemini = RecordingGeminiClient(
        outcomes=['{"action":"continue","spoken_response":"secret-token']
    )
    client = _make_test_client(gemini_factory=RecordingGeminiFactory(client=gemini))
    with (
        caplog.at_level(logging.WARNING),
        client.websocket_connect(
            "/ws/twilio/conversationrelay",
            headers={"X-Twilio-Signature": "valid"},
        ) as ws,
    ):
        ws.send_text(_make_setup_payload())
        ws.send_text(
            _make_prompt_payload(
                voice_prompt="Email me at person@example.com or call +447700900123.",
                last=True,
            )
        )
        assert json.loads(ws.receive_text())["token"] == DEFAULT_FALLBACK_SPOKEN_TEXT

    log_text = caplog.text
    assert "person@example.com" not in log_text
    assert "+447700900123" not in log_text
    assert "secret-token" not in log_text


def test_turn_limit_sends_closing_text_then_end_message() -> None:
    gemini = RecordingGeminiClient(
        outcomes=[json.dumps({"action": "continue", "spoken_response": "First response."})]
    )
    client = _make_test_client(
        gemini_factory=RecordingGeminiFactory(client=gemini),
        settings_override={"conversation_max_turns": 1},
    )
    with client.websocket_connect(
        "/ws/twilio/conversationrelay",
        headers={"X-Twilio-Signature": "valid"},
    ) as ws:
        ws.send_text(_make_setup_payload())
        ws.send_text(_make_prompt_payload(voice_prompt="First turn.", last=True))
        assert json.loads(ws.receive_text())["token"] == "First response."
        ws.send_text(_make_prompt_payload(voice_prompt="Second turn.", last=True))
        assert (
            json.loads(ws.receive_text())["token"]
            == "Thanks for your time. We will end this call here now. Goodbye."
        )
        _receive_end_message(ws, expected_reason=HANDOFF_REASON_MAX_TURNS)

    assert len(gemini.contexts) == 1


def test_provider_error_event_leads_to_safe_shutdown_without_logging_payload(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = _make_test_client()
    with (
        caplog.at_level(logging.WARNING),
        client.websocket_connect(
            "/ws/twilio/conversationrelay",
            headers={"X-Twilio-Signature": "valid"},
        ) as ws,
    ):
        ws.send_text(_make_setup_payload())
        ws.send_text(json.dumps({"type": "error", "description": "token abc123secret"}))
        with pytest.raises(WebSocketDisconnect):
            ws.receive_text()
    assert "abc123secret" not in caplog.text


def test_disconnect_remains_graceful() -> None:
    client = _make_test_client()
    with client.websocket_connect(
        "/ws/twilio/conversationrelay",
        headers={"X-Twilio-Signature": "valid"},
    ) as ws:
        ws.send_text(_make_setup_payload())
        ws.close()


def test_account_sid_mismatch_closes_with_1008_without_gemini(
    caplog: pytest.LogCaptureFixture,
) -> None:
    factory = RecordingGeminiFactory(
        client=RecordingGeminiClient(
            outcomes=[json.dumps({"action": "continue", "spoken_response": "unused"})]
        )
    )
    client = _make_test_client(
        gemini_factory=factory,
        settings_override={"twilio_account_sid": "ACconfigured"},
    )
    with (
        caplog.at_level(logging.WARNING),
        pytest.raises(WebSocketDisconnect) as exc_info,
        client.websocket_connect(
            "/ws/twilio/conversationrelay",
            headers={"X-Twilio-Signature": "valid"},
        ) as ws,
    ):
        ws.send_text(_make_setup_payload())
        ws.receive_text()
    assert exc_info.value.code == 1008
    assert factory.calls == 0
    assert "ACconfigured" not in caplog.text
    assert "AC123" not in caplog.text


def test_matching_account_sid_proceeds_after_setup() -> None:
    gemini = RecordingGeminiClient(
        outcomes=[json.dumps({"action": "continue", "spoken_response": "Proceeding."})]
    )
    factory = RecordingGeminiFactory(client=gemini)
    client = _make_test_client(
        gemini_factory=factory,
        settings_override={"twilio_account_sid": "AC123"},
    )
    with client.websocket_connect(
        "/ws/twilio/conversationrelay",
        headers={"X-Twilio-Signature": "valid"},
    ) as ws:
        ws.send_text(_make_setup_payload())
        ws.send_text(_make_prompt_payload(voice_prompt="Hello there.", last=True))
        assert json.loads(ws.receive_text())["token"] == "Proceeding."
    assert factory.calls == 1
