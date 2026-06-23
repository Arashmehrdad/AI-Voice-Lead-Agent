# Deployment Plan

## Purpose

This document defines how the AI voice lead qualification and appointment-booking platform moves from local development to staging and production.

The deployment must support:

* FastAPI HTTP and WebSocket traffic;
* durable background job processing;
* Hermes monitoring and Telegram administration;
* public HTTPS and secure WebSocket access;
* safe provider configuration;
* restart recovery;
* backups;
* controlled releases and rollbacks.

The first release serves one business. Environment settings remain external so the architecture can later support a QuoteFollow integration without changing the deployment structure.

## Deployment Environments

### Development

Development runs on the local Windows machine.

Purpose:

* application development;
* automated tests;
* provider adapter testing;
* database migration development;
* local Docker Compose verification.

Rules:

* No production data.
* No real outbound calls by default.
* No real confirmation emails by default.
* No production calendar writes.
* Mock or test provider adapters are the default.
* Real provider calls require an explicit development override.
* Secrets are loaded from the approved local `.env` path.
* `.env` is excluded from Git.
* `.env.example` contains variable names only.

Development may use:

* a local PostgreSQL test database; or
* a dedicated Supabase development project.

Development exit requirements:

* unit tests pass;
* state-transition tests pass;
* idempotency tests pass;
* webhook signature tests pass;
* Docker Compose starts successfully;
* health checks pass;
* no production credentials are present.

### Staging

Staging proves the complete workflow using isolated test resources.

Required staging resources:

* separate Supabase staging project;
* Twilio trial or staging number;
* approved test destination numbers;
* separate Google Calendar;
* Resend test sender or staging sender;
* separate Telegram chat;
* staging Cloudflare hostname;
* staging environment variables;
* staging Oracle deployment or isolated staging Compose stack.

Rules:

* Only approved test leads are used.
* No production lead data.
* No production credentials.
* Calls are made only to approved test recipients.
* Calling remains paused by default outside planned test windows.
* Full call recordings remain disabled unless specifically tested and approved.

Staging exit requirements:

* happy-path lead-to-booking flow passes;
* duplicate lead and webhook tests pass;
* Twilio signature verification passes through Cloudflare;
* no-answer, busy, voicemail, failure, and timeout paths pass;
* opt-out and suppression paths pass;
* calendar conflict and timeout reconciliation pass;
* email and Telegram notification tests pass;
* API and worker restart recovery pass;
* backup restore test passes;
* logs are reviewed for secrets and unnecessary personal data.

### Production

Production runs the live single-business service.

Required production resources:

* production Oracle Cloud VM;
* production Supabase project;
* production Twilio number;
* production Google Calendar;
* production Resend sender;
* production Telegram owner chat;
* production Cloudflare Tunnel hostname;
* production environment configuration;
* production backup and alerting configuration.

Rules:

* Only approved and consented leads may be called.
* Production secrets are stored only on the production host or approved secret store.
* Production credentials are not copied into development or staging.
* Calling begins paused.
* Initial launch uses one controlled test lead.
* Wider calling is enabled only after controlled launch evidence is reviewed.
* Backups and alerts must be active before the first production call.

## Docker Compose Services

The production Compose stack contains four primary services.

### `api`

Runs the FastAPI application.

Responsibilities:

* lead intake API;
* Twilio HTTP webhooks;
* ConversationRelay WebSocket;
* Gemini conversation loop;
* Google Calendar availability and booking;
* liveness and readiness endpoints.

Requirements:

* runs as a non-root container user where practical;
* exposes only an internal Compose port;
* receives public traffic only through Cloudflare Tunnel;
* has restart policy enabled;
* has container health checks;
* does not process the durable job queue.

### `worker`

Runs the deterministic background worker.

Responsibilities:

* claim due Supabase jobs;
* initiate Twilio calls;
* process retries;
* send confirmation emails;
* send owner notifications;
* reconcile uncertain Twilio and calendar states;
* recover stale call sessions;
* release expired job leases.

Requirements:

* uses the same application package as `api`;
* starts through a separate worker command;
* has no public network port;
* uses database locking and job leases;
* restarts automatically after failure;
* does not use Gemini to decide whether jobs are permitted.

### `hermes`

Runs the Hermes operational layer.

Responsibilities:

* Telegram administration;
* scheduled monitoring;
* system summaries;
* approved recovery commands;
* owner alerts and manual assistance.

Requirements:

* customer traffic cannot reach Hermes directly;
* Telegram access is allowlisted;
* Hermes uses approved internal endpoints or scripts;
* the product continues processing calls and jobs temporarily if Hermes stops;
* Hermes does not directly claim the production job queue.

### `cloudflared`

Runs Cloudflare Tunnel.

Responsibilities:

* expose approved FastAPI HTTPS routes;
* expose the ConversationRelay secure WebSocket;
* avoid opening broad inbound VM ports;
* maintain the public hostname.

Requirements:

* separate staging and production tunnel configuration;
* automatic restart;
* health monitoring;
* correct forwarding headers for Twilio signature verification.

## Compose Networks And Storage

Use one private Compose network for internal communication.

Public ingress:

```text
Internet
  -> Cloudflare
  -> cloudflared
  -> api
```

Internal communication:

```text
hermes
  -> authenticated internal API or approved scripts

worker
  -> Supabase and external providers

api
  -> Supabase and external providers
```

No local database volume is required because Supabase is the primary database.

Persistent local data may include:

* Hermes configuration and memory;
* application operational logs;
* deployment metadata;
* temporary diagnostic files.

Persistent directories must have documented ownership, permissions, retention, and backup requirements.

## Cloudflare Tunnel Routing

Planned public routes:

```text
/api/leads/*
/webhooks/twilio/*
/webhooks/resend/*
/ws/twilio/*
/health/live
```

The readiness and dependency endpoints should be restricted or authenticated where they reveal provider state.

Requirements:

* WebSocket upgrades must be supported.
* Original scheme and host information must be preserved.
* Twilio signature validation must use the externally visible URL.
* Direct access to the container port must not be publicly exposed.
* Hermes administration endpoints must not be publicly routable unless strongly authenticated and explicitly approved.

## Environment Configuration

Maintain separate configuration files or secret sources for:

```text
development
staging
production
```

Expected environment variable categories:

```text
APP_ENV
APP_PUBLIC_BASE_URL
LOG_LEVEL
CALLING_ENABLED

SUPABASE_URL
SUPABASE_ANON_KEY
SUPABASE_SERVICE_ROLE_KEY

TWILIO_ACCOUNT_SID
TWILIO_AUTH_TOKEN
TWILIO_CALLER_ID

GEMINI_API_KEY
GEMINI_MODEL

GOOGLE_CLIENT_ID
GOOGLE_CLIENT_SECRET
GOOGLE_REFRESH_TOKEN
GOOGLE_CALENDAR_ID

RESEND_API_KEY
RESEND_FROM_EMAIL

TELEGRAM_BOT_TOKEN
TELEGRAM_ALLOWED_USER_IDS
TELEGRAM_ALLOWED_CHAT_IDS

CLOUDFLARE_TUNNEL_TOKEN
```

Rules:

* Real secret values never enter Git.
* Logs never print secret values.
* Production and staging values remain separate.
* Secret rotation is documented before launch.
* `AGENTS.md` may identify the approved `.env` path and variable names, but not secret values.

## Health Checks

### API Health

`/health/live`

Confirms:

* the FastAPI process is running.

`/health/ready`

Confirms:

* required configuration exists;
* Supabase is reachable;
* database migrations are current;
* the application can accept work.

An authenticated dependency check may verify:

* Twilio configuration;
* Gemini access;
* Google Calendar access;
* Resend access;
* Telegram configuration.

### Worker Health

The worker records:

* heartbeat timestamp;
* worker identifier;
* last successful job;
* active job count;
* oldest pending job age;
* dead-lettered job count.

The worker is unhealthy when:

* heartbeat becomes stale;
* job backlog exceeds the approved threshold;
* leases remain expired without recovery;
* repeated provider failures prevent progress.

### Hermes Health

Hermes records:

* heartbeat timestamp;
* last monitoring run;
* last Telegram delivery;
* last approved administrative command.

Hermes failure creates an alert but does not mark the core API and worker unavailable.

### Infrastructure Health

Monitor:

* Docker service state;
* container health;
* Cloudflare Tunnel connection;
* VM disk space;
* VM memory;
* VM CPU;
* system time synchronization;
* Supabase connectivity;
* backup status.

## Logging And Monitoring

Logs must be structured and include:

* request ID;
* lead ID;
* job ID;
* call attempt ID;
* appointment ID;
* provider object ID;
* status transition;
* error category;
* latency;
* retry count.

Logs must not contain:

* API keys;
* OAuth refresh tokens;
* full webhook signatures;
* authorization headers;
* full phone numbers;
* full email addresses;
* full transcripts by default.

Minimum operational metrics:

* leads received;
* calls scheduled;
* calls answered;
* calls completed;
* no-answer, busy, voicemail, and failure rates;
* qualification rate;
* booking rate;
* opt-out rate;
* model latency and errors;
* calendar conflicts and failures;
* email failures;
* job backlog;
* dead-lettered jobs;
* duplicate provider events;
* worker and Hermes heartbeat age.

Primary alert channel:

```text
Telegram
```

Fallback alert channel:

```text
Resend email
```

## Database Migrations

Database migrations must be versioned in the repository.

Rules:

* migrations run before the new application version receives traffic;
* migrations are tested in development and staging;
* destructive migrations require explicit approval;
* application changes should remain compatible during rollout where practical;
* rollback must not blindly reverse data migrations;
* corrective forward migrations are preferred.

The deployed migration version must be visible through an internal health or diagnostics command.

## Continuous Integration

GitHub Actions should run on pull requests and protected branches.

Required checks:

* Python formatting check;
* linting;
* type checking;
* unit tests;
* integration tests using mocks or test containers;
* secret scanning if configured;
* dependency vulnerability checks if configured;
* Docker image build;
* Compose configuration validation.

Deployment must not proceed when required checks fail.

## Deployment Method

Recommended deployment flow:

1. CI validates the commit.
2. A versioned Docker image or reviewed Git SHA is produced.
3. The target environment pulls the approved version.
4. Database migrations run.
5. `api`, `worker`, `hermes`, and `cloudflared` start.
6. Health checks run.
7. Calling remains paused.
8. A no-op worker job is processed.
9. Telegram alert delivery is tested.
10. Calling is enabled only after approval.

Every deployment records:

* Git SHA;
* image version;
* migration version;
* deployment timestamp;
* environment;
* operator;
* health-check result.

## Backup And Recovery

Supabase production backups must be enabled before launch.

Backup requirements:

* documented retention period;
* restricted access;
* known restore process;
* staging restore test before production launch.

Recovery scenarios:

### API Restart

* active provider callbacks continue after restart where possible;
* incomplete call sessions are reconciled by the worker;
* pending jobs remain in Supabase.

### Worker Restart

* expired job leases are released;
* unfinished jobs return to retry or recovery state;
* duplicate external side effects are prevented by idempotency keys.

### Hermes Restart

* core calling and job processing continue;
* monitoring and Telegram administration resume after restart;
* missed scheduled summaries may be regenerated from Supabase.

### Calendar Timeout

* worker searches Google Calendar using stored extended properties;
* an existing event is reused;
* a second event is not created blindly.

### Twilio Uncertainty

* worker queries or reconciles Twilio call state;
* retry occurs only when the previous call outcome is confirmed safe.

## Rollback

### Application Rollback

* keep the previous approved image or Git SHA;
* pause new calling;
* restore the previous application version;
* rerun health checks;
* reconcile active calls and jobs;
* resume calling only after approval.

### Operational Pause

Pausing calling must:

* stop new outbound call jobs;
* leave FastAPI available for existing Twilio callbacks;
* leave worker recovery tasks available;
* preserve booking and notification reconciliation;
* notify the owner.

### Data Recovery

Whole-database restore is a last resort.

Prefer:

* corrective migration;
* audited state repair;
* provider reconciliation;
* targeted record correction.

A full restore requires explicit approval because it may revert valid calls, bookings, suppression records, or provider events.

## Deployment Gates

### Gate 1 - Local Compose

Required evidence:

* all four services start;
* health checks pass;
* mock lead flow passes;
* no real provider actions occur.

### Gate 2 - Staging Infrastructure

Required evidence:

* staging hostname works;
* secure WebSocket connects;
* Supabase is reachable;
* Telegram alert arrives;
* signed Twilio fixture is accepted;
* invalid signature is rejected.

### Gate 3 - Staging End-To-End

Required evidence:

* controlled call succeeds;
* Gemini conversation completes;
* calendar booking is created once;
* email confirmation is sent once;
* owner alert arrives;
* failure and duplicate-event tests pass;
* API and worker restart recovery pass;
* backup restore succeeds.

### Gate 4 - Production Preflight

Required evidence:

* production secrets configured;
* backups enabled;
* alerts working;
* migrations current;
* calling paused;
* rollback version available;
* one no-op job succeeds.

### Gate 5 - Controlled Production Launch

Required evidence:

* one consented test lead is called;
* disclosure is spoken;
* qualification completes;
* booking is created once;
* confirmation is sent;
* owner alert arrives;
* logs contain no secrets or unnecessary personal data.

## Stop Conditions

Deployment stops when:

* production secrets appear in Git, logs, or chat;
* Twilio signatures cannot be verified behind Cloudflare;
* duplicate calls, bookings, or emails are possible;
* pending jobs are lost after restart;
* suppression can be bypassed;
* calendar uncertainty creates duplicate events;
* backups cannot be restored;
* calling cannot be paused safely;
* worker recovery fails;
* staging acceptance criteria remain incomplete.
