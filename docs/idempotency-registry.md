# Idempotency Registry

## Purpose

This document defines the deterministic identifiers used to prevent duplicate leads, calls, jobs, calendar events, emails, alerts, and provider-event processing.

Idempotency is required because:

* providers may retry webhooks;
* workers may restart during an external request;
* network timeouts may hide a successful provider action;
* users may submit the same form repeatedly;
* recovery jobs may run more than once;
* multiple worker processes may operate concurrently.

If a safe idempotency key cannot be constructed, the external side effect must not run.

## General Rules

* Keys use lowercase, colon-separated prefixes.
* UUIDs use canonical lowercase string form.
* Attempt numbers are positive integers beginning at `1`.
* Appointment timestamps are converted to UTC before key construction.
* Model-generated values must never be used as idempotency keys.
* Keys must not contain API keys, tokens, credentials, raw provider payloads, or sensitive conversation content.
* Provider events are inserted before their business logic runs.
* Duplicate requests return an existing result or successful acknowledgement without repeating side effects.
* External provider state must be reconciled after uncertain timeouts before retrying a side effect.

## Canonicalization

Before constructing a key:

* controlled identifiers such as `source_system`, `job_type`, and `event_type` are converted to lowercase;
* leading and trailing whitespace is removed;
* UUIDs are normalized;
* phone numbers are normalized to E.164 before database uniqueness checks;
* email addresses are normalized according to the approved application policy;
* calendar timestamps are converted to UTC;
* raw payloads are represented by a SHA-256 hash rather than embedded in a key.

The application must use one shared key-building module. API routes, worker jobs, recovery scripts, and tests must not independently construct key formats.

## Registry

### Lead Source Identity

Canonical logical key:

```text
lead:{source_system}:{source_entity_id}
```

Created by:

```text
FastAPI lead intake
```

Stored through:

```text
leads.source_system
leads.source_entity_id
```

Database enforcement:

```text
unique (source_system, source_entity_id)
```

The unique constraint applies when `source_entity_id` is present.

Rules:

* `source_entity_id` is required for every accepted lead.
* No production fallback based only on phone number or timestamp is approved.
* A duplicate submission returns the existing lead.
* A source conflict with materially different immutable identity data is rejected for investigation.
* Duplicate intake must not create another initial call job.

### Initiate-Call Job

Canonical key:

```text
job:initiate_call:{lead_id}:{attempt_number}
```

Examples:

```text
job:initiate_call:550e8400-e29b-41d4-a716-446655440000:1
job:initiate_call:550e8400-e29b-41d4-a716-446655440000:2
```

Created by:

```text
FastAPI for attempt 1
worker or recovery logic for later attempts
```

Stored in:

```text
jobs.idempotency_key
```

Database enforcement:

```text
jobs.idempotency_key unique
```

Rules:

* Attempt numbering begins at `1`.
* The next attempt number is derived transactionally from existing call attempts.
* The same attempt number must not be assigned twice.
* Reusing the key returns the existing job.
* A retry creates a new attempt number and a new job key.
* Consent, suppression, pause state, calling hours, and attempt limits are checked again before execution.

### Call Attempt

Canonical key:

```text
call:{lead_id}:{attempt_number}
```

Created by:

```text
deterministic worker
```

Stored in:

```text
call_attempts.idempotency_key
```

Database enforcement:

```text
call_attempts.idempotency_key unique
unique (lead_id, attempt_number)
```

Rules:

* The call-attempt record is created before requesting a Twilio call.
* A duplicate worker execution reuses the existing attempt.
* It must not request a second Twilio call for the same attempt.
* A later retry creates a new attempt row.

### Twilio Call-Status Provider Event

Preferred key when Twilio supplies a callback sequence number:

```text
twilio:call-status:{call_sid}:{sequence_number}:{call_status}
```

Fallback when no reliable sequence number exists:

```text
twilio:call-status:{call_sid}:{call_status}:{payload_hash}
```

Created by:

```text
FastAPI Twilio webhook handler
```

Stored in:

```text
provider_events.idempotency_key
```

Database enforcement:

```text
unique (provider, idempotency_key)
```

Duplicate behaviour:

* keep the existing processing status;
* update `last_seen_at`;
* increment `delivery_count`;
* return the expected successful Twilio response;
* perform no repeated business side effect.

A duplicate delivery does not change an existing event from `processed` to another status.

### ConversationRelay Connect Action Event

Canonical key:

```text
twilio:conversationrelay-action:{session_id}:{session_status}:{payload_hash}
```

Created by:

```text
FastAPI ConversationRelay action callback
```

Stored in:

```text
provider_events.idempotency_key
```

Rules:

* The callback is recorded before session reconciliation.
* Duplicate callbacks do not re-finalize the call session.
* Duplicate callbacks do not create additional retry or escalation jobs.
* Unknown session statuses are stored and reconciled rather than treated as success.

### ConversationRelay WebSocket Messages

ConversationRelay WebSocket messages do not use a Twilio provider-event key unless a future documented Twilio field safely supports one.

The application generates:

```text
local_sequence_number
```

Stored in:

```text
call_sessions.last_sequence_number
```

Rules:

* The counter is local to one call session.
* It orders locally processed WebSocket messages.
* It is not a Twilio event identifier.
* It is not used across reconnects as an external idempotency guarantee.
* Critical actions such as opt-out, booking, and escalation use their own durable keys and state guards.

### Calendar Booking

Canonical key:

```text
calendar:{lead_id}:{appointment_type}:{slot_start_utc}:{slot_end_utc}
```

Created by:

```text
FastAPI booking flow
```

Stored in:

```text
appointments.booking_idempotency_key
```

Database enforcement:

```text
appointments.booking_idempotency_key unique
```

Rules:

* Create or retrieve the local appointment before calling Google Calendar.
* A duplicate booking request returns the existing appointment.
* The application must not issue another Google event-creation request after an uncertain timeout until reconciliation completes.
* Only one active appointment may use the same booking key.

### Google Calendar Correlation Property

Canonical value:

```text
appointment:{appointment_id}
```

Created by:

```text
FastAPI booking flow
```

Stored in:

```text
appointments.google_extended_property_value
```

Database enforcement:

```text
appointments.google_extended_property_value unique
```

The same value is stored in Google Calendar extended properties.

Rules:

* After a timeout, search Google Calendar using this property.
* If an event exists, store its Google event ID and reuse it.
* Do not blindly create another event.
* A local appointment is not marked `booked` until a Google event ID is confirmed.

### Confirmation Email Job

Canonical key:

```text
job:send_confirmation_email:{appointment_id}
```

Created by:

```text
FastAPI after confirmed booking
```

Stored in:

```text
jobs.idempotency_key
```

Rules:

* Only one confirmation-email job exists for an appointment.
* A duplicate request returns the existing job.
* The job is not created until the appointment has a confirmed Google event ID.

### Confirmation Email Side Effect

Canonical key:

```text
email:{appointment_id}:confirmation
```

Created by:

```text
deterministic worker
```

Used as:

```text
Resend Idempotency-Key request header
```

Local state:

```text
appointments.confirmation_status
appointments.resend_message_id
appointments.confirmation_sent_at
```

Rules:

* The same key is sent to Resend on safe retries.
* If `confirmation_status = sent`, the worker does not send again.
* If Resend accepted the request but the local update failed, the worker retries using the same provider idempotency key.
* Email failure does not invalidate an already booked appointment.

### Owner Alert Job

Canonical key:

```text
job:send_owner_alert:{lead_event_id}
```

Created by:

```text
FastAPI or deterministic worker
```

Stored in:

```text
jobs.idempotency_key
```

Rules:

* Each alert-worthy event creates one append-only `lead_events` row.
* The event ID distinguishes separate genuine occurrences.
* Reprocessing the same event reuses the same alert job.
* Two separate failures for the same lead may each generate an alert because they have different event IDs.

### Telegram Owner Alert

Canonical key:

```text
telegram:{lead_event_id}
```

Created by:

```text
deterministic worker or Hermes
```

Recorded through:

```text
lead_events metadata
```

The metadata should record:

```text
telegram_idempotency_key
telegram_message_id
telegram_sent_at
```

Rules:

* The same lead event is not sent twice.
* A different occurrence uses a different lead event ID.
* Telegram failure does not undo an appointment, suppression record, or other completed business action.
* A dedicated alert-delivery table may be added later if event metadata becomes insufficient.

### Twilio Reconciliation Job

Canonical key:

```text
job:reconcile_twilio_call:{call_attempt_id}
```

Created by:

```text
FastAPI or worker
```

Stored in:

```text
jobs.idempotency_key
```

Rules:

* Only one active reconciliation job exists for a call attempt.
* Reconciliation queries provider state before deciding whether redialling is safe.
* It must preserve opt-out, suppression, booking, and escalation outcomes.

### Calendar Reconciliation Job

Canonical key:

```text
job:reconcile_calendar_booking:{appointment_id}
```

Created by:

```text
FastAPI or worker
```

Stored in:

```text
jobs.idempotency_key
```

Rules:

* Reconciliation searches by Google extended property.
* It does not create another event until absence of the original is established.
* Reusing the key returns the existing reconciliation job.

### Stale Session Recovery Job

Canonical key:

```text
job:recover_stale_session:{call_session_id}
```

Created by:

```text
worker recovery scheduler
```

Stored in:

```text
jobs.idempotency_key
```

Rules:

* Only one active recovery job exists for the session.
* Recovery reconciles Twilio status and the ConversationRelay action callback.
* It does not automatically redial.

### Suppression By Phone

Database identity:

```text
suppression_list.phone_e164
```

Database enforcement:

```text
unique phone_e164 where phone_e164 is not null
```

Rules:

* Phone numbers are normalized before insertion.
* Repeated insertion is treated as already suppressed.
* Suppression is checked before creating a call job and immediately before calling Twilio.
* Application logs should reference the suppression row ID rather than placing the full phone number in log-visible keys.

### Suppression By Email

Database identity:

```text
suppression_list.email
```

Database enforcement:

```text
unique email where email is not null
```

Rules:

* Email is normalized according to the approved application policy.
* Repeated insertion is treated as already suppressed.
* Full email addresses must not appear in operational logs.

## Component Responsibilities

### FastAPI

FastAPI creates:

```text
lead:{source_system}:{source_entity_id}
job:initiate_call:{lead_id}:1
twilio:call-status:{...}
twilio:conversationrelay-action:{...}
calendar:{lead_id}:{appointment_type}:{slot_start_utc}:{slot_end_utc}
appointment:{appointment_id}
job:send_confirmation_email:{appointment_id}
job:send_owner_alert:{lead_event_id}
```

FastAPI may also create suppression records when an opt-out occurs during a call.

### Deterministic Worker

The worker creates:

```text
job:initiate_call:{lead_id}:{attempt_number}
call:{lead_id}:{attempt_number}
email:{appointment_id}:confirmation
telegram:{lead_event_id}
job:reconcile_twilio_call:{call_attempt_id}
job:reconcile_calendar_booking:{appointment_id}
job:recover_stale_session:{call_session_id}
```

Before an external action, the worker rechecks:

* job status and lease ownership;
* calling pause state;
* consent;
* suppression;
* lead terminal status;
* attempt limit;
* existing provider identifiers;
* existing side-effect state.

### Hermes

Hermes may:

* invoke authenticated pause and resume endpoints;
* request an approved retry;
* request human follow-up;
* show system summaries;
* send or acknowledge an owner alert.

Hermes must use the same documented keys through approved APIs or scripts.

Hermes must not:

* invent alternative idempotency keys;
* directly claim production jobs;
* bypass consent or suppression;
* repeat external actions based only on conversational judgment.

## Unsafe Key Patterns

The following are prohibited:

```text
random UUID used as the only side-effect identity
timestamp-only key
phone-only call attempt key
model-generated key
raw provider payload embedded in a key
secret or token embedded in a key
one key reused for every retry attempt
one alert key reused for every incident on the same lead
```

## Duplicate Handling Summary

When a duplicate is detected:

1. Retrieve the existing durable record.
2. Preserve its original business outcome.
3. Update delivery or observation metadata where applicable.
4. Return success when the provider expects acknowledgement.
5. Do not repeat an external side effect.
6. Reconcile provider state only when the previous result is uncertain.
7. Record exceptional contradictions through `lead_events`.

## Stage 1 Boundary

This registry defines deterministic formats and ownership only.

Stage 1 does not:

* create application code;
* connect providers;
* execute jobs;
* call leads;
* send emails;
* send Telegram alerts;
* create calendar events.

The SQL migration must enforce all database-backed uniqueness requirements defined here.
