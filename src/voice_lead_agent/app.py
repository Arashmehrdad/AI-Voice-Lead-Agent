from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from hashlib import sha256
from typing import cast
from urllib.parse import parse_qsl
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, WebSocket, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.websockets import WebSocketDisconnect

from voice_lead_agent.auth import require_trusted_source
from voice_lead_agent.config import Settings, get_settings
from voice_lead_agent.errors import ApiError
from voice_lead_agent.repositories import LeadRepository, TwilioWebhookRepository
from voice_lead_agent.schemas import (
    LeadIntakeRequest,
    LeadIntakeResponse,
    LiveResponse,
    ReadyConfiguration,
    ReadyDatabase,
    ReadyResponse,
)
from voice_lead_agent.service import intake_lead
from voice_lead_agent.twilio_adapter import TwilioRequestValidator, TwilioSignatureVerifier
from voice_lead_agent.twiml import fixed_stage3_twiml

REQUEST_ID_HEADER = "X-Request-Id"
MIGRATION_VERSION = "0001_initial_schema"


def create_app(
    *,
    settings: Settings | None = None,
    repository: LeadRepository | None = None,
    twilio_webhook_repository: TwilioWebhookRepository | None = None,
    twilio_signature_verifier: TwilioSignatureVerifier | None = None,
) -> FastAPI:
    resolved_settings = settings
    resolved_repository = repository
    resolved_twilio_webhook_repository = twilio_webhook_repository
    resolved_twilio_signature_verifier = twilio_signature_verifier
    resolved_engine = None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        if resolved_engine is not None:
            await resolved_engine.dispose()

    app = FastAPI(title="AI Voice Lead Agent API", lifespan=lifespan)

    @app.middleware("http")
    async def request_id_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = uuid4().hex
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response

    def current_settings() -> Settings:
        nonlocal resolved_settings
        if resolved_settings is None:
            resolved_settings = get_settings()
        return resolved_settings

    def current_repository() -> LeadRepository:
        nonlocal resolved_repository, resolved_engine
        if resolved_repository is None:
            current = current_settings()
            from voice_lead_agent.db import create_engine, create_sessionmaker
            from voice_lead_agent.sql_repository import SqlLeadRepository

            resolved_engine = create_engine(current.database_url)
            resolved_repository = SqlLeadRepository(create_sessionmaker(resolved_engine))
        return resolved_repository

    def current_twilio_webhook_repository() -> TwilioWebhookRepository:
        nonlocal resolved_twilio_webhook_repository
        if resolved_twilio_webhook_repository is None:
            resolved_twilio_webhook_repository = current_repository()  # type: ignore[assignment]
        return cast(TwilioWebhookRepository, resolved_twilio_webhook_repository)

    def current_twilio_signature_verifier() -> TwilioSignatureVerifier:
        nonlocal resolved_twilio_signature_verifier
        if resolved_twilio_signature_verifier is None:
            current = current_settings()
            if current.twilio_auth_token is None:
                raise RuntimeError("TWILIO_AUTH_TOKEN is required for Twilio webhooks.")
            resolved_twilio_signature_verifier = TwilioRequestValidator(current.twilio_auth_token)
        return resolved_twilio_signature_verifier

    async def trusted_source_dependency(
        authorization: str | None = Header(default=None),
        current: Settings = Depends(current_settings),  # noqa: B008
    ) -> None:
        await require_trusted_source(current, authorization)

    @app.exception_handler(ApiError)
    async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
        return error_response(
            request=request,
            status_code=exc.status_code,
            code=exc.code,
            message=exc.message,
            retryable=exc.retryable,
        )

    @app.exception_handler(HTTPException)
    async def http_error_handler(request: Request, exc: HTTPException) -> JSONResponse:
        detail: dict[str, object] = exc.detail if isinstance(exc.detail, dict) else {}
        return error_response(
            request=request,
            status_code=exc.status_code,
            code=str(detail.get("code", "http_error")),
            message=str(detail.get("message", "The request could not be processed.")),
            retryable=False,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        code = validation_code(exc)
        return error_response(
            request=request,
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code=code,
            message="The request could not be processed.",
            retryable=False,
        )

    @app.get("/health/live", response_model=LiveResponse)
    async def live() -> LiveResponse:
        return LiveResponse(
            status="live",
            service="api",
            timestamp=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        )

    @app.get("/health/ready", response_model=ReadyResponse)
    async def ready(
        response: Response,
        current: Settings = Depends(current_settings),  # noqa: B008
        repo: LeadRepository = Depends(current_repository),  # noqa: B008
    ) -> ReadyResponse:
        reachable = await repo.readiness()
        if not reachable:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        response_status = "ready" if reachable else "not_ready"
        return ReadyResponse(
            status=response_status,
            service="api",
            environment=current.app_env,
            database=ReadyDatabase(reachable=reachable, migration_version=MIGRATION_VERSION),
            configuration=ReadyConfiguration(required_environment_present=True),
            calling_paused=not current.calling_enabled,
        )

    @app.post(
        "/api/leads",
        response_model=LeadIntakeResponse,
        dependencies=[Depends(trusted_source_dependency)],
    )
    async def create_lead(
        request: LeadIntakeRequest,
        repo: LeadRepository = Depends(current_repository),  # noqa: B008
    ) -> JSONResponse:
        status_code, result = await intake_lead(request, repo)
        return JSONResponse(
            status_code=status_code,
            content=LeadIntakeResponse(
                lead_id=result.lead_id,
                status=result.status,
                callable=result.callable,
                call_job_id=result.call_job_id,
                attempt_number=result.attempt_number,
                duplicate=result.duplicate,
                blocked_reason=result.blocked_reason,
            ).model_dump(),
        )

    @app.post("/webhooks/twilio/voice/start")
    async def twilio_voice_start(
        request: Request,
        verifier: TwilioSignatureVerifier = Depends(  # noqa: B008
            current_twilio_signature_verifier
        ),
    ) -> Response:
        body = await request.body()
        params = parse_form_body(body)
        signature = request.headers.get("x-twilio-signature")
        if not verifier.validate(
            url=public_url_for_request(request, current_settings()),
            params=params,
            signature=signature,
        ):
            return error_response(
                request=request,
                status_code=status.HTTP_403_FORBIDDEN,
                code="invalid_signature",
                message="Invalid webhook signature.",
                retryable=False,
            )
        return Response(
            content=fixed_stage3_twiml(),
            media_type="application/xml",
            status_code=status.HTTP_200_OK,
        )

    @app.post("/webhooks/twilio/call-status")
    async def twilio_call_status(
        request: Request,
        verifier: TwilioSignatureVerifier = Depends(  # noqa: B008
            current_twilio_signature_verifier
        ),
        repo: TwilioWebhookRepository = Depends(  # noqa: B008
            current_twilio_webhook_repository
        ),
    ) -> Response:
        body = await request.body()
        params = parse_form_body(body)
        signature = request.headers.get("x-twilio-signature")
        if not verifier.validate(
            url=public_url_for_request(request, current_settings()),
            params=params,
            signature=signature,
        ):
            return error_response(
                request=request,
                status_code=status.HTTP_403_FORBIDDEN,
                code="invalid_signature",
                message="Invalid webhook signature.",
                retryable=False,
            )
        await repo.record_twilio_call_status(
            params=params,
            payload_hash=sha256(body).hexdigest(),
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.websocket("/ws/twilio/conversationrelay")
    async def conversation_relay(
        websocket: WebSocket,
        verifier: TwilioSignatureVerifier = Depends(  # noqa: B008
            current_twilio_signature_verifier
        ),
    ) -> None:
        signature = websocket.headers.get("x-twilio-signature")
        params: dict[str, str] = {}
        if not verifier.validate(
            url=public_ws_url_for_request(websocket, current_settings()),
            params=params,
            signature=signature,
        ):
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        await websocket.accept()
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass

    return app


def validation_code(exc: RequestValidationError) -> str:
    for error in exc.errors():
        ctx = error.get("ctx")
        if isinstance(ctx, dict):
            value_error = ctx.get("error")
            if value_error is not None:
                text = str(value_error)
                for known in (
                    "missing_source_entity_id",
                    "invalid_source_payload_version",
                    "invalid_phone",
                    "invalid_email",
                    "invalid_timezone",
                    "invalid_consent_status",
                    "invalid_consent_source",
                    "invalid_consent_captured_at",
                ):
                    if known in text:
                        return known
        location = error.get("loc", ())
        if location and location[-1] == "source_entity_id":
            return "missing_source_entity_id"
    return "invalid_request"


def error_response(
    *,
    request: Request,
    status_code: int,
    code: str,
    message: str,
    retryable: bool,
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", uuid4().hex)
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "request_id": request_id,
                "retryable": retryable,
            }
        },
    )


def parse_form_body(body: bytes) -> dict[str, str]:
    return {key: value for key, value in parse_qsl(body.decode("utf-8"), keep_blank_values=True)}


def public_url_for_request(request: Request, settings: Settings) -> str:
    path = request.url.path
    query = f"?{request.url.query}" if request.url.query else ""
    return f"{settings.app_public_base_url.rstrip('/')}{path}{query}"


def public_ws_url_for_request(websocket: WebSocket, settings: Settings) -> str:
    base = settings.app_public_base_url.rstrip("/")
    ws_base = base.replace("https://", "wss://").replace("http://", "ws://")
    path = websocket.url.path
    raw_query = websocket.scope.get("query_string", b"").decode("latin-1")
    query = f"?{raw_query}" if raw_query else ""
    return f"{ws_base}{path}{query}"
