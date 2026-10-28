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
an idempotent retry; ordinary idempotent periodic checks remain quiet. That
change left the successful reconciliation event, public health projection and
tool descriptors otherwise unchanged; the health itemization below is HAMCP-242. Events use the existing sink outside the runtime state
lock. A refused or throwing sink increments `audit_write_failures`; it does not
hide the failed attempt, admit authority or queue another probe. An unavailable
sink means a durable reason could not be recorded, not a successful audit.

The 300-second failure pacing, signed authority, retirement, lease/stale-plan
checks and catalog restoration are unchanged. Offline failure and retry controls
exercise production collection with synthetic peers and a controlled clock.
These diagnostics do not prove that health work caused the historical October
disconnect and do not reconstruct missing historical failure details.

# Withheld capability itemization (HAMCP-242)

`get_server_health.home_assistant_core_authority` previously published counts
only. A client reading `unavailable_count: 3` and `withheld_count: 3` could not
tell which capabilities were blocked, why, or which tools they affected, and
could misread the two counts as six capabilities. The block now adds three
fields. Every existing field, count and value is unchanged.

| Field | Meaning |
| --- | --- |
| `withheld_capabilities` | One entry per held, quarantined or unavailable capability profile in the published generation, sorted by `capability_id`. Empty when every evaluated profile is compatible. |
| `withheld_capabilities_truncated` | `true` only when more than 64 entries exist and the list holds the first 64. The counts always cover every profile. |
| `count_semantics` | A fixed string that states how the counts relate. |

Each entry has exactly four fields:

| Field | Meaning |
| --- | --- |
| `capability_id` | The Engineering capability profile identifier, such as `core.script_trace_read`. |
| `disposition` | `held`, `quarantined` or `unavailable`, the same class the matching `*_count` counts. |
| `reason_code` | The fixed-vocabulary reason the coordinator recorded for that decision, such as `positive_authority_missing`. |
| `affected_tools` | Sorted Engineering static tool names whose reviewed requirement table (`routes.STATIC_TOOL_CORE_REQUIREMENTS`) names this profile, or `[]`. |

## Count semantics

- `withheld_count = held_count + quarantined_count + unavailable_count`.
  `withheld_count` is a total, not an additional set: `unavailable_count: 3`
  with `withheld_count: 3` means three capabilities, not six.
- `compatible_count + withheld_count` equals the number of capability profiles
  evaluated in `current_generation`.
- `withheld_capabilities` lists each withheld profile once. Its length equals
  `withheld_count` unless truncated, and the number of entries for each
  disposition equals that disposition's count.

Internal consistency guards enforce these relations. Each one refuses with
`core_authority_projection_inconsistent`:

1. a disposition's list length differs from its count (`held_count`,
   `quarantined_count`, `unavailable_count`; one guard each);
2. the list total differs from `withheld_count`;
3. a capability id appears more than once across dispositions;
4. `compatible_count + withheld_count` differs from the number of decisions
   in `current_generation`, for example a decision in no disposition class.

A refusal stays inside this block. `get_server_health` still succeeds and
every other section is unchanged. The block is then degraded:

- it carries `projection_degraded: true` and `projection_reason_code:
  "core_authority_projection_inconsistent"`. Neither key appears in a normal
  block;
- it keeps only fields the decision partition does not feed:
  `model_version`, `surface`, `current_generation` (the generation that
  failed), `issued_lease_count`, `active_commit_count`,
  `retained_retirement_count`, `capacity_exhaustion_count`,
  `capacity_exhaustion_reason` and `fallback_count`, plus the runtime fields
  `configured`, `initialized`, `observed_core_version`, `identity_agreement`,
  `counters`, `recovery`, `release_registry` and `recent_event_count`;
- it drops `disposition`, all five disposition counts,
  `withheld_capabilities`, `withheld_capabilities_truncated` and
  `count_semantics`. Those are the values in dispute, so the block shows no
  list or count that could contradict another, and it never substitutes
  zeros or an empty list.

```json
{
  "model_version": 1,
  "surface": "home_assistant_core",
  "current_generation": 1,
  "issued_lease_count": 0,
  "active_commit_count": 0,
  "retained_retirement_count": 0,
  "capacity_exhaustion_count": 0,
  "capacity_exhaustion_reason": null,
  "fallback_count": 0,
  "projection_degraded": true,
  "projection_reason_code": "core_authority_projection_inconsistent",
  "configured": true,
  "initialized": true,
  "observed_core_version": "2026.10.3",
  "identity_agreement": true,
  "counters": {"...": "unchanged"},
  "recovery": {"...": "unchanged"},
  "release_registry": {"...": "unchanged"},
  "recent_event_count": 1
}
```

The guards only read the assessment the coordinator already computes. A
refusal changes no admission decision, reason code, lease, route, registry
handling or recovery, and persists nothing: the next consistent read returns
the normal block. The guards can only refuse; they never admit. Any other
coordinator error still fails the call as before. The guards run only in the
health projection. The operator assessment behind the governance runtime
snapshot behaves as it did in beta.43.

## Reading the three-capability case

Under the signed sequence-14 registry, Core 2026.10.3 has positive authority for
26 profiles. It has none for the three dormant November-batch profiles, so the
block reads:

```json
{
  "compatible_count": 26,
  "held_count": 0,
  "quarantined_count": 0,
  "unavailable_count": 3,
  "withheld_count": 3,
  "withheld_capabilities": [
    {
      "capability_id": "core.config_entry_helper_options_operation",
      "disposition": "unavailable",
      "reason_code": "positive_authority_missing",
      "affected_tools": ["create_helper_options_plan"]
    },
    {
      "capability_id": "core.script_trace_read",
      "disposition": "unavailable",
      "reason_code": "positive_authority_missing",
      "affected_tools": ["get_script_trace", "list_script_traces"]
    },
    {
      "capability_id": "core.typed_input_select_option_operation",
      "disposition": "unavailable",
      "reason_code": "positive_authority_missing",
      "affected_tools": ["create_input_select_option_plan"]
    }
  ],
  "withheld_capabilities_truncated": false
}
```

Read this as three blocked capabilities: 26 + 3 = 29 profiles evaluated. Each
lacks positive signed authority for the observed Core version, and four tools
are affected. The fix is signed authority (registry sequence 15) that names
those profiles, not a change to Home Assistant. After that registry is admitted
and the add-on restarts, the list is empty and `compatible_count` is 29.

## Boundaries

- Every value is a fixed-vocabulary identifier from Engineering's own code:
  capability ids of the compiled profiles (the production evidence collector
  emits no other id), the three `CoreDisposition` names, coordinator reason
  codes and Engineering static tool names from
  `STATIC_TOOL_CORE_REQUIREMENTS`. No Home Assistant text, entity, session or
  endpoint value enters the list. The health path serializes and size-bounds
  this block; it does not run the shared sanitizer over it, and this release
  adds none.
- `affected_tools` lists only Engineering static tools. Delegated `ha-mcp`
  reads are not listed, because their client-visible names depend on the
  admitted upstream release. A per-call narrowed tool appears under every
  profile it can require. An empty list means no static tool requires the
  profile; it does not mean the profile has no effect.
- The list is derived from the decisions already published in the current
  generation. It persists nothing new and makes no Core, Supervisor or upstream
  call. Signing, registry content, `list_capabilities` and `server_info` are
  unchanged.
- The whole projection stays within the existing `MAX_REPORT_BYTES` (32 768)
  bound. Because the list limit (64) equals `MAX_CAPABILITIES`, truncation
  cannot occur with the current profile set. The flag guards a future increase
  in that set.
