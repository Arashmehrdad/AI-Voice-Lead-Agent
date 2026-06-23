from __future__ import annotations

import secrets

from fastapi import Header, HTTPException, status

from voice_lead_agent.config import Settings


async def require_trusted_source(
    settings: Settings,
    authorization: str | None = Header(default=None),
) -> None:
    expected = f"Bearer {settings.trusted_source_token}"
    if authorization is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "missing_authorization", "message": "Authorization is required."},
        )
    if not secrets.compare_digest(authorization, expected):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "invalid_token", "message": "Invalid trusted-source token."},
        )
