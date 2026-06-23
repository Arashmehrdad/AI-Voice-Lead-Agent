# Idempotency Registry

## Scope

Every external side effect and inbound provider event must use a deterministic idempotency key. This registry defines the key formats, creating component, storage location, and duplicate behavior for Stage 1.

## Rules

- Keys are lowercase prefixes with colon-delimited fields.
- UUID values use canonical string form.
- Timestamps in keys use UTC ISO-like compact values or database timestamptz-derived canonical strings chosen during implementation.
- Provider event idempotency records are inserted before business logic runs.
- Duplicate requests return successful acknowledgements when safe and do not repeat side effects.
- If a key cannot be constructed safely, the side effect must not run.

## Registry

| Purpose | Format | Created By | Stored In | Unique Constraint | Duplicate Behavior |
| --- | --- | --- | --- | --- | --- |
| Lead source intake | `lead:{source_system}:{source_entity_id}` | FastAPI | `leads.source_system`, `leads.source_entity_id` | partial unique index on `(source_system, source_entity_id)` where `source_entity_id is not null` | Return existing lead and existing active job if present |
| Initiate-call job | `job:initiate_call:{lead_id}` | FastAPI or worker | `jobs.idempotency_key` | `jobs.idempotency_key unique` | Reuse existing pending/running/succeeded job; do not create another call job |
| Call attempt | `call:{lead_id}:{attempt_number}` | deterministic worker | `call_attempts.idempotency_key` | `call_attempts.idempotency_key unique`; unique `(lead_id, attempt_number)` | Return existing attempt; do not request another Twilio call for the same attempt |
| Twilio call-status event with provider event ID | `twilio:{provider_event_id}` | FastAPI webhook | `provider_events.idempotency_key` | unique `(provider, idempotency_key)` | Acknowledge duplicate and do not repeat status side effects |
| Twilio call-status event without provider event ID | `twilio:{call_sid}:{event_type}:{payload_hash}` | FastAPI webhook | `provider_events.idempotency_key` | unique `(provider, idempotency_key)` | Acknowledge duplicate and do not repeat status side effects |
| ConversationRelay WebSocket event | `twilio_ws:{call_sid}:{sequence_number}:{event_type}` | FastAPI voice gateway | `call_sessions.last_sequence_number` and optional `provider_events` if persisted | one session per `call_attempt_id`; monotonic sequence rule | Ignore repeated or older sequence numbers |
| Calendar booking | `calendar:{lead_id}:{appointment_type}:{slot_start_utc}:{slot_end_utc}` | FastAPI voice gateway | `appointments.booking_idempotency_key` | `appointments.booking_idempotency_key unique` | Return existing appointment; reconcile Google event before retrying creation |
| Google Calendar extended property | `appointment:{appointment_id}` | FastAPI voice gateway | `appointments.google_extended_property_value` | `appointments.google_extended_property_value unique` | Search Google Calendar by property before retrying uncertain event creation |
| Confirmation email job | `job:send_confirmation_email:{appointment_id}` | FastAPI or worker | `jobs.idempotency_key` | `jobs.idempotency_key unique` | Reuse existing job |
| Confirmation email side effect | `email:{appointment_id}:confirmation` | deterministic worker | `appointments` via `confirmation_status`, `resend_message_id`; later email table if scoped | one appointment row; status guard | Do not send again when `confirmation_status = 'sent'` |
| Owner alert job | `job:send_owner_alert:{event_type}:{entity_id}` | FastAPI, worker, or Hermes | `jobs.idempotency_key` | `jobs.idempotency_key unique` | Reuse existing alert job |
| Telegram owner alert side effect | `telegram:{event_type}:{entity_id}` | deterministic worker or Hermes | `lead_events.metadata` or future alert table | Stage 1 documents key only; no dedicated table | Do not send a duplicate alert if an equivalent sent event exists |
| Twilio reconciliation job | `job:reconcile_twilio_call:{call_attempt_id}` | FastAPI or worker | `jobs.idempotency_key` | `jobs.idempotency_key unique` | Reuse existing reconciliation job |
| Calendar reconciliation job | `job:reconcile_calendar_booking:{appointment_id}` | FastAPI or worker | `jobs.idempotency_key` | `jobs.idempotency_key unique` | Reuse existing reconciliation job |
| Stale session recovery job | `job:recover_stale_session:{call_session_id}` | worker recovery scheduler | `jobs.idempotency_key` | `jobs.idempotency_key unique` | Reuse existing recovery job |
| Suppression record by phone | `suppress:phone:{phone_e164}` | FastAPI voice gateway, Hermes, or approved script | `suppression_list.phone_e164` | partial unique index on `phone_e164 where phone_e164 is not null` | Treat as already suppressed |
| Suppression record by email | `suppress:email:{email_normalized}` | FastAPI voice gateway, Hermes, or approved script | `suppression_list.email` | partial unique index on `email where email is not null` | Treat as already suppressed |

## Component Responsibilities

### FastAPI

Creates:

- `lead:{source_system}:{source_entity_id}`
- `job:initiate_call:{lead_id}`
- Twilio provider event keys
- ConversationRelay sequence keys or sequence guards
- `calendar:{lead_id}:{appointment_type}:{slot_start_utc}:{slot_end_utc}`
- `appointment:{appointment_id}`
- `job:send_confirmation_email:{appointment_id}`
- suppression keys when opt-out happens during a call

### Deterministic Worker

Creates:

- `call:{lead_id}:{attempt_number}`
- `job:send_owner_alert:{event_type}:{entity_id}`
- `telegram:{event_type}:{entity_id}`
- reconciliation and recovery job keys

The worker must recheck consent, suppression, pause state, terminal lead states, and existing attempts before external side effects.

### Hermes

Creates or requests:

- administrative pause/resume actions through authenticated internal endpoints
- owner alert acknowledgement events
- approved manual retry jobs using the documented `jobs.idempotency_key` formats

Hermes does not directly claim production jobs and is not in the voice loop.

## Unsafe Or Unapproved Keys

Do not use:

- random UUID-only idempotency keys for external side effects;
- phone-only call attempt keys;
- timestamp-only keys;
- model-generated keys;
- keys containing secrets, access tokens, or full raw provider payloads.

## Source Identifier Requirement

For lead intake, `source_entity_id` is strongly recommended and required for reliable deduplication. If a trusted source cannot provide one, a future implementation must define a deterministic fallback before enabling automated calling for that source.

Stage 1 does not approve a fallback that could create duplicate calls.

