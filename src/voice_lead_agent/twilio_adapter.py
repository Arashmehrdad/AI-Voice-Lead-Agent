from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from twilio.http.async_http_client import AsyncTwilioHttpClient  # type: ignore[import-untyped]
from twilio.rest import Client  # type: ignore[import-untyped]


class TwilioVoiceClient(Protocol):
    async def initiate_outbound_call(
        self,
        *,
        to_phone: str,
        from_phone: str,
        voice_url: str,
        status_callback_url: str,
    ) -> str:
        """Initiate a call and return the Twilio Call SID."""


class TwilioSignatureVerifier(Protocol):
    def validate(self, *, url: str, params: dict[str, str], signature: str | None) -> bool:
        """Validate a Twilio webhook signature."""


class TwilioSdkVoiceClient:
    def __init__(self, *, account_sid: str, auth_token: str) -> None:
        self._account_sid = account_sid
        self._auth_token = auth_token
        http_client = AsyncTwilioHttpClient()
        self._client = Client(account_sid, auth_token, http_client=http_client)

    async def initiate_outbound_call(
        self,
        *,
        to_phone: str,
        from_phone: str,
        voice_url: str,
        status_callback_url: str,
    ) -> str:
        call = await self._client.calls.create_async(
            to=to_phone,
            from_=from_phone,
            url=voice_url,
            method="POST",
            status_callback=status_callback_url,
            status_callback_method="POST",
            status_callback_event=["initiated", "ringing", "answered", "completed"],
        )
        return str(call.sid)


@dataclass(frozen=True)
class TwilioRequestValidator:
    auth_token: str

    def validate(self, *, url: str, params: dict[str, str], signature: str | None) -> bool:
        if not signature:
            return False

        from twilio.request_validator import RequestValidator  # type: ignore[import-untyped]

        return bool(RequestValidator(self.auth_token).validate(url, params, signature))
