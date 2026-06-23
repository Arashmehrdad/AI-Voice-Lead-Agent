# API Contracts

## Scope

These contracts implement Stage 1 only. They define request and response shapes for later FastAPI implementation without creating application code, external service configuration, or infrastructure.

Authoritative sources:
- `PLAN.md`
- `docs/architecture.md`
- `docs/data-model.md`
- `docs/threat-model.md`
- `docs/acceptance-criteria.md`
- `docs/deployment-plan.md`

The contracts preserve the documented architecture:
- FastAPI owns public HTTP endpoints, Twilio webhooks, ConversationRelay WebSocket handling, health checks, lead intake, and active booking actions.
- The deterministic worker owns durable job claiming and background side effects.
- Hermes owns owner-facing administration and monitoring, using authenticated internal endpoints or approved scripts.
- Hermes is not part of the real-time voice loop and does not directly claim the production job queue.

## Common Rules

### Content Types

- JSON HTTP requests use `Content-Type: application/json`.
- Twilio webhooks may use Twilio's configured content type; implementation must validate against the exact signed request.
- WebSocket events use JSON messages as provided by Twilio ConversationRelay.

### Request IDs

All HTTP responses should include:

```text
X-Request-Id: <server-generated-or-propagated-id>
```

The request ID is logged with redaction rules from `docs/threat-model.md`.

### Authentication And Verification

- Lead intake requires an approved trusted-source authentication mechanism before production. The exact mechanism is unresolved for Stage 1.
- Twilio HTTP webhooks require valid `X-Twilio-Signature`.
- Twilio ConversationRelay WebSocket handshakes require valid `X-Twilio-Signature`.
- Internal administrative endpoints require authenticated internal access and must not be public unless explicitly approved.
- Health liveness may be public. Readiness and dependency checks should be restricted if they reveal provider or environment state.

### Error Envelope

Errors should use this response shape:

```json
{
  "error": {
    "code": "string",
    "message": "string",
    "request_id": "string",
    "retryable": false
  }
}
```

Messages must not include secrets, full webhook signatures, full phone numbers, full emails, or full provider payloads.

## Lead Intake

Endpoint:

```text
POST /api/leads
```

Owner:

```text
FastAPI
```

Purpose:

Create or reuse one lead using generic source fields, validate consent/suppression, and create a durable `initiate_call` job only when the lead is callable.

Request:

```json
{
  "source_system": "web_form",
  "source_entity_type": "lead",
  "source_entity_id": "external-123",
  "source_payload": {},
  "source_payload_version": 1,
  "full_name": "Example Person",
  "company_name": "Example Company",
  "phone_e164": "+447700900123",
  "email": "person@example.com",
  "timezone": "Europe/London",
  "consent_status": "specific_automated_call_consent",
  "consent_source": "website_quote_form",
  "consent_captured_at": "2026-06-23T12:00:00Z",
  "consent_evidence_uri": "internal://consent/example"
}
```

Required fields:

- `source_system`
- `phone_e164`
- `consent_status`

Recommended fields before production:

- `source_entity_id`
- `source_entity_type`
- `consent_source`
- `consent_captured_at`
- `consent_evidence_uri`

QuoteFollow compatibility:

QuoteFollow must use the same generic fields only:

```json
{
  "source_system": "quotefollow",
  "source_entity_type": "quote",
  "source_entity_id": "quote-123",
  "source_payload": {
    "quote_reference": "quote-123"
  },
  "source_payload_version": 1
}
```

Quote value, expiry date, follow-up stage, and customer estimate remain inside `source_payload` until a dedicated QuoteFollow integration is explicitly scoped.

Success response for a new callable lead:

```json
{
  "lead_id": "uuid",
  "status": "scheduled",
  "call_job_id": "uuid",
  "idempotency_key": "lead:web_form:external-123",
  "duplicate": false
}
```

Success response for a duplicate source submission:

```json
{
  "lead_id": "uuid",
  "status": "scheduled",
  "call_job_id": "uuid",
  "idempotency_key": "lead:web_form:external-123",
  "duplicate": true
}
```

Success response for a stored but not callable lead:

```json
{
  "lead_id": "uuid",
  "status": "new",
  "call_job_id": null,
  "idempotency_key": "lead:web_form:external-123",
  "callable": false,
  "blocked_reason": "consent_status_not_callable"
}
```

Validation errors:

- `invalid_phone`
- `missing_source_system`
- `invalid_consent_status`
- `suppressed_contact`
- `duplicate_source_conflict`

Side effects:

- Insert or reuse `leads`.
- Insert `lead_events`.
- Insert `jobs` with `job_type = initiate_call` only when callable.
- No external provider is contacted in Stage 1 or Stage 2 lead intake.

Idempotency:

- Lead source key: `lead:{source_system}:{source_entity_id}`
- If `source_entity_id` is absent, implementation must use a documented trusted-source fallback before production. Stage 1 does not approve a fallback that could create duplicate calls.
- Call job key: `job:initiate_call:{lead_id}`

## Twilio Call-Status Webhook

Endpoint:

```text
POST /webhooks/twilio/call-status
```

Owner:

```text
FastAPI
```

Purpose:

Receive Twilio call lifecycle status, validate the webhook signature, deduplicate the event, and update `call_attempts` or schedule reconciliation without repeating side effects.

Required headers:

```text
X-Twilio-Signature: <signature>
```

Signature requirements:

- Use the full externally visible URL.
- Account for Cloudflare forwarding headers.
- Reject invalid signatures before business state changes.
- Store invalid attempts only as redacted security `lead_events` when a local lead/call can be safely correlated, otherwise log redacted security telemetry.

Expected Twilio fields:

```json
{
  "CallSid": "CAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "CallStatus": "in-progress",
  "From": "+441234567890",
  "To": "+447700900123",
  "CallDuration": "30",
  "Timestamp": "2026-06-23T12:00:00Z"
}
```

Accepted `CallStatus` mapping:

- `queued` -> keep or set call attempt `dialing`
- `initiated` -> `dialing`
- `ringing` -> `ringing`
- `in-progress` -> `in_progress`
- `completed` -> `completed` unless a stronger business terminal state already exists
- `busy` -> `busy`
- `no-answer` -> `no_answer`
- `failed` -> `failed`
- `canceled` -> `cancelled`

Success response:

```json
{
  "received": true,
  "duplicate": false,
  "call_attempt_id": "uuid",
  "status": "in_progress"
}
```

Duplicate response:

```json
{
  "received": true,
  "duplicate": true,
  "call_attempt_id": "uuid",
  "status": "in_progress"
}
```

Invalid signature response:

```json
{
  "error": {
    "code": "invalid_signature",
    "message": "Invalid webhook signature.",
    "request_id": "string",
    "retryable": false
  }
}
```

Idempotency:

- If Twilio provides a provider event ID: `twilio:{provider_event_id}`
- Otherwise: `twilio:{call_sid}:{event_type}:{payload_hash}`
- Stored in `provider_events(provider, idempotency_key)`.

Side effects:

- Insert `provider_events` before processing.
- Update `call_attempts` only through documented state transitions.
- Insert `lead_events` for significant terminal outcomes.
- Create recovery job if status is ambiguous or references an unknown local call attempt.

## Twilio ConversationRelay WebSocket

Endpoint:

```text
GET /ws/twilio/conversationrelay
```

Owner:

```text
FastAPI voice gateway
```

Purpose:

Accept a verified Twilio ConversationRelay WebSocket, manage an active call session, exchange speech/text events, and persist enough critical state for recovery.

Handshake requirements:

- `X-Twilio-Signature` must be present and valid.
- The externally visible `wss://` URL must be used for signature validation.
- The request must identify the Twilio call SID through Twilio-provided parameters or an equivalent signed payload.
- The call SID must map to a known non-terminal `call_attempts.twilio_call_sid`.
- Invalid handshakes are rejected before a `call_sessions` record is created.

Handshake success behavior:

- Create or reuse `call_sessions` for the call attempt.
- Set session status to `opening` or `active`.
- Persist `conversationrelay_connection_id` if provided.
- Update `last_activity_at`.

Handshake failure close reasons:

- `invalid_signature`
- `unknown_call_sid`
- `call_not_active`
- `duplicate_active_session`
- `server_not_ready`

Inbound event categories:

```json
{
  "event": "start",
  "sequence_number": 1,
  "call_sid": "CAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
}
```

```json
{
  "event": "speech",
  "sequence_number": 2,
  "call_sid": "CAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "text": "I am interested in booking."
}
```

```json
{
  "event": "interrupt",
  "sequence_number": 3,
  "call_sid": "CAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
}
```

```json
{
  "event": "stop",
  "sequence_number": 4,
  "call_sid": "CAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "reason": "call_completed"
}
```

Outbound event categories:

```json
{
  "type": "say",
  "text": "Hello, this is Example Business calling with an automated assistant about your enquiry."
}
```

```json
{
  "type": "end",
  "reason": "opted_out"
}
```

Persistence requirements:

- `call_sessions.last_sequence_number` prevents reprocessing repeated WebSocket events.
- `call_sessions.conversation_summary` and `structured_facts` store redacted recovery state.
- `call_sessions.qualification_result` stores the latest validated decision.
- `lead_events` records opt-out, human escalation, qualification completion, and booking-critical events.

Latency boundary:

- The live loop may call Gemini and Google Calendar for active booking actions.
- The live loop must not claim background jobs, poll Telegram, run recovery batches, or call Hermes.

## Internal Administrative Pause/Resume

Endpoints:

```text
POST /internal/admin/calling/pause
POST /internal/admin/calling/resume
```

Owner:

```text
FastAPI internal API, invoked by Hermes or an approved operator script
```

Purpose:

Pause or resume new outbound call initiation without disabling required webhooks, active-call recovery, or booking reconciliation.

Authentication:

- Internal authenticated access required.
- Must not be exposed publicly unless strong authentication and explicit approval exist.
- Hermes callers must be allowlisted and audited.

Pause request:

```json
{
  "reason": "owner_requested",
  "actor_type": "hermes",
  "actor_id": "telegram-user-id"
}
```

Pause response:

```json
{
  "calling_paused": true,
  "changed": true,
  "business_settings_id": "uuid"
}
```

Resume request:

```json
{
  "reason": "owner_requested",
  "actor_type": "hermes",
  "actor_id": "telegram-user-id"
}
```

Resume response:

```json
{
  "calling_paused": false,
  "changed": true,
  "business_settings_id": "uuid"
}
```

Side effects:

- Update `business_settings.calling_paused`.
- Insert `lead_events` only when a lead-specific action is involved. General platform audit uses deployment/operator logs until a later admin audit table is scoped.
- Worker must check `calling_paused` before initiating outbound calls.

Non-effects:

- Does not reject Twilio callbacks.
- Does not close active calls.
- Does not stop recovery jobs that prevent inconsistent state.
- Does not delete pending jobs.

## Health Endpoints

### Liveness

Endpoint:

```text
GET /health/live
```

Purpose:

Confirm the FastAPI process is running.

Response:

```json
{
  "status": "live",
  "service": "api",
  "timestamp": "2026-06-23T12:00:00Z"
}
```

### Readiness

Endpoint:

```text
GET /health/ready
```

Purpose:

Confirm the API can accept work.

Response:

```json
{
  "status": "ready",
  "service": "api",
  "environment": "staging",
  "database": {
    "reachable": true,
    "migration_version": "0001_initial_schema"
  },
  "configuration": {
    "required_environment_present": true
  },
  "calling_paused": true
}
```

Readiness should fail when:

- Supabase is unreachable.
- Required environment variables for the current environment are missing.
- Migration version is unknown or behind the application requirement.
- The service cannot safely process provider callbacks.

Calling paused is not a readiness failure.

### Dependency Check

Endpoint:

```text
GET /health/dependencies
```

Purpose:

Authenticated diagnostic endpoint for staging/production dependency readiness.

Response:

```json
{
  "status": "degraded",
  "dependencies": {
    "supabase": "ok",
    "twilio": "not_checked",
    "gemini": "not_checked",
    "google_calendar": "not_checked",
    "resend": "not_checked",
    "telegram": "not_checked"
  }
}
```

Stage 1 note:

No external services are contacted in this stage. Later implementation may check dependencies only when credentials and environment policy allow it.

