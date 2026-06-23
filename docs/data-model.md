# Data Model

## Purpose

Supabase PostgreSQL is the durable source of truth for the AI voice lead qualification and appointment-booking system.

The first release uses a minimal schema that supports:

* consent-aware lead intake;
* duplicate prevention;
* outbound call attempts;
* active call state;
* AI qualification outcomes;
* appointment booking;
* background jobs and retries;
* provider webhook deduplication;
* opt-out and suppression;
* operational audit history.

The schema supports one business. It remains compatible with a future QuoteFollow integration through generic source fields rather than QuoteFollow-specific tables.

## Design Principles

* Every external side effect has a unique idempotency key.
* Every inbound provider event is recorded before processing.
* Supabase stores durable state; in-memory state is never the only copy of critical information.
* State transitions are explicit and auditable.
* Opt-out and suppression override pending call activity.
* Provider timeouts are reconciled before side effects are repeated.
* Secrets are stored in environment variables, not database rows.
* Full transcripts and recordings are disabled by default.
* New tables are added only when the current model becomes insufficient.

## Core Tables

### `business_settings`

Stores non-secret configuration for the single business.

Fields:

```text
id uuid primary key
business_name text not null
timezone text not null
calling_hours jsonb not null
max_call_attempts integer not null default 3
retry_policy jsonb not null
qualification_config jsonb not null
appointment_types jsonb not null
calendar_id text not null
calling_paused boolean not null default false
created_at timestamptz not null
updated_at timestamptz not null
```

Rules:

* The first release uses one active row.
* API keys, tokens, passwords, and OAuth credentials are never stored here.
* Business-specific qualification rules remain configuration rather than application code where practical.

### `leads`

Stores the person or organisation being contacted.

Fields:

```text
id uuid primary key

source_system text not null
source_entity_type text
source_entity_id text
source_payload jsonb
source_payload_version integer not null default 1

full_name text
company_name text
phone_e164 text not null
email text
timezone text

status text not null
qualification_result text
qualification_summary text

consent_status text not null
consent_source text
consent_captured_at timestamptz
consent_evidence_uri text

next_call_after timestamptz
attempt_count integer not null default 0

created_at timestamptz not null
updated_at timestamptz not null
```

Recommended unique constraint:

```text
unique (source_system, source_entity_id)
```

The constraint applies when `source_entity_id` is present.

Lead statuses:

```text
new
eligible
scheduled
calling
qualified
not_qualified
booked
needs_human
opted_out
unreachable
invalid_phone
failed
suppressed
```

Consent statuses:

```text
unknown
specific_automated_call_consent
service_follow_up_approved
withdrawn
do_not_call
blocked
```

Rules:

* `unknown`, `withdrawn`, `do_not_call`, and `blocked` leads are not callable.
* A matching suppression record blocks calling regardless of lead status.
* QuoteFollow may later use:

```text
source_system = quotefollow
source_entity_type = quote
source_entity_id = <quote ID>
```

Quote-specific details remain in `source_payload` until a dedicated QuoteFollow integration is approved.

### `lead_events`

Append-only event history for significant lead activity.

Fields:

```text
id uuid primary key
lead_id uuid not null references leads(id)
event_type text not null
actor_type text not null
actor_id text
summary text not null
metadata jsonb
created_at timestamptz not null
```

Actor types:

```text
api
worker
hermes
owner
provider
system
```

Examples:

```text
lead_created
consent_validated
call_scheduled
call_completed
qualification_completed
opted_out
human_escalation_requested
confirmation_email_sent
telegram_alert_sent
manual_retry_requested
```

This table replaces separate early-stage tables for qualification results, human escalations, email records, admin commands, and general audit events.

### `call_attempts`

Stores one outbound call attempt.

Fields:

```text
id uuid primary key
lead_id uuid not null references leads(id)
attempt_number integer not null
idempotency_key text not null unique

twilio_call_sid text unique
status text not null
twilio_status text

started_at timestamptz
answered_at timestamptz
ended_at timestamptz
duration_seconds integer

failure_category text
failure_detail_redacted text
voicemail_detected boolean not null default false

created_at timestamptz not null
updated_at timestamptz not null
```

Idempotency format:

```text
call:{lead_id}:{attempt_number}
```

Call statuses:

```text
scheduled
dialing
ringing
in_progress
completed
no_answer
busy
voicemail
failed
timed_out
cancelled
escalated
```

Rules:

* Only one call attempt may exist for the same lead and attempt number.
* Terminal statuses are not overwritten except through an explicit reconciliation event.
* Retrying creates a new attempt rather than resetting an old attempt.

### `call_sessions`

Stores the ConversationRelay and AI conversation state for an answered call.

Fields:

```text
id uuid primary key
call_attempt_id uuid not null unique references call_attempts(id)
twilio_call_sid text not null
conversationrelay_connection_id text

status text not null
last_sequence_number bigint
last_activity_at timestamptz

conversation_summary text
structured_facts jsonb
qualification_result text
end_reason text

started_at timestamptz not null
ended_at timestamptz

created_at timestamptz not null
updated_at timestamptz not null
```

Session statuses:

```text
opening
active
ending
closed
stale
failed
```

Privacy rules:

* Store a redacted summary and structured facts by default.
* Do not store full audio recordings by default.
* Do not store full transcripts unless retention, encryption, access, and consent requirements are approved.
* A future `conversation_turns` table may be added if production debugging or audit requirements justify it.

### `appointments`

Stores the local appointment and Google Calendar result.

Fields:

```text
id uuid primary key
lead_id uuid not null references leads(id)
call_attempt_id uuid references call_attempts(id)

appointment_type text not null
slot_start timestamptz not null
slot_end timestamptz not null
timezone text not null

status text not null
booking_idempotency_key text not null unique

calendar_id text not null
google_event_id text unique
google_extended_property_value text not null

confirmation_status text not null default 'not_requested'
resend_message_id text
confirmation_sent_at timestamptz

created_at timestamptz not null
updated_at timestamptz not null
```

Appointment statuses:

```text
proposed
booking_pending
booked
confirmed
conflict
cancelled
failed
needs_human
```

Confirmation statuses:

```text
not_requested
pending
sent
failed
```

Idempotency format:

```text
calendar:{lead_id}:{appointment_type}:{slot_start_utc}:{slot_end_utc}
```

Rules:

* Create the local appointment before calling Google Calendar.
* `booked` requires a stored `google_event_id`.
* After a timeout, search by the extended property before creating another event.
* Confirmation email state remains on the appointment for the first release.
* Email attempts and failures are also recorded in `lead_events`.

### `jobs`

Stores durable background work processed by the deterministic worker.

Fields:

```text
id uuid primary key
job_type text not null
status text not null
idempotency_key text not null unique

entity_type text not null
entity_id uuid not null
payload jsonb

run_after timestamptz not null
attempt_count integer not null default 0
max_attempts integer not null default 3

locked_by text
locked_at timestamptz
lease_expires_at timestamptz

last_error_category text
last_error_redacted text

created_at timestamptz not null
updated_at timestamptz not null
```

Job statuses:

```text
pending
claimed
running
retry_scheduled
succeeded
failed
dead_lettered
cancelled
```

Initial job types:

```text
initiate_call
send_confirmation_email
send_owner_alert
reconcile_twilio_call
reconcile_calendar_booking
recover_stale_session
```

Rules:

* The deterministic worker claims jobs using database locking.
* A worker lease prevents a crashed worker from holding a job forever.
* Hermes does not directly claim the production job queue.
* Pending jobs are cancelled when consent is withdrawn, the lead is suppressed, or the requested action becomes unsafe.
* Retry limits are always bounded.

### `provider_events`

Stores inbound provider events before business logic is applied.

Fields:

```text
id uuid primary key
provider text not null
event_type text not null

provider_event_id text
provider_object_id text
idempotency_key text not null
payload_hash text not null

signature_valid boolean not null
processing_status text not null

first_seen_at timestamptz not null
last_seen_at timestamptz not null
processed_at timestamptz

error_detail_redacted text
```

Unique constraint:

```text
unique (provider, idempotency_key)
```

Processing statuses:

```text
received
processed
duplicate
ignored
failed
```

Example keys:

```text
twilio:{provider_event_id}
twilio:{call_sid}:{event_type}:{payload_hash}
resend:{event_id}
telegram:{update_id}
```

Rules:

* Invalid signatures create no business side effects.
* Duplicate events return a successful acknowledgement where required.
* Raw provider payload retention is limited and configurable.

### `suppression_list`

Stores opt-out and do-not-call records independently of lead status.

Fields:

```text
id uuid primary key
phone_e164 text
email text
lead_id uuid references leads(id)

reason text not null
source text not null
created_at timestamptz not null
```

Constraints:

```text
unique phone_e164 where phone_e164 is not null
unique email where email is not null
```

Rules:

* A matching phone number blocks all future automated calls.
* Suppression records are checked immediately before job creation and immediately before call initiation.
* Suppression records are retained as long as necessary to honour the opt-out.

## Relationships

```text
business_settings
    one active configuration

leads
    has many lead_events
    has many call_attempts
    has many appointments
    may have many jobs
    may have one or more suppression records

call_attempts
    has zero or one call_session
    may produce one appointment

provider_events
    may reference provider objects before local correlation is available
```

## Required Indexes

Create indexes for:

```text
leads(status, next_call_after)
leads(phone_e164)
call_attempts(lead_id, attempt_number)
call_attempts(twilio_call_sid)
call_sessions(twilio_call_sid)
appointments(lead_id, status)
jobs(status, run_after)
jobs(lease_expires_at)
provider_events(provider, idempotency_key)
suppression_list(phone_e164)
```

## State Transition Rules

Lead:

```text
new -> eligible -> scheduled -> calling
calling -> qualified | not_qualified | booked | needs_human
calling -> unreachable | failed | opted_out
any callable state -> suppressed
```

Call attempt:

```text
scheduled -> dialing -> ringing -> in_progress
in_progress -> completed | timed_out | failed | escalated
dialing or ringing -> no_answer | busy | voicemail | failed
```

Appointment:

```text
proposed -> booking_pending -> booked -> confirmed
booking_pending -> conflict | failed | needs_human
```

Job:

```text
pending -> claimed -> running -> succeeded
running -> retry_scheduled -> pending
running -> failed -> dead_lettered
pending or retry_scheduled -> cancelled
```

Terminal state changes require an append-only `lead_events` record explaining the reconciliation or manual override.

## Retention Defaults

* Suppression records: retained to continue honouring opt-outs.
* Lead and appointment data: retained for the approved business period.
* Provider events: retain hashes and redacted metadata; limit raw payload storage.
* Call summaries: retain only as long as operationally required.
* Full transcripts and recordings: disabled by default.
* Redacted operational events: retained long enough for troubleshooting and complaints.
