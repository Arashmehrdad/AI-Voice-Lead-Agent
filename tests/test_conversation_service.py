"""Unit tests for the deterministic Stage 4 conversation service."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import cast

import pytest

from voice_lead_agent.conversation_models import (
    ConversationAction,
    ConversationContext,
    ConversationTurn,
)
from voice_lead_agent.conversation_policy import MAX_USER_INPUT_LENGTH
from voice_lead_agent.conversation_service import (
    DEFAULT_FALLBACK_SPOKEN_TEXT,
    OPT_OUT_SPOKEN_TEXT,
    process_turn,
)
from voice_lead_agent.gemini_client import GeminiConversationClient, GeminiError

SYSTEM_INSTRUCTION = "Follow the approved qualification flow only."
HUMAN_HELP_TEXT = "I can arrange for a human colleague to help you."
FALLBACK_TEXT = "Sorry, I am having trouble right now. Please say that again."


@dataclass
class FakeGeminiClient:
    """Recording fake for deterministic service tests."""

    outcome: str | Exception
    calls: list[ConversationContext] = field(default_factory=list)

    async def generate_decision(self, context: ConversationContext) -> str:
        self.calls.append(context)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def _make_history() -> list[ConversationTurn]:
    return [
        ConversationTurn(role="assistant", redacted_text="Hello, I am calling about your enquiry."),
        ConversationTurn(
            role="user",
            redacted_text="My email is [redacted:email] and my phone is [redacted:phone].",
        ),
    ]


async def test_normal_text_calls_gemini_once() -> None:
    client = FakeGeminiClient(
        outcome='{"action":"continue","spoken_response":"Thanks for explaining that."}'
    )

    result = await process_turn(
        client=cast(GeminiConversationClient, client),
        system_instruction=SYSTEM_INSTRUCTION,
        history=[],
        caller_text="I need help with a leaking roof.",
        turn_number=3,
        human_help_text=HUMAN_HELP_TEXT,
        fallback_text=FALLBACK_TEXT,
    )

    assert len(client.calls) == 1
    assert result.gemini_called is True
    assert result.decision.action is ConversationAction.CONTINUE


async def test_gemini_receives_only_redacted_caller_text() -> None:
    client = FakeGeminiClient(
        outcome='{"action":"continue","spoken_response":"Thanks, please continue."}'
    )
    caller_text = (
        "Call +447700900123, email person@example.com, postcode SW1A 1AA, "
        "card 4111 1111 1111 1111, bearer abc123token"
    )

    await process_turn(
        client=cast(GeminiConversationClient, client),
        system_instruction=SYSTEM_INSTRUCTION,
        history=[],
        caller_text=caller_text,
        turn_number=2,
        human_help_text=HUMAN_HELP_TEXT,
        fallback_text=FALLBACK_TEXT,
    )

    transcript = client.calls[0].conversation_text
    assert "+447700900123" not in transcript
    assert "person@example.com" not in transcript
    assert "SW1A 1AA" not in transcript
    assert "4111 1111 1111 1111" not in transcript
    assert "abc123token" not in transcript
    assert "[redacted:phone]" in transcript
    assert "[redacted:email]" in transcript
    assert "[redacted:address]" in transcript
    assert "[redacted:card]" in transcript
    assert "[redacted:secret]" in transcript


async def test_previous_history_is_included_only_in_redacted_form() -> None:
    client = FakeGeminiClient(
        outcome='{"action":"continue","spoken_response":"Thanks, I have that."}'
    )

    await process_turn(
        client=cast(GeminiConversationClient, client),
        system_instruction=SYSTEM_INSTRUCTION,
        history=_make_history(),
        caller_text="The roof started leaking yesterday.",
        turn_number=4,
        human_help_text=HUMAN_HELP_TEXT,
        fallback_text=FALLBACK_TEXT,
    )

    transcript = client.calls[0].conversation_text
    assert "Turn 1 assistant: Hello, I am calling about your enquiry." in transcript
    assert "[redacted:email]" in transcript
    assert "[redacted:phone]" in transcript
    assert "person@example.com" not in transcript
    assert "+447700900123" not in transcript


async def test_opt_out_does_not_call_gemini() -> None:
    client = FakeGeminiClient(
        outcome='{"action":"continue","spoken_response":"This should not be used."}'
    )

    result = await process_turn(
        client=cast(GeminiConversationClient, client),
        system_instruction=SYSTEM_INSTRUCTION,
        history=[],
        caller_text="Please stop calling me.",
        turn_number=2,
        human_help_text=HUMAN_HELP_TEXT,
        fallback_text=FALLBACK_TEXT,
    )

    assert len(client.calls) == 0
    assert result.gemini_called is False
    assert result.decision.action is ConversationAction.OPT_OUT
    assert result.decision.spoken_response == OPT_OUT_SPOKEN_TEXT
    assert result.decision.reason_code == "caller_opt_out"


async def test_opt_out_takes_precedence_over_escalation() -> None:
    client = FakeGeminiClient(
        outcome='{"action":"continue","spoken_response":"This should not be used."}'
    )

    result = await process_turn(
        client=cast(GeminiConversationClient, client),
        system_instruction=SYSTEM_INSTRUCTION,
        history=[],
        caller_text="Let me speak to a human and stop calling me.",
        turn_number=5,
        human_help_text=HUMAN_HELP_TEXT,
        fallback_text=FALLBACK_TEXT,
    )

    assert len(client.calls) == 0
    assert result.decision.action is ConversationAction.OPT_OUT
    assert result.decision.reason_code == "caller_opt_out"


async def test_human_request_does_not_call_gemini() -> None:
    client = FakeGeminiClient(
        outcome='{"action":"continue","spoken_response":"This should not be used."}'
    )

    result = await process_turn(
        client=cast(GeminiConversationClient, client),
        system_instruction=SYSTEM_INSTRUCTION,
        history=[],
        caller_text="I want to speak to a real person.",
        turn_number=2,
        human_help_text=HUMAN_HELP_TEXT,
        fallback_text=FALLBACK_TEXT,
    )

    assert len(client.calls) == 0
    assert result.gemini_called is False
    assert result.decision.action is ConversationAction.HUMAN_ESCALATION
    assert result.decision.spoken_response == HUMAN_HELP_TEXT
    assert result.decision.reason_code == "caller_requested_human"


async def test_valid_gemini_output_is_returned() -> None:
    client = FakeGeminiClient(
        outcome=(
            '{"action":"qualified","spoken_response":"You sound like a good fit.",'
            '"conversation_summary":"Qualified lead.","structured_facts":{"roof_type":"tile"},'
            '"reason_code":"qualified"}'
        )
    )

    result = await process_turn(
        client=cast(GeminiConversationClient, client),
        system_instruction=SYSTEM_INSTRUCTION,
        history=[],
        caller_text="Yes, the property is mine.",
        turn_number=6,
        human_help_text=HUMAN_HELP_TEXT,
        fallback_text=FALLBACK_TEXT,
    )

    assert result.decision.action is ConversationAction.QUALIFIED
    assert result.decision.conversation_summary == "Qualified lead."
    assert result.decision.structured_facts == {"roof_type": "tile"}
    assert result.decision.reason_code == "qualified"


@pytest.mark.parametrize(
    ("model_action", "spoken_response"),
    [
        ("opt_out", "I will stop calling you now."),
        ("human_escalation", "I will transfer you to a human."),
    ],
)
async def test_untrusted_deterministic_model_actions_are_replaced_with_safe_fallback(
    model_action: str, spoken_response: str
) -> None:
    client = FakeGeminiClient(
        outcome=(
            f'{{"action":"{model_action}","spoken_response":"{spoken_response}",'
            f'"reason_code":"model_requested"}}'
        )
    )

    result = await process_turn(
        client=cast(GeminiConversationClient, client),
        system_instruction=SYSTEM_INSTRUCTION,
        history=[],
        caller_text="I have a damp problem in the loft.",
        turn_number=3,
        human_help_text=HUMAN_HELP_TEXT,
        fallback_text=FALLBACK_TEXT,
    )

    assert result.gemini_called is True
    assert result.decision.action is ConversationAction.CONTINUE
    assert result.decision.spoken_response == FALLBACK_TEXT
    assert result.decision.reason_code == "untrusted_deterministic_action"


@pytest.mark.parametrize(
    ("error", "reason_code"),
    [
        (GeminiError("gemini_timeout", "provider timeout raw-secret"), "gemini_timeout"),
        (
            GeminiError("gemini_provider_error", "provider failed raw-secret"),
            "gemini_provider_error",
        ),
    ],
)
async def test_gemini_errors_return_safe_fallback(
    error: GeminiError, reason_code: str, caplog: pytest.LogCaptureFixture
) -> None:
    client = FakeGeminiClient(outcome=error)
    caller_text = "My token is abc123token and my phone is +447700900123."

    result = await process_turn(
        client=cast(GeminiConversationClient, client),
        system_instruction=SYSTEM_INSTRUCTION,
        history=[],
        caller_text=caller_text,
        turn_number=2,
        human_help_text=HUMAN_HELP_TEXT,
        fallback_text=FALLBACK_TEXT,
    )

    assert result.decision.action is ConversationAction.CONTINUE
    assert result.decision.spoken_response == FALLBACK_TEXT
    assert result.decision.reason_code == reason_code
    assert caller_text not in caplog.text
    assert "raw-secret" not in caplog.text
    assert "abc123token" not in caplog.text
    assert "+447700900123" not in caplog.text


async def test_malformed_json_returns_safe_fallback_without_logging_raw_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    raw_model_output = '{"action":"continue","spoken_response":"secret-token'
    caller_text = "My email is person@example.com."
    client = FakeGeminiClient(outcome=raw_model_output)

    result = await process_turn(
        client=cast(GeminiConversationClient, client),
        system_instruction=SYSTEM_INSTRUCTION,
        history=[],
        caller_text=caller_text,
        turn_number=7,
        human_help_text=HUMAN_HELP_TEXT,
        fallback_text=FALLBACK_TEXT,
    )

    assert result.decision.action is ConversationAction.CONTINUE
    assert result.decision.reason_code == "invalid_model_output"
    assert caller_text not in caplog.text
    assert raw_model_output not in caplog.text
    assert "person@example.com" not in caplog.text
    assert "secret-token" not in caplog.text


async def test_invalid_structured_output_returns_safe_fallback() -> None:
    client = FakeGeminiClient(
        outcome=(
            '{"action":"continue","spoken_response":"Thanks.","structured_facts":'
            '{"a":{"b":{"c":{"d":{"e":"too deep"}}}}}}'
        )
    )

    result = await process_turn(
        client=cast(GeminiConversationClient, client),
        system_instruction=SYSTEM_INSTRUCTION,
        history=[],
        caller_text="The issue started two weeks ago.",
        turn_number=4,
        human_help_text=HUMAN_HELP_TEXT,
        fallback_text=FALLBACK_TEXT,
    )

    assert result.decision.action is ConversationAction.CONTINUE
    assert result.decision.reason_code == "invalid_model_output"
    assert result.decision.spoken_response == FALLBACK_TEXT


async def test_empty_model_output_returns_safe_fallback() -> None:
    client = FakeGeminiClient(outcome="   ")

    result = await process_turn(
        client=cast(GeminiConversationClient, client),
        system_instruction=SYSTEM_INSTRUCTION,
        history=[],
        caller_text="The conservatory roof is leaking.",
        turn_number=8,
        human_help_text=HUMAN_HELP_TEXT,
        fallback_text=FALLBACK_TEXT,
    )

    assert result.decision.action is ConversationAction.CONTINUE
    assert result.decision.reason_code == "gemini_empty"
    assert result.decision.spoken_response == FALLBACK_TEXT


async def test_input_is_bounded_before_detection_and_redaction() -> None:
    client = FakeGeminiClient(outcome='{"action":"continue","spoken_response":"Please go on."}')
    caller_text = ("a" * MAX_USER_INPUT_LENGTH) + " stop calling me"

    result = await process_turn(
        client=cast(GeminiConversationClient, client),
        system_instruction=SYSTEM_INSTRUCTION,
        history=[],
        caller_text=caller_text,
        turn_number=9,
        human_help_text=HUMAN_HELP_TEXT,
        fallback_text=FALLBACK_TEXT,
    )

    assert result.gemini_called is True
    assert result.decision.action is ConversationAction.CONTINUE
    assert result.caller_turn.redacted_text == "a" * MAX_USER_INPUT_LENGTH
    assert "stop calling me" not in result.caller_turn.redacted_text
    assert "stop calling me" not in client.calls[0].conversation_text


async def test_service_uses_only_the_injected_client_for_normal_processing() -> None:
    client = FakeGeminiClient(
        outcome='{"action":"end_call","spoken_response":"Thank you for your time."}'
    )

    result = await process_turn(
        client=cast(GeminiConversationClient, client),
        system_instruction=SYSTEM_INSTRUCTION,
        history=[],
        caller_text="That answers my question.",
        turn_number=10,
        human_help_text=HUMAN_HELP_TEXT,
        fallback_text=DEFAULT_FALLBACK_SPOKEN_TEXT,
    )

    assert len(client.calls) == 1
    assert result.gemini_called is True
    assert result.decision.action is ConversationAction.END_CALL
