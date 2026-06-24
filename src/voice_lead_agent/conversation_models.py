"""Stage 4 conversation domain models.

Gemini returns data only. These models define the strict, application-owned
schema for every model decision and the bounded context object the application
assembles before calling the model. Gemini never receives credentials,
repositories, provider tools, or callable side effects through these types.
"""

from __future__ import annotations

import enum
import json
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

# Bounded field limits keep voice responses concise and storage predictable.
MAX_SPOKEN_RESPONSE_LENGTH = 500
MAX_SUMMARY_LENGTH = 500
MAX_FACTS_KEYS = 20
MAX_FACTS_STRING_LENGTH = 200
MAX_FACTS_DEPTH = 3
MAX_FACTS_JSON_LENGTH = 2000
MAX_REASON_CODE_LENGTH = 64

# Structured-facts values are intentionally constrained to JSON primitives and
# shallow containers so qualification facts cannot carry arbitrary payloads.
_ALLOWED_FACT_SCALAR_TYPES = (str, bool, int, float, type(None))


class ConversationAction(enum.StrEnum):
    """Finite, application-controlled actions a conversation decision may take.

    The string values are the wire format exchanged with the model and persisted
    in durable state. No other action string is accepted.
    """

    CONTINUE = "continue"
    QUALIFIED = "qualified"
    NOT_QUALIFIED = "not_qualified"
    OPT_OUT = "opt_out"
    HUMAN_ESCALATION = "human_escalation"
    END_CALL = "end_call"


# Actions whose side effects are driven by deterministic application detection
# of the caller's own words rather than by model authority.
DETERMINISTIC_ACTIONS: frozenset[ConversationAction] = frozenset(
    {ConversationAction.OPT_OUT, ConversationAction.HUMAN_ESCALATION}
)


@dataclass(frozen=True)
class ConversationTurn:
    """One redacted turn of the conversation, safe to send to the model."""

    role: str
    redacted_text: str


@dataclass(frozen=True)
class ConversationContext:
    """Deterministic inputs the application sends to the model.

    The application builds ``system_instruction`` and ``conversation_text`` from
    approved policy and already-redacted turns. The model receives plain text
    only; it never receives secrets, repositories, or tools through this type.
    """

    system_instruction: str
    conversation_text: str
    turn_number: int


class ConversationDecision(BaseModel):
    """Strict structured decision returned by the model and validated by the app.

    Extra fields are forbidden so the model cannot smuggle provider, database,
    or side-effect instructions into application state. All free-text fields are
    bounded and normalized before persistence.
    """

    model_config = ConfigDict(extra="forbid")

    action: ConversationAction
    spoken_response: str
    conversation_summary: str | None = None
    structured_facts: dict[str, object] = Field(default_factory=dict)
    reason_code: str | None = None

    @field_validator("spoken_response")
    @classmethod
    def _validate_spoken_response(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("spoken_response must not be empty")
        if len(text) > MAX_SPOKEN_RESPONSE_LENGTH:
            raise ValueError("spoken_response exceeds maximum length")
        return text

    @field_validator("conversation_summary")
    @classmethod
    def _validate_summary(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        if not text:
            return None
        if len(text) > MAX_SUMMARY_LENGTH:
            raise ValueError("conversation_summary exceeds maximum length")
        return text

    @field_validator("reason_code")
    @classmethod
    def _validate_reason_code(cls, value: str | None) -> str | None:
        if value is None:
            return None
        token = value.strip()
        if not token:
            return None
        if len(token) > MAX_REASON_CODE_LENGTH or not _is_reason_code(token):
            raise ValueError("reason_code must be a lowercase token")
        return token

    @field_validator("structured_facts")
    @classmethod
    def _validate_facts(cls, value: dict[str, object]) -> dict[str, object]:
        _validate_fact_object(value)
        encoded = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
        if len(encoded) > MAX_FACTS_JSON_LENGTH:
            raise ValueError("structured_facts payload exceeds maximum size")
        return value


def _is_reason_code(token: str) -> bool:
    if not token[0].islower():
        return False
    return all(ch.islower() or ch.isdigit() or ch == "_" for ch in token)


def _validate_fact_object(value: dict[str, object], *, depth: int = 0) -> None:
    if depth > MAX_FACTS_DEPTH:
        raise ValueError("structured_facts nesting too deep")
    if len(value) > MAX_FACTS_KEYS:
        raise ValueError("structured_facts has too many keys")
    for key, item in value.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError("structured_facts keys must be non-empty strings")
        _validate_fact_value(item, depth=depth)


def _validate_fact_value(value: object, *, depth: int = 0) -> None:
    if depth > MAX_FACTS_DEPTH:
        raise ValueError("structured_facts nesting too deep")
    if isinstance(value, bool | int | float | None.__class__):
        return
    if isinstance(value, str):
        if len(value) > MAX_FACTS_STRING_LENGTH:
            raise ValueError("structured_facts string value too long")
        return
    if isinstance(value, dict):
        _validate_fact_object(_as_fact_dict(value), depth=depth + 1)
        return
    if isinstance(value, list):
        if len(value) > MAX_FACTS_KEYS:
            raise ValueError("structured_facts list too long")
        for item in value:
            _validate_fact_value(item, depth=depth + 1)
        return
    raise ValueError("structured_facts contains unsupported value type")


def _as_fact_dict(value: dict[object, object]) -> dict[str, object]:
    coerced: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise ValueError("structured_facts keys must be strings")
        coerced[key] = item
    return coerced


def parse_decision(raw: str) -> ConversationDecision:
    """Parse and structurally validate a model JSON response.

    Raises :class:`pydantic.ValidationError` for unknown actions, malformed
    JSON, oversized fields, or smuggled extra fields.
    """

    return ConversationDecision.model_validate_json(raw)


__all__ = [
    "ConversationAction",
    "ConversationContext",
    "ConversationDecision",
    "ConversationTurn",
    "DETERMINISTIC_ACTIONS",
    "MAX_REASON_CODE_LENGTH",
    "MAX_SPOKEN_RESPONSE_LENGTH",
    "MAX_SUMMARY_LENGTH",
    "ValidationError",
    "parse_decision",
]
