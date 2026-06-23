# Production Plan

## Purpose

Design a production-ready, single-business AI voice lead qualification and appointment-booking platform before any code, infrastructure, dependencies, or external integrations are created.

Owner goal: qualify leads by outbound AI voice call, book real appointments into Google Calendar when appropriate, alert the owner when human action is needed, and recover safely from transient failures without adding unnecessary services.

## Essential Roles

- Product Lead: keep the MVP single-business, narrow, consent-aware, and measurable.
- Engineering Lead: define service boundaries, data contracts, state transitions, tests, and implementation order.
- Launch/DevOps Lead: define environments, deployment, health checks, CI, recovery, backups, and rollback.
- Security/Privacy Reviewer: define consent, secret handling, webhook verification, redaction, auditability, and safe admin controls.
- UX/Conversation Lead: define the call journey, disclosure, qualification behavior, escalation, voicemail, and booking handoff.

Subagents are not used in this run because the available delegation tool is restricted to explicit user requests for subagents. The main agent is consolidating the role perspectives directly.

## Current Verdict

Proceed with staged planning only. Implementation should not begin until Stage 0 and Stage 1 evidence is available, especially consent basis, Twilio ConversationRelay handshake behavior, Google Calendar booking constraints, and production environment decisions.

## Non-Goals

- No application code.
- No dependency installation.
- No Docker, GitHub Actions, Supabase, Twilio, Google, Resend, Telegram, Hermes, or Cloudflare configuration.
- No production data import.
- No live calls.
- No generalized multi-tenant platform.
- No QuoteFollow-specific implementation in this project yet.
- No owner-facing web dashboard in the MVP unless later approved.
- No additional queue, cache, workflow, or observability service unless a later stage proves Docker Compose plus Supabase persistent job state is insufficient.

## Assumptions

- The first release serves one business with one owner/admin group.
- The architecture may be reused for the future QuoteFollow project, but this release remains single-business and should not add multi-tenant abstractions prematurely.
- Leads are only contacted where the business has a documented lawful basis and, for automated marketing calls in the UK, specific consent for automated marketing calls where required.
- Calls use Twilio ConversationRelay for outbound voice transport, speech-to-text, text-to-speech, and the WebSocket stream.
- Gemini API is the only runtime conversation model.
- Hermes is responsible for orchestration, retries, scheduled recovery, and Telegram administration, but it is never inside the latency-sensitive per-turn voice WebSocket loop.
- FastAPI owns HTTP APIs, Twilio webhooks, and the ConversationRelay WebSocket gateway.
- Supabase PostgreSQL is the source of truth for leads, call attempts, appointments, jobs, event deduplication, audit records, and auth.
- Google Calendar is the source of truth for live availability and booked appointments.
- Resend sends appointment confirmations and operational email if needed.
- Telegram is an owner/admin control plane, not a customer communication channel.
- Oracle Cloud VM runs Docker Compose.
- Cloudflare Tunnel terminates public HTTPS and secure WebSocket ingress to the VM.

## Unresolved Decisions

- Which parts of this platform should eventually become reusable for QuoteFollow, and whether QuoteFollow needs quote follow-up workflows, quote-specific CRM fields, or different consent/disclosure language.
- Exact lawful basis and consent capture process for each lead source.
- Whether the call is direct marketing, service follow-up, or appointment fulfilment for each lead source.
- Business calling hours, quiet periods, retry spacing, and maximum attempts.
- Whether call recording is enabled; if yes, separate consent, retention, redaction, and access policies are required.
- Whether Twilio Answering Machine Detection is used; if used, validate Ofcom abandoned/silent-call implications before launch.
- The qualification script, disqualifying answers, escalation triggers, and business-specific appointment types.
- Google Calendar structure: one shared business calendar, one owner calendar, or multiple staff calendars.
- Appointment duration, buffers, cancellation window, timezone policy, and overbooking rules.
- Whether Supabase Auth is used only for owner/admin APIs or also for future UI access.
- Gemini prompt safety boundaries and whether conversation summaries may include sensitive information.
- Data retention periods for leads, transcripts, call events, audit logs, and failed job records.

## Planned Documents

- `PLAN.md`: staged implementation plan, gates, evidence, assumptions, and next actions.
- `docs/architecture.md`: system boundaries, lead-to-call-to-booking flow, runtime behavior, and operational responsibilities.
- `docs/data-model.md`: Supabase tables, relationships, statuses, idempotency keys, and retention notes.
- `docs/threat-model.md`: assets, trust boundaries, threats, mitigations, webhook verification, secrets, logging, and UK consent requirements.
- `docs/acceptance-criteria.md`: feature, quality, security, compliance, and operational acceptance criteria.
- `docs/deployment-plan.md`: environments, Docker Compose deployment, CI, release, backup, recovery, monitoring, and rollback plan.

## Implementation Stages

### Stage 0: Requirements And Compliance Preflight

Entry criteria:
- Planning documents exist and are reviewed.
- Owner identifies lead sources and intended call purpose.
- Owner identifies business calendar rules and appointment types.

Exit criteria:
- Lead consent requirements are documented per lead source.
- Call disclosure text is approved.
- Data retention periods are approved.
- Launch region, timezone, calling hours, and escalation contacts are approved.
- Go/no-go decision is recorded.

Tests:
- Tabletop review of three lead sources: opted-in lead, unclear consent lead, and opt-out lead.
- Tabletop review of an answered call, voicemail, busy line, failed call, qualified booking, and human escalation.

Evidence required:
- Approved compliance checklist.
- Approved conversation disclosure.
- Approved retry and calling-hours policy.
- Approved appointment rules.

### Stage 1: Contracts, Schema, And State Machines

Entry criteria:
- Stage 0 evidence complete.
- External provider account owners identified, but services are not connected yet.

Exit criteria:
- Supabase schema migration plan is designed.
- Status values and state transitions are locked.
- Idempotency-key formats are locked.
- API/webhook contract tests are specified.
- Conversation state contract between FastAPI and Gemini is specified.

Tests:
- Schema review against all required flows.
- State-machine review for duplicate events, retries, booking idempotency, and recovery.
- Failure-mode review for timeout, voicemail, busy, failed call, provider errors, and owner escalation.

Evidence required:
- Reviewed ERD or table map.
- Reviewed status transition matrix.
- Reviewed idempotency-key registry.
- Reviewed API/webhook contract checklist.

### Stage 2: Core API And Persistence Skeleton

Entry criteria:
- Stage 1 contracts approved.
- Development environment variables are defined as placeholders only.

Exit criteria:
- FastAPI service can run locally with health checks.
- Supabase persistence layer is implemented behind repository interfaces.
- No outbound calls, emails, calendar writes, Gemini calls, or Telegram sends occur.
- Unit tests cover state transitions and idempotency.

Tests:
- Existing Python formatter, lint, typecheck, and pytest commands.
- Contract tests for lead creation, call attempt creation, event deduplication, and job claiming.
- Secret scan if configured.

Evidence required:
- Test output with exit codes.
- Example local `.env.example`, not real secrets.
- API contract output or generated OpenAPI snapshot.

### Stage 3: Twilio Webhook And ConversationRelay Gateway

Entry criteria:
- Stage 2 complete.
- Twilio test credentials available in development or staging only.
- Webhook signature verification behavior is tested locally.

Exit criteria:
- FastAPI rejects unsigned or invalid Twilio HTTP webhooks.
- FastAPI rejects ConversationRelay WebSocket handshakes with invalid `X-Twilio-Signature`.
- The WebSocket loop stays latency-sensitive: receive Twilio message, update in-memory call session, call Gemini, return response, persist summarized state asynchronously where possible.
- No Hermes orchestration code runs inside the WebSocket turn loop.

Tests:
- Signature verification unit and integration tests.
- WebSocket connection acceptance/rejection tests.
- Latency budget tests for mocked Twilio and mocked Gemini.
- Duplicate Twilio event replay tests.

Evidence required:
- Signed webhook fixture tests.
- WebSocket handshake test logs.
- p95 mocked turn latency report.
- Deduplication test output.

### Stage 4: Hermes Orchestration And Recovery Jobs

Entry criteria:
- Stage 3 complete.
- Job state model and recovery windows approved.

Exit criteria:
- Hermes claims due jobs from Supabase using safe locking.
- Hermes schedules call attempts, retry jobs, stale-call recovery, failed-job recovery, calendar reconciliation, and owner alerts.
- Telegram admin commands are authenticated and audited.
- Hermes never directly handles real-time Twilio audio/text turn exchange.

Tests:
- Job claim race tests.
- Retry/backoff tests.
- Recovery job tests for stale in-progress calls and stuck bookings.
- Telegram command authorization tests.

Evidence required:
- Job transition test output.
- Recovery tabletop log.
- Admin command audit examples using fake data.

### Stage 5: Calendar Booking And Email Confirmation

Entry criteria:
- Stage 4 complete.
- Calendar rules and confirmation templates approved.

Exit criteria:
- Availability is checked against Google Calendar before offering slots.
- Booking is idempotent by appointment and calendar idempotency key.
- Duplicate booking attempts return the existing appointment instead of creating a second event.
- Resend sends confirmation only after calendar booking is committed.
- Calendar failures create recovery jobs or human escalation, not silent success.

Tests:
- Mock Google Calendar availability and event creation tests.
- Duplicate booking tests.
- Calendar timeout and conflict tests.
- Email confirmation idempotency tests.

Evidence required:
- Booking idempotency test output.
- Calendar conflict test output.
- Confirmation email preview using fake data.

### Stage 6: End-To-End Staging

Entry criteria:
- Staging environment exists with separate credentials, numbers, domains, calendars, and test leads.
- Owner approves staged test scripts.

Exit criteria:
- A staged test lead can move from intake to outbound call to qualification to calendar booking to confirmation email.
- Voicemail, busy, no-answer, failed-call, opt-out, human escalation, and duplicate webhook paths are proven.
- Metrics, logs, redaction, alerts, backups, and recovery are observed in staging.

Tests:
- End-to-end happy path.
- End-to-end failure paths.
- Load smoke test for concurrent call limit expected at launch.
- Backup and restore drill.
- Cloudflare Tunnel restart drill.
- Oracle VM restart drill.

Evidence required:
- Staging runbook output.
- Redacted logs.
- Metrics screenshots or exported metrics.
- Backup restore evidence.
- Owner approval for production launch.

### Stage 7: Production Launch

Entry criteria:
- Stage 6 complete.
- Production secrets are stored only in production environment variables.
- Production runbook and rollback path are approved.

Exit criteria:
- Production deployment is live.
- Health checks pass.
- One controlled production call is completed.
- Owner receives operational alert test.
- Backup schedule is confirmed.
- Daily recovery jobs are confirmed.

Tests:
- Production smoke test using a consented test lead.
- Health check and dependency check.
- Alert test.
- Rollback rehearsal on staging immediately before production.

Evidence required:
- Production deployment record.
- Health check output.
- Controlled-call audit record.
- Owner acceptance.

## Cross-Cutting Policies

### Service Boundaries

- FastAPI voice gateway: latency-sensitive HTTP/WebSocket ingress, Twilio signature verification, conversation turn handling, and minimal per-turn persistence.
- Hermes: background orchestration, scheduled recovery, job retries, outbound call initiation, stale-state repair, calendar reconciliation, and Telegram admin.
- Supabase: durable state, auth, audit logs, job state, deduplication records, and relational reporting.
- Provider adapters: Twilio, Gemini, Google Calendar, Resend, Telegram, each behind a narrow interface with retries and idempotency.

### Retry Limits

- Provider API call: one immediate retry for transient network errors inside non-real-time jobs; no unbounded retries in the voice loop.
- Outbound call attempts: configurable maximum, initially 3 attempts per lead across approved calling windows.
- Calendar booking: retry once for transient timeout, then reconcile before retrying event creation.
- Email send: retry up to 3 times with backoff, then owner alert.
- Webhook handling: process each provider event idempotently once; repeated delivery returns success if already processed.
- Recovery job: one repair pass per stale record per run; repeated unresolved records escalate to owner after configured threshold.

### Stop Conditions

- Consent basis is missing or ambiguous for a lead source.
- Twilio signature validation cannot be proven for HTTP webhooks and WebSocket handshakes.
- Calendar idempotency cannot prevent duplicate bookings.
- Gemini conversation cannot reliably provide approved disclosure and opt-out handling.
- Logs contain unredacted secrets, tokens, or unnecessary personal data.
- Staging cannot prove recovery from duplicate events, stale calls, and calendar conflicts.
- Production error, abandoned/silent-call, or complaint rate breaches approved thresholds.

## Evidence Checklist

- Six planning documents exist.
- Official source references are captured for UK automated marketing calls, Ofcom silent/abandoned call policy, and Twilio signature validation.
- Schema includes idempotency keys and event deduplication.
- Architecture separates Hermes orchestration from the WebSocket voice loop.
- Stages include entry criteria, exit criteria, tests, and evidence.
- No application code, dependencies, infrastructure, or external service connections are created.

## Ranked Next Actions

1. Owner reviews planning documents and confirms assumptions or corrections.
2. Owner classifies lead sources and consent basis.
3. Owner approves call disclosure, opt-out behavior, and escalation rules.
4. Owner approves calendar rules, appointment types, and booking constraints.
5. Engineering creates Stage 1 contracts and schema migration plan.
6. Security reviews webhook verification, secrets, audit logs, and UK compliance boundaries.
7. DevOps prepares environment and deployment runbooks without provisioning production until approved.

## Completion Definition

This planning run is complete when only the six requested documents exist, they cover the lead-to-call-to-booking flow and operational requirements, and no code, dependencies, infrastructure, credentials, or external service connections have been created.
