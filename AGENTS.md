# Project Agent Instructions

## Project

This repository implements a production-oriented AI voice lead qualification and appointment-booking system.

Current architecture:

* FastAPI handles lead intake, health endpoints, Twilio webhooks, ConversationRelay WebSocket traffic, Gemini interaction, and calendar booking.
* A deterministic Python worker handles durable jobs, outbound call initiation, retries, recovery, email delivery, and alerts.
* Supabase PostgreSQL is the durable source of truth.
* Hermes is limited to Telegram administration, monitoring, approved recovery assistance, and owner notifications.
* Hermes is not part of the real-time voice loop and does not directly claim production jobs.
* Codex is used only for development and maintenance.

## Authoritative Files

The following files are authoritative and read-only unless the user explicitly requests a change:

* `PLAN.md`
* `docs/architecture.md`
* `docs/data-model.md`
* `docs/threat-model.md`
* `docs/acceptance-criteria.md`
* `docs/deployment-plan.md`
* `docs/api-contracts.md`
* `docs/state-transitions.md`
* `docs/idempotency-registry.md`
* `supabase/migrations/0001_initial_schema.sql`
* `.env.example`

Do not silently modify these files to make implementation easier.

When implementation conflicts with an authoritative file:

1. Stop the affected work.
2. Identify the exact contradiction.
3. Report the affected files and behaviour.
4. Wait for user approval before changing the design.

## Stage Discipline

Implement only the stage explicitly requested by the user.

Do not:

* begin later stages;
* add unrelated features;
* create speculative infrastructure;
* implement future integrations early;
* redesign the architecture;
* expand the data model;
* create additional database tables;
* rewrite working code without a concrete reason.

Before starting a stage:

```powershell
git branch --show-current
git status --short
```

Confirm that required files from previous stages exist on the current branch.

Do not assume `main` contains the latest stage. Stage branches may be based on the previous stage branch until pull requests are merged.

Never run:

```powershell
git branch -M main
```

unless the user explicitly asks to rename the current branch.

Do not commit, merge, push, rebase, or delete branches unless explicitly requested.

## Change Control

Before editing:

1. Read the relevant authoritative files.
2. Inspect the existing implementation and tests.
3. Identify the smallest set of files that must change.
4. Preserve established interfaces unless the requested stage requires changing them.

Prefer:

* small focused changes;
* existing abstractions;
* existing project configuration;
* explicit types;
* deterministic behaviour;
* database-backed safety;
* tests that prove the requested behaviour.

Avoid:

* broad rewrites;
* duplicate logic;
* unnecessary wrappers;
* oversized functions;
* hidden side effects;
* placeholder code presented as complete;
* comments that merely repeat the code.

Do not modify files outside the requested scope without explaining why they are required.

## Python Environment

The project requires Python 3.12.

Use the project virtual environment:

```powershell
.\.venv\Scripts\python.exe --version
```

It must report Python 3.12.x.

Run tools through the project environment where practical:

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m mypy src tests
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
```

Do not validate the project under Python 3.11 and report it as Python 3.12 validation.

Do not create another virtual environment when `.venv` already exists.

The `.venv` directory must remain excluded from Git.

## Dependencies

Use `pyproject.toml` as the dependency authority.

Before adding a dependency:

1. Check whether the project already provides the required capability.
2. Confirm that the dependency is necessary for the current stage.
3. Add it to the correct runtime or development dependency group.
4. Explain why it was added.
5. Run the full validation suite.

Do not install unrelated packages.

Do not introduce dependency managers or lockfile formats that the project does not already use unless explicitly requested.

Use official provider SDKs when required by the stage.

## Database Rules

The SQL migration is the schema authority.

Do not:

* generate ORM migrations;
* create tables automatically from SQLAlchemy models;
* add new tables without explicit approval;
* change constraints to make code pass;
* bypass database uniqueness constraints;
* replace database safety with in-memory checks.

Use SQLAlchemy 2.x async access with `asyncpg`.

Database URLs must use:

```text
postgresql+asyncpg://
```

External side effects must be protected by durable idempotency.

Use database time for:

* job scheduling;
* job leases;
* lock timestamps;
* retry timing;
* durable state transitions.

For concurrent job claiming, use PostgreSQL row locking such as:

```sql
FOR UPDATE SKIP LOCKED
```

Do not claim that an in-memory test proves PostgreSQL concurrency correctness.

Real PostgreSQL integration testing remains required for database locking, transactions, constraints, and concurrent conflict handling.

## Transaction Rules

Operations described as atomic must use one database transaction.

Examples:

* lead creation and initial job creation;
* job claim and lease assignment;
* call-attempt creation and provider request preparation;
* provider-event insertion and deduplication state.

If a required later operation fails, the transaction must not leave partial durable state unless the architecture explicitly requires a recoverable intermediate state.

Do not use a separate existence check followed by an unconditional insert where a unique constraint and `ON CONFLICT` are required.

## Idempotency Rules

Use only the formats defined in `docs/idempotency-registry.md`.

Do not invent alternative key formats.

Examples:

```text
lead:{source_system}:{source_entity_id}
job:initiate_call:{lead_id}:{attempt_number}
call:{lead_id}:{attempt_number}
calendar:{lead_id}:{appointment_type}:{slot_start_utc}:{slot_end_utc}
email:{appointment_id}:confirmation
telegram:{lead_event_id}
```

Do not use:

* random UUIDs as the only side-effect identity;
* timestamps as the only identity;
* phone-only call keys;
* model-generated keys;
* raw provider payloads in keys;
* secrets in keys.

When provider state is uncertain, reconcile before retrying.

Never blindly repeat:

* calls;
* calendar event creation;
* email delivery;
* Telegram alerts;
* provider callback processing.

## Lead Safety

Before initiating an outbound call, recheck:

* calling is not paused;
* consent is callable;
* no phone suppression exists;
* no email suppression exists;
* the lead is not terminal;
* the current time is within approved calling hours;
* the attempt limit is not exceeded;
* no equivalent call attempt already exists.

Suppression and opt-out override all callable states.

Required consent mapping:

```text
unknown -> new
specific_automated_call_consent -> eligible or scheduled
service_follow_up_approved -> eligible or scheduled
withdrawn -> opted_out
do_not_call -> opted_out
blocked -> suppressed
```

Do not map `do_not_call` to `new`.

Do not move opted-out or suppressed leads back to callable states.

## Provider Integration Rules

Use official provider documentation and real staging fixtures.

Do not invent:

* webhook fields;
* callback event names;
* WebSocket message types;
* provider sequence numbers;
* retry semantics;
* signature-validation behaviour.

Unknown provider fields must be accepted safely where appropriate.

Provider callbacks must be authenticated before business state changes.

Twilio validation must use:

* the official Twilio request validator;
* the exact externally visible URL;
* all signed request parameters;
* correct trusted-proxy handling.

Do not make real provider calls during automated tests.

Use injected adapters and mocks.

## Gemini Rules

Gemini output is untrusted.

Gemini must not:

* access credentials;
* write directly to Supabase;
* call providers directly;
* create arbitrary calendar events;
* decide whether compliance checks may be bypassed;
* generate idempotency keys;
* execute administrative commands.

Gemini may return structured decisions only.

Deterministic application code must validate:

* qualification outcomes;
* requested actions;
* selected appointment slots;
* opt-out requests;
* escalation decisions;
* call termination decisions.

Customer speech must never become a Hermes administrative instruction.

## Hermes Rules

Hermes is an operational layer only.

Hermes may:

* display summaries;
* send alerts;
* receive allowlisted Telegram commands;
* call authenticated internal endpoints;
* request approved recovery actions.

Hermes must not:

* enter the real-time voice loop;
* directly claim production jobs;
* bypass consent or suppression;
* modify database state through unrestricted generated SQL;
* initiate calls without deterministic application checks;
* become required for core API or worker availability.

The core system must continue temporarily when Hermes is unavailable.

## Secret Handling

Never commit:

* `.env`;
* API keys;
* tokens;
* passwords;
* private keys;
* OAuth refresh tokens;
* provider credentials;
* production URLs containing credentials.

`.env.example` contains variable names only.

Do not print environment contents.

Do not include secrets in:

* logs;
* exceptions;
* test snapshots;
* documentation;
* commit messages;
* prompts;
* Telegram alerts.

Mask phone numbers and email addresses in operational logs.

If a secret is discovered, stop and report the exact file without repeating the secret value.

## Error Handling

Catch only exceptions that can be handled meaningfully.

Avoid broad:

```python
except Exception:
```

unless it is a deliberate process boundary that logs, contains, and re-raises or converts the failure safely.

Prefer provider-specific, SQLAlchemy-specific, validation-specific, or network-specific exceptions.

Do not hide programming errors by converting every exception into a successful or retryable result.

Health checks may convert expected dependency exceptions into an unavailable status.

Errors returned through the API must use the structured error envelope.

Do not expose stack traces or provider payloads to API clients.

## Request IDs

Generate request IDs server-side until trusted-proxy propagation is explicitly implemented.

Do not trust arbitrary client-supplied `X-Request-Id` values.

Request IDs must not contain personal data.

## Health Endpoints

`/health/live` checks only whether the process is running.

`/health/ready` checks whether the service can safely receive work.

When a required dependency such as PostgreSQL is unavailable, readiness must return:

```text
503 Service Unavailable
```

Calling being paused is not a readiness failure.

Health checks must not create provider side effects.

## Testing Rules

Every behaviour change requires tests.

Tests must cover:

* success;
* validation failure;
* authentication failure;
* duplicate delivery;
* retries;
* terminal states;
* suppression;
* opt-out;
* transaction rollback;
* concurrency where relevant;
* provider failure;
* restart or recovery behaviour where relevant.

Do not weaken tests to make implementation pass.

When an authoritative contract changes, update implementation and tests together.

In-memory repositories may test business logic but do not prove:

* PostgreSQL locking;
* transaction isolation;
* SQL syntax;
* constraint behaviour;
* concurrent `ON CONFLICT` behaviour;
* lease recovery.

Mark missing PostgreSQL integration validation clearly.

Do not leave temporary debugging output such as:

```python
print(response.json())
```

in committed tests.

## Quality Gates

Before reporting completion, run:

```powershell
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m mypy src tests
.\.venv\Scripts\python.exe -m pytest
git diff --check
git status --short
```

If formatting fails, run:

```powershell
.\.venv\Scripts\python.exe -m ruff format .
```

Then repeat all quality gates.

Do not report success when:

* any check failed;
* a check ran under the wrong Python version;
* tests were skipped without disclosure;
* only mocked tests passed for behaviour requiring PostgreSQL validation;
* required files are untracked unintentionally.

## Completion Report

At the end of a task, report only:

1. Files created or changed.
2. Behaviour implemented.
3. Dependencies added or removed.
4. Exact validation commands run.
5. Pass or fail result for each command.
6. Tests skipped or not possible.
7. Unresolved decisions.
8. Contradictions found.
9. Work deliberately deferred to later stages.

Do not claim production readiness based only on unit tests.

Do not commit or push unless the user explicitly requests it.

Stop after the requested stage and wait for review.
