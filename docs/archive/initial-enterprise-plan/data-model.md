# Data Model

## Principles

- Supabase PostgreSQL is the durable source of truth.
- Provider APIs are external systems of record only for their domain: Twilio calls, Google Calendar events, Resend emails, Telegram messages.
- Every external side effect has an idempotency key.
- Every inbound provider event has duplicate prevention.
- State transitions are explicit and auditable.
- Personally identifiable information is minimized and redacted in logs.
- The model should remain reusable for a future QuoteFollow project by keeping business-specific fields in `lead_payload`, `appointment_types`, templates, and qualification criteria until QuoteFollow requirements justify dedicated tables.

## Core Tables

### `business_settings`

Single-row configuration for the business.

Fields:
- `id uuid primary key`
- `business_name text not null`
- `timezone text not null`
- `calling_hours jsonb not null`
- `max_call_attempts int not null default 3`
- `appointment_types jsonb not null`
- `calendar_id text not null`
- `owner_telegram_chat_id text`
- `created_at timestamptz not null`
- `updated_at timestamptz not null`

Notes:
- Keep non-secret operational settings here.
- Secrets stay in environment variables, not database rows.

### `leads`

Represents a person or business contact to qualify.

Fields:
- `id uuid primary key`
- `external_source text`
- `external_id text`
- `full_name text`
- `company_name text`
- `phone_e164 text not null`
- `email text`
- `timezone text`
- `lead_payload jsonb`
- `status lead_status not null`
- `qualification_status qualification_status not null default 'unknown'`
- `consent_status consent_status not null`
- `consent_source text`
- `consent_captured_at timestamptz`
- `consent_evidence_uri text`
- `last_call_attempt_at timestamptz`
- `next_call_after timestamptz`
- `attempt_count int not null default 0`
- `suppressed_at timestamptz`
- `suppression_reason text`
- `created_at timestamptz not null`
- `updated_at timestamptz not null`

Unique keys:
- `(external_source, external_id)` when both are present.
- Normalized dedupe index chosen during implementation, likely `(phone_e164, external_source)` or a configurable dedupe key.

Statuses:
- `new`
- `eligible`
- `scheduled`
- `calling`
- `qualified`
- `not_qualified`
- `booked`
- `needs_human`
- `opted_out`
- `unreachable`
- `invalid_phone`
- `failed`
- `suppressed`

Consent statuses:
- `unknown`
- `not_required_for_purpose`
- `specific_automated_call_consent`
- `live_call_only`
- `withdrawn`
- `do_not_call`
- `blocked`

Rules:
- Leads with `unknown`, `withdrawn`, `do_not_call`, or `blocked` consent are not callable.
- For UK automated marketing calls, require `specific_automated_call_consent` unless legal review confirms the call is outside that category.
- Future QuoteFollow fields such as quote ID, quote value, quote expiry, quote status, and estimator notes should initially live in `lead_payload` or a later scoped `quotes` table, not be hardcoded into the core calling model.

### `lead_events`

Append-only lead audit timeline.

Fields:
- `id uuid primary key`
- `lead_id uuid references leads(id)`
- `event_type text not null`
- `actor_type text not null`
- `actor_id text`
- `summary text not null`
- `metadata jsonb`
- `created_at timestamptz not null`

Actor types:
- `system`
- `owner`
- `hermes`
- `fastapi`
- `provider`

### `call_attempts`

One outbound call attempt.

Fields:
- `id uuid primary key`
- `lead_id uuid references leads(id)`
- `twilio_call_sid text`
- `idempotency_key text not null unique`
- `attempt_number int not null`
- `status call_attempt_status not null`
- `twilio_status text`
- `started_at timestamptz`
- `answered_at timestamptz`
- `ended_at timestamptz`
- `duration_seconds int`
- `failure_category text`
- `failure_detail text`
- `voicemail_detected boolean not null default false`
- `human_escalation_id uuid`
- `created_at timestamptz not null`
- `updated_at timestamptz not null`

Idempotency key:
- `call_attempt:{lead_id}:{attempt_number}`

Statuses:
- `scheduled`
- `dialing`
- `ringing`
- `in_progress`
- `completed`
- `no_answer`
- `busy`
- `voicemail`
- `failed`
- `timed_out`
- `cancelled`
- `escalated`

### `call_sessions`

Runtime session state for ConversationRelay.

Fields:
- `id uuid primary key`
- `call_attempt_id uuid references call_attempts(id)`
- `twilio_call_sid text not null`
- `conversationrelay_connection_id text`
- `status call_session_status not null`
- `model_session_id text`
- `last_sequence_number bigint`
- `last_activity_at timestamptz`
- `started_at timestamptz not null`
- `ended_at timestamptz`
- `end_reason text`
- `created_at timestamptz not null`
- `updated_at timestamptz not null`

Statuses:
- `opening`
- `active`
- `ending`
- `closed`
- `stale`
- `failed`

### `conversation_turns`

Structured call transcript and decision record.

Fields:
- `id uuid primary key`
- `call_session_id uuid references call_sessions(id)`
- `sequence_number bigint not null`
- `speaker text not null`
- `text_redacted text`
- `text_full_encrypted text`
- `intent text`
- `model_decision jsonb`
- `latency_ms int`
- `created_at timestamptz not null`

Unique keys:
- `(call_session_id, sequence_number, speaker)`

Retention:
- Default stores redacted text and structured summaries.
- Full transcript storage requires owner approval, encryption, retention period, and access policy.

### `qualification_results`

Final or intermediate qualification result.

Fields:
- `id uuid primary key`
- `lead_id uuid references leads(id)`
- `call_attempt_id uuid references call_attempts(id)`
- `status qualification_status not null`
- `score numeric`
- `criteria jsonb not null`
- `summary text`
- `next_action text`
- `created_at timestamptz not null`

Statuses:
- `unknown`
- `qualified`
- `not_qualified`
- `needs_human`
- `insufficient_info`

### `appointments`

Local booking record tied to Google Calendar.

Fields:
- `id uuid primary key`
- `lead_id uuid references leads(id)`
- `call_attempt_id uuid references call_attempts(id)`
- `appointment_type text not null`
- `status appointment_status not null`
- `slot_start timestamptz not null`
- `slot_end timestamptz not null`
- `timezone text not null`
- `calendar_id text not null`
- `google_event_id text`
- `booking_idempotency_key text not null unique`
- `google_extended_property_key text not null`
- `confirmation_email_id uuid`
- `created_at timestamptz not null`
- `updated_at timestamptz not null`

Statuses:
- `proposed`
- `booking_pending`
- `booked`
- `confirmation_pending`
- `confirmed`
- `conflict`
- `cancelled`
- `failed`
- `needs_human`

Idempotency key:
- `calendar_booking:{lead_id}:{appointment_type}:{slot_start_utc}:{slot_end_utc}`

Calendar rule:
- Never create a second Google Calendar event if an appointment exists with the same booking idempotency key.
- On timeout, reconcile by Google extended property before retrying.

### `email_messages`

Outbound email state.

Fields:
- `id uuid primary key`
- `lead_id uuid references leads(id)`
- `appointment_id uuid references appointments(id)`
- `provider text not null default 'resend'`
- `provider_message_id text`
- `idempotency_key text not null unique`
- `to_email text not null`
- `template text not null`
- `status email_status not null`
- `failure_detail text`
- `sent_at timestamptz`
- `created_at timestamptz not null`
- `updated_at timestamptz not null`

Statuses:
- `pending`
- `sending`
- `sent`
- `delivered`
- `bounced`
- `failed`
- `suppressed`

Idempotency key:
- `confirmation_email:{appointment_id}`

### `jobs`

Persistent Hermes job state.

Fields:
- `id uuid primary key`
- `job_type text not null`
- `status job_status not null`
- `idempotency_key text not null unique`
- `entity_type text not null`
- `entity_id uuid not null`
- `payload jsonb`
- `run_after timestamptz not null`
- `attempt_count int not null default 0`
- `max_attempts int not null default 3`
- `locked_by text`
- `locked_at timestamptz`
- `last_error text`
- `created_at timestamptz not null`
- `updated_at timestamptz not null`

Statuses:
- `pending`
- `claimed`
- `running`
- `succeeded`
- `retry_scheduled`
- `failed`
- `dead_lettered`
- `cancelled`

Job types:
- `schedule_call`
- `initiate_call`
- `send_confirmation_email`
- `send_owner_alert`
- `recover_stale_call`
- `recover_calendar_booking`
- `reconcile_twilio_call`
- `reconcile_calendar_event`
- `cleanup_expired_data`
- `backup_check`

Claiming:
- Hermes claims due jobs with row locking.
- Stale claimed jobs are released by recovery after a configured lease timeout.

### `provider_events`

Inbound webhook and provider event deduplication.

Fields:
- `id uuid primary key`
- `provider text not null`
- `event_type text not null`
- `provider_event_id text`
- `provider_object_id text`
- `idempotency_key text not null`
- `payload_hash text not null`
- `signature_valid boolean not null`
- `processing_status provider_event_status not null`
- `first_seen_at timestamptz not null`
- `last_seen_at timestamptz not null`
- `processed_at timestamptz`
- `error_detail text`

Unique keys:
- `(provider, idempotency_key)`

Statuses:
- `received`
- `processed`
- `duplicate`
- `ignored`
- `failed`

Idempotency key examples:
- `twilio:{event_id}` when Twilio provides an event ID.
- `twilio:{call_sid}:{event_type}:{payload_hash}` otherwise.
- `resend:{event_id}`
- `telegram:{update_id}`

### `human_escalations`

Owner follow-up requests.

Fields:
- `id uuid primary key`
- `lead_id uuid references leads(id)`
- `call_attempt_id uuid references call_attempts(id)`
- `status escalation_status not null`
- `reason text not null`
- `summary text not null`
- `priority text not null`
- `telegram_message_id text`
- `assigned_to text`
- `acknowledged_at timestamptz`
- `resolved_at timestamptz`
- `resolution_note text`
- `created_at timestamptz not null`
- `updated_at timestamptz not null`

Statuses:
- `open`
- `alert_pending`
- `alert_sent`
- `acknowledged`
- `resolved`
- `cancelled`

### `admin_commands`

Telegram and owner administrative action audit.

Fields:
- `id uuid primary key`
- `source text not null`
- `source_message_id text`
- `actor_id text not null`
- `command text not null`
- `arguments jsonb`
- `status admin_command_status not null`
- `result_summary text`
- `created_at timestamptz not null`
- `completed_at timestamptz`

Statuses:
- `received`
- `authorized`
- `rejected`
- `succeeded`
- `failed`

### `audit_logs`

Security and data access audit.

Fields:
- `id uuid primary key`
- `actor_type text not null`
- `actor_id text`
- `action text not null`
- `entity_type text`
- `entity_id uuid`
- `ip_address inet`
- `user_agent text`
- `metadata_redacted jsonb`
- `created_at timestamptz not null`

### `suppression_list`

Do-not-call and opt-out records.

Fields:
- `id uuid primary key`
- `phone_e164 text`
- `email text`
- `reason text not null`
- `source text not null`
- `lead_id uuid references leads(id)`
- `created_at timestamptz not null`

Unique keys:
- `phone_e164` where not null.
- `email` where not null.

## Relationships

- `leads` has many `call_attempts`.
- `call_attempts` has zero or one active `call_sessions`.
- `call_sessions` has many `conversation_turns`.
- `leads` has many `qualification_results`.
- `leads` has zero or many `appointments`, but normally one active booked appointment.
- `appointments` has zero or one `email_messages` confirmation.
- `leads` has many `lead_events`.
- `jobs` points to one entity by `entity_type` and `entity_id`.
- `provider_events` may reference provider object IDs rather than local IDs because events can arrive before local correlation.
- `human_escalations` belongs to one lead and optionally one call attempt.

## State Transition Rules

Lead:
- `new` -> `eligible` after consent and dedupe checks.
- `eligible` -> `scheduled` when call job created.
- `scheduled` -> `calling` when call attempt starts.
- `calling` -> `qualified`, `not_qualified`, `booked`, `needs_human`, `opted_out`, `unreachable`, or `failed`.
- Any callable state -> `opted_out` when opt-out is received.
- Any callable state -> `suppressed` when suppression record is added.

Call attempt:
- `scheduled` -> `dialing` -> `ringing` -> `in_progress` -> terminal status.
- Terminal statuses: `completed`, `no_answer`, `busy`, `voicemail`, `failed`, `timed_out`, `cancelled`, `escalated`.
- Terminal statuses are not overwritten except by explicit reconciliation that records the previous value in `lead_events`.

Appointment:
- `proposed` -> `booking_pending` -> `booked` -> `confirmation_pending` -> `confirmed`.
- `booking_pending` -> `conflict`, `failed`, or `needs_human`.
- `booked` is only allowed when `google_event_id` is present.

Job:
- `pending` -> `claimed` -> `running` -> `succeeded`.
- `running` -> `retry_scheduled` -> `pending` when retryable.
- `running` -> `dead_lettered` when retry budget is exhausted.
- Any pending job may become `cancelled` if the entity enters a terminal state that makes the job unsafe.

## Retention And Privacy Defaults

- Leads: retain for the approved business retention period.
- Suppression records: retain as long as needed to honor opt-outs.
- Provider events: retain payload hashes and redacted metadata; raw payload retention must be time-limited.
- Conversation turns: retain redacted summaries by default.
- Full transcripts or recordings: disabled unless separately approved.
- Audit logs: retain long enough to investigate access, complaints, and operational incidents.
