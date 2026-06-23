-- Stage 1 initial schema for the single-business AI voice lead agent.
-- This migration intentionally creates only the nine initial tables approved in
-- docs/data-model.md. Status values are text columns with CHECK constraints.

create extension if not exists pgcrypto;

create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

create table public.business_settings (
  id uuid primary key default gen_random_uuid(),
  business_name text not null,
  timezone text not null,
  calling_hours jsonb not null,
  max_call_attempts integer not null default 3,
  retry_policy jsonb not null,
  qualification_config jsonb not null,
  appointment_types jsonb not null,
  calendar_id text not null,
  calling_paused boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint business_settings_max_call_attempts_positive
    check (max_call_attempts > 0)
);

create unique index business_settings_single_row_idx
  on public.business_settings ((true));

create trigger set_business_settings_updated_at
before update on public.business_settings
for each row execute function public.set_updated_at();

create table public.leads (
  id uuid primary key default gen_random_uuid(),
  source_system text not null,
  source_entity_type text,
  source_entity_id text,
  source_payload jsonb,
  source_payload_version integer not null default 1,
  full_name text,
  company_name text,
  phone_e164 text not null,
  email text,
  timezone text,
  status text not null,
  qualification_result text,
  qualification_summary text,
  consent_status text not null,
  consent_source text,
  consent_captured_at timestamptz,
  consent_evidence_uri text,
  next_call_after timestamptz,
  attempt_count integer not null default 0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint leads_source_payload_version_positive
    check (source_payload_version > 0),
  constraint leads_attempt_count_nonnegative
    check (attempt_count >= 0),
  constraint leads_status_check
    check (status in (
      'new',
      'eligible',
      'scheduled',
      'calling',
      'qualified',
      'not_qualified',
      'booked',
      'needs_human',
      'opted_out',
      'unreachable',
      'invalid_phone',
      'failed',
      'suppressed'
    )),
  constraint leads_qualification_result_check
    check (
      qualification_result is null
      or qualification_result in (
        'unknown',
        'qualified',
        'not_qualified',
        'needs_human',
        'insufficient_info'
      )
    ),
  constraint leads_consent_status_check
    check (consent_status in (
      'unknown',
      'specific_automated_call_consent',
      'service_follow_up_approved',
      'withdrawn',
      'do_not_call',
      'blocked'
    )),
  constraint leads_phone_e164_shape
    check (phone_e164 ~ '^\+[1-9][0-9]{7,14}$'),
  constraint leads_email_shape
    check (email is null or position('@' in email) > 1)
);

create unique index leads_source_entity_unique_idx
  on public.leads (source_system, source_entity_id)
  where source_entity_id is not null;

create index leads_status_next_call_after_idx
  on public.leads (status, next_call_after);

create index leads_phone_e164_idx
  on public.leads (phone_e164);

create index leads_consent_status_idx
  on public.leads (consent_status);

create trigger set_leads_updated_at
before update on public.leads
for each row execute function public.set_updated_at();

create table public.lead_events (
  id uuid primary key default gen_random_uuid(),
  lead_id uuid not null references public.leads(id) on delete cascade,
  event_type text not null,
  actor_type text not null,
  actor_id text,
  summary text not null,
  metadata jsonb,
  created_at timestamptz not null default now(),
  constraint lead_events_actor_type_check
    check (actor_type in (
      'api',
      'worker',
      'hermes',
      'owner',
      'provider',
      'system'
    ))
);

create index lead_events_lead_id_created_at_idx
  on public.lead_events (lead_id, created_at desc);

create index lead_events_event_type_idx
  on public.lead_events (event_type);

create table public.call_attempts (
  id uuid primary key default gen_random_uuid(),
  lead_id uuid not null references public.leads(id) on delete cascade,
  attempt_number integer not null,
  idempotency_key text not null unique,
  twilio_call_sid text unique,
  status text not null,
  twilio_status text,
  started_at timestamptz,
  answered_at timestamptz,
  ended_at timestamptz,
  duration_seconds integer,
  failure_category text,
  failure_detail_redacted text,
  voicemail_detected boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint call_attempts_attempt_number_positive
    check (attempt_number > 0),
  constraint call_attempts_duration_nonnegative
    check (duration_seconds is null or duration_seconds >= 0),
  constraint call_attempts_time_order_check
    check (
      ended_at is null
      or started_at is null
      or ended_at >= started_at
    ),
  constraint call_attempts_status_check
    check (status in (
      'scheduled',
      'dialing',
      'ringing',
      'in_progress',
      'completed',
      'no_answer',
      'busy',
      'voicemail',
      'failed',
      'timed_out',
      'cancelled',
      'escalated'
    )),
  constraint call_attempts_idempotency_key_shape
    check (idempotency_key ~ '^call:[0-9a-f-]{36}:[0-9]+$')
);

create unique index call_attempts_lead_attempt_number_idx
  on public.call_attempts (lead_id, attempt_number);

create index call_attempts_lead_id_attempt_number_idx
  on public.call_attempts (lead_id, attempt_number);

create index call_attempts_twilio_call_sid_idx
  on public.call_attempts (twilio_call_sid);

create index call_attempts_status_idx
  on public.call_attempts (status);

create trigger set_call_attempts_updated_at
before update on public.call_attempts
for each row execute function public.set_updated_at();

create table public.call_sessions (
  id uuid primary key default gen_random_uuid(),
  call_attempt_id uuid not null unique references public.call_attempts(id) on delete cascade,
  twilio_call_sid text not null,
  conversationrelay_connection_id text,
  status text not null,
  last_sequence_number bigint,
  last_activity_at timestamptz,
  conversation_summary text,
  structured_facts jsonb,
  qualification_result text,
  end_reason text,
  started_at timestamptz not null default now(),
  ended_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint call_sessions_status_check
    check (status in (
      'opening',
      'active',
      'ending',
      'closed',
      'stale',
      'failed'
    )),
  constraint call_sessions_qualification_result_check
    check (
      qualification_result is null
      or qualification_result in (
        'unknown',
        'qualified',
        'not_qualified',
        'needs_human',
        'insufficient_info'
      )
    ),
  constraint call_sessions_sequence_nonnegative
    check (last_sequence_number is null or last_sequence_number >= 0),
  constraint call_sessions_time_order_check
    check (ended_at is null or ended_at >= started_at)
);

create index call_sessions_twilio_call_sid_idx
  on public.call_sessions (twilio_call_sid);

create index call_sessions_status_last_activity_idx
  on public.call_sessions (status, last_activity_at);

create trigger set_call_sessions_updated_at
before update on public.call_sessions
for each row execute function public.set_updated_at();

create table public.appointments (
  id uuid primary key default gen_random_uuid(),
  lead_id uuid not null references public.leads(id) on delete cascade,
  call_attempt_id uuid references public.call_attempts(id) on delete set null,
  appointment_type text not null,
  slot_start timestamptz not null,
  slot_end timestamptz not null,
  timezone text not null,
  status text not null,
  booking_idempotency_key text not null unique,
  calendar_id text not null,
  google_event_id text unique,
  google_extended_property_value text not null unique,
  confirmation_status text not null default 'not_requested',
  resend_message_id text,
  confirmation_sent_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint appointments_slot_order_check
    check (slot_end > slot_start),
  constraint appointments_status_check
    check (status in (
      'proposed',
      'booking_pending',
      'booked',
      'confirmed',
      'conflict',
      'cancelled',
      'failed',
      'needs_human'
    )),
  constraint appointments_confirmation_status_check
    check (confirmation_status in (
      'not_requested',
      'pending',
      'sent',
      'failed'
    )),
  constraint appointments_booked_requires_google_event_id
    check (
      status not in ('booked', 'confirmed')
      or google_event_id is not null
    ),
  constraint appointments_confirmation_sent_requires_timestamp
    check (
      confirmation_status <> 'sent'
      or confirmation_sent_at is not null
    ),
  constraint appointments_booking_key_shape
    check (booking_idempotency_key ~ '^calendar:[0-9a-f-]{36}:.+:.+:.+$'),
  constraint appointments_google_extended_property_shape
    check (google_extended_property_value ~ '^appointment:[0-9a-f-]{36}$')
);

create index appointments_lead_id_status_idx
  on public.appointments (lead_id, status);

create index appointments_slot_start_idx
  on public.appointments (slot_start);

create index appointments_call_attempt_id_idx
  on public.appointments (call_attempt_id);

create trigger set_appointments_updated_at
before update on public.appointments
for each row execute function public.set_updated_at();

create table public.jobs (
  id uuid primary key default gen_random_uuid(),
  job_type text not null,
  status text not null,
  idempotency_key text not null unique,
  entity_type text not null,
  entity_id uuid not null,
  payload jsonb,
  run_after timestamptz not null,
  attempt_count integer not null default 0,
  max_attempts integer not null default 3,
  locked_by text,
  locked_at timestamptz,
  lease_expires_at timestamptz,
  last_error_category text,
  last_error_redacted text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint jobs_job_type_check
    check (job_type in (
      'initiate_call',
      'send_confirmation_email',
      'send_owner_alert',
      'reconcile_twilio_call',
      'reconcile_calendar_booking',
      'recover_stale_session'
    )),
  constraint jobs_status_check
    check (status in (
      'pending',
      'claimed',
      'running',
      'retry_scheduled',
      'succeeded',
      'failed',
      'dead_lettered',
      'cancelled'
    )),
  constraint jobs_entity_type_check
    check (entity_type in (
      'lead',
      'call_attempt',
      'call_session',
      'appointment',
      'provider_event',
      'business_settings'
    )),
  constraint jobs_attempt_count_nonnegative
    check (attempt_count >= 0),
  constraint jobs_max_attempts_positive
    check (max_attempts > 0),
  constraint jobs_attempt_count_not_over_max
    check (attempt_count <= max_attempts),
  constraint jobs_lock_fields_consistent
    check (
      (
        status not in ('claimed', 'running')
        and locked_by is null
        and locked_at is null
        and lease_expires_at is null
      )
      or
      (
        status in ('claimed', 'running')
        and locked_by is not null
        and locked_at is not null
        and lease_expires_at is not null
        and lease_expires_at > locked_at
      )
    )
);

create index jobs_status_run_after_idx
  on public.jobs (status, run_after);

create index jobs_lease_expires_at_idx
  on public.jobs (lease_expires_at);

create index jobs_entity_idx
  on public.jobs (entity_type, entity_id);

create index jobs_due_claim_idx
  on public.jobs (run_after, id)
  where status in ('pending', 'retry_scheduled');

create trigger set_jobs_updated_at
before update on public.jobs
for each row execute function public.set_updated_at();

comment on table public.jobs is
  'Durable worker jobs. Safe claiming must use row locking, for example selecting due pending/retry_scheduled rows FOR UPDATE SKIP LOCKED, then setting claimed lock fields and lease_expires_at in the same transaction. Expired leases may be released by recovery.';

create table public.provider_events (
  id uuid primary key default gen_random_uuid(),
  provider text not null,
  event_type text not null,
  provider_event_id text,
  provider_object_id text,
  idempotency_key text not null,
  payload_hash text not null,
  signature_valid boolean not null,
  processing_status text not null,
  first_seen_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now(),
  processed_at timestamptz,
  error_detail_redacted text,
  constraint provider_events_processing_status_check
    check (processing_status in (
      'received',
      'processed',
      'duplicate',
      'ignored',
      'failed'
    )),
  constraint provider_events_first_last_seen_order
    check (last_seen_at >= first_seen_at),
  constraint provider_events_processed_after_first_seen
    check (processed_at is null or processed_at >= first_seen_at)
);

alter table public.provider_events
  add constraint provider_events_provider_idempotency_unique
  unique (provider, idempotency_key);

create index provider_events_provider_idempotency_idx
  on public.provider_events (provider, idempotency_key);

create index provider_events_provider_object_idx
  on public.provider_events (provider, provider_object_id);

create index provider_events_processing_status_idx
  on public.provider_events (processing_status);

create table public.suppression_list (
  id uuid primary key default gen_random_uuid(),
  phone_e164 text,
  email text,
  lead_id uuid references public.leads(id) on delete set null,
  reason text not null,
  source text not null,
  created_at timestamptz not null default now(),
  constraint suppression_list_contact_present
    check (phone_e164 is not null or email is not null),
  constraint suppression_list_phone_e164_shape
    check (phone_e164 is null or phone_e164 ~ '^\+[1-9][0-9]{7,14}$'),
  constraint suppression_list_email_shape
    check (email is null or position('@' in email) > 1)
);

create unique index suppression_list_phone_e164_unique_idx
  on public.suppression_list (phone_e164)
  where phone_e164 is not null;

create unique index suppression_list_email_unique_idx
  on public.suppression_list (email)
  where email is not null;

create index suppression_list_phone_e164_idx
  on public.suppression_list (phone_e164);

create index suppression_list_lead_id_idx
  on public.suppression_list (lead_id);

