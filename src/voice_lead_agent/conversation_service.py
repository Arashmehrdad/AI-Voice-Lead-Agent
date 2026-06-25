"""Deterministic Stage 4 conversation-turn processor.

This module processes one completed caller turn using deterministic safety
checks first, then optionally calls Gemini with redacted context only. It owns
no persistence, settings, Twilio, or database state.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from pydantic import ValidationError

from voice_lead_agent.conversation_models import (
    DETERMINISTIC_ACTIONS,
    ConversationAction,
    ConversationContext,
    ConversationDecision,
    ConversationTurn,
    parse_decision,
)
from voice_lead_agent.conversation_policy import (
    bound_user_input,
    detect_caller_intent,
    redact_turn,
    safe_fallback_decision,
)
from voice_lead_agent.gemini_client import GeminiConversationClient, GeminiError

_log = logging.getLogger(__name__)

# Fixed application-owned spoken text for deterministic paths.
OPT_OUT_SPOKEN_TEXT = (
    "I understand. I have recognised your request to stop these automated calls. "
    "This call will end now. Goodbye."
)
DEFAULT_FALLBACK_SPOKEN_TEXT = (
    "I'm sorry, I'm having a little trouble right now. Could you please repeat that?"
)


@dataclass(frozen=True)
class ConversationTurnResult:
    """Immutable result of processing one caller turn."""

    decision: ConversationDecision
    caller_turn: ConversationTurn
    gemini_called: bool


def _build_transcript(history: Sequence[ConversationTurn], current_turn: ConversationTurn) -> str:
    """Build a deterministic readable transcript from redacted turns only."""
    lines: list[str] = []
    for index, turn in enumerate(history, start=1):
        lines.append(f"Turn {index} {turn.role}: {turn.redacted_text}")
    lines.append(f"Turn {len(history) + 1} {current_turn.role}: {current_turn.redacted_text}")
    return "\n".join(lines)


def _is_bounded_reason_code(value: str) -> bool:
    if not value or len(value) > 64 or not value[0].islower():
        return False
    return all(ch.islower() or ch.isdigit() or ch == "_" for ch in value)


def _fallback_reason_code(raw_value: str, *, default: str) -> str:
    value = raw_value.strip()
    if _is_bounded_reason_code(value):
        return value
    return default


async def process_turn(
    *,
    client: GeminiConversationClient,
    system_instruction: str,
    history: Sequence[ConversationTurn],
    caller_text: str,
    turn_number: int,
    human_help_text: str,
    fallback_text: str,
) -> ConversationTurnResult:
    """Process one completed caller turn and return a validated decision."""
    bounded = bound_user_input(caller_text)
    intent = detect_caller_intent(bounded)
    caller_turn = redact_turn("user", bounded)

    if intent.is_opt_out:
        decision = ConversationDecision(
            action=ConversationAction.OPT_OUT,
            spoken_response=OPT_OUT_SPOKEN_TEXT,
            conversation_summary=None,
            structured_facts={"deterministic": True},
            reason_code="caller_opt_out",
        )
        return ConversationTurnResult(
            decision=decision, caller_turn=caller_turn, gemini_called=False
        )

    if intent.is_escalation:
        decision = ConversationDecision(
            action=ConversationAction.HUMAN_ESCALATION,
            spoken_response=human_help_text,
            conversation_summary=None,
            structured_facts={"deterministic": True},
            reason_code="caller_requested_human",
        )
        return ConversationTurnResult(
            decision=decision, caller_turn=caller_turn, gemini_called=False
        )

    context = ConversationContext(
        system_instruction=system_instruction,
        conversation_text=_build_transcript(history, caller_turn),
        turn_number=turn_number,
    )

    try:
        raw = await client.generate_decision(context)
    except GeminiError as exc:
        reason_code = _fallback_reason_code(exc.category, default="gemini_failure")
        _log.warning(
            "conversation_turn: Gemini failure category=%s, returning safe fallback",
            reason_code,
        )
        decision = safe_fallback_decision(reason_code=reason_code, spoken_response=fallback_text)
        return ConversationTurnResult(
            decision=decision, caller_turn=caller_turn, gemini_called=True
        )

    if not raw.strip():
        _log.warning("conversation_turn: empty Gemini output, returning safe fallback")
        decision = safe_fallback_decision(reason_code="gemini_empty", spoken_response=fallback_text)
        return ConversationTurnResult(
            decision=decision, caller_turn=caller_turn, gemini_called=True
        )

    try:
        decision = parse_decision(raw)
    except ValidationError:
        _log.warning("conversation_turn: invalid Gemini output, returning safe fallback")
        decision = safe_fallback_decision(
            reason_code="invalid_model_output", spoken_response=fallback_text
        )
        return ConversationTurnResult(
            decision=decision, caller_turn=caller_turn, gemini_called=True
        )

    if decision.action in DETERMINISTIC_ACTIONS:
        _log.warning(
            "conversation_turn: Gemini returned untrusted deterministic action=%s",
            decision.action.value,
        )
        decision = safe_fallback_decision(
            reason_code="untrusted_deterministic_action",
            spoken_response=fallback_text,
        )

    return ConversationTurnResult(decision=decision, caller_turn=caller_turn, gemini_called=True)


__all__ = [
    "ConversationTurnResult",
    "DEFAULT_FALLBACK_SPOKEN_TEXT",
    "OPT_OUT_SPOKEN_TEXT",
    "process_turn",
]
