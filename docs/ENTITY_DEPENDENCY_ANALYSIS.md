# Entity dependency analysis

Beta 7 adds the read-only, engineering-native `entity_dependency_analysis` tool. It
answers “what inspected Home Assistant configuration depends on this entity?” without
returning full configuration documents.

## Input contract

```text
entity_id: string (required)
detail_level: summary | standard | evidence = summary
include_indirect: boolean = false
max_depth: integer 1..3 = 2
source_types: array[automation|blueprint|script|scene|group|template|dashboard] = []
limit: integer 1..100 = 50
cursor: opaque string = ""
refresh_index: boolean = false
```

Empty `source_types` means every configured source. A syntactically valid missing
entity is analyzed normally because stale references may remain.

## Current provider coverage

| Source | Beta 7 status | Provider and behavior |
| --- | --- | --- |
| Automations | complete or partial per scan | Direct HA automation configuration API with bounded concurrency |
| Blueprint inputs | complete when automation input is readable | Direct automation configuration evidence |
| Blueprint role resolution | complete or partial | Read-only blueprint mount, safe paths, YAML `!input` resolution |
| Entity current state | transitional direct | Exact entity state requires direct HA REST; no fallback or Standard MCP claim |
| Entity registry | transitional direct | HA WebSocket entity registry |
| Scripts, scenes, groups, template source, dashboards | unavailable | No reliable complete configuration adapter yet |
| Static YAML/packages/custom integrations | outside coverage | No arbitrary filesystem or `.storage` scan |

Standard HA MCP lacks an exact entity-ID contract for this evidence and is never fabricated.
Direct evidence is labeled `direct_ha_api`.

## Matching and response semantics

Automation configuration is recursively traversed through triggers, conditions,
actions, targets/data, choose, if/then/else, repeat, parallel, sequence,
wait-for-trigger, variables, and nested lists. Exact edges require an explicit
entity-bearing context plus a canonical literal ID. Aliases, descriptions,
notification/log prose, service names, device/area IDs, and unrelated strings are
not scanned for dotted tokens.

Beta 18 uses a bounded function-aware tokenizer for Jinja. Literal references through
`states()`, `states.domain.object`, `states['domain.object']`, `is_state()`,
`is_state_attr()`, `state_attr()`, and `expand()` are detected outside comments and
quoted prose. Dynamic helper or `states[...]` arguments produce bounded target-free
unresolved evidence. Decimals, versions, IP addresses, URLs, hostnames, object/member
access, and arbitrary dotted labels do not create graph edges. Blueprint input values
remain visible under the established supported input context. No template is executed,
and no full automation or blueprint source is returned.

Every detail level honors `limit` from 1 through 100. Pagination reports
`requested_limit`, `effective_limit`, `maximum_limit`, clamping state/reason, returned
count, total, and cursor state; no detail level silently substitutes a lower cap.
Evidence mode adds bounded redacted excerpts and static indirect paths.

Assessment values are deliberately cautious:

- `not_safe`: a direct reference exists;
- `references_found`: an explicit static indirect chain exists;
- `unknown_due_to_incomplete_coverage`: zero findings with incomplete coverage;
- `no_references_detected_within_coverage`: zero findings in requested complete sources.

A missing entity with references is a possible stale-reference condition. Zero findings
never imply absolute safety when relevant sources were not inspected.

## Index, pagination, and invalidation

The process-local index stores bounded normalized edges and safe metadata, not raw
configurations. It has separate soft and hard TTLs, a bounded edge count,
deterministic generation fingerprint, cache metrics, and opaque generation-bound
cursors. Invalid cursors return `invalid_cursor`; old-generation cursors return
`stale_cursor`. The RC2dev5 freshness and prewarm defaults are detailed below.

`refresh_index=true` rebuilds read-only evidence. Successful governed apply/rollback,
legacy automation upsert/delete, and relevant reloads invalidate the index. Restart
resets it deterministically.

The index records original build duration separately from the current lookup and
request duration. On a cache hit, source coverage reports `duration_ms=0` for current
provider work, preserves `index_build_duration_ms` as provenance, and marks
`cached_provenance=true`. Health reports truncation as a cumulative process event count
and unresolved dynamic references as current index state, so repeated cache hits do not
look like duplicate current findings.

## Known limitations and connector impact

Dynamic entity construction cannot always be resolved. Device triggers may not map to
one entity. Runtime automation action-to-trigger causality is not inferred. Dashboard,
static YAML/package, script, scene, group, template-source, and custom-integration
coverage remains unavailable.

The historical Beta 18 manifest contained 36 tools. Beta 18 changed no tool schema, so
connector recreation is not normally required. Refresh only the beta connector if it
still presents an older manifest. Never place a real secret or private connector URL
in source, logs, or screenshots.

RC2dev5 keeps construction single-flight: concurrent cold callers await one
shared build. Each build reuses one state inventory and one entity-registry
snapshot and reports request/timing breakdown, queue time, observed concurrency,
and parsing time. `cumulative_queue_wait_ms` is accumulated per-request effort,
not wall time; maximum, average, and build-wall-clock values are also reported.
A valid warm lookup and cursor continuation make zero Home Assistant requests.

Soft TTL defaults to 600 seconds and hard TTL to 3600 seconds. Between them,
the old generation is returned immediately as explicitly stale evidence while
one background refresh runs. A failed refresh preserves that generation only
until hard expiry. Hard-expired or configuration-invalidated evidence is not
returned as authoritative. The new generation is published atomically and
makes generation-bound cursors stale.

Automation configuration reads use bounded concurrency of eight. Beta/RC
prewarming defaults on with a 45-second startup delay, first performs a safe
`/config` connectivity probe, and uses the same single-flight build path. It
does not block startup or non-index tools and retries failures no faster than
every 300 seconds. Health exposes the prewarm attempt, timestamps, next retry,
and bounded failure category.
