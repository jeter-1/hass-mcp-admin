# HA MCP Engineering Server 2.1.1-beta.3

Status: standalone 2.1A Home Assistant restart-verification correction.
Deployment acceptance is outside this source change.

## Corrected restart evidence

Home Assistant restart verification now accumulates authoritative evidence
instead of replacing it on each readback attempt. After an approved dispatch
has been persisted, a direct Home Assistant Core connection timeout,
unavailability, or Supervisor proxy 502/503/504 response records a bounded
outage observation with its earliest and latest timestamps, observation count,
failure category, and evidence source. New outage evidence is accepted only
from the immutable 180-second observation interval calculated from the original
persisted dispatch timestamp. The initial active-probe budget remains
independently bounded to 15 attempts at one-second intervals, approximately 15
seconds. Core may remain reachable throughout that initial loop and a direct
Core outage observed by later reconciliation at `T+60s` still qualifies.
Reconciliation cannot recalculate or extend the 180-second deadline, so a later
unrelated Core outage cannot verify an old restart plan. Planning failures,
approval failures, pre-dispatch validation, and unrelated upstream-provider
failures do not establish restart evidence.

Later readback adds reconnection, unchanged Home Assistant identity,
post-restart configuration validation, Engineering runtime and tool-catalog
restoration, governance and audit health, exact upstream admission, dependency
recovery, and zero-fallback evidence without erasing the outage. A terminal
verified Home Assistant restart requires all of the existing recovery checks
plus:

- confirmed restart dispatch;
- at least one qualified post-dispatch Core outage observation; and
- a later successful Core identity read, recorded explicitly as
  `home_assistant_reconnected=true` and `reconnected_at=<timestamp>`.

Provider acknowledgement or current availability alone is not restart proof.
No optional entity such as `sensor.uptime` is queried or required.
An incomplete historical `outage_observed` flag is not authoritative: the
complete timestamps, count, source, category, consumed approval, dispatch
record, and observation deadline must validate as one unit.

## Recovery and safety

Initial, repeated-apply, startup, and periodic verification use the same
storage-backed merge. Every recovery pass is readback-only. Once dispatch is
persisted, no pass can send another restart, recreate approval, or change the
approved target. Multiple unavailable observations advance only the latest
timestamp and count while preserving the earliest observation.
When a qualified outage was recorded before the deadline, readback-only
recovery may complete after that deadline or normal plan expiry. When no
qualified outage was recorded, the plan reports
`restart_evidence_window_expired` and future outages remain ineligible.
The immutable deadline is exactly validated as original dispatch time plus 180
seconds; missing, malformed, shortened, widened, or recomputed deadlines fail
closed. Process restart, startup reconciliation, and repeated apply preserve
the original value and cannot reopen the window. Once qualified outage
evidence exists, recovery may take indefinitely and does not require another
outage or dispatch.

Historical records remain readable and retain their plan hashes. An older plan
without authoritative persisted Core-outage evidence is not inferred to have
restarted from elapsed time, provider response, or current availability; it
remains pending and requires manual review.

## Unchanged boundaries

This release adds no MCP tool or public schema. The catalog remains 45
Engineering tools plus 26 delegated reads, or 71 total. Engineering
self-restart `process_identity`, upstream add-on `upstream_readmission`,
ordinary add-on `provider_acknowledgement`, exact add-on identity, external
approval, plan hashing, stable v1.1.2, reviewed upstream contracts, and zero
fallback are unchanged.
