# HA MCP Engineering Server 2.1.1-beta.3

Status: standalone 2.1A Home Assistant restart-verification correction.
Deployment acceptance is outside this source change.

## Corrected restart evidence

Home Assistant restart verification now accumulates authoritative evidence
instead of replacing it on each readback attempt. After an approved dispatch
has been persisted, a direct Home Assistant Core connection timeout,
unavailability, or Supervisor proxy 502/503/504 response records a bounded
outage observation with its earliest and latest timestamps, observation count,
and evidence source. Planning failures, approval failures, pre-dispatch
validation, and unrelated upstream-provider failures do not establish restart
evidence.

Later readback adds reconnection, unchanged Home Assistant identity,
post-restart configuration validation, Engineering runtime and tool-catalog
restoration, governance and audit health, exact upstream admission, dependency
recovery, and zero-fallback evidence without erasing the outage. A terminal
verified Home Assistant restart requires all of the existing recovery checks
plus:

- confirmed restart dispatch;
- at least one qualified post-dispatch Core outage observation; and
- a later successful Core reconnection.

Provider acknowledgement or current availability alone is not restart proof.
No optional entity such as `sensor.uptime` is queried or required.

## Recovery and safety

Initial, repeated-apply, startup, and periodic verification use the same
storage-backed merge. Every recovery pass is readback-only. Once dispatch is
persisted, no pass can send another restart, recreate approval, or change the
approved target. Multiple unavailable observations advance only the latest
timestamp and count while preserving the earliest observation.

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
