# Acceptance Criteria

## Product Acceptance

- The platform supports exactly one business without tenant-switching complexity.
- A lead can be created with consent metadata, phone number, and optional email.
- A lead without sufficient consent is not called.
- An opted-out or suppressed lead is not called again.
- The first AI spoken turn identifies the business, states the automated nature where required, states the purpose, and offers opt-out or human help.
- The AI qualifies the lead using only approved business criteria.
- The AI can book an appointment only after confirming a real available slot.
- A booked appointment appears in Google Calendar exactly once.
- A confirmation email is sent exactly once per booked appointment.
- The owner receives Telegram alerts for bookings, escalations, repeated failures, and recovery issues.

## Lead-To-Booking Flow Acceptance

Happy path:
- Lead intake creates a lead.
- Hermes schedules and initiates a call.
- Twilio connects ConversationRelay to FastAPI.
- FastAPI verifies Twilio signature.
- FastAPI conducts the conversation with Gemini.
- Lead qualifies.
- Google Calendar availability is checked.
- Lead chooses a slot.
- Appointment is created idempotently.
- Resend confirmation is sent.
- Owner summary is sent through Telegram.
- Lead status becomes `booked`.

Non-happy paths:
- No answer schedules a bounded retry.
- Busy schedules a bounded retry.
- Voicemail records the outcome and retries only within policy.
- Failed provider call records failure and retries only if transient.
- Invalid phone marks lead `invalid_phone`.
- Opt-out suppresses the lead immediately.
- Human request creates escalation and stops automated calling.
- Calendar conflict returns to slot selection or escalates.
- Calendar timeout does not promise a booking until reconciled.
- Duplicate webhook does not duplicate side effects.

## Architecture Acceptance

- FastAPI owns the latency-sensitive ConversationRelay WebSocket loop.
- Hermes owns orchestration, scheduling, retry, recovery, and Telegram administration.
- Hermes is not called inside the per-turn WebSocket loop.
- FastAPI does not run scheduled recovery jobs.
- All durable state needed for recovery is stored in Supabase.
- Provider-specific logic is isolated behind adapter boundaries.
- No extra queue, cache, workflow engine, Kubernetes cluster, or observability service is introduced without explicit evidence and approval.
- The MVP does not add QuoteFollow-specific behavior, but business-specific qualification criteria, appointment types, disclosure copy, and templates are isolated enough that a future QuoteFollow adaptation does not require rewriting the voice gateway or job engine.

## Data Model Acceptance

- Supabase schema includes leads, lead events, call attempts, call sessions, conversation turns, qualification results, appointments, emails, jobs, provider events, human escalations, admin commands, audit logs, suppression list, and business settings.
- Every external side effect has a unique idempotency key.
- Every inbound provider event has duplicate-event prevention.
- Booking idempotency prevents duplicate calendar events for the same lead, appointment type, and slot.
- Status values are finite and documented.
- Terminal state transitions are protected from accidental overwrite.
- Consent status, source, capture time, and evidence location are stored before calling.

## Security Acceptance

- Twilio HTTP webhooks require valid `X-Twilio-Signature`.
- Twilio ConversationRelay WebSocket handshakes require valid `X-Twilio-Signature`.
- Invalid signatures create no business side effects.
- Telegram admin commands are restricted to allowlisted users and chats.
- Supabase admin routes require authenticated owner/admin access.
- Secrets are read only from environment variables or approved secret stores.
- No secrets are committed to Git.
- Logs redact tokens, signatures, full phone numbers, full emails, and unnecessary personal data.
- Prompt-injection attempts cannot directly trigger provider side effects without deterministic validation.

## UK Compliance Acceptance

- Lead sources are classified before production.
- Automated marketing calls are blocked unless consent is documented where required.
- The caller number is displayed where required.
- The call identifies the business.
- The call gives required contact information or an approved path to it.
- Withdrawal of consent stops automated calls.
- Complaint, disputed consent, or data rights requests trigger human escalation.
- Silent, abandoned, failed, and timed-out call indicators are monitored.
- Owner/legal approval is recorded before production launch.

## Operational Acceptance

- Health checks exist for FastAPI liveness and readiness.
- Hermes writes heartbeat and job health state.
- Metrics cover calls, bookings, failures, opt-outs, escalations, provider latency, duplicate events, and job backlog.
- Alerts cover provider failures, signature spikes, call failure thresholds, booking failures, stale jobs, and backup failures.
- Database backups are scheduled and restore-tested.
- Recovery jobs can repair stale calls, stuck jobs, and uncertain calendar bookings.
- Rollback procedure is documented and rehearsed in staging.

## Environment Acceptance

Development:
- Uses fake or sandbox provider credentials.
- Can run without making real calls, sending real emails, or booking real calendars by default.
- Contains no production data.

Staging:
- Uses separate provider resources from production.
- Uses test phone numbers, test leads, test calendar, and owner-approved scripts.
- Proves full flow before production.

Production:
- Uses production credentials only through environment variables.
- Runs on Oracle Cloud VM through Docker Compose.
- Exposes HTTPS and secure WebSockets through Cloudflare Tunnel.
- Has backups, monitoring, alerting, recovery jobs, and rollback path.

## Stage Evidence Acceptance

Each stage is accepted only when:
- Entry criteria were met before work started.
- Exit criteria are satisfied.
- Required tests were run.
- Evidence is attached or recorded.
- Failures are classified as related or unrelated.
- Owner decisions are recorded when needed.

## Quality Gate Acceptance

When coding begins in later stages:
- Use existing project scripts first.
- Run Python format/checks/typecheck/tests if Python code changes and tools are configured.
- Run JS/TS format/lint/typecheck/test/build if JS/TS code changes and tools are configured.
- Run configured pre-commit if present.
- Run configured security or secret scans if present.
- Report skipped checks and why.
- Do not claim completion while required relevant checks fail.

Current planning run:
- Only Markdown planning documents are created.
- No code quality gates are applicable yet.
- Verification is limited to file existence and content review.

## Launch Acceptance

Production launch is accepted only after:
- Staging happy path passes.
- Staging failure paths pass.
- Backup restore drill passes.
- Signature verification tests pass.
- Calendar idempotency tests pass.
- Duplicate webhook tests pass.
- Owner/legal compliance approval is recorded.
- Controlled production test call succeeds.
- Owner confirms operational alerts are received.
