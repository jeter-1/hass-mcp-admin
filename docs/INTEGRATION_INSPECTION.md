# Bounded Alarmo configuration inspection

This source-only increment implements `get_integration_inspection` behind the
existing Engineering endpoint. It has not been released, deployed or accepted
against the household installation. This branch reconciles the reviewed inspector with beta.10, preserving its
verification, health, logbook and lock-recovery changes. Version metadata remains
beta.10; no inspector release is prepared.

The tool takes an exact `alarm_control_panel` entity, `integration="alarmo"`,
`limit=25` (strict integer, 1–50), and an optional opaque cursor. Extra arguments
are rejected before SDK validation. The new source catalog has 56 static tools
and 25 delegated reads (81 total under healthy upstream admission). All 55
previous static descriptors and the 25 delegated contracts remain unchanged.

## Identity and authority

The code-owned adapter reviews official Alarmo 1.10.19 at
`169e134f4b70d87aae36ba54a72a398ecc960afd`. The manifest must match that profile;
reported version is not proof of installed file integrity or absence of local
modifications. P0b remains a later installed-acceptance requirement. No installed
compatibility is inferred from synthetic fixtures.

Core authority requires both `core.basic_websocket_read` and the additive
`core.integration_inspection_metadata_read`. The new profile is semantic and
read-only. Production observation opts in to its structural evidence using the
existing reviewed probe/transport; it sends no additional startup probes. The
original 19 declarations/fingerprints and compiled exact authority are unchanged.
Existing signed Core 2026.9.3 and 2026.9.4 data can admit 19 of the 20 known references and
correctly withholds this tool. A structural observation alone cannot admit it.

The three Core metadata implementations and their three reviewed test files are
byte-identical between Core 2026.9.3 and 2026.9.4 at
`9212531f40a0b7b23229a90d688dd79d9dfccff4`. This establishes source continuity for
those reads, not assembled Core/Alarmo execution or installed admission. The
source comparison and hashes are retained in the source-review record.

The [unsigned applicability record](evidence/alarmo-inspection-core-applicability.json)
does not authorize dispatch. R3 remains open: the current registry preparer
refuses replacement of an existing release's capability list during renewal.
A separately scoped correction, review and owner-authorized signing/activation
are required. No trust data, registry mechanism or preparation script changes
are included here.

## Collection and interpretation

Nine sequential application reads maximum: manifest, domain-filtered config
entries, Alarmo entities, areas, sensors, selected general configuration, sensor
groups, exact-ID registry enrichment, and final manifest. The five Alarmo
callbacks collect integration-wide collections; Engineering projects the
selected area or master. Registry IDs are derived internally (513 maximum).
No generic command, options flow, diagnostic dump, filesystem, user, action,
readiness or subscription interface is exposed. `ha_get_integration` remains
mixed/requires-wrapper and unexposed; no ha-mcp feature call or fallback occurs.

The response distinguishes complete selected fields, partial evidence,
unavailable sources, empty membership, omitted records and stale identity.
It reports per-area/per-mode configured times. Explicit null, zero, false,
empty collections and missing fields remain distinct. Sensor-specific timings
are excluded; no aggregate or effective delay is inferred.

Missing or malformed final identity evidence preserves useful facts as partial
with an unavailable manifest bracket; only a validated conflicting identity
establishes drift. A master entity contradicting an observed disabled master
flag remains visible with a scope gap. A group member whose area cannot be joined
does not prove an outside-area member: scope is unknown unless another known
member proves it, and the unresolved join still makes coverage partial. A report
with only identity or unavailable placeholders returns a fixed unavailable
failure; observed false/zero/null facts and exact empty membership remain useful.

Static mode eligibility uses three-valued logic:
`enabled AND area_mode.enabled AND (mode IN configured_modes OR always_on)`.
This is configured eligibility, not proof of arming, current protection,
readiness, bypass behavior or the actual runtime watcher set. A false conjunct
can prove exclusion despite another unknown; other unknowns remain indeterminate.

Every fact reference resolves to an included safe excerpt with original field
names and JSON Pointer hierarchy. Source fingerprints hash only positively
selected sanitized projections. Names, codes/hashes, code lengths, users, MQTT,
action payloads, templates and registry options are excluded before hashing,
caching or audit, even when global redaction is disabled. Known-secret filtering
also removes matching identifiers without reconstructing them from another source.

## Bounds and continuation

The fresh request is limited to 30 seconds including parsing/cleanup, normalization,
snapshot fitting and first-page packing, five seconds per
command, one second socket close, and two concurrent collections without a queue.
Transport accepts only the expected three-frame exchange (stricter than the
16-frame ceiling), 64 KiB auth frames, 2 MiB results and 8 MiB aggregate budget.
It rejects duplicate JSON keys, nonfinite numbers, unsolicited frames and
redirects, with no automatic retry. Auth frames are reserved before selecting
the remaining result budget. Oversized messages are refused before JSON decoding;
these are application-message bounds, not a claim to control a peer's wire traffic.

Projection shares 50,000 examined structural items and depth 16 across sources;
only recognized fields are visited, including malformed/rejected items. Opaque
excluded subtrees are never walked. Caps are 32 areas, five modes each, 512 sensors,
128 groups, 2,048 member edges, 513 registry IDs, 128-character identifiers and
512-character pointers. Sixteen fixed-code gaps are retained with omissions.

Snapshots contain sanitized data only, maximum 2 MiB each, eight snapshots/16 MiB
total, five-minute TTL and LRU eviction. Opaque tokens bind authenticated caller,
target, integration, limit, offset, fingerprint and Core generation. Continuation
rechecks current authority and performs zero provider reads. Expiration, eviction,
revocation or a mismatched cursor never causes implicit recollection.
Observed read-authority/authentication loss purges this inspector's cached
snapshots and tokens and advances a private invalidation generation. Pending
captures bind that generation before collection and recheck it at cooperative
fitting boundaries, before insertion and before publication. A capture predating
the loss cannot repopulate the cache; its refusal also cannot purge a later
authorized capture. An observed Core authority generation or version replacement
also advances the private generation once, before issuing a new binding. The
last observed authority is tracked independently of cached snapshots, so even
a pending-only old capture cannot invalidate a newer valid continuation. This
is request-time invalidation, not a background
revocation observer or a new provider read.

Whole rows and their evidence are packed within a 49,152-byte working page and
60,000-byte complete response ceiling, further restricted by the configured
response limit. Rows and evidence are sized once, with cooperative fitting
batches and bounded metadata-width refinement; fitting does not repeatedly
serialize the remaining report. Output omissions are determined before freezing
the snapshot. Every page and replay retains the same assessment, omissions and
positive membership counts, including unknown omission totals. Smaller rows
following an oversized row remain eligible. Mandatory
metadata that cannot fit produces a fixed failure. All captures are non-atomic;
stable manifest/fingerprint does not establish unchanged configuration.

## Validation and stage

See [acceptance](ALARMO_INSPECTION_ACCEPTANCE.md),
[source references](evidence/alarmo-inspection-source-review.json), and
[ADR-024](architecture/ADR-024-SANITIZED-INTEGRATION-INSPECTION.md).
The source-only authorization does not include delivery, release, activation,
deployment or live tests. Completed beta.10 acceptance and existing operational holds remain
separate. Removing this unactivated increment requires a source revert; no
production record format or installed configuration has changed.
