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
| Entity current state | transitional direct fallback | Standard MCP preferred but unavailable; explicit direct HA REST fallback |
| Entity registry | transitional direct | HA WebSocket entity registry |
| Scripts, scenes, groups, template source, dashboards | unavailable | No reliable complete configuration adapter yet |
| Static YAML/packages/custom integrations | outside coverage | No arbitrary filesystem or `.storage` scan |

Standard HA MCP nested delegation remains unavailable and is never fabricated.
Direct evidence is labeled `direct_ha_api`.

## Matching and response semantics

Automation configuration is recursively traversed through triggers, conditions,
actions, targets/data, choose, if/then/else, repeat, parallel, sequence,
wait-for-trigger, variables, and nested lists. Exact tokens prevent substring matches.
Aliases, descriptions, notification/log text, and unrelated free-form strings are
excluded.

Literal Jinja references through `states()`, `states.domain.object`, `is_state()`,
`state_attr()`, `expand()`, and literal lists are detected. Dynamic construction is
not guessed; it produces a bounded unresolved warning and source path. Blueprint input
values remain visible even if source parsing fails. No full automation or blueprint
source is returned.

Summary mode returns at most 10 findings. Standard/evidence paginate up to 100.
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
configurations. It has a five-minute TTL, bounded edge count, deterministic generation
fingerprint, cache metrics, and opaque generation-bound cursors. Invalid cursors return
`invalid_cursor`; old-generation cursors return `stale_cursor`.

`refresh_index=true` rebuilds read-only evidence. Successful governed apply/rollback,
legacy automation upsert/delete, and relevant reloads invalidate the index. Restart
resets it deterministically.

## Known limitations and connector impact

Dynamic entity construction cannot always be resolved. Device triggers may not map to
one entity. Runtime automation action-to-trigger causality is not inferred. Dashboard,
static YAML/package, script, scene, group, template-source, and custom-integration
coverage remains unavailable.

Beta 7 changes the manifest from 32 to 33 tools. Recreate the ChatGPT beta connector or
use the cache marker `?manifest=beta7` if the tool is absent. Never place a real secret
or private connector URL in source, logs, or screenshots.
