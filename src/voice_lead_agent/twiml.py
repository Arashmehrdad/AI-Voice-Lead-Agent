from __future__ import annotations

from collections.abc import Mapping
from xml.sax.saxutils import escape, quoteattr


def fixed_stage3_twiml(*, business_name: str = "AI Voice Lead Agent Test Business") -> str:
    message = (
        f"Hello. This is {business_name}. This is an automated call. "
        "This is a technical Stage 3 test of the outbound calling system. Goodbye."
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f"<Say>{escape(message)}</Say>"
        "<Hangup />"
        "</Response>"
    )


def hangup_twiml() -> str:
    return '<?xml version="1.0" encoding="UTF-8"?><Response><Hangup /></Response>'


def conversation_relay_twiml(
    *,
    websocket_url: str,
    welcome_greeting: str,
    language: str | None = None,
    custom_parameters: Mapping[str, str] | None = None,
) -> str:
    """Build the Stage 4 ConversationRelay TwiML response."""
    attributes = [
        f"url={quoteattr(websocket_url)}",
        f"welcomeGreeting={quoteattr(welcome_greeting)}",
        'dtmfDetection="true"',
    ]
    if language:
        attributes.append(f"language={quoteattr(language)}")

    parameters_xml = ""
    for name, value in (custom_parameters or {}).items():
        parameters_xml += f"<Parameter name={quoteattr(name)} value={quoteattr(value)} />"

    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        "<Connect>"
        f"<ConversationRelay {' '.join(attributes)}>"
        f"{parameters_xml}"
        "</ConversationRelay>"
        "</Connect>"
        "</Response>"
    )
