# Core reconciliation failure attribution

Each reconciliation attempt that stops at an early runtime fence now writes one
`home_assistant_core_authority_reconciliation_failed` event through the configured
existing audit sink. Wakeup/coalescing hints are not attempts and emit no such event.
The result is `withheld`; no decision generation is invented or published.

The bounded summary uses the existing `ha-core-authority-audit-v1` model and fixed
trigger, phase and reason codes. It covers unavailable lifecycle monitoring,
registry movement during collection/selection, connection-generation movement and
stale monitor attachment. It retains only an allowlisted observation reason and
an observed validated Core version, if available. Unknown reason/trigger values
become `other`. Provider exceptions, endpoints, sessions and credentials are never
copied into this event. In particular, `core_observation_unavailable` cannot
recover an HTTP status or timeout subtype that the collector discarded.

Successful retry audit events now identify `failure_retry` explicitly, including
an idempotent retry; ordinary idempotent periodic checks remain quiet. The
successful reconciliation event, public health projection and tool descriptors
are otherwise unchanged. Events use the existing sink outside the runtime state
lock. A refused or throwing sink increments `audit_write_failures`; it does not
hide the failed attempt, admit authority or queue another probe. An unavailable
sink means a durable reason could not be recorded, not a successful audit.

The 300-second failure pacing, signed authority, retirement, lease/stale-plan
checks and catalog restoration are unchanged. Offline failure and retry controls
exercise production collection with synthetic peers and a controlled clock.
These diagnostics do not prove that health work caused the historical October
disconnect and do not reconstruct missing historical failure details.
