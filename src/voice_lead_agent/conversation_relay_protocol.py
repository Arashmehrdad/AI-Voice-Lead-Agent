"""Twilio ConversationRelay WebSocket protocol models and parser.

This module owns the strict typed representations of every **inbound** JSON
message that Twilio sends over the ConversationRelay WebSocket, plus the
single **outbound** text-token message used to make Twilio speak.

Inbound JSON types: setup, prompt, interrupt, dtmf, error.
WebSocket lifecycle events (connect, disconnect) are not part of this module.

All field names and JSON shapes are taken directly from the official Twilio
documentation:
  https://www.twilio.com/docs/voice/conversationrelay/websocket-messages

Do not add fields that are not present in that documentation.

The parser never logs the full raw payload.  Callers receive either a typed
event or a :class:`ConversationRelayParseError` with a bounded, non-sensitive
message.
"""

from __future__ import annotations

import enum
import json
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
)

# ---------------------------------------------------------------------------
# Field limits
# ---------------------------------------------------------------------------

# Prompt text from the caller passes through STT; keep a generous but bounded
# limit to prevent unbounded memory use if a provider behaves unexpectedly.
MAX_VOICE_PROMPT_LENGTH = 1000

# Error descriptions from Twilio are provider-generated; cap them.
MAX_ERROR_DESCRIPTION_LENGTH = 500

# Interrupt utterance is a partial transcript; keep the same limit as prompts.
MAX_UTTERANCE_LENGTH = 1000


# ---------------------------------------------------------------------------
# Inbound event-type enum
# ---------------------------------------------------------------------------


class ConversationRelayEventType(enum.StrEnum):
    """Finite set of inbound JSON event types documented by Twilio."""

    SETUP = "setup"
    PROMPT = "prompt"
    INTERRUPT = "interrupt"
    DTMF = "dtmf"
    ERROR = "error"


# ---------------------------------------------------------------------------
# Individual inbound event models
# ---------------------------------------------------------------------------


class SetupEvent(BaseModel):
    """Sent immediately after the WebSocket connection is established.

    ``sessionId``, ``callSid``, ``accountSid``, ``from``, and ``to`` are
    required.  ``sessionId``, ``callSid``, and ``accountSid`` must not be
    blank.  Unknown extra fields are accepted and ignored.
    """

    model_config = ConfigDict(extra="ignore", strict=True)

    type: Literal["setup"] = "setup"
    session_id: str = Field(alias="sessionId")
    call_sid: str = Field(alias="callSid")
    account_sid: str = Field(alias="accountSid")
    from_number: str = Field(alias="from")
    to_number: str = Field(alias="to")
    forwarded_from: str = Field(default="", alias="forwardedFrom")
    call_type: str = Field(default="", alias="callType")
    caller_name: str = Field(default="", alias="callerName")
    direction: str = Field(default="", alias="direction")
    call_status: str = Field(default="", alias="callStatus")
    parent_call_sid: str = Field(default="", alias="parentCallSid")
    custom_parameters: dict[str, str] = Field(default_factory=dict, alias="customParameters")

    @field_validator("session_id", "call_sid", "account_sid")
    @classmethod
    def _require_non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value


class PromptEvent(BaseModel):
    """Transcribed caller speech, streamed as the caller talks.

    ``voicePrompt``, ``lang``, and ``last`` are required.  ``last`` is a
    strict boolean.  ``voicePrompt`` is bounded but may be empty (Twilio
    does not document a non-empty restriction).
    """

    model_config = ConfigDict(extra="ignore", strict=True)

    type: Literal["prompt"] = "prompt"
    voice_prompt: str = Field(alias="voicePrompt")
    lang: str = Field(alias="lang")
    last: bool = Field(alias="last")

    @field_validator("voice_prompt")
    @classmethod
    def _bound_voice_prompt(cls, value: str) -> str:
        if len(value) > MAX_VOICE_PROMPT_LENGTH:
            raise ValueError(f"voicePrompt exceeds maximum length of {MAX_VOICE_PROMPT_LENGTH}")
        return value


class InterruptEvent(BaseModel):
    """Caller started speaking while TTS was playing.

    ``utteranceUntilInterrupt`` and ``durationUntilInterruptMs`` are required.
    ``durationUntilInterruptMs`` must be a strict non-negative integer.
    """

    model_config = ConfigDict(extra="ignore", strict=True)

    type: Literal["interrupt"] = "interrupt"
    utterance_until_interrupt: str = Field(alias="utteranceUntilInterrupt")
    duration_until_interrupt_ms: int = Field(alias="durationUntilInterruptMs")

    @field_validator("utterance_until_interrupt")
    @classmethod
    def _bound_utterance(cls, value: str) -> str:
        if len(value) > MAX_UTTERANCE_LENGTH:
            raise ValueError(
                f"utteranceUntilInterrupt exceeds maximum length of {MAX_UTTERANCE_LENGTH}"
            )
        return value

    @field_validator("duration_until_interrupt_ms")
    @classmethod
    def _non_negative_duration(cls, value: int) -> int:
        if value < 0:
            raise ValueError("durationUntilInterruptMs must be non-negative")
        return value


class DtmfEvent(BaseModel):
    """Caller pressed a key on the phone keypad.

    ``digit`` is required and must be exactly one character from the standard
    telephone keypad: ``0``–``9``, ``*``, or ``#``.
    """

    model_config = ConfigDict(extra="ignore", strict=True)

    type: Literal["dtmf"] = "dtmf"
    digit: str = Field(alias="digit")

    @field_validator("digit")
    @classmethod
    def _validate_digit(cls, value: str) -> str:
        if len(value) != 1 or value not in "0123456789*#":
            raise ValueError("digit must be exactly one of 0-9, *, #")
        return value


class ErrorEvent(BaseModel):
    """An error occurred during the ConversationRelay session.

    ``description`` is required and bounded at 500 characters.  Oversized
    descriptions are rejected, not silently truncated.
    """

    model_config = ConfigDict(extra="ignore", strict=True)

    type: Literal["error"] = "error"
    description: str = Field(alias="description")

    @field_validator("description")
    @classmethod
    def _bound_description(cls, value: str) -> str:
        if len(value) > MAX_ERROR_DESCRIPTION_LENGTH:
            raise ValueError(
                f"description exceeds maximum length of {MAX_ERROR_DESCRIPTION_LENGTH}"
            )
        return value


# ---------------------------------------------------------------------------
# Discriminated union of all inbound event types
# ---------------------------------------------------------------------------

InboundConversationRelayEvent = SetupEvent | PromptEvent | InterruptEvent | DtmfEvent | ErrorEvent


# ---------------------------------------------------------------------------
# Outbound text-token message
# ---------------------------------------------------------------------------


class TextTokenMessage(BaseModel):
    """Outbound message that makes Twilio speak text to the caller.

    Only documented fields are permitted: ``type``, ``token``, ``last``,
    ``lang``, ``interruptible``, ``preemptible``.  Extra fields are
    forbidden.

    ``type`` is always ``"text"``.  ``token`` must be a string (empty
    strings are permitted).  ``last`` defaults to ``False``.  ``None``
    optional fields are excluded from JSON serialisation.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    type: Literal["text"] = "text"
    token: str
    last: bool = False
    lang: str | None = None
    interruptible: bool | None = None
    preemptible: bool | None = None

    def to_json(self) -> str:
        """Serialise to the JSON wire format sent to Twilio.

        ``None`` optional fields are excluded so the payload is minimal.
        """
        return self.model_dump_json(exclude_none=True)


# ---------------------------------------------------------------------------
# Parse error
# ---------------------------------------------------------------------------


class ConversationRelayParseError(Exception):
    """Raised when an inbound WebSocket payload cannot be safely parsed.

    The ``category`` field is a short non-sensitive reason code suitable for
    logging.  The full raw payload is **never** included in the message or
    attributes of this exception.
    """

    def __init__(self, category: str, message: str) -> None:
        self.category = category
        super().__init__(message)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

_EVENT_MODELS: dict[str, type[BaseModel]] = {
    "setup": SetupEvent,
    "prompt": PromptEvent,
    "interrupt": InterruptEvent,
    "dtmf": DtmfEvent,
    "error": ErrorEvent,
}


def parse_inbound_event(
    raw: str,
) -> InboundConversationRelayEvent:
    """Parse a raw WebSocket text payload into a typed inbound event.

    The full raw payload is **never** included in exception messages or
    logged by this function.

    Raises :class:`ConversationRelayParseError` for:

    - empty string;
    - JSON that is not valid or not an object;
    - missing or non-string ``type`` field;
    - unrecognised ``type`` value;
    - field validation failures within a known event type.
    """
    if not raw or not raw.strip():
        raise ConversationRelayParseError("empty_payload", "Received an empty WebSocket message.")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise ConversationRelayParseError(
            "malformed_json", "WebSocket message is not valid JSON."
        ) from None

    if not isinstance(data, dict):
        raise ConversationRelayParseError("not_object", "WebSocket message is not a JSON object.")

    event_type_raw = data.get("type")
    if not isinstance(event_type_raw, str) or not event_type_raw:
        raise ConversationRelayParseError(
            "missing_type", "WebSocket message has no string 'type' field."
        )

    model_cls = _EVENT_MODELS.get(event_type_raw)
    if model_cls is None:
        raise ConversationRelayParseError(
            "unknown_type",
            "Unknown ConversationRelay event type.",
        )

    try:
        return model_cls.model_validate(data)  # type: ignore[return-value]
    except ValidationError:
        raise ConversationRelayParseError(
            "validation_error",
            f"ConversationRelay {event_type_raw!r} event failed validation.",
        ) from None


__all__ = [
    "ConversationRelayEventType",
    "ConversationRelayParseError",
    "DtmfEvent",
    "ErrorEvent",
    "InboundConversationRelayEvent",
    "InterruptEvent",
    "MAX_ERROR_DESCRIPTION_LENGTH",
    "MAX_UTTERANCE_LENGTH",
    "MAX_VOICE_PROMPT_LENGTH",
    "PromptEvent",
    "SetupEvent",
    "TextTokenMessage",
    "parse_inbound_event",
]
