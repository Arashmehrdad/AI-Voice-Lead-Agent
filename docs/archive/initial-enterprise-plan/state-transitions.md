# State Transitions

## Scope

This document defines the exact allowed state transitions for Stage 1. Application code, database updates, worker processing, and recovery scripts must follow these transitions unless a later migration explicitly updates this document.

Terminal state changes require an append-only `lead_events` record explaining the reconciliation, provider event, or manual override.

## Lead Status

Allowed values:

```text
new
eligible
scheduled
calling
qualified
not_qualified
booked
needs_human
opted_out
unreachable
invalid_phone
failed
suppressed
```

Allowed transitions:

| From | To | Actor | Reason |
| --- | --- | --- | --- |
| none | new | FastAPI | Lead accepted but not yet callable or not yet scheduled |
| none | eligible | FastAPI | Lead accepted and consent/suppression checks allow calling |
| none | scheduled | FastAPI | Lead accepted, callable, and call job created in the same transaction |
| new | eligible | FastAPI or worker | Consent/suppression check later confirms lead is callable |
| eligible | scheduled | FastAPI or worker | Call job created |
| scheduled | calling | worker | Call attempt is being initiated |
| calling | qualified | FastAPI voice gateway | Lead qualifies but no booking yet |
| calling | not_qualified | FastAPI voice gateway | Lead does not meet criteria |
| calling | booked | FastAPI voice gateway | Appointment booked with Google event ID |
| calling | needs_human | FastAPI voice gateway or worker | Human escalation required |
| calling | unreachable | worker | Maximum no-answer, busy, or voicemail attempts exhausted |
| calling | failed | worker | Permanent or unrecoverable call failure |
| calling | opted_out | FastAPI voice gateway | Lead withdraws consent or requests no more calls |
| new | suppressed | FastAPI or worker | Suppression record exists |
| eligible | suppressed | FastAPI or worker | Suppression record added |
| scheduled | suppressed | FastAPI or worker | Suppression record added before calling |
| calling | suppressed | FastAPI voice gateway or worker | Suppression record added during active workflow |
| qualified | booked | FastAPI voice gateway | Appointment booked after qualification |
| qualified | needs_human | FastAPI voice gateway or worker | Booking or qualification requires owner follow-up |
| scheduled | opted_out | FastAPI or worker | Opt-out received before call |
| eligible | opted_out | FastAPI or worker | Opt-out received before scheduling |
| new | opted_out | FastAPI or worker | Opt-out received before eligibility |
| scheduled | failed | worker | Job or provider failure prevents call |
| eligible | failed | worker | Permanent validation failure |
| new | invalid_phone | FastAPI or worker | Phone validation fails |
| eligible | invalid_phone | worker | Provider confirms permanent phone issue |
| scheduled | invalid_phone | worker | Provider confirms permanent phone issue |
| calling | invalid_phone | worker | Provider confirms permanent phone issue |

Blocked transitions:

- Any terminal status back to `calling` without creating a new call attempt and a `lead_events` reconciliation record.
- `opted_out` or `suppressed` to any callable status.
- `booked` to another terminal status unless manual reconciliation is explicitly audited.

Callable statuses:

```text
new
eligible
scheduled
calling
qualified
```

Suppression and opt-out override all callable statuses.

## Lead Qualification Result

Allowed values:

```text
unknown
qualified
not_qualified
needs_human
insufficient_info
```

Allowed transitions:

| From | To | Actor | Reason |
| --- | --- | --- | --- |
| null | unknown | FastAPI | Default |
| unknown | qualified | FastAPI voice gateway | Gemini decision validated by application code |
| unknown | not_qualified | FastAPI voice gateway | Gemini decision validated by application code |
| unknown | needs_human | FastAPI voice gateway | Escalation required |
| unknown | insufficient_info | FastAPI voice gateway | Call ended before enough information |
| insufficient_info | qualified | FastAPI voice gateway | Later call gathers enough information |
| insufficient_info | not_qualified | FastAPI voice gateway | Later call gathers enough information |
| qualified | needs_human | FastAPI voice gateway or worker | Booking or compliance issue requires owner follow-up |

## Consent Status

Allowed values:

```text
unknown
specific_automated_call_consent
service_follow_up_approved
withdrawn
do_not_call
blocked
```

Callable consent statuses:

```text
specific_automated_call_consent
service_follow_up_approved
```

Allowed transitions:

| From | To | Actor | Reason |
| --- | --- | --- | --- |
| null | unknown | FastAPI | Consent not provided or not trusted |
| unknown | specific_automated_call_consent | FastAPI | Consent evidence accepted |
| unknown | service_follow_up_approved | FastAPI | Service follow-up basis accepted |
| specific_automated_call_consent | withdrawn | FastAPI voice gateway, Hermes, or approved script | Lead withdraws consent |
| service_follow_up_approved | withdrawn | FastAPI voice gateway, Hermes, or approved script | Lead withdraws consent |
| any | do_not_call | FastAPI voice gateway, Hermes, or approved script | Lead requests no further calls |
| any | blocked | owner or approved script | Compliance or safety block |

`withdrawn`, `do_not_call`, and `blocked` are terminal for automated calling unless owner/legal approval creates a future audited correction path.

## Call Attempt Status

Allowed values:

```text
scheduled
dialing
ringing
in_progress
completed
no_answer
busy
voicemail
failed
timed_out
cancelled
escalated
```

Allowed transitions:

| From | To | Actor | Reason |
| --- | --- | --- | --- |
| none | scheduled | worker | Attempt record created before provider request |
| scheduled | dialing | worker | Twilio outbound call requested |
| dialing | ringing | Twilio webhook | Twilio reports ringing |
| dialing | in_progress | Twilio webhook or WebSocket | Call answered |
| ringing | in_progress | Twilio webhook or WebSocket | Call answered |
| in_progress | completed | Twilio webhook or voice gateway | Call completes with business outcome |
| dialing | no_answer | Twilio webhook or worker reconciliation | No answer |
| ringing | no_answer | Twilio webhook or worker reconciliation | No answer |
| dialing | busy | Twilio webhook or worker reconciliation | Busy |
| ringing | busy | Twilio webhook or worker reconciliation | Busy |
| dialing | voicemail | Twilio webhook, AMD, or worker inference | Voicemail |
| ringing | voicemail | Twilio webhook, AMD, or worker inference | Voicemail |
| in_progress | voicemail | voice gateway or worker inference | Answering machine or voicemail behavior detected |
| scheduled | cancelled | worker | Calling paused, suppressed, or no longer safe before dialing |
| dialing | cancelled | worker or Twilio webhook | Call cancelled before answer |
| ringing | cancelled | worker or Twilio webhook | Call cancelled before answer |
| in_progress | timed_out | voice gateway or worker | Session exceeded timeout |
| scheduled | failed | worker | Provider request failed before dialing |
| dialing | failed | Twilio webhook or worker | Provider failure |
| ringing | failed | Twilio webhook or worker | Provider failure |
| in_progress | failed | voice gateway or worker | Runtime failure |
| in_progress | escalated | voice gateway | Human escalation |

Terminal values:

```text
completed
no_answer
busy
voicemail
failed
timed_out
cancelled
escalated
```

Terminal status overwrite rule:

Terminal statuses are not overwritten except through explicit reconciliation with a `lead_events` record containing previous status, new status, reason, actor, and provider evidence.

## Call Session Status

Allowed values:

```text
opening
active
ending
closed
stale
failed
```

Allowed transitions:

| From | To | Actor | Reason |
| --- | --- | --- | --- |
| none | opening | FastAPI voice gateway | Verified WebSocket handshake starts |
| opening | active | FastAPI voice gateway | Start event received or session ready |
| active | ending | FastAPI voice gateway | End requested or terminal decision reached |
| ending | closed | FastAPI voice gateway or worker | Stop event received or session finalized |
| active | closed | FastAPI voice gateway | Normal provider stop |
| opening | failed | FastAPI voice gateway | Session setup fails after accepted handshake |
| active | failed | FastAPI voice gateway | Runtime failure |
| opening | stale | worker | No activity before lease/stale threshold |
| active | stale | worker | No activity before lease/stale threshold |
| stale | closed | worker | Reconciled as completed or ended |
| stale | failed | worker | Reconciled as unrecoverable |

## Appointment Status

Allowed values:

```text
proposed
booking_pending
booked
confirmed
conflict
cancelled
failed
needs_human
```

Allowed transitions:

| From | To | Actor | Reason |
| --- | --- | --- | --- |
| none | proposed | FastAPI voice gateway | Slot presented or selected before external booking |
| proposed | booking_pending | FastAPI voice gateway | Lead confirms slot and local idempotency record created |
| booking_pending | booked | FastAPI voice gateway | Google event ID stored |
| booked | confirmed | worker | Confirmation email sent or confirmation completed |
| booking_pending | conflict | FastAPI voice gateway or worker | Calendar conflict |
| booking_pending | failed | FastAPI voice gateway or worker | Calendar booking failed after reconciliation |
| booking_pending | needs_human | FastAPI voice gateway or worker | Calendar uncertainty needs owner |
| proposed | cancelled | FastAPI voice gateway | Lead declines or call ends |
| booked | cancelled | owner or future approved cancellation flow | Appointment cancelled |
| conflict | proposed | FastAPI voice gateway | New slot offered in same call |
| failed | needs_human | worker | Failure requires owner follow-up |

Rule:

`booked` and `confirmed` require `google_event_id`.

## Appointment Confirmation Status

Allowed values:

```text
not_requested
pending
sent
failed
```

Allowed transitions:

| From | To | Actor | Reason |
| --- | --- | --- | --- |
| null | not_requested | FastAPI | Default |
| not_requested | pending | FastAPI or worker | Appointment booked and confirmation job created |
| pending | sent | worker | Resend send succeeds |
| pending | failed | worker | Resend send fails after retry budget |
| failed | pending | worker or owner-approved retry | Retry confirmation |

## Job Status

Allowed values:

```text
pending
claimed
running
retry_scheduled
succeeded
failed
dead_lettered
cancelled
```

Allowed transitions:

| From | To | Actor | Reason |
| --- | --- | --- | --- |
| none | pending | FastAPI or worker | Job created |
| pending | claimed | worker | Worker claims due job with row lock and lease |
| retry_scheduled | claimed | worker | Worker claims due retry with row lock and lease |
| claimed | running | worker | Worker starts execution |
| running | succeeded | worker | Job completed |
| running | retry_scheduled | worker | Retryable failure and attempts remain |
| retry_scheduled | pending | worker or scheduler | Retry becomes due |
| running | failed | worker | Non-retryable failure |
| failed | dead_lettered | worker | Failure recorded for manual inspection |
| running | dead_lettered | worker | Retry budget exhausted |
| pending | cancelled | FastAPI or worker | No longer safe to run |
| retry_scheduled | cancelled | FastAPI or worker | No longer safe to retry |
| claimed | pending | worker recovery | Lease expired before running |
| running | pending | worker recovery | Lease expired and job is safe to retry |

Safe job-claiming requirements:

- Claim only jobs where `status in ('pending', 'retry_scheduled')`.
- Claim only jobs where `run_after <= now()`.
- Use row locking such as `FOR UPDATE SKIP LOCKED`.
- Set `status = 'claimed'`, `locked_by`, `locked_at`, and `lease_expires_at` in the same transaction.
- A worker may move its own claimed job to `running`.
- Recovery may release expired leases when `lease_expires_at < now()`.
- Before executing `initiate_call`, recheck `business_settings.calling_paused`, lead consent, suppression list, lead terminal state, and attempt count.
- Hermes does not directly claim production jobs.

## Provider Event Processing Status

Allowed values:

```text
received
processed
duplicate
ignored
failed
```

Allowed transitions:

| From | To | Actor | Reason |
| --- | --- | --- | --- |
| none | received | FastAPI | New event inserted before business processing |
| received | processed | FastAPI | Business processing completed |
| received | ignored | FastAPI | Valid event has no required action |
| received | failed | FastAPI | Processing failed after dedupe insert |
| processed | duplicate | FastAPI | Later duplicate receipt may update duplicate marker if desired |
| ignored | duplicate | FastAPI | Later duplicate receipt may update duplicate marker if desired |

Duplicate handling:

- If `(provider, idempotency_key)` already exists, do not repeat side effects.
- Update `last_seen_at`.
- Return a successful acknowledgement where the provider expects one.

