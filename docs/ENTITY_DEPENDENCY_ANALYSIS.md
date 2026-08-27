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

Beta 39 uses a bounded, whole-template obligation ledger for Jinja evidence. The
runtime pins Jinja 3.1.6, the exact version shared by the supported Home Assistant
2026.7.2, 2026.8.0, and 2026.8.1 lanes, and uses Home Assistant's reviewed `do` and
`loopcontrols` parse extensions. Templates are parsed only: no loader is installed,
no template is compiled or rendered, and no Home Assistant helper is invoked.

Every dependency-sensitive AST construct creates a terminal obligation before
precision analysis tries to resolve it. The terminal is an exact dependency, a
target-specific exclusion, a proven dependency-neutral result, bounded semantic
opacity, or coverage failure. Whole-template binding state covers assignments,
loops, conditionals, local macros, collections, subscriptions, attributes, filters,
tests, and later invocation. Mapping, sequence, and namespace receiver semantics are
kept distinct; bounded constructor and mapping-iteration projections preserve the
keys and values that Jinja can later consume. Runtime scalar values remain tainted
when transported into an entity selector while render-only formatting remains
dependency-neutral. Unknown syntax, callables, receivers, external-template
content, parse errors, and exceeded limits produce explicit opacity or failure; they
cannot disappear as zero evidence. Comments, raw blocks, ordinary message formatting,
and proven scalar or mapping operations remain dependency-neutral.

Surrounding configuration supplies bounded typed provenance rather than a flat bag
of dotted strings. Root automation/script variables are evaluated in insertion
order and retain the externally supplied run-variable alternative that Home
Assistant preserves. Variables actions render each value in insertion order,
publish it to the next value, honor `enabled`, and do not transfer bindings between
parallel siblings. Exact configuration maps/lists, Jinja loop metadata, Home
Assistant `repeat` variants, exact zone triggers, event/payload mappings, and
reviewed date/time results keep their distinct provenance. Runtime payload values
and run-variable overrides remain harmless when only formatted, but become bounded
opacity when consumed as entity selectors. Aggregate context conversion, lexical
scope, macro capture, recursion, value depth, member count, and scalar size are
bounded explicitly; a breached bound is coverage failure.

Beta 47 extends this typed transport through finite collection projections.
Fixed trigger state objects and complete non-helper domain collections retain
their entity exclusion while `map(attribute=...)` projects reviewed scalar,
timestamp and context attributes. Complete literal candidates also survive
mapping values and reviewed string transport. A dynamic scalar remains tainted
when later consumed as an entity selector, so the additional precision cannot
turn arbitrary selection into absence of evidence.

The reviewed semantic registry derives the complete standard Jinja 3.1.6 filter
and test vocabulary from the pinned package itself, so every name Jinja binds -
including the `d`/`default`, `e`/`escape`, `count`/`length`, and comparison-test
aliases - carries the reviewed category of the implementation it resolves to.
On top of that it declares Home Assistant state helpers, translated-state
helpers, `expand`, `closest`, `distance`, area/device/floor/integration/label
entity-set producers, dynamic filter/test dispatch, and state-bearing trigger,
wait, and `this` context. A future or unknown helper becomes opaque until
reviewed rather than being inferred harmless. `as_timestamp` is reviewed as a
dependency-neutral scalar conversion; its runtime value cannot become an entity
selector without explicit opaque evidence.

Source provenance is maintained in an independently reviewed declaration and
generated deterministically offline. Generation verifies each referenced source
against an independent witness rather than against the declaration itself:
Jinja path/blob pairs are recomputed as git blob SHA-1 values from the
installed pinned distribution, and Home Assistant path/blob pairs are checked
against immutable captured official-source evidence. A wrong blob, a copied
attribution, or a path that does not exist at a supported tag fails generation.

The registry's semantics are reviewed against Home Assistant 2026.7.2,
2026.8.0, and 2026.8.1. They are not bound to the version reported by the
connected instance: nothing reads the connected version, and it does not
participate in dependency evidence or its fingerprint.

The `attr` filter is modelled as real attribute access with no mapping-item
fallback, matching `jinja2.filters.do_attr`. On a mapping receiver it yields
undefined, which carries no dependency provenance, instead of reading the item
of the same name. Static template imports/includes/extends bind the
external name and calling configuration identity but remain opaque because Beta 39
does not retrieve custom-template content.

An automation's raw `use_blueprint` mapping cannot prove that the blueprint body
is helper-independent. The direct dependency provider discharges that bounded
external-source obligation only after it reads, resolves, and analyzes the exact
blueprint source. F3 configuration locking, which sees only current and proposed
automation configuration, retains the conservative helper-dependency guard for
blueprint create, update, or removal. This adds no blueprint write or reload
authority. Discharge is evidence-bound to the raw path/configuration fingerprint,
the resolved configuration fingerprint, and the complete resolved obligation
ledger; a mismatched or missing ledger retains opacity.

The public dependency graph remains a bounded compatibility projection: exact
dependencies become findings, unresolved or opaque obligations become dynamic
evidence, and neutral/excluded terminals do not create false graph edges. Full
template or automation bodies are never retained. Blueprint input values remain
visible under the established supported input context.

Beta 45 separates the completeness of an entity-candidate universe from exact
runtime-value precision. A bounded filter can therefore leave its selected
subset uncertain while preserving proof that every possible result belongs to
one complete finite candidate set. Exact candidates and complete domain unions
support authoritative target inclusion or exclusion; diagnostic candidates on
an opaque terminal never do. Literal `label_entities` membership is resolved
against the same complete bounded registry generation as the dependency scan,
and the label identity plus membership fingerprint is retained in the ledger.
Dynamic labels, incomplete registries, truncation and overflow remain opaque or
fail coverage. Consequences and F3 locks consume those authoritative terminal
outcomes rather than reconstructing discarded proofs downstream.

Beta 47 advances helper plans to `helper-dependency-risk-v6`. Downstream action
effects attach only to a surviving exact target edge or genuine target-capable
opacity. Reviewed direct physical effects remain proportional and exact
safety-critical effects remain approval-actionable through elevated approval
when every effect is classified. Notification display templates are certified
only when parse-only analysis proves a literal prefix cannot enter the reviewed
exact controls or `command_`/`kiosk_` namespaces. Dynamic-first notification
content, effect-bearing controls, custom/dynamic services and unresolved
transitive actions remain conservative. Persisted v3, v4 and v5 evidence stays
readable but requires replanning and has no current execution authority.

Beta 48 advances new helper plans to `helper-dependency-risk-v7` and makes the
target-independent selector scope an explicit part of every obligation.
Complete finite candidates, complete domains and dependency-neutral operations
are resolved against the requested helper before downstream profile selection;
only exact inclusion, genuine target-capable opacity or target-relevant coverage
failure can attach consequence. Candidate hints on unresolved evidence remain
non-authoritative. Persisted v3-v6 evidence stays readable with its historical
classification but requires replanning and has no current execution authority.

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

Dynamic entity construction cannot always be resolved. Bounded semantic opacity is
reported separately from missing inventory coverage; it is never described as exact.
Device triggers may not map to one entity. General or multi-hop runtime automation
action-to-trigger causality is not inferred. The exact helper provider's bounded
`state_changed` and `call_service` event-trigger contracts are modeled so those direct
downstream effects cannot disappear. Imported custom-template source is not retrieved. Dashboard,
static YAML/package, script, scene, group, template-source, and custom-integration
coverage remains unavailable unless a later reviewed provider supplies it.

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

Automation inventory, configuration bodies, blueprint source bytes, event selectors,
template sources, AST work, abstract values, retained identities, and public evidence
all have deterministic limits. Exceeding a limit creates explicit coverage failure;
it does not silently clip authoritative helper-risk evidence. Automation configuration
reads use bounded concurrency of eight. Beta/RC
prewarming defaults on with a 45-second startup delay, first performs a safe
`/config` connectivity probe, and uses the same single-flight build path. It
does not block startup or non-index tools and retries failures no faster than
every 300 seconds. Health exposes the prewarm attempt, timestamps, next retry,
and bounded failure category.
