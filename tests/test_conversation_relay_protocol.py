"""Unit tests for the Twilio ConversationRelay protocol models and parser.

All tests use static JSON fixtures — no network calls are made.
Every documented inbound event type, the outbound text-token message, and all
error branches of the parser are covered.
"""

from __future__ import annotations

import json
import logging
import traceback

import pytest
from pydantic import ValidationError

from voice_lead_agent.conversation_relay_protocol import (
    MAX_ERROR_DESCRIPTION_LENGTH,
    MAX_UTTERANCE_LENGTH,
    MAX_VOICE_PROMPT_LENGTH,
    ConversationRelayEventType,
    ConversationRelayParseError,
    DtmfEvent,
    ErrorEvent,
    InterruptEvent,
    PromptEvent,
    SetupEvent,
    TextTokenMessage,
    parse_inbound_event,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _setup_payload(**overrides: object) -> str:
    base: dict[str, object] = {
        "type": "setup",
        "sessionId": "VX00000000000000000000000000000000",
        "callSid": "CA00000000000000000000000000000000",
        "accountSid": "AC00000000000000000000000000000000",
        "from": "+18005550100",
        "to": "+18005550101",
        "forwardedFrom": "+18005550102",
        "callType": "PSTN",
        "callerName": "Alice",
        "direction": "inbound",
        "callStatus": "RINGING",
        "parentCallSid": "",
        "customParameters": {},
    }
    base.update(overrides)
    return json.dumps(base)


def _prompt_payload(**overrides: object) -> str:
    base: dict[str, object] = {
        "type": "prompt",
        "voicePrompt": "Hi! Can you tell me about life?",
        "lang": "en-US",
        "last": True,
    }
    base.update(overrides)
    return json.dumps(base)


def _interrupt_payload(**overrides: object) -> str:
    base: dict[str, object] = {
        "type": "interrupt",
        "utteranceUntilInterrupt": "Life is a complex set of",
        "durationUntilInterruptMs": 460,
    }
    base.update(overrides)
    return json.dumps(base)


def _dtmf_payload(**overrides: object) -> str:
    base: dict[str, object] = {"type": "dtmf", "digit": "1"}
    base.update(overrides)
    return json.dumps(base)


def _error_payload(**overrides: object) -> str:
    base: dict[str, object] = {
        "type": "error",
        "description": 'Invalid message received: { "foo" : "bar" }',
    }
    base.update(overrides)
    return json.dumps(base)


# ---------------------------------------------------------------------------
# SetupEvent — valid parsing
# ---------------------------------------------------------------------------


def test_valid_setup_parses_required_fields() -> None:
    event = parse_inbound_event(_setup_payload())
    assert isinstance(event, SetupEvent)
    assert event.session_id == "VX00000000000000000000000000000000"
    assert event.call_sid == "CA00000000000000000000000000000000"
    assert event.account_sid == "AC00000000000000000000000000000000"
    assert event.from_number == "+18005550100"
    assert event.to_number == "+18005550101"


def test_setup_maps_all_documented_fields() -> None:
    event = parse_inbound_event(_setup_payload())
    assert isinstance(event, SetupEvent)
    assert event.forwarded_from == "+18005550102"
    assert event.call_type == "PSTN"
    assert event.caller_name == "Alice"
    assert event.direction == "inbound"
    assert event.call_status == "RINGING"
    assert event.parent_call_sid == ""
    assert event.custom_parameters == {}


def test_setup_with_custom_parameters() -> None:
    event = parse_inbound_event(
        _setup_payload(customParameters={"callReference": "bar", "intent": "roofing"})
    )
    assert isinstance(event, SetupEvent)
    assert event.custom_parameters == {"callReference": "bar", "intent": "roofing"}


def test_setup_type_is_setup() -> None:
    event = parse_inbound_event(_setup_payload())
    assert isinstance(event, SetupEvent)
    assert event.type == "setup"


# ---------------------------------------------------------------------------
# SetupEvent — required identifiers
# ---------------------------------------------------------------------------


def test_setup_missing_session_id_rejected() -> None:
    data = json.loads(_setup_payload())
    del data["sessionId"]
    with pytest.raises(ConversationRelayParseError) as exc:
        parse_inbound_event(json.dumps(data))
    assert exc.value.category == "validation_error"


def test_setup_missing_call_sid_rejected() -> None:
    data = json.loads(_setup_payload())
    del data["callSid"]
    with pytest.raises(ConversationRelayParseError) as exc:
        parse_inbound_event(json.dumps(data))
    assert exc.value.category == "validation_error"


def test_setup_missing_account_sid_rejected() -> None:
    data = json.loads(_setup_payload())
    del data["accountSid"]
    with pytest.raises(ConversationRelayParseError) as exc:
        parse_inbound_event(json.dumps(data))
    assert exc.value.category == "validation_error"


def test_setup_missing_from_rejected() -> None:
    data = json.loads(_setup_payload())
    del data["from"]
    with pytest.raises(ConversationRelayParseError) as exc:
        parse_inbound_event(json.dumps(data))
    assert exc.value.category == "validation_error"


def test_setup_missing_to_rejected() -> None:
    data = json.loads(_setup_payload())
    del data["to"]
    with pytest.raises(ConversationRelayParseError) as exc:
        parse_inbound_event(json.dumps(data))
    assert exc.value.category == "validation_error"


def test_setup_blank_session_id_rejected() -> None:
    with pytest.raises(ConversationRelayParseError) as exc:
        parse_inbound_event(_setup_payload(sessionId="   "))
    assert exc.value.category == "validation_error"


def test_setup_blank_call_sid_rejected() -> None:
    with pytest.raises(ConversationRelayParseError) as exc:
        parse_inbound_event(_setup_payload(callSid=""))
    assert exc.value.category == "validation_error"


def test_setup_blank_account_sid_rejected() -> None:
    with pytest.raises(ConversationRelayParseError) as exc:
        parse_inbound_event(_setup_payload(accountSid="  "))
    assert exc.value.category == "validation_error"


# ---------------------------------------------------------------------------
# SetupEvent — alias / dump shape
# ---------------------------------------------------------------------------


def test_setup_alias_names_match_camel_case() -> None:
    event = parse_inbound_event(_setup_payload())
    assert isinstance(event, SetupEvent)
    dumped = event.model_dump(by_alias=True)
    assert "sessionId" in dumped
    assert "callSid" in dumped
    assert "accountSid" in dumped
    assert "from" in dumped
    assert "to" in dumped
    assert "forwardedFrom" in dumped
    assert "callType" in dumped
    assert "callerName" in dumped
    assert "direction" in dumped
    assert "callStatus" in dumped
    assert "parentCallSid" in dumped
    assert "customParameters" in dumped


# ---------------------------------------------------------------------------
# SetupEvent — extra-field ignored (forward-compatibility)
# ---------------------------------------------------------------------------


def test_setup_ignores_extra_fields() -> None:
    """Twilio may add fields in future; extra fields must be safely ignored."""
    data = json.loads(_setup_payload())
    data["newFutureField"] = "some_value"
    event = parse_inbound_event(json.dumps(data))
    assert isinstance(event, SetupEvent)
    assert not hasattr(event, "newFutureField")
    assert "newFutureField" not in event.model_dump()


# ---------------------------------------------------------------------------
# PromptEvent — valid
# ---------------------------------------------------------------------------


def test_valid_prompt_parses() -> None:
    event = parse_inbound_event(_prompt_payload())
    assert isinstance(event, PromptEvent)
    assert event.voice_prompt == "Hi! Can you tell me about life?"
    assert event.lang == "en-US"
    assert event.last is True


def test_prompt_last_false() -> None:
    event = parse_inbound_event(_prompt_payload(last=False))
    assert isinstance(event, PromptEvent)
    assert event.last is False


def test_prompt_empty_voice_prompt_accepted() -> None:
    """Twilio does not document a non-empty restriction on voicePrompt."""
    event = parse_inbound_event(_prompt_payload(voicePrompt=""))
    assert isinstance(event, PromptEvent)
    assert event.voice_prompt == ""


def test_prompt_at_max_length_accepted() -> None:
    at_limit = "x" * MAX_VOICE_PROMPT_LENGTH
    event = parse_inbound_event(_prompt_payload(voicePrompt=at_limit))
    assert isinstance(event, PromptEvent)
    assert len(event.voice_prompt) == MAX_VOICE_PROMPT_LENGTH


# ---------------------------------------------------------------------------
# PromptEvent — required fields
# ---------------------------------------------------------------------------


def test_prompt_missing_voice_prompt_rejected() -> None:
    data = json.loads(_prompt_payload())
    del data["voicePrompt"]
    with pytest.raises(ConversationRelayParseError) as exc:
        parse_inbound_event(json.dumps(data))
    assert exc.value.category == "validation_error"


def test_prompt_missing_lang_rejected() -> None:
    data = json.loads(_prompt_payload())
    del data["lang"]
    with pytest.raises(ConversationRelayParseError) as exc:
        parse_inbound_event(json.dumps(data))
    assert exc.value.category == "validation_error"


def test_prompt_missing_last_rejected() -> None:
    data = json.loads(_prompt_payload())
    del data["last"]
    with pytest.raises(ConversationRelayParseError) as exc:
        parse_inbound_event(json.dumps(data))
    assert exc.value.category == "validation_error"


# ---------------------------------------------------------------------------
# PromptEvent — strict types
# ---------------------------------------------------------------------------


def test_prompt_last_string_rejected() -> None:
    """last='true' (string) must be rejected; strict bool required."""
    with pytest.raises(ConversationRelayParseError) as exc:
        parse_inbound_event(_prompt_payload(last="true"))
    assert exc.value.category == "validation_error"


def test_prompt_last_int_rejected() -> None:
    with pytest.raises(ConversationRelayParseError) as exc:
        parse_inbound_event(_prompt_payload(last=1))
    assert exc.value.category == "validation_error"


def test_prompt_oversized_voice_prompt_rejected() -> None:
    oversized = "x" * (MAX_VOICE_PROMPT_LENGTH + 1)
    with pytest.raises(ConversationRelayParseError) as exc:
        parse_inbound_event(_prompt_payload(voicePrompt=oversized))
    assert exc.value.category == "validation_error"


def test_prompt_ignores_extra_fields() -> None:
    data = json.loads(_prompt_payload())
    data["extra"] = "nope"
    event = parse_inbound_event(json.dumps(data))
    assert isinstance(event, PromptEvent)
    assert not hasattr(event, "extra")
    assert "extra" not in event.model_dump()


# ---------------------------------------------------------------------------
# PromptEvent — alias shape
# ---------------------------------------------------------------------------


def test_prompt_alias_dump_shape() -> None:
    event = parse_inbound_event(_prompt_payload())
    assert isinstance(event, PromptEvent)
    dumped = event.model_dump(by_alias=True)
    assert "voicePrompt" in dumped
    assert "lang" in dumped
    assert "last" in dumped
    assert dumped["type"] == "prompt"


# ---------------------------------------------------------------------------
# InterruptEvent — valid
# ---------------------------------------------------------------------------


def test_valid_interrupt_parses() -> None:
    event = parse_inbound_event(_interrupt_payload())
    assert isinstance(event, InterruptEvent)
    assert event.utterance_until_interrupt == "Life is a complex set of"
    assert event.duration_until_interrupt_ms == 460


def test_interrupt_zero_duration_accepted() -> None:
    event = parse_inbound_event(_interrupt_payload(durationUntilInterruptMs=0))
    assert isinstance(event, InterruptEvent)
    assert event.duration_until_interrupt_ms == 0


# ---------------------------------------------------------------------------
# InterruptEvent — required fields
# ---------------------------------------------------------------------------


def test_interrupt_missing_utterance_rejected() -> None:
    data = json.loads(_interrupt_payload())
    del data["utteranceUntilInterrupt"]
    with pytest.raises(ConversationRelayParseError) as exc:
        parse_inbound_event(json.dumps(data))
    assert exc.value.category == "validation_error"


def test_interrupt_missing_duration_rejected() -> None:
    data = json.loads(_interrupt_payload())
    del data["durationUntilInterruptMs"]
    with pytest.raises(ConversationRelayParseError) as exc:
        parse_inbound_event(json.dumps(data))
    assert exc.value.category == "validation_error"


# ---------------------------------------------------------------------------
# InterruptEvent — strict types and bounds
# ---------------------------------------------------------------------------


def test_interrupt_duration_string_rejected() -> None:
    """durationUntilInterruptMs='460' (string) must be rejected; strict int."""
    with pytest.raises(ConversationRelayParseError) as exc:
        parse_inbound_event(_interrupt_payload(durationUntilInterruptMs="460"))
    assert exc.value.category == "validation_error"


def test_interrupt_negative_duration_rejected() -> None:
    with pytest.raises(ConversationRelayParseError) as exc:
        parse_inbound_event(_interrupt_payload(durationUntilInterruptMs=-1))
    assert exc.value.category == "validation_error"


def test_interrupt_oversized_utterance_rejected() -> None:
    oversized = "y" * (MAX_UTTERANCE_LENGTH + 1)
    with pytest.raises(ConversationRelayParseError) as exc:
        parse_inbound_event(_interrupt_payload(utteranceUntilInterrupt=oversized))
    assert exc.value.category == "validation_error"


def test_interrupt_utterance_at_max_accepted() -> None:
    at_limit = "z" * MAX_UTTERANCE_LENGTH
    event = parse_inbound_event(_interrupt_payload(utteranceUntilInterrupt=at_limit))
    assert isinstance(event, InterruptEvent)
    assert len(event.utterance_until_interrupt) == MAX_UTTERANCE_LENGTH


def test_interrupt_ignores_extra_fields() -> None:
    data = json.loads(_interrupt_payload())
    data["surprise"] = True
    event = parse_inbound_event(json.dumps(data))
    assert isinstance(event, InterruptEvent)
    assert not hasattr(event, "surprise")
    assert "surprise" not in event.model_dump()


# ---------------------------------------------------------------------------
# DtmfEvent — valid
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("digit", ["0", "1", "5", "9", "*", "#"])
def test_valid_dtmf_digits(digit: str) -> None:
    event = parse_inbound_event(_dtmf_payload(digit=digit))
    assert isinstance(event, DtmfEvent)
    assert event.digit == digit


# ---------------------------------------------------------------------------
# DtmfEvent — invalid
# ---------------------------------------------------------------------------


def test_dtmf_invalid_letter_rejected() -> None:
    with pytest.raises(ConversationRelayParseError) as exc:
        parse_inbound_event(_dtmf_payload(digit="X"))
    assert exc.value.category == "validation_error"


def test_dtmf_empty_digit_rejected() -> None:
    with pytest.raises(ConversationRelayParseError) as exc:
        parse_inbound_event(_dtmf_payload(digit=""))
    assert exc.value.category == "validation_error"


def test_dtmf_multi_char_rejected() -> None:
    with pytest.raises(ConversationRelayParseError) as exc:
        parse_inbound_event(_dtmf_payload(digit="12"))
    assert exc.value.category == "validation_error"


def test_dtmf_missing_digit_rejected() -> None:
    with pytest.raises(ConversationRelayParseError) as exc:
        parse_inbound_event(json.dumps({"type": "dtmf"}))
    assert exc.value.category == "validation_error"


def test_dtmf_ignores_extra_fields() -> None:
    data = json.loads(_dtmf_payload())
    data["extra"] = 1
    event = parse_inbound_event(json.dumps(data))
    assert isinstance(event, DtmfEvent)
    assert not hasattr(event, "extra")
    assert "extra" not in event.model_dump()


# ---------------------------------------------------------------------------
# ErrorEvent — valid
# ---------------------------------------------------------------------------


def test_valid_error_parses() -> None:
    event = parse_inbound_event(_error_payload())
    assert isinstance(event, ErrorEvent)
    assert "Invalid message" in event.description


def test_error_at_max_length_accepted() -> None:
    at_limit = "E" * MAX_ERROR_DESCRIPTION_LENGTH
    event = parse_inbound_event(_error_payload(description=at_limit))
    assert isinstance(event, ErrorEvent)
    assert len(event.description) == MAX_ERROR_DESCRIPTION_LENGTH


# ---------------------------------------------------------------------------
# ErrorEvent — required and bounded
# ---------------------------------------------------------------------------


def test_error_missing_description_rejected() -> None:
    """description is a required field."""
    with pytest.raises(ConversationRelayParseError) as exc:
        parse_inbound_event(json.dumps({"type": "error"}))
    assert exc.value.category == "validation_error"


def test_error_oversized_description_rejected_not_truncated() -> None:
    """Oversized descriptions must be rejected, not silently truncated."""
    long_desc = "X" * (MAX_ERROR_DESCRIPTION_LENGTH + 1)
    with pytest.raises(ConversationRelayParseError) as exc:
        parse_inbound_event(_error_payload(description=long_desc))
    assert exc.value.category == "validation_error"


def test_error_ignores_extra_fields() -> None:
    data = json.loads(_error_payload())
    data["code"] = 500
    event = parse_inbound_event(json.dumps(data))
    assert isinstance(event, ErrorEvent)
    assert not hasattr(event, "code")
    assert "code" not in event.model_dump()


# ---------------------------------------------------------------------------
# Disconnect is NOT a protocol event
# ---------------------------------------------------------------------------


def test_disconnect_json_is_unknown_type() -> None:
    """disconnect is not a ConversationRelay JSON event; parser must reject."""
    with pytest.raises(ConversationRelayParseError) as exc:
        parse_inbound_event(json.dumps({"type": "disconnect"}))
    assert exc.value.category == "unknown_type"


def test_unknown_type_value_not_echoed_in_error() -> None:
    """The unknown type value must not appear in the error message."""
    with pytest.raises(ConversationRelayParseError) as exc:
        parse_inbound_event(json.dumps({"type": "book_appointment"}))
    assert exc.value.category == "unknown_type"
    assert "book_appointment" not in str(exc.value)


# ---------------------------------------------------------------------------
# Parser — structural error branches
# ---------------------------------------------------------------------------


def test_parser_empty_string() -> None:
    with pytest.raises(ConversationRelayParseError) as exc:
        parse_inbound_event("")
    assert exc.value.category == "empty_payload"


def test_parser_whitespace_only() -> None:
    with pytest.raises(ConversationRelayParseError) as exc:
        parse_inbound_event("   ")
    assert exc.value.category == "empty_payload"


def test_parser_malformed_json() -> None:
    with pytest.raises(ConversationRelayParseError) as exc:
        parse_inbound_event("{not valid json")
    assert exc.value.category == "malformed_json"


def test_parser_json_array() -> None:
    with pytest.raises(ConversationRelayParseError) as exc:
        parse_inbound_event('["type", "setup"]')
    assert exc.value.category == "not_object"


def test_parser_json_null() -> None:
    with pytest.raises(ConversationRelayParseError) as exc:
        parse_inbound_event("null")
    assert exc.value.category == "not_object"


def test_parser_json_string() -> None:
    with pytest.raises(ConversationRelayParseError) as exc:
        parse_inbound_event('"just a string"')
    assert exc.value.category == "not_object"


def test_parser_json_number() -> None:
    with pytest.raises(ConversationRelayParseError) as exc:
        parse_inbound_event("42")
    assert exc.value.category == "not_object"


def test_parser_missing_type_field() -> None:
    with pytest.raises(ConversationRelayParseError) as exc:
        parse_inbound_event(json.dumps({"voicePrompt": "hello"}))
    assert exc.value.category == "missing_type"


def test_parser_non_string_type_field() -> None:
    with pytest.raises(ConversationRelayParseError) as exc:
        parse_inbound_event(json.dumps({"type": 42}))
    assert exc.value.category == "missing_type"


def test_parser_empty_string_type_field() -> None:
    with pytest.raises(ConversationRelayParseError) as exc:
        parse_inbound_event(json.dumps({"type": ""}))
    assert exc.value.category == "missing_type"


# ---------------------------------------------------------------------------
# Parser security — no raw payload in error messages
# ---------------------------------------------------------------------------


def test_validation_error_message_no_caller_text() -> None:
    """Sensitive caller data must never appear in parse error messages."""
    sensitive_payload = json.dumps(
        {
            "type": "prompt",
            "voicePrompt": "SENSITIVE_CALLER_DATA",
            "lang": "en-US",
            "last": "not_a_bool",  # triggers strict bool rejection
        }
    )
    with pytest.raises(ConversationRelayParseError) as exc:
        parse_inbound_event(sensitive_payload)
    assert "SENSITIVE_CALLER_DATA" not in str(exc.value)
    assert sensitive_payload not in str(exc.value)


def test_unknown_type_error_no_full_raw_payload() -> None:
    large_payload = json.dumps({"type": "unknown_type_xyz", "data": "A" * 500})
    with pytest.raises(ConversationRelayParseError) as exc:
        parse_inbound_event(large_payload)
    assert "A" * 500 not in str(exc.value)


# ---------------------------------------------------------------------------
# Parser security — exception chaining suppressed
# ---------------------------------------------------------------------------


def test_malformed_json_cause_is_none() -> None:
    """JSONDecodeError chaining must be suppressed with from None."""
    with pytest.raises(ConversationRelayParseError) as exc:
        parse_inbound_event("{bad json")
    assert exc.value.__cause__ is None


def test_validation_error_cause_is_none() -> None:
    """Pydantic ValidationError chaining must be suppressed with from None."""
    with pytest.raises(ConversationRelayParseError) as exc:
        parse_inbound_event(_prompt_payload(last="not_a_bool"))
    assert exc.value.__cause__ is None


# ---------------------------------------------------------------------------
# Parser security — traceback contains no sensitive data
# ---------------------------------------------------------------------------


def test_traceback_contains_no_sensitive_caller_text() -> None:
    sensitive_payload = json.dumps(
        {
            "type": "prompt",
            "voicePrompt": "SECRET_CALLER_UTTERANCE",
            "lang": "en-US",
            "last": "not_a_bool",
        }
    )
    with pytest.raises(ConversationRelayParseError) as exc:
        parse_inbound_event(sensitive_payload)
    lines = traceback.format_exception(type(exc.value), exc.value, exc.value.__traceback__)
    full_traceback = "".join(lines)
    assert "SECRET_CALLER_UTTERANCE" not in full_traceback
    assert sensitive_payload not in full_traceback


def test_traceback_contains_no_raw_payload_for_validation() -> None:
    payload = json.dumps(
        {
            "type": "setup",
            "sessionId": "",
            "callSid": "CA123",
            "accountSid": "AC123",
            "from": "+1555",
            "to": "+1666",
            "customParameters": {"secret": "HIDDEN_VALUE"},
        }
    )
    with pytest.raises(ConversationRelayParseError) as exc:
        parse_inbound_event(payload)
    lines = traceback.format_exception(type(exc.value), exc.value, exc.value.__traceback__)
    full_traceback = "".join(lines)
    assert "HIDDEN_VALUE" not in full_traceback


# ---------------------------------------------------------------------------
# Parser security — caplog contains no raw payload
# ---------------------------------------------------------------------------


def test_caplog_contains_no_raw_payload(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Parser itself performs no logging, so caplog must remain empty."""
    sensitive_payload = json.dumps(
        {
            "type": "prompt",
            "voicePrompt": "PRIVATE_CALLER_TEXT",
            "lang": "en-US",
            "last": "not_a_bool",
        }
    )
    with caplog.at_level(logging.DEBUG), pytest.raises(ConversationRelayParseError):
        parse_inbound_event(sensitive_payload)
    all_log_text = " ".join(record.getMessage() for record in caplog.records)
    assert "PRIVATE_CALLER_TEXT" not in all_log_text
    assert sensitive_payload not in all_log_text


# ---------------------------------------------------------------------------
# Outbound TextTokenMessage — exact JSON shape
# ---------------------------------------------------------------------------


def test_text_token_exact_json_minimal() -> None:
    msg = TextTokenMessage(token="Hello world!")
    raw = msg.to_json()
    payload = json.loads(raw)
    assert payload == {"type": "text", "token": "Hello world!", "last": False}


def test_text_token_optional_fields_omitted() -> None:
    msg = TextTokenMessage(token="Hi")
    payload = json.loads(msg.to_json())
    assert "lang" not in payload
    assert "interruptible" not in payload
    assert "preemptible" not in payload


def test_text_token_all_optional_fields_present() -> None:
    msg = TextTokenMessage(
        token="Hola!",
        last=False,
        lang="es-ES",
        interruptible=True,
        preemptible=False,
    )
    payload = json.loads(msg.to_json())
    assert payload["lang"] == "es-ES"
    assert payload["interruptible"] is True
    assert payload["preemptible"] is False


def test_text_token_last_true() -> None:
    msg = TextTokenMessage(token="Goodbye.", last=True)
    payload = json.loads(msg.to_json())
    assert payload["last"] is True


def test_text_token_empty_string_accepted() -> None:
    """No documented requirement forbids an empty token string."""
    msg = TextTokenMessage(token="")
    assert msg.token == ""
    payload = json.loads(msg.to_json())
    assert payload["token"] == ""


# ---------------------------------------------------------------------------
# Outbound TextTokenMessage — rejection
# ---------------------------------------------------------------------------


def test_text_token_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        TextTokenMessage(token="Hi", undocumented_field="oops")  # type: ignore[call-arg]


def test_text_token_rejects_wrong_type_value() -> None:
    with pytest.raises(ValidationError):
        TextTokenMessage(type="play", token="Hi")  # type: ignore[arg-type]


def test_text_token_type_is_always_text() -> None:
    msg = TextTokenMessage(token="Hi there.")
    assert msg.type == "text"
    payload = json.loads(msg.to_json())
    assert payload["type"] == "text"


def test_text_token_rejects_last_string() -> None:
    """last='false' (string) must be rejected; strict bool required."""
    with pytest.raises(ValidationError):
        TextTokenMessage(token="Hi", last="false")  # type: ignore[arg-type]


def test_text_token_rejects_interruptible_int() -> None:
    """interruptible=1 (int) must be rejected; strict bool required."""
    with pytest.raises(ValidationError):
        TextTokenMessage(token="Hi", interruptible=1)  # type: ignore[arg-type]


def test_text_token_rejects_preemptible_string() -> None:
    """preemptible='true' (string) must be rejected; strict bool required."""
    with pytest.raises(ValidationError):
        TextTokenMessage(token="Hi", preemptible="true")  # type: ignore[arg-type]


def test_text_token_rejects_token_none() -> None:
    """token=None must be rejected; str does not accept None."""
    with pytest.raises(ValidationError):
        TextTokenMessage(token=None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Enum sanity
# ---------------------------------------------------------------------------


def test_event_type_enum_values() -> None:
    assert ConversationRelayEventType.SETUP.value == "setup"
    assert ConversationRelayEventType.PROMPT.value == "prompt"
    assert ConversationRelayEventType.INTERRUPT.value == "interrupt"
    assert ConversationRelayEventType.DTMF.value == "dtmf"
    assert ConversationRelayEventType.ERROR.value == "error"
    assert not hasattr(ConversationRelayEventType, "DISCONNECT")
