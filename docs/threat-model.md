# Threat Model

## Purpose

This document identifies the main security, privacy, compliance, and operational risks for the AI voice lead qualification and appointment-booking platform.

The first release serves one business and processes:

* lead contact details;
* consent evidence;
* call metadata;
* qualification summaries;
* appointment details;
* provider webhooks;
* owner Telegram commands;
* API keys and OAuth credentials.

The design assumes that customer speech, public webhooks, provider callbacks, and external lead payloads are untrusted.

## Protected Assets

The system must protect:

* lead names, phone numbers, email addresses, and source data;
* consent records and suppression status;
* call outcomes and conversation summaries;
* appointment dates and Google Calendar event identifiers;
* Twilio, Gemini, Supabase, Google, Resend, Telegram, and Cloudflare credentials;
* production database and backups;
* owner Telegram account and administrative permissions;
* business reputation and compliance records.

## Trust Boundaries

Important trust boundaries are:

```text
Internet -> Cloudflare Tunnel
Cloudflare Tunnel -> FastAPI
Twilio -> FastAPI webhooks and WebSocket
FastAPI -> Gemini
FastAPI and worker -> Supabase
FastAPI -> Google Calendar
Worker -> Twilio, Resend, and Telegram
Hermes -> approved internal API or scripts
GitHub Actions -> production deployment
Operator computer -> Oracle VM
```

Requests crossing these boundaries require authentication, validation, or strict allowlisting.

## Threats And Controls

### Unauthorised Or Unlawful Calls

Threats:

* calling without sufficient consent;
* calling a suppressed or opted-out number;
* excessive retries;
* calling outside approved hours;
* continued persuasion after an opt-out request.

Controls:

* store consent status, source, capture time, and evidence before scheduling;
* block calls when consent is unknown, withdrawn, blocked, or insufficient;
* check the suppression list both when creating and when executing a call job;
* enforce calling hours and maximum attempts;
* cancel pending call jobs after opt-out;
* disclose the business identity and automated nature of the caller;
* escalate disputed consent or complaints to a human.

Development and staging calls are restricted to approved test numbers.

### Spoofed Twilio Requests

Threat:

An attacker sends fake Twilio status callbacks or opens an unauthorised ConversationRelay WebSocket.

Controls:

* validate `X-Twilio-Signature` for every HTTP webhook;
* validate the initial ConversationRelay WebSocket handshake;
* use the full externally visible URL during signature validation;
* account for Cloudflare forwarding headers;
* reject invalid signatures before database changes;
* record redacted security events for repeated failures.

### Duplicate Events And Side Effects

Threat:

Provider retries or application restarts create duplicate calls, calendar events, emails, or alerts.

Controls:

* record inbound provider events before processing;
* enforce a unique provider idempotency key;
* use deterministic idempotency keys for every external side effect;
* acknowledge already-processed duplicate events without repeating work;
* reconcile uncertain provider state before retrying;
* create local appointment records before Google Calendar events.

### Prompt Injection And Model Overreach

Threat:

A lead instructs Gemini to ignore policy, reveal hidden instructions, access tools, create unsupported bookings, or perform administrative actions.

Controls:

* treat all lead speech as untrusted;
* provide Gemini only minimal approved context;
* require structured model output;
* validate model decisions in deterministic application code;
* keep provider credentials and direct tool access away from Gemini;
* prevent Gemini from writing to Supabase or calling providers directly;
* allow only predefined qualification, booking, opt-out, escalation, and call-ending actions;
* escalate unsupported, sensitive, legal, financial, medical, or low-confidence situations.

Customer input must never reach Hermes administrative commands.

### Unauthorised Telegram Administration

Threat:

An unauthorised user pauses calling, retries leads, accesses summaries, or triggers maintenance actions.

Controls:

* allowlist Telegram user IDs and chat IDs;
* allow only documented commands;
* require confirmation for destructive or high-impact actions;
* audit all accepted and rejected commands;
* expose no secret values through Telegram;
* make Hermes call only authenticated internal endpoints or approved scripts;
* keep the core API and worker independent from Hermes availability.

### Secret Exposure

Threat:

Credentials are exposed through Git, logs, shell history, crash traces, chat messages, database rows, or CI output.

Controls:

* load secrets from environment variables or approved secret stores;
* keep `.env` outside Git;
* include variable names only in `.env.example`;
* redact authorization headers, tokens, signatures, API keys, and OAuth credentials;
* avoid printing environment contents;
* separate development, staging, and production credentials;
* rotate any secret suspected of exposure;
* use least-privilege provider credentials where available.

`AGENTS.md` may document the approved `.env` location and variable names but must not contain secret values.

### Personal Data Exposure

Threat:

Logs, transcripts, backups, alerts, or provider payloads expose unnecessary personal information.

Controls:

* mask phone numbers and email addresses in logs;
* store conversation summaries and structured facts by default;
* disable full recordings and transcripts by default;
* define retention periods before production;
* encrypt sensitive stored content where full transcripts are later approved;
* restrict Supabase and backup access;
* avoid sending full customer details in Telegram alerts;
* delete or anonymise data according to the approved retention policy.

### Job Queue Abuse Or Failure

Threats:

* two workers execute the same job;
* a crashed worker leaves jobs permanently locked;
* retries continue indefinitely;
* jobs run after consent is withdrawn;
* a malicious payload causes unsafe provider actions.

Controls:

* claim jobs using database locking;
* use expiring worker leases;
* use bounded retry counts;
* validate job type and payload against schemas;
* recheck consent and suppression immediately before calling;
* cancel unsafe pending jobs;
* move exhausted jobs to a visible dead-letter state;
* require manual approval for high-impact recovery actions.

Hermes does not directly control the production job queue.

### Calendar Booking Errors

Threat:

A timeout or retry creates duplicate appointments or falsely tells a lead that a booking succeeded.

Controls:

* create a local appointment with a unique booking key first;
* store the local appointment identifier in Google Calendar extended properties;
* search for an existing event before retrying creation;
* require a Google event ID before marking the appointment booked;
* never promise a booking while provider status is uncertain;
* escalate unresolved conflicts or failures.

### Silent Or Broken Calls

Threat:

Model latency, WebSocket failure, provider errors, or container restarts produce silence, repeated messages, abandoned calls, or misleading responses.

Controls:

* keep the live voice loop separate from Hermes and background jobs;
* set strict model and WebSocket timeouts;
* provide a safe fallback message where technically possible;
* end the call politely after repeated failures;
* record incomplete call outcomes;
* reconcile call state through Twilio callbacks;
* alert the owner when failure thresholds are exceeded;
* test restart and disconnect scenarios in staging.

### Supply Chain And Deployment Risk

Threat:

Compromised dependencies, containers, CI workflows, or deployment credentials affect production.

Controls:

* pin dependencies where practical;
* run formatting, linting, type checking, tests, and security scans;
* review dependency and container changes;
* protect production deployment branches and secrets;
* deploy reviewed Git SHAs or versioned images;
* keep a known-good rollback version;
* avoid running containers with unnecessary privileges;
* expose only required routes through Cloudflare Tunnel.

### Backup And Recovery Risk

Threat:

Backups are unavailable, contain exposed personal data, or restore an inconsistent system state.

Controls:

* enable production database backups;
* restrict backup access;
* document retention and deletion;
* test restoration in staging;
* record migration and application versions;
* prefer targeted correction and provider reconciliation over full restore;
* preserve suppression and opt-out state during recovery.

## Security Logging And Alerts

Record security-relevant events including:

* invalid webhook signatures;
* duplicate-event spikes;
* unauthorised Telegram commands;
* consent or suppression overrides;
* repeated provider failures;
* secret-redaction failures;
* unusual job backlog or expired leases;
* manual production actions;
* calendar reconciliation failures.

Alerts should be sent through Telegram, with Resend email as a fallback where possible.

Alerts must contain identifiers and redacted summaries, not credentials or unnecessary personal data.

## Production Stop Conditions

Production calling must remain paused when:

* consent rules are unresolved;
* Twilio signature verification is not proven;
* suppression can be bypassed;
* duplicate calls, bookings, or confirmation emails are possible;
* secrets or full personal data appear in logs;
* Gemini can directly trigger unchecked side effects;
* jobs are lost or duplicated after restart;
* calling cannot be paused safely;
* backups cannot be restored;
* provider uncertainty produces false success;
* staging failure-path tests have not passed.

## Residual Risk

The system cannot eliminate all risks from:

* provider outages;
* speech-recognition mistakes;
* model misunderstandings;
* incorrect lead information;
* calendar changes made outside the platform;
* regulatory interpretation.

These risks are reduced through bounded automation, explicit confirmation, deterministic validation, audit trails, monitoring, and human escalation.

This document is an engineering security plan and not legal advice. Commercial deployment requires the business owner to confirm the lawful basis, consent wording, retention policy, disclosure script, and escalation process.
