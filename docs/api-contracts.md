# API Contracts

## Purpose

This document defines the HTTP and WebSocket contracts for the production AI voice lead qualification and appointment-booking platform.

These are design contracts only. They do not create application code, credentials, provider configuration, or infrastructure.

The architecture remains:

* FastAPI owns public HTTP endpoints, Twilio webhooks, ConversationRelay WebSocket handling, lead intake, health checks, active conversation logic, and calendar booking.
* The deterministic worker owns durable job execution, outbound call initiation, retries, confirmation emails, alerts, and reconciliation.
* Hermes owns owner-facing monitoring and approved administration.
* Hermes is not part of the real-time voice loop and does not directly claim production jobs.

## Common Rules

### Public Base URL

All externally signed provider URLs are constructed from:

```text
APP_PUBLIC_BASE_URL
```

Example:

```text
https://voice.example.com
```

Twilio signature verification must use the exact externally visible URL, including:

* scheme;
* host;
* path;
* port when present;
* query string when present;
* all signed request parameters.

Cloudflare forwarding headers must be handled through an explicit trusted-proxy configuration. Client-supplied forwarding headers must not be trusted from arbitrary sources.

### Request IDs

Every HTTP response includes:

```text
X-Request-Id: <request-id>
```

The application either accepts a valid incoming request ID from an approved proxy or generates one.

Request IDs must not contain personal data or credentials.

### JSON Content Type

Application-owned JSON endpoints require:

```text
Content-Type: application/json
```

Twilio callbacks normally use:

```text
Content-Type: application/x-www-form-urlencoded
```

The implementation must validate the actual signed body and all parameters received rather than relying on a fixed list of fields.

### Error Envelope

Application JSON errors use:

```json
{
  "error": {
    "code": "invalid_request",
    "message": "The request could not be processed.",
    "request_id": "request-id",
    "retryable": false
  }
}
```

Error messages must not expose:

* credentials;
* tokens;
* webhook signatures;
* full phone numbers;
* full email addresses;
* raw provider payloads;
* internal stack traces.

### Time Format

Application-owned JSON timestamps use UTC RFC 3339:

```text
2026-06-23T12:00:00Z
```

Provider timestamps are accepted in the provider's documented format and normalized before persistence.

### Phone Numbers

Application-owned phone numbers use E.164 format:

```text
+447700900123
```

## Trusted Lead Intake

### Endpoint

```text
POST /api/leads
```

### Owner

```text
FastAPI
```

### Authentication

Required header:

```text
Authorization: Bearer <trusted-source-token>
```

The token is loaded from environment configuration and compared using a timing-safe method.

A future multi-source implementation may use separate credentials per source, but the first release uses one approved trusted-source credential.

Unauthenticated requests return:

```text
401 Unauthorized
```

Authenticated but unauthorized sources return:

```text
403 Forbidden
```

### Purpose

Create one lead from an approved source and create one initial call job when the lead is callable.

### Request

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
  "consent_source": "website_enquiry_form",
  "consent_captured_at": "2026-06-23T12:00:00Z",
  "consent_evidence_uri": "internal://consent/external-123"
}
```

### Required Fields

```text
source_system
source_entity_type
source_entity_id
source_payload_version
phone_e164
consent_status
consent_source
consent_captured_at
```

`source_entity_id` is required for every accepted submission.

If it is missing:

* return `422 Unprocessable Entity`;
* create no lead;
* create no job;
* create no external side effect.

No phone-based or timestamp-based deduplication fallback is approved for production.

### QuoteFollow Compatibility

A future QuoteFollow request uses the same contract:

```json
{
  "source_system": "quotefollow",
  "source_entity_type": "quote",
  "source_entity_id": "quote-123",
  "source_payload": {
    "quote_reference": "quote-123"
  },
  "source_payload_version": 1,
  "full_name": "Example Person",
  "phone_e164": "+447700900123",
  "consent_status": "specific_automated_call_consent",
  "consent_source": "quotefollow_form",
  "consent_captured_at": "2026-06-23T12:00:00Z"
}
```

Quote-specific fields remain inside `source_payload` until a dedicated QuoteFollow integration is approved.

### New Callable Lead Response

Status:

```text
201 Created
```

Body:

```json
{
  "lead_id": "uuid",
  "status": "scheduled",
  "callable": true,
  "call_job_id": "uuid",
  "attempt_number": 1,
  "duplicate": false
}
```

The lead and initial call job must be created in the same database transaction.

### Duplicate Submission Response

Status:

```text
200 OK
```

Body:

```json
{
  "lead_id": "uuid",
  "status": "scheduled",
  "callable": true,
  "call_job_id": "uuid",
  "attempt_number": 1,
  "duplicate": true
}
```

The existing lead and existing initial call job are returned. No new call job is created.

### Stored But Not Callable Lead Response

Status:

```text
201 Created
```

Body:

```json
{
  "lead_id": "uuid",
  "status": "new",
  "callable": false,
  "call_job_id": null,
  "duplicate": false,
  "blocked_reason": "consent_status_not_callable"
}
```

### Validation Codes

```text
invalid_phone
invalid_email
invalid_timezone
missing_source_entity_id
invalid_source_payload_version
invalid_consent_status
missing_consent_evidence
suppressed_contact
source_identity_conflict
```

### Idempotency

Lead identity:

```text
lead:{source_system}:{source_entity_id}
```

Initial call job:

```text
job:initiate_call:{lead_id}:1
```

Retry jobs use later deterministic attempt numbers:

```text
job:initiate_call:{lead_id}:{attempt_number}
```

## Twilio Outbound Voice TwiML

### Endpoint

```text
POST /webhooks/twilio/voice/start
```

### Owner

```text
FastAPI
```

### Authentication

Require a valid Twilio signature:

```text
X-Twilio-Signature
```

Reject invalid signatures before returning call instructions.

### Purpose

Return the TwiML that connects an approved outbound call to ConversationRelay.

### Expected Request Parameters

The implementation accepts all signed Twilio parameters. Relevant correlation fields include:

```text
AccountSid
CallSid
From
To
Direction
CallStatus
```

The `CallSid` must map to a known non-terminal local call attempt.

### Success Response

Status:

```text
200 OK
```

Content type:

```text
application/xml
```

Representative response:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect action="https://voice.example.com/webhooks/twilio/conversationrelay/action">
    <ConversationRelay
      url="wss://voice.example.com/ws/twilio/conversationrelay"
      welcomeGreeting="Hello. This is Example Business calling with an automated AI assistant about your enquiry."
      language="en-GB"
      dtmfDetection="true">
      <Parameter name="call_attempt_id" value="local-call-attempt-uuid" />
    </ConversationRelay>
  </Connect>
</Response>
```

The local call-attempt identifier may be included as a custom parameter because Twilio returns custom `<Parameter>` values in the initial `setup` message.

Do not place credentials, sensitive personal data, or unrestricted internal context in TwiML parameters.

### Failure Behaviour

Unknown, terminal, suppressed, or unsafe calls return TwiML that ends the call without connecting ConversationRelay:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Hangup />
</Response>
```

## Twilio Call-Status Callback

### Endpoint

```text
POST /webhooks/twilio/call-status
```

### Owner

```text
FastAPI
```

### Content Type

Expected:

```text
application/x-www-form-urlencoded
```

The implementation must accept additional Twilio parameters without breaking validation.

### Authentication

Require:

```text
X-Twilio-Signature
```

Use:

* the exact external callback URL;
* all received form parameters;
* the configured Twilio auth token;
* the official Twilio request validator.

Invalid signatures return:

```text
403 Forbidden
```

No business state may change.

### Relevant Parameters

```text
AccountSid
CallSid
CallStatus
From
To
Direction
Timestamp
CallbackSource
SequenceNumber
CallDuration
SipResponseCode
```

Not every field is present for every event.

### Configured Progress Events

The outbound call request should subscribe to:

```text
initiated
ringing
answered
completed
```

The callback may carry these `CallStatus` values:

```text
queued
initiated
ringing
in-progress
completed
busy
failed
no-answer
canceled
```

### Local Mapping

```text
queued       -> dialing
initiated    -> dialing
ringing      -> ringing
in-progress  -> in_progress
completed    -> completed
busy         -> busy
failed       -> failed
no-answer    -> no_answer
canceled     -> cancelled
```

A later callback must not overwrite a stronger validated business outcome without an explicit reconciliation event.

For example, a generic Twilio `completed` status must not erase:

```text
opted_out
needs_human
booked
not_qualified
```

### Idempotency

Preferred key:

```text
twilio:call-status:{CallSid}:{SequenceNumber}:{CallStatus}
```

If `SequenceNumber` is unavailable:

```text
twilio:call-status:{CallSid}:{CallStatus}:{payload_hash}
```

The provider event is inserted before state processing.

Duplicate delivery:

* returns success;
* updates `last_seen_at`;
* increments `delivery_count`;
* performs no business side effect;
* preserves the original processing result.

### Response

Valid new or duplicate callbacks return:

```text
204 No Content
```

## ConversationRelay WebSocket

### Endpoint

```text
GET /ws/twilio/conversationrelay
```

Protocol upgrade:

```text
wss://
```

### Owner

```text
FastAPI voice gateway
```

### Handshake Authentication

Twilio includes its signature in the initial WebSocket handshake.

The implementation must read the header case-insensitively and expect the ASGI-normalized form:

```text
x-twilio-signature
```

Validate using:

* the Twilio auth token;
* the exact external `wss://` URL;
* the official Twilio request validator.

Reject the WebSocket before acceptance when:

```text
invalid_signature
server_not_ready
calling_disabled_for_call
```

The local call identity is confirmed from the signed Twilio `setup` message rather than trusting an unsigned client query parameter.

### Initial Setup Message

Twilio sends `setup` after the connection is established.

Representative shape:

```json
{
  "type": "setup",
  "sessionId": "VX00000000000000000000000000000000",
  "accountSid": "AC00000000000000000000000000000000",
  "parentCallSid": "",
  "callSid": "CA00000000000000000000000000000000",
  "from": "+441234567890",
  "to": "+447700900123",
  "forwardedFrom": "",
  "callType": "PSTN",
  "callerName": "",
  "direction": "outbound",
  "callStatus": "IN_PROGRESS",
  "customParameters": {
    "call_attempt_id": "local-call-attempt-uuid"
  }
}
```

Validation after setup:

* `accountSid` matches the configured account;
* `callSid` maps to the expected call attempt;
* `call_attempt_id` maps to the same call;
* the attempt is non-terminal;
* the lead is not suppressed;
* no conflicting active call session exists.

After validation, create or reuse one `call_sessions` record.

### Messages Received From Twilio

#### Prompt

Sent when the caller speaks:

```json
{
  "type": "prompt",
  "voicePrompt": "I would like to book an appointment.",
  "lang": "en-GB",
  "last": true
}
```

The application should generate an AI turn only when the configured conversation policy considers the prompt complete, normally when:

```text
last = true
```

#### DTMF

Sent when DTMF detection is enabled and the caller presses a key:

```json
{
  "type": "dtmf",
  "digit": "1"
}
```

#### Interrupt

Sent when the caller interrupts current TTS playback:

```json
{
  "type": "interrupt",
  "utteranceUntilInterrupt": "Your available appointment is",
  "durationUntilInterruptMs": 460
}
```

#### Error

Sent when ConversationRelay reports an error:

```json
{
  "type": "error",
  "description": "Invalid message received."
}
```

Errors are categorized and recorded without storing unnecessary raw personal data.

### Messages Sent To Twilio

#### Text

The primary response type:

```json
{
  "type": "text",
  "token": "I can help with that.",
  "last": true,
  "interruptible": true,
  "preemptible": false
}
```

Streaming is supported by sending multiple `text` messages with:

```text
last = false
```

and setting:

```text
last = true
```

on the final token of the talk cycle.

#### Play

Optional media playback:

```json
{
  "type": "play",
  "source": "https://example.com/audio/fallback.mp3",
  "loop": 1,
  "preemptible": false,
  "interruptible": true
}
```

#### Send Digits

Optional DTMF output:

```json
{
  "type": "sendDigits",
  "digits": "9"
}
```

#### Language

Optional language switch:

```json
{
  "type": "language",
  "ttsLanguage": "en-GB",
  "transcriptionLanguage": "en-GB"
}
```

#### End

Ends the ConversationRelay session and returns control to Twilio:

```json
{
  "type": "end",
  "handoffData": "{\"reasonCode\":\"opted-out\"}"
}
```

`handoffData` contains only a short non-sensitive reason code or redacted operational context.

### Local Event Ordering

ConversationRelay WebSocket messages must not be assumed to contain a provider sequence number.

The application maintains an internal counter:

```text
local_sequence_number
```

This counter:

* is generated by the application;
* orders locally processed messages;
* is not sent to Twilio;
* is not treated as a Twilio event identifier;
* is not used as cross-connection provider idempotency.

Critical external actions such as suppression and booking use their own durable idempotency keys.

### WebSocket Failure Behaviour

On unexpected disconnect:

* mark the call session as stale or failed;
* create a reconciliation job;
* do not blindly start another call;
* wait for Twilio call status and the Connect action callback;
* preserve already completed opt-out or booking actions.

## ConversationRelay Connect Action Callback

### Endpoint

```text
POST /webhooks/twilio/conversationrelay/action
```

### Owner

```text
FastAPI
```

### Purpose

Receive the result after the Twilio `<Connect>` verb ends.

This callback is required for:

* normal session completion;
* application-requested end;
* caller hang-up;
* WebSocket failure;
* ConversationRelay failure;
* handoff data processing;
* final session reconciliation.

### Authentication

Treat this as a signed Twilio webhook.

Require:

```text
X-Twilio-Signature
```

Validate the exact external URL and all received parameters.

### Relevant Parameters

```text
AccountSid
CallSid
CallStatus
From
To
Direction
ApplicationSid
SessionId
SessionStatus
SessionDuration
HandoffData
ErrorCode
ErrorMessage
```

`HandoffData`, `ErrorCode`, and `ErrorMessage` are conditional.

### Supported Session Statuses

The contract must safely handle at least:

```text
ended
failed
completed
```

Unknown future statuses are stored as redacted provider data and reconciled rather than treated as success.

### Idempotency

```text
twilio:conversationrelay-action:{SessionId}:{SessionStatus}:{payload_hash}
```

### Behaviour

When `SessionStatus` indicates normal completion:

* finalize the local call session;
* preserve any validated business outcome;
* return terminal TwiML.

When the application sent an `end` message:

* parse the JSON-encoded `HandoffData`;
* execute only allowlisted reason handling;
* never execute commands from arbitrary handoff text.

When the callback indicates failure:

* store the redacted error;
* create one reconciliation or human-follow-up job;
* do not falsely mark the call successful;
* do not redial until the previous call state is reconciled.

### Response

Default terminal response:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Hangup />
</Response>
```

For a recoverable technical failure, the approved fallback may instead return:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Say>Sorry, we are having a technical problem. A member of the team will follow up.</Say>
  <Hangup />
</Response>
```

Automatic WebSocket reconnection is outside the initial release unless staging tests prove that it is safe and preserves call consistency.

## Internal Calling Administration

### Pause Endpoint

```text
POST /internal/admin/calling/pause
```

### Resume Endpoint

```text
POST /internal/admin/calling/resume
```

### Owner

```text
FastAPI internal administration API
```

### Authentication

Required:

```text
Authorization: Bearer <internal-admin-token>
```

These endpoints must not be routed publicly through Cloudflare unless explicitly approved and strongly protected.

### Request

```json
{
  "reason": "owner_requested",
  "actor_type": "hermes",
  "actor_id": "telegram-user-id"
}
```

### Pause Response

```json
{
  "calling_paused": true,
  "changed": true
}
```

### Resume Response

```json
{
  "calling_paused": false,
  "changed": true
}
```

### Behaviour

Pause:

* blocks the worker from initiating new calls;
* does not terminate active calls;
* does not reject Twilio callbacks;
* does not stop booking reconciliation;
* does not delete pending jobs.

Resume:

* permits eligible due call jobs to be claimed;
* does not bypass consent, suppression, calling-hour, or attempt-limit checks.

Repeated pause or resume requests are safe and return:

```json
{
  "calling_paused": true,
  "changed": false
}
```

or:

```json
{
  "calling_paused": false,
  "changed": false
}
```

## Health Endpoints

### Liveness

```text
GET /health/live
```

Response:

```json
{
  "status": "live",
  "service": "api",
  "timestamp": "2026-06-23T12:00:00Z"
}
```

Liveness checks only whether the process is running.

### Readiness

```text
GET /health/ready
```

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

Readiness fails when:

* Supabase is unreachable;
* required configuration is absent;
* the migration version is incompatible;
* the API cannot safely receive provider callbacks.

`calling_paused = true` is not a readiness failure.

### Dependency Diagnostics

```text
GET /health/dependencies
```

Authentication:

```text
Authorization: Bearer <internal-admin-token>
```

Representative response:

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

Dependency diagnostics must:

* avoid creating provider side effects;
* use short timeouts;
* redact provider responses;
* distinguish unavailable, misconfigured, and not checked;
* never return credentials or internal tokens.

## Stage 1 Completion Boundary

This document defines contracts only.

Stage 1 must not:

* create FastAPI routes;
* install dependencies;
* contact Twilio;
* connect Supabase;
* call Gemini;
* create calendar events;
* send email;
* configure Hermes;
* create infrastructure.

Provider payloads must be verified against real staging fixtures during the relevant implementation stage. Unknown provider fields must be accepted safely and must not break signature validation.
