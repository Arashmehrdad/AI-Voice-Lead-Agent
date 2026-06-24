"""Deterministic conversation policy: disclosure, opt-out, escalation, redaction.

All caller text is untrusted. Opt-out and human-escalation are detected
*before* the model is consulted, deterministically, so the model cannot
override caller safety intent. Free text is redacted before it is persisted or
returned to the application.

This module owns no database or provider state. It produces validated
:class:`ConversationDecision` values and redacted strings only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from voice_lead_agent.conversation_models import (
    ConversationAction,
    ConversationDecision,
    ConversationTurn,
)

# --- Bounds --------------------------------------------------------------

MAX_USER_INPUT_LENGTH = 500

# --- Deterministic opt-out and escalation detection ----------------------

# Phrases are matched as whole-word/phrase boundaries inside a normalized
# (lowercased, collapsed whitespace) copy of the caller utterance so that
# punctuation and casing do not defeat detection. Variants are deliberately
# conservative: false negatives are acceptable (the model is still asked),
# false positives are not (we never opt somebody out who did not ask).
OPT_OUT_PHRASES: tuple[str, ...] = (
    "stop calling",
    "stop calling me",
    "do not call me",
    "don't call me",
    "don't call",
    "remove me",
    "remove me from your list",
    "take me off",
    "take me off your list",
    "opt me out",
    "opt out",
    "i do not consent",
    "i don't consent",
    "no longer contact",
    "do not contact me",
    "don't contact me",
    "unsubscribe",
    "stop contacting me",
    "put me on the do not call list",
    "add me to the do not call list",
)

ESCALATION_PHRASES: tuple[str, ...] = (
    "human",
    "real person",
    "real human",
    "speak to someone",
    "speak with someone",
    "speak to a person",
    "speak to a human",
    "talk to a person",
    "talk to a human",
    "talk to someone",
    "call me back",
    "complaint",
    "i want to complain",
    "i dispute",
    "disputed consent",
    "dispute the consent",
    "data request",
    "data subject request",
    "delete my data",
    "legal action",
    "solicitor",
    "lawyer",
)

_WHITESPACE_RE = re.compile(r"\s+")


# Compiled phrase patterns for whole-phrase boundary matching.
# Each phrase is wrapped so it matches as a complete phrase (preceded and
# followed by a non-word character or start/end of string) rather than as a
# substring of a longer word.  This prevents "i want to complain" matching
# inside "i want to complaint".
def _compile_phrase_patterns(phrases: tuple[str, ...]) -> list[tuple[re.Pattern[str], str]]:
    """Return ``(pattern, phrase)`` pairs sorted longest-phrase-first."""
    result = []
    for phrase in sorted(phrases, key=len, reverse=True):
        # Escape the phrase and anchor with word/phrase boundaries.
        escaped = re.escape(phrase)
        pattern = re.compile(r"(?<!\w)" + escaped + r"(?!\w)")
        result.append((pattern, phrase))
    return result


_OPT_OUT_PATTERNS: list[tuple[re.Pattern[str], str]] = _compile_phrase_patterns(OPT_OUT_PHRASES)
_ESCALATION_PATTERNS: list[tuple[re.Pattern[str], str]] = _compile_phrase_patterns(
    ESCALATION_PHRASES
)

# --- Redaction patterns ---------------------------------------------------

# Payment-card-like runs: 12–19 consecutive digits, optionally separated by
# single spaces or hyphens (e.g. "4111 1111 1111 1111").  A leading + is
# excluded so E.164 phone numbers are not mis-classified as cards.
# Redacted *before* phone numbers.
_CARD_RE = re.compile(r"(?<!\+)\b\d(?:[ -]?\d){11,18}\b")

# Phone numbers.  Two accepted forms:
#   • E.164: leading + followed by 7–15 digits (spaces/separators allowed).
#   • Grouped local format: digits in groups joined by dots, dashes, or
#     parentheses — but NOT plain spaces alone, which would also match card
#     numbers that survived the card pass.
_PHONE_RE = re.compile(
    r"\+\d[\d\s().\-]{6,}\d"  # E.164 with + prefix
    r"|\(?\d{2,4}\)?[().\-]\d{2,4}[().\-]\d{2,4}(?:[().\-]\d{1,4})?"  # grouped: non-space sep
)
# A conservative email pattern.
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b", re.UNICODE)
# Bearer/authorization/token-looking secrets.
_TOKEN_RE = re.compile(
    r"(?i)\b(bearer|token|api[_\-\s]?key|secret|password|auth)\b[:=\s]+[A-Za-z0-9._\-/+=]+"
)
# Postcode-like / full address lines: redact UK/US-style postcodes to be safe.
_POSTCODE_RE = re.compile(r"\b[A-Za-z]{1,2}\d[A-Za-z\d]?\s*\d[A-Za-z]{2}\b|\b\d{5}(?:-\d{4})?\b")


@dataclass(frozen=True)
class DetectionResult:
    """Outcome of deterministic caller-intent detection for one turn."""

    is_opt_out: bool = False
    is_escalation: bool = False
    phrase: str | None = None

    @property
    def detected(self) -> bool:
        return self.is_opt_out or self.is_escalation


def normalize_text(value: str) -> str:
    return _WHITESPACE_RE.sub(" ", value.strip().lower())


def bound_user_input(value: str) -> str:
    """Truncate overly long caller input before any processing."""
    return value.strip()[:MAX_USER_INPUT_LENGTH]


def detect_opt_out(text: str) -> bool:
    normalized = normalize_text(text)
    if not normalized:
        return False
    return any(pattern.search(normalized) for pattern, _ in _OPT_OUT_PATTERNS)


def detect_escalation(text: str) -> str | None:
    """Return the matched escalation phrase, or ``None`` if not an escalation.

    Phrases are checked longest-first so that ``"real human"`` is returned in
    preference to the shorter ``"human"`` substring it contains. Whole-phrase
    boundary matching prevents ``"i want to complain"`` matching inside
    ``"i want to complaint"``.
    """
    normalized = normalize_text(text)
    if not normalized:
        return None
    for pattern, phrase in _ESCALATION_PATTERNS:
        if pattern.search(normalized):
            return phrase
    return None


def detect_caller_intent(text: str) -> DetectionResult:
    """Detect opt-out and human-escalation intent deterministically.

    Opt-out takes precedence over escalation: a request to stop calling is
    treated as an opt-out regardless of other wording. Within each category,
    longer phrases are matched before shorter sub-phrases, and whole-phrase
    boundary matching prevents spurious substring matches.
    """
    normalized = normalize_text(text)
    if not normalized:
        return DetectionResult()

    for pattern, phrase in _OPT_OUT_PATTERNS:
        if pattern.search(normalized):
            return DetectionResult(is_opt_out=True, phrase=phrase)

    for pattern, phrase in _ESCALATION_PATTERNS:
        if pattern.search(normalized):
            return DetectionResult(is_escalation=True, phrase=phrase)

    return DetectionResult()


# --- Deterministic disclosure and safe fallback text ---------------------


def build_first_turn_disclosure(
    *, business_name: str, ai_disclosure_text: str, purpose: str
) -> str:
    """Construct the mandatory first spoken turn deterministically.

    The first turn must identify the business, disclose that the caller is an
    automated AI assistant, state the approved purpose, offer an opt-out path,
    and offer human help. We construct this rather than trusting the model.
    """
    business = business_name.strip() or "the business"
    purpose_text = purpose.strip() or "your enquiry"
    pieces = [
        f"Hello, this is {business}.",
        ai_disclosure_text.strip() or "This is an automated AI assistant.",
        f"I'm calling about {purpose_text}.",
        "If you would prefer not to receive these calls, just say so and I will stop.",
        "You can also ask to speak with a human at any time.",
    ]
    return " ".join(piece for piece in pieces if piece)


def safe_fallback_decision(*, reason_code: str, spoken_response: str) -> ConversationDecision:
    """Build a policy-safe fallback decision when the model cannot be trusted."""
    return ConversationDecision(
        action=ConversationAction.CONTINUE,
        spoken_response=spoken_response,
        conversation_summary=None,
        structured_facts={"fallback": True, "reason_code": reason_code},
        reason_code=reason_code,
    )


# --- Redaction -----------------------------------------------------------


def redact_text(text: str) -> str:
    """Deterministically redact sensitive content from free text.

    Replaces card-like numbers, phone numbers, emails, token-like secrets, and
    postcode-like fragments with stable placeholders.  Card numbers are
    redacted *before* phone numbers so that long digit runs (e.g. Visa/MC
    numbers written with spaces) are not mis-classified as E.164 phones.
    """
    redacted = _TOKEN_RE.sub("[redacted:secret]", text)
    redacted = _EMAIL_RE.sub("[redacted:email]", redacted)
    redacted = _CARD_RE.sub("[redacted:card]", redacted)
    redacted = _PHONE_RE.sub("[redacted:phone]", redacted)
    redacted = _POSTCODE_RE.sub("[redacted:address]", redacted)
    return redacted


def redact_turn(role: str, text: str) -> ConversationTurn:
    """Build a redacted conversation turn bound for persistence or the model."""
    return ConversationTurn(role=role, redacted_text=redact_text(text))


__all__ = [
    "DETECTION_PRECEDENCE",
    "DetectionResult",
    "ESCALATION_PHRASES",
    "MAX_USER_INPUT_LENGTH",
    "OPT_OUT_PHRASES",
    "bound_user_input",
    "build_first_turn_disclosure",
    "detect_caller_intent",
    "detect_escalation",
    "detect_opt_out",
    "normalize_text",
    "redact_text",
    "redact_turn",
    "safe_fallback_decision",
]

# Sentinel for documentation; opt-out is checked before escalation.
DETECTION_PRECEDENCE = ("opt_out", "human_escalation")
