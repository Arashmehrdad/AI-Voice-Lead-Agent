# Acceptance Criteria

## Purpose

These criteria define when the AI voice lead qualification and appointment-booking system is complete enough to move from development to staging and from staging to production.

The first release supports one business. QuoteFollow compatibility is maintained through generic source fields and adapter boundaries, but QuoteFollow-specific workflows are outside the initial scope.

## Lead Intake

The lead intake feature is accepted when:

* A valid lead can be submitted with a phone number, consent status, consent source, and capture time.
* Phone numbers are normalized before storage.
* Leads without an approved consent state are not scheduled for a call.
* Suppressed or opted-out leads cannot receive new call jobs.
* Repeated submissions with the same source identifier do not create duplicate call jobs.
* Every accepted or rejected lead action is recorded in durable state.
* QuoteFollow or another external system can later identify a lead using generic source fields without changing the core calling workflow.

## Durable Job Processing

The background worker is accepted when:

* Due jobs are claimed safely so that two workers cannot process the same job simultaneously.
* Jobs remain available after an API, worker, container, or VM restart.
* Transient failures create bounded retries with recorded reasons.
* Permanent failures are not retried indefinitely.
* Stale job locks can be released safely.
* Failed jobs remain visible for manual inspection.
* Every external side effect uses a unique idempotency key.

## Twilio Calling

The Twilio integration is accepted when:

* One approved lead creates one outbound call attempt.
* Twilio HTTP webhook signatures are verified before business state changes.
* ConversationRelay WebSocket connections are rejected when signature validation fails.
* Duplicate Twilio callbacks do not duplicate calls, retries, status changes, or other side effects.
* Call outcomes are recorded as completed, no answer, busy, voicemail, failed, timed out, or escalated.
* No-answer and busy outcomes follow the approved retry policy.
* Invalid or permanently unreachable numbers are not retried unnecessarily.
* A fixed-script staging call can reach an approved test number successfully.

## AI Conversation

The Gemini conversation loop is accepted when:

* The first spoken turn identifies the business and states that the caller is an automated AI assistant.
* The assistant explains the purpose of the call.
* The assistant follows only the approved qualification flow.
* The lead can request a human at any time.
* Opt-out language immediately stops automated persuasion and prevents future calls.
* Unsupported, sensitive, low-confidence, or complaint-related requests create a human escalation.
* Lead speech is treated as untrusted input.
* Gemini cannot directly access provider credentials, call external services, or mutate database state.
* Structured model decisions are validated by deterministic application code before any side effect.
* Model timeout or failure ends or transfers the call safely without falsely promising a booking.
* The real-time voice loop works without Hermes.

## Calendar Booking

Calendar booking is accepted when:

* Only slots returned by Google Calendar availability checks are offered.
* The lead confirms the selected slot before booking.
* A local appointment record is created before the external calendar request.
* Repeated booking requests for the same lead and slot create only one Google Calendar event.
* A calendar timeout triggers reconciliation before another event-creation attempt.
* The system never tells the lead that an appointment is booked until the calendar event is confirmed.
* Calendar conflicts return the lead to slot selection or human escalation.

## Confirmation And Owner Notification

The confirmation flow is accepted when:

* A confirmation email is created only after a calendar event is confirmed.
* Retry or webhook duplication cannot send the same confirmation repeatedly.
* The owner receives a Telegram notification for successful bookings.
* The owner receives alerts for escalations, repeated provider failures, stale jobs, and unresolved booking states.
* Telegram failure does not roll back a successfully created appointment.
* Operational alerts contain no secrets or unnecessary personal data.

## Hermes Administration

Hermes integration is accepted when:

* Hermes provides monitoring, summaries, alerts, and approved administrative actions.
* Telegram users and chats are restricted through allowlists.
* Unauthorized commands are rejected.
* Pause, resume, retry, and human-follow-up actions are audited.
* Hermes invokes only approved scripts or authenticated internal endpoints.
* Customer speech cannot reach Hermes administrative tools.
* FastAPI and the deterministic worker continue operating temporarily if Hermes is unavailable.

## Security And Privacy

The security baseline is accepted when:

* Real credentials exist only in environment variables or an approved secret store.
* `.env`, API keys, OAuth tokens, private keys, and service credentials are absent from Git.
* Logs redact full phone numbers, email addresses, authorization headers, tokens, and signatures.
* Full call recording is disabled by default.
* Transcript storage follows the approved retention policy.
* Prompt injection cannot bypass consent, suppression, booking, retry, or escalation rules.
* Owner/admin routes require authentication.
* Production and staging credentials are separate.

## Operational Readiness

The system is operationally accepted when:

* Liveness and readiness endpoints report meaningful status.
* Container health checks exist.
* API, worker, Hermes, database, and Cloudflare Tunnel failures are detectable.
* Job backlog, call failures, model latency, calendar failures, and provider errors are observable.
* Backups are enabled for production data.
* A staging restore test succeeds.
* Restarting the API and worker does not lose pending jobs.
* Calling can be paused without disabling required Twilio callbacks.
* A documented rollback procedure exists.

## Staging Gate

Staging is complete when the following paths pass:

* successful lead-to-call-to-booking flow;
* duplicate lead submission;
* invalid Twilio signature;
* no answer;
* busy;
* voicemail;
* invalid phone number;
* model timeout;
* opt-out;
* human escalation;
* calendar conflict;
* calendar timeout and reconciliation;
* duplicate booking attempt;
* confirmation-email retry;
* API and worker restart;
* unauthorized Telegram command.

## Production Launch Gate

Production launch is allowed only when:

* Staging acceptance tests pass.
* Consent and disclosure wording are approved.
* Backup restore evidence exists.
* Signature verification is proven through the production ingress path.
* Idempotency tests show no duplicate calls, bookings, or confirmation emails.
* Production secrets are configured outside the repository.
* One controlled call to an approved test lead succeeds.
* The owner receives the expected booking and failure alerts.
* Pause and rollback procedures have been tested.

## Completion Definition

The product is accepted when a consented lead can move safely from intake to outbound AI call, qualification, confirmed calendar booking, confirmation email, owner notification, and durable audit state without duplicate side effects, lost jobs, uncontrolled retries, misleading booking claims, or unsafe model actions.
