# AI Voice Lead Agent - Production Plan

## Purpose

Build a production-ready, single-business AI voice lead qualification and appointment-booking system.

The system will:

* receive consented leads;
* call leads automatically;
* disclose that the caller is an automated AI assistant;
* ask approved qualification questions;
* offer genuine available appointment times;
* create a Google Calendar booking exactly once;
* send confirmation messages;
* notify the owner through Telegram;
* recover safely from provider failures and restarts.

The initial release is designed for one business. It may later integrate with QuoteFollow, but the first version will not include QuoteFollow-specific tables, workflows, or multi-tenant architecture.

## Core Architecture

### FastAPI

FastAPI owns:

* lead intake endpoints;
* Twilio HTTP webhooks;
* Twilio ConversationRelay WebSocket connections;
* Twilio signature verification;
* the real-time Gemini conversation loop;
* Google Calendar availability checks;
* appointment creation;
* health endpoints.

FastAPI must remain focused during active calls. It must not run scheduled monitoring, long-running recovery tasks, or Telegram command polling.

### Deterministic Worker

A separate worker process owns:

* claiming due jobs from Supabase;
* initiating outbound Twilio calls;
* retrying transient failures;
* sending confirmation emails;
* reconciling uncertain call and calendar states;
* releasing stale job locks;
* processing recovery tasks.

The worker will use deterministic code and database state. It will not depend on an LLM to decide whether a job should run.

The worker may use the same Python package and Docker image as FastAPI, with a separate startup command.

### Hermes

Hermes provides:

* Telegram administration;
* scheduled monitoring;
* operational summaries;
* owner alerts;
* approved maintenance commands;
* manual recovery assistance.

Hermes is not the primary job queue worker and is not part of the real-time voice loop.

The core calling and booking product must continue operating temporarily if Hermes is unavailable.

### Supabase

Supabase PostgreSQL is the durable source of truth for:

* leads;
* consent;
* suppression records;
* call attempts;
* active call sessions;
* appointments;
* background jobs;
* inbound provider-event deduplication;
* operational history.

### External Services

* Twilio ConversationRelay: outbound calling, speech recognition, and speech generation.
* Gemini API: runtime conversational reasoning.
* Google Calendar: availability and bookings.
* Resend: confirmation emails.
* Telegram: owner control and notifications.
* Oracle Cloud VM: production hosting.
* Cloudflare Tunnel: HTTPS and secure WebSocket ingress.
* Docker Compose: service management.
* GitHub Actions: automated checks and deployment gates.

## QuoteFollow Compatibility

The system will use generic source references:

* `source_system`
* `source_entity_type`
* `source_entity_id`
* `source_payload`
* `source_payload_version`

A future QuoteFollow integration can submit a quote as a lead source without changing the voice gateway, worker, calling workflow, or booking engine.

QuoteFollow-specific concepts such as quote value, expiry date, follow-up stage, and customer estimate will not be added until the integration is explicitly scoped.

## Implementation Stages

### Stage 1 - Contracts And Minimal Schema

Create:

* API and webhook contracts;
* status values and transition rules;
* minimal Supabase schema;
* idempotency-key registry;
* provider adapter interfaces;
* `.env.example`;
* migration plan.

Initial tables:

* `business_settings`
* `leads`
* `lead_events`
* `call_attempts`
* `call_sessions`
* `appointments`
* `jobs`
* `provider_events`
* `suppression_list`

Exit gate:

* schema supports the full lead-to-booking workflow;
* every external side effect has an idempotency key;
* duplicate events cannot repeat side effects;
* state transitions are documented.

### Stage 2 - Lead Intake And Durable Jobs

Implement:

* FastAPI application skeleton;
* liveness and readiness checks;
* lead creation;
* phone normalization;
* consent eligibility checks;
* suppression checks;
* duplicate lead handling;
* job creation;
* deterministic worker job claiming.

No real external provider actions occur in this stage.

Exit gate:

* one valid submission creates one lead and one job;
* duplicate submissions do not create duplicate calls;
* suppressed or ineligible leads do not receive jobs;
* jobs survive application restarts.

### Stage 3 - Twilio Fixed-Script Call

Implement:

* Twilio outbound-call initiation;
* signed webhook validation;
* ConversationRelay WebSocket validation;
* call-status callbacks;
* a fixed non-AI test script;
* call outcome persistence.

Exit gate:

* one approved test lead triggers one call;
* invalid Twilio signatures are rejected;
* call statuses are stored;
* duplicate callbacks do not create duplicate effects;
* unanswered and failed calls create correct retry state.

### Stage 4 - Gemini Conversation Loop

Implement:

* approved system prompt;
* automated-assistant disclosure;
* qualification questions;
* structured Gemini decisions;
* opt-out handling;
* human escalation;
* transcript redaction and summaries;
* latency and timeout handling.

Gemini must not directly call providers or mutate the database. Application code validates every model decision before performing an action.

Exit gate:

* the agent follows the approved script;
* opt-out stops future calls;
* unsupported or risky requests escalate;
* prompt injection cannot bypass deterministic policy;
* the voice loop operates without Hermes.

### Stage 5 - Calendar Booking And Confirmation

Implement:

* Google Calendar availability lookup;
* configurable appointment duration and buffers;
* slot confirmation;
* idempotent event creation;
* booking reconciliation after timeouts;
* Resend confirmation email;
* Telegram booking alert.

Exit gate:

* only genuinely available slots are offered;
* retries cannot create duplicate events;
* confirmation is sent once;
* uncertain booking states are reconciled before another event is created.

### Stage 6 - Hermes Administration And Monitoring

Implement:

* Telegram allowlists;
* pause and resume controls;
* failed-job summaries;
* retry approved lead;
* mark lead for human follow-up;
* daily operational summary;
* stale heartbeat and backlog alerts.

Hermes commands must call approved scripts or authenticated internal endpoints. Hermes must not receive unrestricted production database or shell authority through customer input.

Exit gate:

* unauthorized Telegram users are rejected;
* administrative actions are audited;
* calling can be paused safely;
* Hermes failure does not stop existing FastAPI and worker services.

### Stage 7 - Production Hardening And Launch

Implement and verify:

* Docker Compose deployment;
* Oracle Cloud VM configuration;
* Cloudflare Tunnel;
* staging and production separation;
* structured redacted logs;
* provider timeouts and retry limits;
* backups and restore procedure;
* health checks and alerts;
* container restart recovery;
* GitHub Actions checks;
* controlled production rollout.

Exit gate:

* staging happy path passes;
* failure-path tests pass;
* backup restore is proven;
* secrets and unnecessary personal data are absent from logs;
* one controlled production call completes successfully;
* the owner receives booking and failure alerts;
* rollback and pause procedures are tested.

## Production Safety Rules

* No call without an approved consent state.
* No further calls after opt-out or suppression.
* No external side effect without an idempotency key.
* No booking promise until Google Calendar confirms the event.
* No unbounded retries.
* No Gemini-controlled direct provider access.
* No real secret values in Git, documentation, logs, or test fixtures.
* No full call recording by default.
* No multi-tenant or QuoteFollow-specific expansion during the initial release.

## Stop Conditions

Development or deployment stops if:

* Twilio signature verification cannot be proven;
* duplicate calls, bookings, or emails are possible;
* consent state is unclear;
* suppression checks can be bypassed;
* provider failures produce silent success;
* secrets or unredacted personal data appear in logs;
* pending jobs are lost after a restart;
* staging recovery tests fail;
* the system cannot be paused safely.

## Completion Definition

The project is production-ready when a consented lead can move through intake, outbound AI call, qualification, real calendar booking, confirmation, owner notification, and durable audit state while handling duplicate events, failures, retries, opt-outs, restarts, and provider timeouts safely.
