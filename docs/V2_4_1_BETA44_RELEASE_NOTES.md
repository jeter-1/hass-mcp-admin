# Engineering 2.4.1-beta.44 release notes

Engineering 2.4.1-beta.44 itemizes the withheld Core capabilities in
`get_server_health` (HAMCP-242). It follows 2.4.1-beta.43 and includes it.
There is no dependency, add-on configuration, input schema, tool, admission
or signed registry change.

Acceptance: [`V2_4_1_BETA44_ACCEPTANCE.md`](V2_4_1_BETA44_ACCEPTANCE.md).

## Why

On beta.43 with Home Assistant Core 2026.10.3, `get_server_health` reports
`home_assistant_core_authority` as counts only: `compatible_count` 26,
`unavailable_count` 3, `withheld_count` 3. A client could not tell which
capabilities were blocked, why, or which tools they affected. It could also
read the two counts as six blocked capabilities, when `withheld_count` is a
total that already includes the three unavailable ones.

## The change

`home_assistant_core_authority` gains three fields. Every existing field,
count and value is unchanged.

- **`withheld_capabilities`.** One entry per held, quarantined or
  unavailable capability profile in the published generation, sorted by
  `capability_id`. Each entry is `{capability_id, disposition, reason_code,
  affected_tools}`:
  - `disposition` is `held`, `quarantined` or `unavailable`;
  - `reason_code` is the coordinator's existing fixed-vocabulary reason for
    that decision;
  - `affected_tools` is the sorted list of Engineering static tools whose
    reviewed requirement table (`routes.STATIC_TOOL_CORE_REQUIREMENTS`) names
    the profile, or `[]`.

  The list is empty when every evaluated profile is compatible.
- **`withheld_capabilities_truncated`.** The list holds at most 64 entries
  (`MAX_CAPABILITIES`). This flag is `true` only when more exist. The counts
  always cover every profile.
- **`count_semantics`.** A fixed string: `withheld_count = held_count +
  quarantined_count + unavailable_count` (a total, not an additional set),
  and `compatible_count + withheld_count` is the number of capability
  profiles evaluated in `current_generation`.

The list and the counts must agree. If they ever disagree, the projection
fails with `core_authority_projection_inconsistent` rather than publishing a
contradiction. An assessment whose compatible and withheld decisions do not
cover every decision fails the same way.

On beta.43's live state, the block now reads:

| `capability_id` | `disposition` | `reason_code` | `affected_tools` |
|---|---|---|---|
| `core.config_entry_helper_options_operation` | `unavailable` | `positive_authority_missing` | `create_helper_options_plan` |
| `core.script_trace_read` | `unavailable` | `positive_authority_missing` | `get_script_trace`, `list_script_traces` |
| `core.typed_input_select_option_operation` | `unavailable` | `positive_authority_missing` | `create_input_select_option_plan` |

That is three blocked capabilities (26 + 3 = 29 evaluated). Each lacks
positive signed authority for Core 2026.10.3 in registry sequence 14. After
sequence 15 names them and the add-on restarts, the list is empty and
`compatible_count` is 29.

How to read the block: see
[`CORE_AUTHORITY_DIAGNOSTICS.md`](CORE_AUTHORITY_DIAGNOSTICS.md).

## Boundaries

- **Engineering identifiers only.** Every value is a profile id, one of the
  three disposition names, a coordinator reason code or an Engineering tool
  name. No Home Assistant text enters the list, and the final-envelope
  sanitizer still runs over the whole response.
- **Delegated reads are not listed.** `affected_tools` lists Engineering
  static tools only, because the client-visible names of delegated `ha-mcp`
  reads depend on the admitted upstream release. An empty list does not mean
  a profile affects nothing.
- **Nothing new is persisted or called.** The list derives from decisions the
  coordinator already published. Building it makes no Core, Supervisor or
  upstream call.
- **Bounded.** The whole projection stays within the existing
  `MAX_REPORT_BYTES` (32 768). With every runtime profile withheld (29), it
  is under 12 000 bytes.

## Compatibility

- Additive public health fields only. No existing health field is removed,
  renamed or changed.
- No admission decision, reason code, lease, registry handling, recovery,
  signing, routing, provider, policy, input schema, tool, catalog count,
  `list_capabilities` or `server_info` change.
- No persisted format, workflow, Dockerfile, dependency, Supervisor role,
  add-on option or stable-v1 change.

**Protected runtime files.** The HAMCP-242 task authorized this scoped
change:
- `ha_core_readmission/coordinator.py`: the projection fields, the
  consistency checks;
- `ha_core_readmission/routes.py`: `static_tools_requiring`, a read of the
  existing requirement table;
- the version.

## Rollback

Reinstall the 2.4.1-beta.43 artifact. No stored format changes.
