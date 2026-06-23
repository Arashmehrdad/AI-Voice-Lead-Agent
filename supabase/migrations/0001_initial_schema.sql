-- Stage 1 initial schema for the single-business AI voice lead agent.
-- Creates exactly the nine approved MVP tables.
-- Status values use text columns with CHECK constraints rather than PostgreSQL enums.

begin;

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
  calling_hours jsonb not null default '{}'::jsonb,
  max_call_attempts integer not null default 3,
  retry_policy jsonb not null default '{}'::jsonb,
  qualification_config jsonb not null default '{}'::jsonb,
  appointment_types jsonb not null default '[]'::jsonb,
  calendar_id text not null,
  calling_paused boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint business_settings_business_name_not_blank
    check (btrim(business_name) <> ''),
  constraint business_settings_timezone_not_blank
    check (btrim(timezone) <> ''),
  constraint business_settings_calendar_id_not_blank
    check (btrim(calendar_id) <> ''),
  constraint business_settings_max_call_attempts_range
    check (max_call_attempts between 1 and 10),
  constraint business_settings_calling_hours_object
    check (jsonb_typeof(calling_hours) = 'object'),
  constraint business_settings_retry_policy_object
    check (jsonb_typeof(retry_policy) = 'object'),
  constraint business_settings_qualification_config_object
    check (jsonb_typeof(qualification_config) = 'object'),
  constraint business_settings_appointment_types_array
    check (jsonb_typeof(appointment_types) = 'array')
);

create unique index business_settings_single_row_idx
  on public.business_settings ((true));

create trigger set_business_settings_updated_at
before update on public.business_settings
for each row execute function public.set_updated_at();

create table public.leads (
  id uuid primary key default gen_random_uuid(),
  source_system text not null,
  source_entity_type text not null,
  source_entity_id text not null,
  source_payload jsonb not null default '{}'::jsonb,
  source_payload_version integer not null default 1,
  full_name text,
  company_name text,
  phone_e164 text not null,
  email text,
  timezone text,
  status text not null default 'new',
  qualification_result text not null default 'unknown',
  qualification_summary text,
  consent_status text not null default 'unknown',
  consent_source text,
  consent_captured_at timestamptz,
  consent_evidence_uri text,
  next_call_after timestamptz,
  attempt_count integer not null default 0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint leads_source_identity_unique
    unique (source_system, source_entity_id),
  constraint leads_source_system_not_blank
    check (btrim(source_system) <> ''),
  constraint leads_source_entity_type_not_blank
    check (btrim(source_entity_type) <> ''),
  constraint leads_source_entity_id_not_blank
    check (btrim(source_entity_id) <> ''),
  constraint leads_source_payload_object
    check (jsonb_typeof(source_payload) = 'object'),
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
    check (qualification_result in (
      'unknown',
      'qualified',
      'not_qualified',
      'needs_human',
      'insufficient_info'
    )),
  constraint leads_consent_status_check
    check (consent_status in (
      'unknown',
      'specific_automated_call_consent',
      'service_follow_up_approved',
      'withdrawn',
      'do_not_call',
      'blocked'
    )),
  constraint leads_callable_consent_has_evidence
    check (
      consent_status not in (
        'specific_automated_call_consent',
        'service_follow_up_approved'
      )
      or (
        consent_source is not null
        and btrim(consent_source) <> ''
        and consent_captured_at is not null
      )
    ),
  constraint leads_phone_e164_shape
    check (phone_e164 ~ '^\+[1-9][0-9]{7,14}$'),
  constraint leads_email_shape
    check (
      email is null
      or (
        btrim(email) = email
        and position('@' in email) > 1
      )
    )
);

create index leads_status_next_call_after_idx
  on public.leads (status, next_call_after);

create index leads_phone_e164_idx
  on public.leads (phone_e164);

create index leads_consent_status_idx
  on public.leads (consent_status);

create index leads_created_at_idx
  on public.leads (created_at desc);

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
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  constraint lead_events_event_type_not_blank
    check (btrim(event_type) <> ''),
  constraint lead_events_summary_not_blank
    check (btrim(summary) <> ''),
  constraint lead_events_metadata_object
    check (jsonb_typeof(metadata) = 'object'),
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

create index lead_events_event_type_created_at_idx
  on public.lead_events (event_type, created_at desc);

create table public.call_attempts (
  id uuid primary key default gen_random_uuid(),
  lead_id uuid not null references public.leads(id) on delete cascade,
  attempt_number integer not null,
  idempotency_key text not null unique,
  twilio_call_sid text unique,
  status text not null default 'scheduled',
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
  constraint call_attempts_lead_attempt_unique
    unique (lead_id, attempt_number),
  constraint call_attempts_attempt_number_positive
    check (attempt_number > 0),
  constraint call_attempts_duration_nonnegative
    check (duration_seconds is null or duration_seconds >= 0),
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
  constraint call_attempts_idempotency_key_matches_row
    check (
      idempotency_key =
      'call:' || lead_id::text || ':' || attempt_number::text
    ),
  constraint call_attempts_answered_after_started
    check (
      answered_at is null
      or started_at is null
      or answered_at >= started_at
    ),
  constraint call_attempts_ended_after_started
    check (
      ended_at is null
      or started_at is null
      or ended_at >= started_at
    ),
  constraint call_attempts_ended_after_answered
    check (
      ended_at is null
      or answered_at is null
      or ended_at >= answered_at
    )
);

create index call_attempts_status_created_at_idx
  on public.call_attempts (status, created_at);

create index call_attempts_lead_created_at_idx
  on public.call_attempts (lead_id, created_at desc);

create trigger set_call_attempts_updated_at
before update on public.call_attempts
for each row execute function public.set_updated_at();

create table public.call_sessions (
  id uuid primary key default gen_random_uuid(),
  call_attempt_id uuid not null unique references public.call_attempts(id) on delete cascade,
  twilio_call_sid text not null unique,
  conversationrelay_session_id text unique,
  status text not null default 'opening',
  last_sequence_number bigint not null default 0,
  last_activity_at timestamptz not null default now(),
  conversation_summary text,
  structured_facts jsonb not null default '{}'::jsonb,
  qualification_result text not null default 'unknown',
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
    check (qualification_result in (
      'unknown',
      'qualified',
      'not_qualified',
      'needs_human',
      'insufficient_info'
    )),
  constraint call_sessions_sequence_nonnegative
    check (last_sequence_number >= 0),
  constraint call_sessions_structured_facts_object
    check (jsonb_typeof(structured_facts) = 'object'),
  constraint call_sessions_time_order_check
    check (ended_at is null or ended_at >= started_at)
);

comment on column public.call_sessions.last_sequence_number is
  'Application-generated local WebSocket message sequence. It is not a Twilio provider event identifier.';

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
  status text not null default 'proposed',
  booking_idempotency_key text not null unique,
  calendar_id text not null,
  google_event_id text unique,
  google_extended_property_value text not null unique,
  confirmation_status text not null default 'not_requested',
  resend_message_id text,
  confirmation_sent_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint appointments_appointment_type_not_blank
    check (btrim(appointment_type) <> ''),
  constraint appointments_timezone_not_blank
    check (btrim(timezone) <> ''),
  constraint appointments_calendar_id_not_blank
    check (btrim(calendar_id) <> ''),
  constraint appointments_slot_order_check
    check (slot_end > slot_start),
  constraint appointments_status_check
    check (status in (
      'proposed',
      'booking_pending',
      'booked',
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
    check (status <> 'booked' or google_event_id is not null),
  constraint appointments_confirmation_sent_requires_provider_data
    check (
      confirmation_status <> 'sent'
      or (
        resend_message_id is not null
        and confirmation_sent_at is not null
      )
    ),
  constraint appointments_booking_key_matches_lead
    check (
      booking_idempotency_key like
      'calendar:' || lead_id::text || ':%'
    ),
  constraint appointments_google_extended_property_matches_row
    check (
      google_extended_property_value = 'appointment:' || id::text
    )
);

create index appointments_lead_status_idx
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
  status text not null default 'pending',
  idempotency_key text not null unique,
  entity_type text not null,
  entity_id uuid not null,
  attempt_number integer,
  payload jsonb not null default '{}'::jsonb,
  run_after timestamptz not null default now(),
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
      'lead_event',
      'call_attempt',
      'call_session',
      'appointment',
      'provider_event',
      'business_settings'
    )),
  constraint jobs_payload_object
    check (jsonb_typeof(payload) = 'object'),
  constraint jobs_attempt_count_nonnegative
    check (attempt_count >= 0),
  constraint jobs_max_attempts_positive
    check (max_attempts > 0),
  constraint jobs_attempt_count_not_over_max
    check (attempt_count <= max_attempts),
  constraint jobs_attempt_number_usage
    check (
      (
        job_type = 'initiate_call'
        and attempt_number is not null
        and attempt_number > 0
      )
      or (
        job_type <> 'initiate_call'
        and attempt_number is null
      )
    ),
  constraint jobs_entity_and_key_match_job_type
    check (
      (
        job_type = 'initiate_call'
        and entity_type = 'lead'
        and idempotency_key =
          'job:initiate_call:' || entity_id::text || ':' || attempt_number::text
      )
      or (
        job_type = 'send_confirmation_email'
        and entity_type = 'appointment'
        and idempotency_key =
          'job:send_confirmation_email:' || entity_id::text
      )
      or (
        job_type = 'send_owner_alert'
        and entity_type = 'lead_event'
        and idempotency_key =
          'job:send_owner_alert:' || entity_id::text
      )
      or (
        job_type = 'reconcile_twilio_call'
        and entity_type = 'call_attempt'
        and idempotency_key =
          'job:reconcile_twilio_call:' || entity_id::text
      )
      or (
        job_type = 'reconcile_calendar_booking'
        and entity_type = 'appointment'
        and idempotency_key =
          'job:reconcile_calendar_booking:' || entity_id::text
      )
      or (
        job_type = 'recover_stale_session'
        and entity_type = 'call_session'
        and idempotency_key =
          'job:recover_stale_session:' || entity_id::text
      )
    ),
  constraint jobs_lock_fields_consistent
    check (
      (
        status in ('claimed', 'running')
        and locked_by is not null
        and btrim(locked_by) <> ''
        and locked_at is not null
        and lease_expires_at is not null
        and lease_expires_at > locked_at
      )
      or (
        status not in ('claimed', 'running')
        and locked_by is null
        and locked_at is null
        and lease_expires_at is null
      )
    )
);

create index jobs_due_claim_idx
  on public.jobs (run_after, id)
  where status in ('pending', 'retry_scheduled');

create index jobs_active_lease_idx
  on public.jobs (lease_expires_at)
  where status in ('claimed', 'running');

create index jobs_entity_idx
  on public.jobs (entity_type, entity_id);

create index jobs_status_created_at_idx
  on public.jobs (status, created_at);

create trigger set_jobs_updated_at
before update on public.jobs
for each row execute function public.set_updated_at();

comment on table public.jobs is
  'Durable worker queue. Claim due jobs with FOR UPDATE SKIP LOCKED and set claimed lock fields plus lease_expires_at in the same transaction.';

create table public.provider_events (
  id uuid primary key default gen_random_uuid(),
  provider text not null,
  event_type text not null,
  provider_event_id text,
  provider_object_id text,
  idempotency_key text not null,
  payload_hash text not null,
  signature_valid boolean not null default true,
  processing_status text not null default 'received',
  delivery_count integer not null default 1,
  first_seen_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now(),
  processed_at timestamptz,
  error_detail_redacted text,
  constraint provider_events_provider_idempotency_unique
    unique (provider, idempotency_key),
  constraint provider_events_provider_not_blank
    check (btrim(provider) <> ''),
  constraint provider_events_event_type_not_blank
    check (btrim(event_type) <> ''),
  constraint provider_events_idempotency_key_not_blank
    check (btrim(idempotency_key) <> ''),
  constraint provider_events_payload_hash_sha256
    check (payload_hash ~ '^[0-9a-f]{64}$'),
  constraint provider_events_signature_valid_true
    check (signature_valid),
  constraint provider_events_processing_status_check
    check (processing_status in (
      'received',
      'processed',
      'ignored',
      'failed'
    )),
  constraint provider_events_delivery_count_positive
    check (delivery_count > 0),
  constraint provider_events_first_last_seen_order
    check (last_seen_at >= first_seen_at),
  constraint provider_events_processing_timestamp_consistent
    check (
      (
        processing_status = 'received'
        and processed_at is null
      )
      or (
        processing_status in ('processed', 'ignored', 'failed')
        and processed_at is not null
        and processed_at >= first_seen_at
      )
    )
);

create index provider_events_provider_object_idx
  on public.provider_events (provider, provider_object_id);

create index provider_events_processing_status_idx
  on public.provider_events (processing_status, first_seen_at);

create index provider_events_last_seen_at_idx
  on public.provider_events (last_seen_at);

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
  constraint suppression_list_reason_not_blank
    check (btrim(reason) <> ''),
  constraint suppression_list_source_not_blank
    check (btrim(source) <> ''),
  constraint suppression_list_phone_e164_shape
    check (phone_e164 is null or phone_e164 ~ '^\+[1-9][0-9]{7,14}$'),
  constraint suppression_list_email_shape
    check (
      email is null
      or (
        btrim(email) = email
        and position('@' in email) > 1
      )
    )
);

create unique index suppression_list_phone_e164_unique_idx
  on public.suppression_list (phone_e164)
  where phone_e164 is not null;

create unique index suppression_list_email_unique_idx
  on public.suppression_list (lower(email))
  where email is not null;

create index suppression_list_lead_id_idx
  on public.suppression_list (lead_id);

-- Direct browser/client access is denied by default. The API and worker use
-- server-side credentials. Policies can be added later only for an approved use.
alter table public.business_settings enable row level security;
alter table public.leads enable row level security;
alter table public.lead_events enable row level security;
alter table public.call_attempts enable row level security;
alter table public.call_sessions enable row level security;
alter table public.appointments enable row level security;
alter table public.jobs enable row level security;
alter table public.provider_events enable row level security;
alter table public.suppression_list enable row level security;

comment on table public.lead_events is
  'Append-only operational and audit events. Application code should not update existing rows.';

comment on table public.provider_events is
  'One row per unique provider delivery identity. Duplicate deliveries increment delivery_count and update last_seen_at without changing processing_status.';

comment on column public.appointments.booking_idempotency_key is
  'calendar:{lead_id}:{appointment_type}:{slot_start_utc}:{slot_end_utc}';

comment on column public.appointments.google_extended_property_value is
  'appointment:{appointment_id}; also stored as a Google Calendar extended property.';

comment on column public.appointments.resend_message_id is
  'Provider message identifier for a confirmation sent with Resend Idempotency-Key email:{appointment_id}:confirmation.';

commit;
