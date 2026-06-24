"""Gemini conversation client protocol and official ``google-genai`` adapter.

Gemini is untrusted. It only ever receives a :class:`ConversationContext`
(plain text) and returns raw JSON text that the application structurally
validates. The adapter carries no credentials beyond the configured API key and
holds no references to repositories, Twilio clients, or side-effect functions.

All provider access is behind :class:`GeminiConversationClient` so tests use
fakes and never contact Gemini.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from google.genai.types import GenerateContentConfig

from voice_lead_agent.conversation_models import (
    MAX_SPOKEN_RESPONSE_LENGTH,
    MAX_SUMMARY_LENGTH,
    ConversationContext,
)

if TYPE_CHECKING:
    from google.genai import Client as GenaiClient


class GeminiError(Exception):
    """Bounded failure raised when a model response cannot be safely used.

    The ``category`` is a short, non-sensitive reason code persisted by the
    application. It must never contain provider payloads, stack traces, or
    personal data.
    """

    def __init__(self, category: str, message: str) -> None:
        self.category = category
        super().__init__(message)


@runtime_checkable
class GeminiConversationClient(Protocol):
    """Provider boundary for the conversation loop.

    Implementations return the raw JSON text produced by the model. The caller
    (the application) is responsible for structurally validating it.
    """

    async def generate_decision(self, context: ConversationContext) -> str:
        """Return the model's raw JSON response text for ``context``.

        Raises :class:`GeminiError` with a bounded category on timeout,
        provider failure, or empty output.
        """


@dataclass(frozen=True)
class GeminiClientConfig:
    """Typed, bounded configuration for the adapter."""

    model: str
    timeout_seconds: float
    max_output_tokens: int
    temperature: float


# The schema handed to the SDK. It mirrors :class:`ConversationDecision` and is
# declared as a plain dict (the SDK's documented native schema shape) so the
# module does not import SDK types at runtime or create a second source of
# truth that can drift from the Pydantic model. The Pydantic model remains the
# validation authority for the parsed output.
RESPONSE_SCHEMA: dict[str, object] = {
    "type": "object",
    "required": ["action", "spoken_response"],
    "properties": {
        "action": {
            "type": "string",
            "enum": [
                "continue",
                "qualified",
                "not_qualified",
                "opt_out",
                "human_escalation",
                "end_call",
            ],
        },
        "spoken_response": {
            "type": "string",
            "maxLength": MAX_SPOKEN_RESPONSE_LENGTH,
            "minLength": 1,
        },
        "conversation_summary": {
            "type": "string",
            "maxLength": MAX_SUMMARY_LENGTH,
        },
        "reason_code": {"type": "string", "maxLength": 64},
        "structured_facts": {"type": "object"},
    },
    "additionalProperties": False,
}


class GoogleGeminiConversationClient:
    """Official ``google-genai`` adapter using the asynchronous client.

    Structured output is requested through ``response_schema`` +
    ``response_mime_type`` so the model returns JSON conforming to
    :data:`RESPONSE_SCHEMA`. The application still re-validates with the
    Pydantic model, because model output is untrusted.
    """

    def __init__(self, *, client: GenaiClient, config: GeminiClientConfig) -> None:
        self._client = client
        self._config = config

    async def generate_decision(self, context: ConversationContext) -> str:
        sdk_config = self._build_config(context)
        try:
            response = await asyncio.wait_for(
                self._client.aio.models.generate_content(
                    model=self._config.model,
                    contents=context.conversation_text,
                    config=sdk_config,
                ),
                timeout=self._config.timeout_seconds,
            )
        except TimeoutError as exc:
            raise GeminiError("gemini_timeout", "Gemini call timed out.") from exc
        except Exception as exc:
            category = _classify_provider_exception(exc)
            raise GeminiError(category, "Gemini provider call failed.") from exc

        text = _extract_text(response)
        if not text or not text.strip():
            raise GeminiError("gemini_empty", "Gemini returned an empty response.")
        return text

    def _build_config(self, context: ConversationContext) -> GenerateContentConfig:
        return GenerateContentConfig(
            system_instruction=context.system_instruction,
            temperature=self._config.temperature,
            max_output_tokens=self._config.max_output_tokens,
            response_mime_type="application/json",
            response_schema=RESPONSE_SCHEMA,
        )


def _extract_text(response: object) -> str:
    # The SDK exposes a ``text`` convenience attribute. Defensive fallback keeps
    # the adapter robust to candidates that omit text (e.g. safety blocks).
    text = getattr(response, "text", None)
    if isinstance(text, str):
        return text
    return ""


def _classify_provider_exception(exc: Exception) -> str:
    # Map SDK-specific exceptions to bounded categories without embedding their
    # messages. Falls back to a generic provider-failure category otherwise.
    name = type(exc).__name__
    if "Timeout" in name or "timeout" in str(exc).lower():
        return "gemini_timeout"
    if name in {"ServerError", "APIError", "ClientError"}:
        return "gemini_provider_error"
    return "gemini_provider_error"


__all__ = [
    "GeminiClientConfig",
    "GeminiConversationClient",
    "GeminiError",
    "GoogleGeminiConversationClient",
    "RESPONSE_SCHEMA",
]
