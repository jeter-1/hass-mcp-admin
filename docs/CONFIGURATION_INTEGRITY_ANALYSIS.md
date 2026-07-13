# Configuration Integrity Analysis

`configuration_integrity_analysis` is the Beta 17 Engineering-native,
read-only system-wide configuration audit. Beta 18 hardens the shared reference
classifier used by this tool and the other dependency-index consumers. It
correlates the shared dependency index with one complete current-state inventory
and one entity-registry inventory. It does not mutate Home Assistant, execute a
service, generate a cleanup command, or create a governance plan.

## Input contract

```json
{
  "source_types": [
    "automation",
    "blueprint",
    "script",
    "scene",
    "group",
    "template",
    "dashboard"
  ],
  "finding_types": [
    "missing_entity_reference",
    "disabled_entity_reference",
    "registry_only_entity_reference",
    "orphan_registry_candidate",
    "unresolved_dynamic_reference"
  ],
  "include_orphan_candidates": true,
  "detail_level": "standard",
  "limit": 20,
  "cursor": "",
  "refresh_index": false
}
```

Empty `source_types` and `finding_types` arrays select every supported value.
`detail_level` is `summary`, `standard`, or `evidence`. `limit` is 1–100;
detail-specific response caps may lower the effective page size and are
reported explicitly. `cursor` is an opaque signed continuation token.
`refresh_index=true` is valid only on a first-page request.

The capability catalog declares category `analysis`, lifecycle
`beta_native`, risk `read`, route `engineering_native`, provider
`engineering`, and policy `global_configuration_integrity_read`. Standard HA
MCP has no exact global integrity mapping and no fallback is allowed.

## Evidence collection and source coverage

A new analysis performs, at most:

1. one lookup or build of the shared dependency index;
2. one bounded complete `/states` read;
3. one `config/entity_registry/list` WebSocket read; and
4. deterministic local classification over sanitized normalized sets.

There is no request per reference or entity. Provider activity is bounded and
concurrent inventory timing distinguishes cumulative attempts from current
wall-clock time. Every response reports current states, entity registry,
automation, blueprint, script, scene, group, template, and dashboard coverage
as `complete`, `partial`, `not_supported`, `not_requested`, or `failed`.
Unsupported requested sources prevent a complete assessment. Unsupported but
unrequested sources do not make a deliberately scoped assessment partial.

The current dependency provider supports automation and bounded blueprint
evidence. Script, scene, group, template, dashboard, static YAML, packages,
custom-integration configuration, external systems, and runtime history are
reported honestly when unsupported. The server never turns absent evidence
from an unsupported source into a clean conclusion.

## Entity-reference classification

Dotted syntax alone is not evidence of an entity reference. Beta 17 scanned
generic dotted tokens inside some bracketed templates, which allowed decimals
and member expressions such as `1.1`, `c.id`, and `ns.lines` to enter the shared
index as exact edges. Beta 18 removes that global scan at the shared extraction
boundary. Ignoring confirmed non-entity text is not a coverage failure.

An exact reference requires both a trusted context and a canonical literal ID.
Trusted structured contexts are explicit `entity_id` values, including bounded
lists, under triggers, conditions, action targets/data, nested choose/if/repeat
structures, group membership, scenes, and supported blueprint inputs. Service,
device, area, message, description, and arbitrary variable fields are not
promoted merely because their values contain a period.

The centralized literal helper list is:

- `states('sensor.example')`
- `is_state('binary_sensor.example', 'on')`
- `is_state_attr('climate.example', 'hvac_mode', 'cool')`
- `state_attr('sensor.example', 'unit_of_measurement')`
- `expand('group.example')`

Literal `states['sensor.example']` and `states.sensor.example` lookups remain
supported. The bounded tokenizer examines only template code outside comments
and quoted prose; it never executes Jinja. A recognized helper or `states[...]`
lookup with a dynamic argument becomes target-free
`unresolved_dynamic_reference` evidence. General attribute access such as
`c.name`, URLs, hostnames, IP addresses, versions, filenames, package names,
service names, UUIDs, MAC addresses, and free-form prose are ignored.

Canonical IDs contain exactly one period, non-empty lowercase ASCII
alphanumeric/underscore components, at least one letter in each component, and
at most 255 characters. Whitespace, uppercase, extra periods, numeric-only
components, template syntax, URLs, versions, and IP addresses are rejected.
Domains are not allow-listed, so valid custom-integration domains remain
supported. Context plus validation establishes exact evidence; the validator is
never used as a free-text detector.

## Finding classifications

`missing_entity_reference` is an exact canonical static reference whose target
is absent from both the state machine and entity registry. Arbitrary text,
malformed values, service names, device or area IDs, and unresolved templates
cannot create this finding.

`disabled_entity_reference` is an exact static reference to an entity-registry
entry with `disabled_by`. Results distinguish user-disabled,
integration-disabled, and other disabled entries. This is a review risk, not a
claim that the reference is broken.

`registry_only_entity_reference` is an exact static reference to a registry
entry with no current state and no explicit disabled classification. It may be
unloaded, temporarily absent, or stale; the evidence cannot decide safely.

`orphan_registry_candidate` is a registry entry with no state and no exact
inbound reference in the inspected full dependency scope. It is only a manual
review candidate. A state value of `unavailable` still means the entity exists
and never creates this candidate. Candidate generation is omitted when source
filtering hides possible consumers or the foundational inventories fail.

`unresolved_dynamic_reference` is a requested-scope template or expression
capable of selecting entity IDs that cannot be resolved statically. It has
limited confidence, preserves a bounded source ID and configuration path, and
never invents a target entity ID.

## Severity and assessment

- High: an exact missing reference from an enabled consumer.
- Medium: a missing reference from a disabled or unknown-state consumer; a
  disabled or registry-only target; an unresolved requested-scope dynamic
  reference; or incomplete required coverage.
- Low: a conservative orphan-registry candidate.
- Info: reserved for useful non-defect relationships.

Repeated paths do not increase severity. Final assessments are
`review_required`, `no_confirmed_integrity_findings`, or
`assessment_incomplete`. The tool deliberately avoids “safe” and “clean”.

## Deduplication and result counters

Exact findings are deterministically grouped by finding type, target entity,
source type, and source ID. All unique bounded configuration paths remain on
that one finding. Different source objects remain different findings. Orphan
candidates are one per registry entry. Dynamic findings are one per source
object and path. Finding IDs and ordering are deterministic.

Whole-analysis fields are:

- `finding_count` and totals by severity, finding type, and source type;
- `unique_source_object_count`;
- `unique_target_entity_count`;
- `unique_orphan_candidate_count`;
- `unresolved_dynamic_reference_count`;
- `manual_review_required`, `final_assessment`, and `result_status`.

Severity and type totals each sum to `finding_count`. Source totals cover only
findings that have a source object; orphan candidates intentionally have none.
Pagination never changes whole-analysis totals.

## Dynamic references and orphan safeguards

The dynamic summary distinguishes requested-scope unresolved references,
out-of-scope unresolved references, reported dynamic findings, and the manual
review decision. Requested-scope unresolved references prevent an absolute
no-finding conclusion even if a finding filter omits their detailed rows.

No candidate is automatically safe to delete. State absence alone is not proof
of obsolescence. Unsupported sources, external clients, dynamic templates, and
integrations may still consume an entity. Disabled entries may be intentional,
and Home Assistant may recreate an entry after an integration reload. Static
YAML and packages are not claimed as inspected without actual bounded support.

## Pagination lifecycle and provenance

The first page is classified only after a refreshed index, when requested, has
become the final active generation. The immutable sanitized snapshot binds the
active generation and full index fingerprint, query fingerprint, evidence
fingerprint, original analysis timestamp, snapshot ID, and offset. Its fixed
TTL is 300 seconds.

Continuation excludes `refresh_index` from its query fingerprint, reads only
the snapshot, and performs no HA call, provider dispatch, index lookup/build,
or new classification. It retains the original timestamp, coverage, totals,
and index provenance. A tampered token returns `invalid_cursor`; an expired
snapshot or replaced/invalidated index returns `stale_cursor`; changed query
semantics fail closed. A pagination snapshot is not a general result cache.

The response reports cache hit/refresh state, index generation, fingerprint,
build timestamp, current lookup/build duration, original build duration,
snapshot TTL, current-request timing, HA cumulative attempt time, HA wall-clock
span, request count, and maximum concurrency.

## Validation and errors

Validation failures use `invalid_request` with bounded details:

```json
{
  "field": "finding_types",
  "reason": "unsupported_value",
  "value": "unknown_type",
  "operation": "configuration_integrity_analysis"
}
```

Invalid detail level, limit, source or finding type, cursor form, query binding,
and continuation-only options fail before HA access, provider dispatch,
dependency lookup, classification, or snapshot creation. Raw exceptions,
filesystem paths, secrets, and stack traces are never returned.

## Observability and audit

`get_server_health` has a `configuration_integrity_analysis` group containing
request, success, partial, failure, finding, severity/type/source, unique source
and target, orphan candidate, dynamic reference, manual-review, source failure,
truncation, cursor, index-cache, and last-outcome counters. Request count
includes continuations. Terminal outcomes and whole-analysis aggregates count
new analyses only. Cursor failures use cursor-specific counters and are not
failed new analyses. Validation failures add a failed request but no findings.

Audit records contain the tool and read/Engineering classifications, requested
source and finding types, orphan flag, detail, limit, refresh flag, cursor
presence as a Boolean, bounded terminal summary, endpoint categories, request
ID, and server version. They never contain raw cursor material, configuration,
unbounded evidence, authentication material, or secrets.

## Read-only guarantees

The implementation has no service execution, entity-registry update,
automation write, governance creation/approval/apply, reload, restart, or
general-result-cache path. It returns evidence and limitations only. It never
generates deletion commands or an automatic cleanup plan.

## Deployed Beta 18 acceptance

This procedure is entirely read-only:

1. Call `server_info`; verify `2.0.0-beta.18`, 36 tools, and 25 canonical tools.
2. Call `list_capabilities`; verify the unchanged beta-native tool and provider matrix.
3. Call `get_server_health`; record clean integrity/provider baselines.
4. Confirm `configuration_integrity_analysis` appears in real `tools/list` and is callable.
5. Run an automation-only summary request with `include_orphan_candidates=false`, `refresh_index=true`, low `limit`, and default finding types.
6. Follow at least two cursor pages when present.
7. Confirm every page retains the timestamp, totals, coverage, and provenance; continuation reports zero HA requests and no index build; cursor counters rise while terminal aggregates count once.
8. Confirm `1.1`, `8.8`, `c.id`, `c.limit`, `c.name`, `grace.get`, `ns.bad`, `ns.ids`, and `ns.lines` never appear as exact target entity IDs.
9. Inspect every remaining `missing_entity_reference` and cross-check selected targets with `get_entity`, `list_entity_registry`, and `entity_dependency_analysis`.
10. Verify dynamic references are separate, bounded, target-free, and require review in requested scope.
11. Send an invalid limit or unsupported value outside the MCP enum path; verify field-level `invalid_request` and no upstream activity.
12. Alter one cursor character and verify fail-closed `invalid_cursor`.
13. Recheck health counters and bounded audit records.
14. Verify no Home Assistant state, registry, service, automation, plan, reload, or restart mutation occurred.

Beta 18 adds no tool and changes no input schema, so connector recreation is not
normally required. Refresh only the beta connector if a client caches server
version or an older 35-tool manifest; never expose the authenticated URL.
