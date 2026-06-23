# Threat Model

## Scope

The system processes lead contact data, call metadata, conversation summaries, appointment details, owner/admin commands, OAuth credentials, API keys, webhook requests, and AI model prompts/responses.

Primary risk areas:
- Unlawful or unwanted automated calls.
- Spoofed provider webhooks.
- Duplicate events causing duplicate calls, emails, or calendar bookings.
- Secret leakage.
- Prompt injection during calls.
- Unauthorized owner/admin commands.
- Data exposure through logs, transcripts, backups, or provider payloads.
- Operational failure causing silent calls, abandoned calls, missed bookings, or repeated retries.

## Assets

- Lead personal data: name, phone, email, business context, consent evidence.
- Call data: Twilio call SID, call status, transcript summaries, qualification result.
- Appointment data: calendar IDs, event IDs, times, attendee contact details.
- Secrets: Twilio auth token, Gemini API key, Supabase service role key, Google OAuth credentials, Resend API key, Telegram bot token, Cloudflare Tunnel token.
- Admin access: Telegram owner IDs, Supabase Auth accounts, deployment SSH access.
- Business reputation and compliance posture.

## Trust Boundaries

- Internet to Cloudflare Tunnel.
- Cloudflare Tunnel to Oracle VM containers.
- Twilio to FastAPI HTTP webhook and WebSocket endpoints.
- FastAPI to Gemini API.
- FastAPI/Hermes to Supabase.
- FastAPI/Hermes to Google Calendar.
- Hermes to Resend.
- Hermes to Telegram.
- GitHub Actions to deployment environment.
- Operator shell to production VM.

## Threats And Mitigations

### Spoofed Twilio Webhooks Or WebSocket Connections

Threat:
- Attacker sends fake call status events or opens a fake ConversationRelay WebSocket.

Mitigations:
- Validate `X-Twilio-Signature` for every Twilio HTTP webhook.
- Validate `X-Twilio-Signature` on the initial ConversationRelay WebSocket handshake.
- Use the exact externally visible URL when validating signatures.
- Reject invalid signatures before parsing business payloads or mutating state.
- Record failed signature attempts as security events with redacted metadata.

### Duplicate Provider Events

Threat:
- Provider retries cause duplicated side effects.

Mitigations:
- Insert into `provider_events` with a unique `(provider, idempotency_key)` before processing.
- Use deterministic idempotency keys when provider event IDs are absent.
- Return success for duplicates already processed.
- Make side effects idempotent: call attempts, calendar bookings, confirmation emails, Telegram alerts.

### Duplicate Calendar Bookings

Threat:
- Timeout or retry creates two calendar events for one lead and slot.

Mitigations:
- Create local appointment with unique booking idempotency key before Google Calendar event creation.
- Store local appointment ID and booking idempotency key in Google Calendar extended properties.
- On timeout, search for existing event by extended property before retrying.
- Send confirmation email only after a single local appointment reaches `booked`.

### Secret Leakage

Threat:
- Secrets appear in logs, repository, issue reports, CI output, shell history, or database rows.

Mitigations:
- Store secrets only in environment variables or provider secret stores.
- Provide `.env.example` with placeholder names only.
- Never commit `.env`, tokens, keys, OAuth refresh tokens, or private keys.
- Redact authorization headers, webhook signatures, tokens, and provider credentials from logs.
- Restrict production secrets to production deployment context.
- Rotate any secret suspected of exposure.

### Unauthorized Telegram Commands

Threat:
- Attacker or wrong chat issues pause, retry, export, or admin commands.

Mitigations:
- Allowlist Telegram user IDs and chat IDs.
- Audit every command in `admin_commands`.
- Require explicit confirmation for destructive or high-impact commands.
- Reject unknown commands and commands from non-allowlisted actors.
- Never expose secrets through Telegram.

### Prompt Injection And Model Overreach

Threat:
- Lead asks the AI to ignore instructions, reveal prompts, make unsupported claims, or perform unsafe actions.

Mitigations:
- Use a strict system prompt with approved business facts and allowed actions.
- Treat lead speech as untrusted input.
- Require structured model decisions for booking, opt-out, and escalation.
- Validate model output against deterministic policy before side effects.
- Escalate legal, medical, financial, complaint, data rights, and low-confidence cases.
- Do not let Gemini directly call providers or mutate state.

### Excessive Or Unlawful Calling

Threat:
- System calls leads without adequate consent or calls too often.

Mitigations:
- Block calling unless lead consent state permits the call purpose.
- Maintain suppression list.
- Stop immediately on opt-out.
- Enforce calling hours and maximum attempts.
- Record consent evidence and source.
- Stage 0 legal/compliance preflight is a launch blocker.

### Silent Or Abandoned Calls

Threat:
- Connection, latency, AMD, or orchestration failures create silent or abandoned calls.

Mitigations:
- Keep ConversationRelay loop minimal and latency-tested.
- Monitor call answer to first disclosure latency.
- Do not use aggressive predictive dialing.
- Use one call attempt per explicitly scheduled job.
- If automated systems fail after answer, play an approved brief fallback message or end politely where technically possible.
- Track failed, silent, abandoned, no-audio, and timeout indicators.
- Alert owner if thresholds are breached.

### Data Exposure Through Logs Or Transcripts

Threat:
- Logs or transcripts contain more personal data than needed.

Mitigations:
- Store redacted summaries by default.
- Mask phone and email in logs.
- Encrypt full transcript fields if enabled.
- Restrict database access.
- Define retention windows.
- Avoid logging model prompts with full lead personal data unless scrubbed.

### Backup Exposure

Threat:
- Database backups contain personal data and are accessed improperly.

Mitigations:
- Use encrypted backups.
- Restrict backup access to owner/operator.
- Test restore without exporting unnecessary production personal data.
- Define backup retention and deletion policy.

### Supply Chain And Deployment Risk

Threat:
- CI, dependency, or container compromise.

Mitigations:
- GitHub Actions runs tests before deployment.
- Dependencies are pinned when implementation begins.
- No secrets in pull requests or logs.
- Production deployment uses least-privilege keys.
- Docker images are rebuilt from reviewed source.
- Rollback uses a previously known-good image or Git tag.

## UK Consent And Automated-Call Disclosure Requirements

Planning requirements based on current official guidance:
- UK PECR rules are stricter for automated marketing calls than live calls.
- Automated marketing calls require consent; general marketing consent or live-call consent is not enough.
- The caller must identify who is calling.
- The call must display a caller number where required.
- The message must provide an address or Freephone/contact number where required.
- Automated calls must stop when consent is withdrawn.
- B2B calls are still covered by PECR rules.
- Repeated silent or abandoned calls may be persistent misuse under Ofcom policy.

Platform policy:
- The first spoken turn must identify the business and disclose that the recipient is speaking with an automated AI assistant where required.
- The first spoken turn must explain the purpose of the call.
- The system must honor opt-out and human-agent requests.
- The platform must store consent status, source, timestamp, and evidence URI before calling.
- Leads without sufficient consent are blocked from automated calling.
- Complaint, disputed consent, or data rights requests trigger human escalation.

Legal note:
- These documents are an engineering plan, not legal advice. Owner/legal review is required before production calling.

## Environment Variable Secret Handling

Required secret names should be defined during implementation, likely:
- `SUPABASE_URL`
- `SUPABASE_ANON_KEY`
- `SUPABASE_SERVICE_ROLE_KEY`
- `TWILIO_ACCOUNT_SID`
- `TWILIO_AUTH_TOKEN`
- `TWILIO_CALLER_ID`
- `GEMINI_API_KEY`
- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`
- `GOOGLE_REFRESH_TOKEN`
- `GOOGLE_CALENDAR_ID`
- `RESEND_API_KEY`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_ALLOWED_USER_IDS`
- `TELEGRAM_ALLOWED_CHAT_IDS`
- `CLOUDFLARE_TUNNEL_TOKEN`
- `APP_PUBLIC_BASE_URL`
- `ENVIRONMENT`

Rules:
- Real values never go in Git.
- `.env.example` contains keys with empty or placeholder values only.
- Production secrets are only present on the production VM or approved deployment secret store.
- Development and staging use separate provider accounts, phone numbers, calendars, and Telegram chats where practical.

## Security Logging And Alerting

Security events:
- Invalid webhook signature.
- Repeated duplicate event replay.
- Unauthorized Telegram command.
- Supabase auth failure spike.
- Environment readiness failure.
- Secret redaction failure.
- Unexpected provider object mismatch.
- Manual override or suppression-list change.

Alerts:
- Telegram alert to owner for operational incidents.
- Email fallback if Telegram delivery fails and Resend is healthy.
- Escalate to manual operator action when recovery jobs fail repeatedly.

## Source References

- ICO telephone marketing checklist: https://ico.org.uk/for-organisations/direct-marketing-and-privacy-and-electronic-communications/guide-to-pecr/electronic-and-telephone-marketing/telephone-marketing/
- ICO automated marketing calls: https://ico.org.uk/for-organisations/advice-for-small-organisations/direct-marketing-and-data-protection/marketing-and-data-protection-in-detail/
- ICO business-to-business marketing: https://ico.org.uk/for-organisations/direct-marketing-and-privacy-and-electronic-communications/business-to-business-marketing/
- Ofcom silent and abandoned calls: https://www.ofcom.org.uk/phones-and-broadband/unwanted-calls-and-messages/refresher-messaging-on-silent-and-abandoned-calls
- Ofcom persistent misuse policy: https://www.ofcom.org.uk/phones-and-broadband/unwanted-calls-and-messages/persistent_misuse
- Twilio ConversationRelay WebSocket signature validation: https://www.twilio.com/docs/voice/conversationrelay/onboarding
- Twilio webhook signatures: https://www.twilio.com/docs/usage/webhooks/getting-started-twilio-webhooks

