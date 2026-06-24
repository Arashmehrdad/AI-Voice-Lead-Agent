from __future__ import annotations

from xml.sax.saxutils import escape


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
