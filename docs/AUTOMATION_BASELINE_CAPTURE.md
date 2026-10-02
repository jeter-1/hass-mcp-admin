# Native loaded-automation baseline capture

This owner-approved source increment adds `capture_automation_baseline(limit=25,
cursor="")`. Release preparation, signed applicability and installed acceptance
remain separate. It consumes the unchanged
[offline baseline contract](AUTOMATION_AUDIT_BASELINE.md).

## Scope and identity

`loaded_automation_entities-v1` means all administrator-visible automation states
at the initial REST inventory, including operational `off`. It does not mean all
stored YAML, packages or registry-disabled automations. Registry-only discoveries
are supplemental partial evidence; the registry can silently omit serialization
failures. `ADDED` and `REMOVED` in a comparison mean membership in this named loaded
scope, not creation or deletion from disk. Full stored inventory remains open.

A canonical configuration ID must agree between the state `attributes.id` and the
matching automation-platform registry `unique_id`, without a duplicate ID or
disabled registry entry. Unmapped objects are omitted with a counted limitation,
not assigned synthetic canonical IDs. REST configuration failures remain explicit
unreadable records. There is no fallback to loaded/expanded WebSocket configuration,
so package-only configurations may remain unknown. Referenced blueprint bodies are
not part of the returned configuration's fingerprint.

`supervised_core_registry_lineage-v1` hashes a domain-separated tuple of the unique
persisted Hass.io Core device-registry ID and its uniquely loaded Hass.io
config-entry ID. Only the fingerprint is exported. This is registry-lineage
assurance, not a Core UUID, host identity or cross-clone discriminator. Ordinary
restart/update preserves it while those records survive. It changes when either
persisted device or config-entry ID changes. Core can restore a removed device
from its tombstone with the same ID; that restoration alone does not change
lineage. Recreating the config entry changes its ID even if Core restores the
device ID. Restored clones may share the lineage. Cross-clone comparisons are outside
this assurance. The owner approved that limitation for this first increment.

## Collection, authority and privacy

A capture uses one frozen configured origin/token pair and one authenticated
WebSocket. Both capture fences read, in order:

1. `auth/current_user`: retain only strict `is_admin=true`.
2. `config_entries/get` with fixed `domain=hassio`: retain only ID/domain/loaded state.
3. `config/device_registry/list`: select the unique `("hassio", "core")` anchor.
4. `GET /api/states`: inventory loaded automations, retaining entity/configuration
   IDs and operational on/off/unknown/unavailable state only.
5. `config/entity_registry/list`: retain only automation mapping fields.

The REST state reader is deliberate: exact Core's WebSocket `get_states` may
silently omit unserializable states. REST fails the response instead. An unreadable
initial inventory refuses capture. The same credential and admission bind all reads;
principal evidence from an unrelated connector or account is never substituted.

Between fences, four workers read `GET /api/config/automation/config/{verified_id}`.
No caller can supply an endpoint, command, path, identity, provider or credential.
The full returned JSON data object is hashed ephemerally under
`automation-audit-config-json-python-v1`. Raw configurations are never retained in
snapshots, exports, audit or diagnostics. No sanitized/truncated projection is
substituted for the full hash. The recomputed-hashes assertion refers only to
successfully readable records; unreadable/omitted records have no digest.

Before/after each transport operation, request-scoped Core authority must remain
valid at the original generation/version. The new semantic capability is
`core.automation_baseline_metadata_read`, in addition to the existing REST,
WebSocket, non-device registry, direct-device registry and configuration-read
profiles. Existing profile fingerprints and signed entries are unchanged. The
readiness builder reuses existing transport evidence; it does not issue new
readmission probes or admit itself. Old signed entries withhold this capability.

Changed/missing/ambiguous identity, privilege loss or authority drift refuses the
capture. If only the final bounded collection fails, retained useful records may
be exported with unestablished identity and unknown fence continuity. Changed
inventory/mappings stay explicit; comparison cannot use that capture as absence
authority. Equal before/after observations do not prove atomicity: changes and
reversions within the interval may be missed.

Sensitive metadata from auth, config entries and device records is discarded before
retention or hashing. Retained identifiers must pass the existing sanitizer unchanged,
even when optional redaction is disabled. Fixed diagnostic reasons never reflect
rejected keys, exception text, payloads or cursor values. Audit records contain only
scope, bounded limit, continuation presence and bounded capture summaries; no raw
configuration, auth identity or cursor is recorded.

## Bounds and frozen export

| Boundary | Limit |
|---|---:|
| Retained baseline records | 1,000 |
| Rows per global inventory input | 20,000 |
| WebSocket frame / REST state response | 4 MiB |
| Accepted authentication payload | 64 KiB |
| Each configuration response | 256 KiB |
| Total transport bytes | 32 MiB |
| JSON values / nesting / string bytes | 100,000 / 64 / 16 KiB |
| Collection / individual read deadlines | 45 s / 5 s |
| Concurrent configuration reads | 4 |
| Application reads, including auth | At most 1,011 |
| Retained snapshots | 2 × 2 MiB, 600 s TTL |
| Page records / response data | Default 25, maximum 100 / 48,000 bytes |

Configuration scheduling stops with ten seconds reserved for the final fence.
Network operations share the collection deadline; cancellation drains owned bounded
pure worker computation and closes transport before returning. There is no detached
capture job, retry, redirect, fallback or automatic refresh. Parsing, projection,
hashing, validation and page fitting run outside the event loop. The accepted auth
payload bound is checked before JSON decoding; the connection's wire-frame ceiling
remains 4 MiB. Unexpected/control frames are refused, not automatically ponged.

Reaching the record ceiling is explicitly partial even at exactly 1,000. Unmapped
and registry-only ID previews retain at most 16 IDs each; counts and their precision
remain separate. A byte/structural failure is never accepted as a complete inventory.

A continuation cursor is HMAC-bound to the caller and the immutable snapshot.
Continuations make zero HA reads. Expiration/restart/tampering fails explicitly;
unexpired snapshots are never silently evicted. At capacity or during another
capture, a new capture is refused. No snapshot is retained after failed/cancelled
initial capture. This adds no durable server store or background watcher.

To export, retain the first `baseline_header` and append `records` from every page
in offset order. Verify every header/digest agrees, offsets are contiguous, the
last cursor is null, and collected count equals `total_records`. Last-page
`complete_export=true` means the server has no further records; it does not prove
that a client retained earlier pages. Construct `{**baseline_header, "records":
all_records}`, serialize sorted compact JSON with `ensure_ascii=True`, UTF-8, and
verify `artifact_sha256` before saving. Do not add diagnostics to baseline bytes.

Two distinct ordered exports can then be passed unchanged to:

```bash
python scripts/compare_automation_baselines.py earlier.json later.json --output comparison.json
```

Legacy October evidence remains unresolved and is never retroactively upgraded.

## Exact Core source evidence and validation stages

Semantic inspection uses Core commit
`9212531f40a0b7b23229a90d688dd79d9dfccff4` (2026.9.4), archive SHA-256
`c8d814b63c0b7043ad54980044f9824c9582450af9cac38ba376fadc37fa4056`:

- `components/api/__init__.py:206`: admin REST states, no serialization omission.
- `components/websocket_api/commands.py:370`: WebSocket omission boundary.
- `components/auth/__init__.py:481` and `auth/__init__.py:443`: actual connection
  principal and MFA read delegation. Built-in TOTP/notify setup readers load stored
  metadata but do not invoke setup flows, validation or save operations.
- `components/config/view.py:98`: administrator-only fixed configuration lookup.
- `components/config/entity_registry.py:39`: partial registry serialization.
- `components/hassio/coordinator.py:1173` and `helpers/device_registry.py:379,527`:
  persisted Core device identity and config-entry linkage.

Source-function tests verify the archive before executing isolated functions with
synthetic inputs. They do not substitute for an assembled disposable Core lane.
That lane must prove authenticated admin/nonadmin reads, actual Core device/entry
projection, lossless REST configuration and state behavior, fixed requests,
zero writes, artifact reconstruction and refusal controls before release readiness.
See [installed acceptance](AUTOMATION_BASELINE_CAPTURE_ACCEPTANCE.md).
