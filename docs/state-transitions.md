# State Transitions

## Purpose

This document defines the allowed state transitions for:

* leads;
* consent;
* call attempts;
* active call sessions;
* appointments;
* confirmation delivery;
* background jobs;
* provider events.

Application code, the deterministic worker, Hermes administration, and recovery scripts must follow these transitions.

Any exceptional correction to a terminal state must create an append-only `lead_events` record containing:

* previous state;
* new state;
* actor;
* reason;
* provider evidence where applicable;
* timestamp.

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

### Meaning

| Status          | Meaning                                                |
| --------------- | ------------------------------------------------------ |
| `new`           | Lead stored but not approved for automated calling     |
| `eligible`      | Consent and suppression checks permit calling          |
| `scheduled`     | A current or future call job exists                    |
| `calling`       | An outbound call attempt is active                     |
| `qualified`     | Qualification succeeded but booking is incomplete      |
| `not_qualified` | Qualification completed and lead did not meet criteria |
| `booked`        | A confirmed Google Calendar event exists               |
| `needs_human`   | Automated processing stopped for human follow-up       |
| `opted_out`     | Lead withdrew consent or requested no further calls    |
| `unreachable`   | Approved attempt limit was exhausted without contact   |
| `invalid_phone` | Phone number is invalid or permanently unreachable     |
| `failed`        | Permanent unrecoverable workflow failure               |
| `suppressed`    | Contact is blocked by the suppression list             |

### Callable Lead Statuses

```text
eligible
scheduled
calling
qualified
```

`new` is not callable.

### Allowed Transitions

| From                     | To              | Trigger                                                                |
| ------------------------ | --------------- | ---------------------------------------------------------------------- |
| none                     | `new`           | Lead stored but not currently callable                                 |
| none                     | `eligible`      | Lead accepted and callable                                             |
| none                     | `scheduled`     | Lead accepted and initial call job created atomically                  |
| `new`                    | `eligible`      | Consent or eligibility is approved later                               |
| `eligible`               | `scheduled`     | Call job created                                                       |
| `scheduled`              | `calling`       | Worker begins a call attempt                                           |
| `calling`                | `scheduled`     | Attempt ends with a retryable outcome and another attempt is scheduled |
| `calling`                | `qualified`     | Validated qualification succeeds                                       |
| `calling`                | `not_qualified` | Validated qualification fails                                          |
| `calling`                | `booked`        | Google Calendar event is confirmed                                     |
| `calling`                | `needs_human`   | Lead requests human help or automation cannot continue safely          |
| `calling`                | `opted_out`     | Lead withdraws consent or requests no further calls                    |
| `calling`                | `unreachable`   | Maximum safe attempts are exhausted                                    |
| `calling`                | `invalid_phone` | Provider confirms permanent number failure                             |
| `calling`                | `failed`        | Permanent unrecoverable platform failure                               |
| `qualified`              | `booked`        | Appointment is confirmed after qualification                           |
| `qualified`              | `needs_human`   | Booking or qualification requires manual follow-up                     |
| `scheduled`              | `opted_out`     | Opt-out arrives before the next call                                   |
| `eligible`               | `opted_out`     | Opt-out arrives before scheduling                                      |
| `new`                    | `opted_out`     | Opt-out is recorded before eligibility                                 |
| any non-suppressed state | `suppressed`    | Suppression record is created                                          |
| `scheduled`              | `invalid_phone` | Permanent phone issue confirmed before contact                         |
| `eligible`               | `invalid_phone` | Phone validation fails permanently                                     |

### Retry Rule

A retryable call failure does not move the lead to `failed`.

Examples of retryable outcomes:

```text
no_answer
busy
voicemail
temporary_provider_failure
temporary_network_failure
```

When another attempt is allowed:

```text
calling -> scheduled
```

The lead becomes `unreachable` only after the approved attempt limit is exhausted.

### Terminal Lead Statuses

```text
not_qualified
booked
needs_human
opted_out
unreachable
invalid_phone
failed
suppressed
```

`opted_out` and `suppressed` must never transition back to a callable status.

## Qualification Result

Allowed values:

```text
unknown
qualified
not_qualified
needs_human
insufficient_info
```

Allowed transitions:

| From                | To                  | Trigger                                               |
| ------------------- | ------------------- | ----------------------------------------------------- |
| null                | `unknown`           | Default                                               |
| `unknown`           | `qualified`         | Validated model decision                              |
| `unknown`           | `not_qualified`     | Validated model decision                              |
| `unknown`           | `needs_human`       | Escalation required                                   |
| `unknown`           | `insufficient_info` | Call ends before enough information is collected      |
| `insufficient_info` | `qualified`         | A later attempt gathers sufficient information        |
| `insufficient_info` | `not_qualified`     | A later attempt gathers sufficient information        |
| `qualified`         | `needs_human`       | Booking or compliance issue requires manual follow-up |

Gemini cannot update this state directly. FastAPI validates the structured decision first.

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

Callable consent states:

```text
specific_automated_call_consent
service_follow_up_approved
```

Allowed transitions:

| From           | To                                | Trigger                                   |
| -------------- | --------------------------------- | ----------------------------------------- |
| null           | `unknown`                         | Consent not supplied or not trusted       |
| `unknown`      | `specific_automated_call_consent` | Valid consent evidence accepted           |
| `unknown`      | `service_follow_up_approved`      | Approved service follow-up basis accepted |
| callable state | `withdrawn`                       | Lead withdraws consent                    |
| any            | `do_not_call`                     | Lead requests no further calls            |
| any            | `blocked`                         | Owner or compliance block                 |

When consent becomes `withdrawn`, `do_not_call`, or `blocked`:

* create or update suppression;
* cancel pending call jobs;
* prevent new call attempts;
* stop automated persuasion during an active call.

These states are terminal for automated calling unless a future audited legal correction process is explicitly approved.

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

### Allowed Transitions

| From          | To            | Trigger                                                 |
| ------------- | ------------- | ------------------------------------------------------- |
| none          | `scheduled`   | Attempt record created before contacting Twilio         |
| `scheduled`   | `dialing`     | Twilio outbound request accepted                        |
| `dialing`     | `ringing`     | Twilio reports ringing                                  |
| `dialing`     | `in_progress` | Call answered                                           |
| `ringing`     | `in_progress` | Call answered                                           |
| `in_progress` | `completed`   | Conversation ends with a normal business outcome        |
| `dialing`     | `no_answer`   | No answer                                               |
| `ringing`     | `no_answer`   | No answer                                               |
| `dialing`     | `busy`        | Busy result                                             |
| `ringing`     | `busy`        | Busy result                                             |
| `dialing`     | `voicemail`   | Voicemail detected                                      |
| `ringing`     | `voicemail`   | Voicemail detected                                      |
| `in_progress` | `voicemail`   | Answering machine detected after connection             |
| `scheduled`   | `cancelled`   | Calling paused, consent withdrawn, or suppression added |
| `dialing`     | `cancelled`   | Provider or operator cancels before answer              |
| `ringing`     | `cancelled`   | Provider or operator cancels before answer              |
| `scheduled`   | `failed`      | Permanent failure before dialing                        |
| `dialing`     | `failed`      | Provider failure                                        |
| `ringing`     | `failed`      | Provider failure                                        |
| `in_progress` | `failed`      | Runtime failure                                         |
| `in_progress` | `timed_out`   | Conversation exceeds approved timeout                   |
| `in_progress` | `escalated`   | Human follow-up required                                |

Terminal attempt statuses:

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

A retry creates a new `call_attempts` row with a new `attempt_number`.

A terminal attempt is not reset or reused.

A later Twilio callback must not overwrite a stronger validated business outcome without a reconciliation event.

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

| From      | To        | Trigger                                                        |
| --------- | --------- | -------------------------------------------------------------- |
| none      | `opening` | Verified ConversationRelay connection begins                   |
| `opening` | `active`  | Valid Twilio `setup` message is accepted                       |
| `active`  | `ending`  | Application sends the ConversationRelay `end` message          |
| `ending`  | `closed`  | Connect action callback or provider status confirms completion |
| `active`  | `closed`  | Caller hangs up or normal provider termination occurs          |
| `opening` | `failed`  | Session initialization fails                                   |
| `active`  | `failed`  | Unrecoverable runtime failure                                  |
| `opening` | `stale`   | No valid activity before timeout                               |
| `active`  | `stale`   | Connection disappears without final confirmation               |
| `stale`   | `closed`  | Worker reconciliation confirms normal termination              |
| `stale`   | `failed`  | Worker reconciliation confirms unrecoverable failure           |

A stale session must be reconciled before a new call attempt is scheduled.

## Appointment Status

Allowed values:

```text
proposed
booking_pending
booked
conflict
cancelled
failed
needs_human
```

The previous `confirmed` appointment state is removed.

Email delivery is tracked separately through `confirmation_status`.

### Allowed Transitions

| From              | To                | Trigger                                                        |
| ----------------- | ----------------- | -------------------------------------------------------------- |
| none              | `proposed`        | A genuine available slot is offered or selected                |
| `proposed`        | `booking_pending` | Lead confirms the slot and local idempotency record is created |
| `booking_pending` | `booked`          | Google event ID is stored                                      |
| `booking_pending` | `conflict`        | Slot is no longer available                                    |
| `booking_pending` | `failed`          | Booking fails after safe reconciliation                        |
| `booking_pending` | `needs_human`     | Provider state remains uncertain                               |
| `proposed`        | `cancelled`       | Lead declines the slot or call ends                            |
| `conflict`        | `proposed`        | A new valid slot is offered                                    |
| `booked`          | `cancelled`       | Approved cancellation workflow completes                       |
| `failed`          | `needs_human`     | Manual follow-up is required                                   |

Rules:

* `booked` requires `google_event_id`.
* `booking_pending` never means the lead has a confirmed booking.
* A timeout does not permit another create request until reconciliation is complete.
* Email failure does not change a booked appointment to failed.

## Confirmation Status

Allowed values:

```text
not_requested
pending
sent
failed
```

Allowed transitions:

| From            | To              | Trigger                                             |
| --------------- | --------------- | --------------------------------------------------- |
| null            | `not_requested` | Default                                             |
| `not_requested` | `pending`       | Appointment becomes booked and email job is created |
| `pending`       | `sent`          | Resend accepts the idempotent email request         |
| `pending`       | `failed`        | Retry budget is exhausted                           |
| `failed`        | `pending`       | Owner-approved or safe automated retry              |

A `sent` confirmation must not be sent again.

The Resend request must use:

```text
email:{appointment_id}:confirmation
```

as its `Idempotency-Key`.

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

### Meaning

| Status            | Meaning                                    |
| ----------------- | ------------------------------------------ |
| `pending`         | Ready or waiting for `run_after`           |
| `claimed`         | Reserved by a worker lease                 |
| `running`         | Worker is executing the job                |
| `retry_scheduled` | Retryable failure with a future retry time |
| `succeeded`       | Completed successfully                     |
| `failed`          | Non-retryable permanent failure            |
| `dead_lettered`   | Retry budget exhausted                     |
| `cancelled`       | Job became unsafe or unnecessary           |

### Allowed Transitions

| From              | To                | Trigger                                                      |
| ----------------- | ----------------- | ------------------------------------------------------------ |
| none              | `pending`         | Job created                                                  |
| `pending`         | `claimed`         | Worker claims due job                                        |
| `retry_scheduled` | `claimed`         | Worker claims a due retry                                    |
| `claimed`         | `running`         | Worker starts execution                                      |
| `running`         | `succeeded`       | Work completes                                               |
| `running`         | `retry_scheduled` | Retryable failure and attempts remain                        |
| `running`         | `failed`          | Permanent non-retryable failure                              |
| `running`         | `dead_lettered`   | Retry budget is exhausted                                    |
| `pending`         | `cancelled`       | Job is no longer safe or needed                              |
| `retry_scheduled` | `cancelled`       | Retry is no longer safe                                      |
| `claimed`         | `pending`         | Lease expires before execution begins                        |
| `running`         | `pending`         | Lease expires and reconciliation proves re-execution is safe |

There is no automatic:

```text
failed -> dead_lettered
```

transition.

`failed` and `dead_lettered` are separate terminal outcomes.

### Safe Claiming Rules

A worker may claim only jobs where:

```text
status IN ('pending', 'retry_scheduled')
run_after <= now()
```

Claiming must:

* use row locking such as `FOR UPDATE SKIP LOCKED`;
* set `claimed`, `locked_by`, `locked_at`, and `lease_expires_at` atomically;
* prevent two workers claiming the same job;
* recheck safety immediately before external side effects.

Before `initiate_call`, recheck:

* calling is not paused;
* consent is callable;
* no suppression exists;
* lead is not terminal;
* the attempt number is valid;
* no equivalent attempt already exists;
* current time is within approved calling hours.

Hermes does not directly claim production jobs.

## Provider Event Processing Status

Allowed values:

```text
received
processed
ignored
failed
```

`duplicate` is not a processing status.

### Allowed Transitions

| From       | To          | Trigger                        |
| ---------- | ----------- | ------------------------------ |
| none       | `received`  | New provider event inserted    |
| `received` | `processed` | Business processing succeeds   |
| `received` | `ignored`   | Valid event requires no action |
| `received` | `failed`    | Business processing fails      |

### Duplicate Delivery

When `(provider, idempotency_key)` already exists:

* do not insert a new event row;
* do not change the original processing status;
* update `last_seen_at`;
* increment `delivery_count`;
* return the provider's expected success response;
* perform no repeated business side effect.

A duplicate delivery never changes:

```text
processed -> duplicate
```

or:

```text
ignored -> duplicate
```

## Suppression Override

Whenever a matching suppression record is created:

1. set the lead to `suppressed` or `opted_out`;
2. cancel pending and retry-scheduled call jobs;
3. prevent new call attempts;
4. allow active calls only enough time to acknowledge the request and end safely;
5. preserve booking or complaint evidence already created;
6. record a `lead_events` entry.

Suppression takes priority over all non-terminal automation states.

## Recovery Rules

Recovery must reconcile external state before repeating actions.

Examples:

* Query Twilio before redialling an uncertain call.
* Search Google Calendar by extended property before recreating an event.
* Check confirmation status and Resend idempotency before resending email.
* Release a worker lease only after its expiry.
* Preserve opt-out, suppression, booking, and human-escalation outcomes during recovery.

Recovery must never convert uncertainty into assumed success.
