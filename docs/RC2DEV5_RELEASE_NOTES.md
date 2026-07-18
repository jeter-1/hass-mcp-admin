# RC2dev5 live-acceptance corrections

Version `2.0.0-rc2-dev5` is a focused corrective release based directly on
verified RC2dev4 merge and image source
`4c4b673f2234924e3dbaeae46bcd224e179603d2`. It adds no Engineering tools,
changes no public tool input schema, and requires no governance-store migration.
Stable add-on version `1.1.2` is unchanged.

## Findings corrected

- The dependency index now has a 600-second soft TTL and a 3600-second hard
  TTL. Soft-expired evidence is returned immediately with its exact age while
  one shared background refresh runs. Evidence beyond the hard TTL and evidence
  invalidated by a configuration mutation are never presented as authoritative.
- Startup prewarming is enabled for the beta/RC add-on after a 45-second delay.
  It first verifies Home Assistant connectivity, uses the same single-flight
  build path, never blocks server startup, and retries failures no sooner than
  300 seconds.
- A sanitized missing-dashboard envelope derived from reviewed `ha-mcp` 7.13.0
  source commit `f4eb53621ccb814cb7123d2811e06eda3577129c` maps to the
  non-retryable `dashboard_not_found` domain outcome. It no longer marks the
  upstream provider unreachable or failed.
- Expected entity, automation, dashboard, and change-plan misses use explicit
  domain-outcome source-coverage categories instead of provider-failure labels.
- Reliability summary mode returns deduplicated root-cause groups with bounded
  representative paths, plus bounded intentional-use notes. Full path evidence
  remains available in standard and evidence modes.
- Plan responses identify `approval_lifecycle` as authoritative and mark
  `status` as legacy compatibility data. Persisted RC2dev3/RC2dev4 records are
  not rewritten.
- Relayed Home Assistant log sanitization treats webhook identifiers as
  sensitive identifiers. It masks webhook paths and narrowly recognized prose
  without hiding ordinary Git SHAs or unrelated hashes.
- Promotion now configures deterministic Git identity, records explicit phases,
  verifies immutable version/SHA images and platform labels, and always reports
  partial success truthfully.

## Dependency freshness contract

The named build states are `unbuilt`, `building`, `valid`,
`stale_refreshing`, `stale_available`, `hard_expired`, `invalidated`,
`failed_without_index`, and `refresh_failed_stale_available`. Freshness is
reported independently as `current`, `stale_within_hard_ttl`, `hard_expired`,
`invalidated`, or `unavailable`.

A soft-expired request receives the previous successful generation immediately,
with `evidence_stale: true`, `evidence_age_seconds`, and
`maximum_evidence_age_seconds`. One background refresh atomically publishes a
new generation. A failed refresh preserves the last good generation only until
its hard expiry. Configuration invalidation is distinct from age expiration and
does not permit stale evidence reuse. Generation- or fingerprint-bound cursors
become `stale_cursor` after successful replacement and never trigger a build.

## Configuration defaults

| Option | Default | Contract |
|---|---:|---|
| `prewarm_enabled` | `true` | Schedule nonblocking prewarm after connectivity succeeds. |
| `prewarm_startup_delay_seconds` | `45` | Delay startup work so foundation tools become available first. |
| `prewarm_retry_delay_seconds` | `300` | Minimum delay after a failed attempt; prevents retry storms. |
| `dependency_index_soft_ttl_seconds` | `600` | Begin stale-while-refresh behavior. |
| `dependency_index_hard_ttl_seconds` | `3600` | Maximum age at which stale evidence may be served. |

Existing installations without these keys receive the defaults. The legacy
`dependency_index_prewarm` value remains readable as compatibility data, but
does not override the new safe default; `prewarm_enabled` is the sole control.
Delays cannot be negative,
soft TTL must be positive, retry delay must be at least 300 seconds, and hard
TTL must be greater than soft TTL; invalid combinations fail startup validation
with bounded configuration errors.

## Metrics and observability

Evidence freshness, provider health, index build state, and cursor validity are
separate dimensions. Expected domain outcomes and rejected validation,
authorization, or cursor requests do not increment provider operational
failures or terminal analysis failures. Index profiles label cumulative queue
wait as per-request accumulated effort and separately expose maximum and average
request wait plus build wall-clock time.

Dashboard errors remain distinct: `dashboard_not_found` is a healthy domain
outcome; authentication, connection, timeout, protocol, invalid-response, and
upstream-internal errors remain operational failures.

## Release-control note

RC2dev4 image publication and anonymous verification completed at digest
`sha256:828c8ef3e78731d8910cbc2c27429221bf10b9c05700e335457fb8c767d05963`,
but annotated-tag creation failed because the workflow Git identity was empty.
`v2.0.0-rc2-dev4` therefore does not exist. This is a known release-control gap;
RC2dev5 fixes future promotion and does not create or backfill the missing tag.

The RC2dev5 workflow records validation, build, push, anonymous verification,
digest capture, tag creation, tag verification, and final summary as explicit
phases. A failure after image publication reports the existing digest and the
failed phase. Recovery must verify that the immutable artifact has the expected
version and source revision before completing the tag; it must not rebuild or
overwrite a different artifact.

## Coverage and deferred work

The index continues to cover automations and locally readable blueprint roles.
Script, scene, group, template, dashboard, and static-YAML dependency ingestion
remain deferred. The live Raspberry Pi provisional cold-build target remains
under 25 seconds, or off the foreground path. RC2dev5 addresses the foreground
soft-expiry stall but does not promise that the underlying 82-automation rebuild
itself is below 25 seconds.
