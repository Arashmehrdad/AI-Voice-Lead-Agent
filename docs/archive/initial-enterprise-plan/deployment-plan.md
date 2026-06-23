# Deployment Plan

## Purpose

Define how the single-business AI voice lead qualification platform will move from local development to staging and production without creating infrastructure yet.

The platform may inform a future QuoteFollow deployment. Deployment design should therefore keep environment names, domains, provider credentials, templates, and business settings externalized, while still treating this repository as a single-business MVP until QuoteFollow is explicitly scoped.

## Environments

### Development

Purpose:
- Local implementation and automated tests.

Rules:
- No production data.
- No live outbound calls by default.
- Provider adapters default to mocks or sandbox/test credentials.
- Secrets come from local environment variables only.
- `.env.example` may list required variable names but no real values.

Evidence before leaving development:
- Unit tests pass.
- Contract tests pass.
- Signature verification tests pass with fixtures.
- State-machine and idempotency tests pass.

### Staging

Purpose:
- Prove full flow with isolated external resources.

Resources:
- Separate Supabase project or schema.
- Separate Twilio number or verified test configuration.
- Separate Google Calendar.
- Separate Resend sender/domain or test mode.
- Separate Telegram chat.
- Separate Cloudflare Tunnel hostname.
- Staging environment variables only.

Rules:
- Use test leads and owner-approved test recipients only.
- Exercise real provider behavior before production.
- No production credentials or production lead data.

Evidence before production:
- End-to-end happy path.
- No-answer, busy, voicemail, failed, timeout, opt-out, human escalation, duplicate webhook, and calendar conflict paths.
- Backup and restore drill.
- Cloudflare Tunnel restart drill.
- Oracle VM container restart drill.
- Owner acceptance.

### Production

Purpose:
- Live single-business operation.

Resources:
- Production Supabase project.
- Production Twilio number and verified configuration.
- Production Google Calendar.
- Production Resend sender/domain.
- Production Telegram owner chat.
- Production Oracle Cloud VM.
- Production Cloudflare Tunnel hostname.
- Production environment variables.

Rules:
- Only consented and approved leads.
- Rollout begins with a small controlled batch.
- Production secrets are not copied to development or staging.
- Production database backups are enabled before first live call.

## Docker Compose Plan

Planned services:
- `api`: FastAPI app, HTTP endpoints, WebSocket voice gateway.
- `hermes`: orchestration, scheduled recovery, Telegram admin.
- `cloudflared`: Cloudflare Tunnel sidecar if managed in Compose.

Not planned for MVP:
- Redis.
- Celery/RQ.
- Kubernetes.
- Separate message broker.
- Separate metrics database.
- Full dashboard container.

Rationale:
- Supabase persistent jobs are sufficient for one-business workload and recovery needs.
- FastAPI and Hermes have clear boundaries.
- Docker Compose is operationally simpler for an Oracle Cloud VM.

## Cloudflare Tunnel Plan

Ingress:
- Public HTTPS routes to FastAPI HTTP endpoints.
- Public secure WebSocket route to FastAPI ConversationRelay endpoint.

Requirements:
- Preserve host, scheme, and forwarding headers needed for Twilio signature validation.
- Avoid exposing broad inbound VM ports.
- Monitor tunnel process health.
- Use separate staging and production hostnames.

## GitHub Actions Plan

CI responsibilities when code exists:
- Checkout.
- Set up Python.
- Install dependencies from locked files.
- Run formatter/check in check mode where available.
- Run lint where available.
- Run typecheck where available.
- Run tests.
- Run secret scan/security tools if configured.
- Build Docker images.

Deployment responsibilities when approved:
- Deploy only from protected branch or tag.
- Require CI pass.
- Use environment-scoped secrets.
- Record deployed Git SHA.
- Support rollback to previous image/SHA.

No deployment workflow should be created until implementation reaches the deployment stage.

## Environment Variables

Environment variables are the required secret handling mechanism.

Categories:
- App environment: `ENVIRONMENT`, `APP_PUBLIC_BASE_URL`, logging level.
- Supabase: URL, anon key, service role key.
- Twilio: account SID, auth token, caller ID.
- Gemini: API key and model configuration.
- Google: OAuth client, refresh token, calendar ID.
- Resend: API key and sender identity.
- Telegram: bot token, allowed user IDs, allowed chat IDs.
- Cloudflare: tunnel token.

Rules:
- Real values never enter Git.
- Production values are only stored on production VM or approved deployment secret store.
- Staging and production use separate values.
- Secret rotation procedure is required before production.

## Health Checks

FastAPI:
- Liveness: process is running.
- Readiness: database reachable and required environment variables present.
- Dependency check: optional authenticated endpoint checks provider readiness.

Hermes:
- Heartbeat timestamp in Supabase.
- Job loop can claim and complete a test no-op job in staging.
- Recovery job last-success timestamp.

VM:
- Docker service healthy.
- Disk space threshold.
- Memory and CPU threshold.
- Time synchronization.
- Cloudflare Tunnel connected.

Supabase:
- Database reachable.
- Backup status known.
- Migration version current.

## Metrics And Alerts

Minimum metrics:
- Calls attempted, answered, completed, failed.
- No-answer, busy, voicemail, timeout rates.
- Qualification and booking rates.
- Opt-out and human escalation rates.
- Gemini latency and errors.
- Twilio webhook and WebSocket signature failures.
- Duplicate provider events.
- Calendar booking conflicts and failures.
- Email delivery failures.
- Job backlog and dead-letter count.
- Recovery job repairs and unresolved records.
- Backup success/failure.

Alert channels:
- Telegram primary.
- Email fallback through Resend if Telegram fails and email is healthy.

Alert examples:
- Production API readiness fails.
- Hermes heartbeat stale.
- Call failure rate exceeds threshold.
- Signature validation failures spike.
- Booking failures exceed threshold.
- Job backlog exceeds threshold.
- Backup fails.
- Disk usage high.

## Backup And Recovery

Backups:
- Supabase automated backups enabled for production.
- Export or snapshot strategy documented before launch.
- Backup retention period approved by owner.
- Backup access restricted.

Restore drills:
- Staging restore from staging backup before production.
- Production restore procedure documented before launch.
- Restore evidence includes database version, row counts, and application readiness.

Recovery jobs:
- Stale call sessions: reconcile with Twilio and mark terminal or schedule retry.
- Stuck jobs: release expired locks or dead-letter after retry budget.
- Calendar uncertainty: search Google Calendar by extended property before retrying creation.
- Email uncertainty: check local idempotency and provider status before resending.
- Provider event replay: dedupe by provider idempotency key.

## Rollback Plan

Application rollback:
- Keep previous deployable image or Git SHA.
- Revert container image and restart services.
- Do not roll back database migrations blindly.
- Migrations must include forward-fix or explicit rollback notes.

Operational rollback:
- Pause Hermes call scheduling.
- Stop new outbound calls.
- Keep FastAPI available for provider callbacks if calls are in progress.
- Alert owner.
- Reconcile in-progress calls and bookings before resuming.

Data rollback:
- Prefer corrective migrations or manual audited fixes over whole-database restore.
- Whole restore is only for severe data corruption and requires owner approval.

## Small Gated Deployment Stages

### Deployment Stage A: Local Compose Dry Run

Entry criteria:
- Core app and Hermes exist.
- Local tests pass.

Exit criteria:
- Docker Compose starts locally.
- Health checks pass.
- No real providers are called.

Tests:
- Container start test.
- Local health check.
- Mock provider flow.

Evidence:
- Compose logs.
- Health check output.
- Test output.

### Deployment Stage B: Staging Infrastructure

Entry criteria:
- Deployment Stage A complete.
- Staging credentials approved.

Exit criteria:
- Staging VM or staging target runs Compose.
- Staging Cloudflare Tunnel is reachable.
- Staging Supabase is connected.
- Provider test credentials are configured.

Tests:
- Readiness check.
- Signed Twilio webhook fixture.
- WebSocket handshake test.
- Test Telegram alert.

Evidence:
- Staging URL.
- Redacted env inventory.
- Health output.
- Alert receipt.

### Deployment Stage C: Staging End-To-End

Entry criteria:
- Deployment Stage B complete.
- Owner-approved test leads.

Exit criteria:
- Full staged lead-to-booking flow passes.
- Failure paths pass.
- Recovery jobs pass.

Tests:
- Happy path.
- Failure path suite.
- Duplicate-event replay.
- Calendar idempotency.
- Backup restore drill.

Evidence:
- Redacted logs.
- Metrics snapshot.
- Restore evidence.
- Owner approval.

### Deployment Stage D: Production Preflight

Entry criteria:
- Deployment Stage C complete.
- Production secrets available.
- Legal/owner approval recorded.

Exit criteria:
- Production VM ready.
- Production env variables configured.
- Backups enabled.
- Rollback path ready.
- Hermes scheduling paused until controlled test.

Tests:
- Production readiness check.
- Alert check.
- Backup status check.
- No-op job check.

Evidence:
- Health output.
- Backup confirmation.
- Alert receipt.
- Deployment record.

### Deployment Stage E: Controlled Production Launch

Entry criteria:
- Deployment Stage D complete.
- One consented test lead approved.

Exit criteria:
- Controlled call succeeds.
- Booking and confirmation work.
- Owner alert received.
- No duplicate side effects.
- Calling remains paused or limited until owner approves wider rollout.

Tests:
- One production happy path.
- One production opt-out or suppression dry path if feasible without nuisance.

Evidence:
- Redacted call record.
- Calendar event ID.
- Email provider message ID.
- Telegram alert.
- Owner approval.

## Operational Runbook Outline

Daily:
- Check Hermes heartbeat.
- Check job backlog and dead-letter queue.
- Check prior-day call outcomes and opt-outs.
- Check backup success.

Weekly:
- Review failed calls and escalation quality.
- Review consent/source anomalies.
- Review costs and provider usage.
- Test owner alert path.

Incident:
- Pause Hermes scheduling.
- Preserve FastAPI callback availability.
- Identify affected leads/calls/appointments.
- Reconcile provider state.
- Notify owner.
- Apply fix or rollback.
- Record post-incident notes and prevention action.

## Stop Conditions

- Production secrets appear in logs, repo, or chat.
- Signature validation cannot be verified behind Cloudflare Tunnel.
- Calendar idempotency fails in staging.
- Backup restore cannot be proven.
- Staging creates duplicate calls, emails, or calendar events.
- Consent review is incomplete.
- Owner alerting fails.
- Hermes cannot be paused safely.
