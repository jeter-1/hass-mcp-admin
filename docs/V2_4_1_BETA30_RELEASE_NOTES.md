# Engineering 2.4.1-beta.30 release notes

Engineering 2.4.1-beta.30 adds one read-only tool,
`get_configuration_definitions` (HAMCP-132 slice 2, items B and D). For
YAML-defined template, SQL and helper entities it reports:
- how each entity is defined in `configuration.yaml`, as an allowlisted
  projection;
- how the definition was matched to the entity, with the strength of that
  match stated;
- why an entity is unavailable, from Core's read-only signals.

The tool cannot write, reload, create, call a service or open a flow.

Links:
- Design: [`HAMCP_132_DEFINITION_VISIBILITY_DESIGN.md`](HAMCP_132_DEFINITION_VISIBILITY_DESIGN.md)
  (draft PR #291), section 3 B and D, section 4 slice 2, and the owner
  decisions in section 8.
- Component review (Part 0):
  [`HAMCP_132_SLICE2_COMPONENT_REVIEW.md`](HAMCP_132_SLICE2_COMPONENT_REVIEW.md).
- Josh's decision of 2026-10-21 (section 11 of the review): the
  `ha_mcp_tools` custom component is not used, now or later.
- Acceptance: [`V2_4_1_BETA30_ACCEPTANCE.md`](V2_4_1_BETA30_ACCEPTANCE.md).

beta.30 follows these releases and includes everything in them:
- 2.4.1-beta.29 (HAMCP-213 PR 1, safety-critical acknowledgement);
- 2.4.1-beta.28 (HAMCP-215, Core authority recovery);
- 2.4.1-beta.27 (HAMCP-132 slice 1).

There is no dependency change: both hashed locks are beta.29's.

The catalog grows from 90 to **91** tools, or 92 with the optional YAML reader
admitted: 63 static plus 28 delegated. No existing tool, input schema,
description, signed registry entry, HOLD, support window, workflow, add-on
permission, stored format or stable-v1 file changes.

## `get_configuration_definitions`

Input: `entity_ids` (0 to 50 strict entity IDs, no duplicates) and
`integrations` (from a closed set of twelve, no duplicates). At least one must
be non-empty. The twelve integrations are `template`, `sql`, `filter`,
`derivative`, `statistics`, `group`, `integration`, `min_max`,
`history_stats`, `threshold`, `trend` and `utility_meter`. Arguments are
validated before the SDK can coerce them, a string is never split, and a
rejection never echoes the input.

### Sources

Each source is listed in `sources` with its own status and closed reason.

| Source | Read | What it gives |
| --- | --- | --- |
| `configuration_yaml:<path>` | admitted `ha_config_get_yaml` through the new `configuration_definitions` gateway purpose | definitions under `template`, `sql`, `group`, `utility_meter`, `sensor` and `binary_sensor`; only the paths the request needs |
| `configuration_yaml:homeassistant.packages` | the same reader; `homeassistant` only to classify a missing packages key | whether packages could hold more definitions |
| `states` | REST `GET /api/states` | whether a state exists and is `unavailable` or `unknown`; no value is copied |
| `config/entity_registry/list` | Core WebSocket | platform, unique ID, config entry and `disabled_by` |
| `entity/source` | Core WebSocket | the platform that created the entity |
| `config` | REST `GET /api/config` | the loaded `components` only |
| `config_entries/get:<domain>` | Core WebSocket, one closed domain each | UI entries' identity and state. Core never returns an entry's `data` or `options` here |
| `manifest/get:ha_mcp_tools` | Core WebSocket (Core's command, not the component's) | whether the component is installed, and its version |

### Definitions

Each definition has an ID such as `sql[0]`, `sensor[1]` or
`group.downstairs_lights`, and comes in one of four forms:
- an integration key (`template:`, `sql:`, `group:`, `utility_meter:`);
- a `sensor:` or `binary_sensor:` platform entry;
- a template blueprint block, reported by path only;
- a legacy platform entry.

Fields are a positive per-integration allowlist taken from Core 2026.10.1's
schemas (`schemas.py`). Unrecognized keys are counted, never copied.

What is always withheld:
- **`db_url`**, in every position: at the top level, nested, and inside
  lists.
- A `!secret` or `!env_var` reference, with its name.
- Any nested user-defined key that looks like a credential or connection key.
- A credential-bearing URI inside an allowlisted text field, such as a SQL
  query, which becomes `{"withheld": "credential_uri"}`. The shared sanitizer
  redacts userinfo only for `http(s)` URLs.

Each definition lists what was withheld in `withheld`.

What is disclosed but never followed:
- `!include` at any level, and packages. The report says each was not
  followed, and is partial.
- Labelled keys such as `template 2:`, which the YAML reader cannot address.

A legacy `platform: sql`, `template` or `utility_meter` entry under `sensor:`
creates no entity in this Core. It is flagged
`unsupported_legacy_platform` and is never an expected entity. Other
platforms are counted by name only.

### Matching

Each requested entity's `definition` gives a method:
- **`exact_config_entry`**: the registry row names a config entry of one of
  the twelve integrations. The definition is UI-defined and
  `definition_not_readable_without_flow` (owner decision (i)). The entry's
  identity and state are listed under `ui_entries`. Its free-form `reason`
  text is withheld; `reason_present` and a pattern-checked translation key
  are given instead.
- **`exact_unique_id`**: the registry's (domain, platform, unique ID) equals
  the unique ID Core derives from one YAML definition.
- **`heuristic`**: no registry row, and Core's naming rules applied to a
  static name, default name or key give this entity ID. Confidence is stated:
  - `high`: one candidate, its platform confirmed by `entity/source`, every
    YAML source complete and the registry read;
  - `medium`: the platform is unconfirmed or a source is incomplete;
  - `low`: the ID matched only after removing a collision suffix (`_2`).

  The slugify port covers ASCII text without `&`. Any other text derives no
  ID.
- **`ambiguous`**: several definitions derive the same ID or share the same
  unique ID. No candidate is chosen.
- **`unmatched`** or **`not_applicable`**, each with a reason.

A registered entity is never matched by name: a YAML definition without a
unique ID cannot create a registry row. Each definition also lists its
expected entities, with the same match vocabulary from the definition's side.

### Diagnosis

Each requested entity gets a `classification` with its `reasons`:

| Classification | Typical reasons |
| --- | --- |
| `available` | none |
| `disabled` | `registry_disabled:<by>`, `config_entry_disabled:<by>` |
| `deployment_dependency` | `config_entry_state:<state>`, `integration_not_loaded:<d>`, `platform_not_loaded:<p>.<d>`, `legacy_platform_unsupported` |
| `transient` | `config_entry_state:setup_retry` or an in-progress state |
| `dependency_unavailable` | `source_entity_missing:<id>`, `source_entity_unavailable:<id>` (5 of each at most) |
| `entity_level` | the one matched definition's dependencies are fine, but the entity is `unavailable` or `unknown` |
| `not_found` | `no_state`, `definition_absent` |
| `undetermined` | `source_failed:<source>`, `definition_not_matched` |

Each classification uses only the signals it names. If a source is missing,
or there is no single matched definition, the answer is `undetermined`, not
`entity_level`. `disabled_by` is a closed value; anything else is
`unrecognized`.

The `capability` block reports the `ha_mcp_tools` component from Core's own
reads only. Its `component_commands` is `not_used_owner_decision`. It
classifies:
- a missing manifest as `not_installed`;
- a disabled, unloaded or failed entry as a deployment dependency;
- a retrying or in-progress entry as `transient`;
- a permission failure as `permission`;
- a version before 1.1.0 as `upstream_feature_gap`.

A loaded component stays `undetermined`, because its cause is not observable
without the component's own commands.

### Completeness, bounds and sanitization

The report is `partial`, with each cause in `omissions`, when any of these
holds:
- a source is unavailable or refused;
- an include or package is not followed;
- a definition is not projected;
- a definition or UI-entry limit is reached;
- a response-size trim drops rows;
- a redaction occurs.

The tool fails with `sources_unavailable` only when every source is
unavailable or refused. Without the YAML reader admitted, the YAML sources
are `unavailable` (`yaml_reader_not_admitted`), no delegated call is made, and
the Core-side answers are still reported.

The bounds are:
- 200 definitions and 50 UI entries;
- 4,096 characters per text value;
- a 50,000-character report inside the 60,000-character envelope.

Over the budget, trailing definitions are dropped first, then UI entries, by
their sanitized size. The listed count follows the trim.

The complete envelope is assembled first: report, omissions, coverage and
every identifier. The strict shared sanitizer then runs once on it, with the
Home Assistant token and the access secret as known secrets. Nothing is added
afterwards.

## Boundaries

- **Reads only.** The reads are the eight above. An AST test pins the new
  package's literals, client calls, reader paths, imports and dispatch.
  - No `ha_mcp_tools/*` command, options flow, `.storage` file or Supervisor
    endpoint is reachable.
  - No new batch-allowlist shape is added.
- **YAML reader.** A new gateway purpose, `configuration_definitions`, fixes
  its own path set: `template`, `sql`, `group`, `utility_meter`, `sensor`,
  `binary_sensor`, `homeassistant.packages` and `homeassistant`. Its audit
  event is `configuration_definitions_yaml_read`.
  - The `recorder_settings` purpose keeps its three paths, so neither
    purpose can read the other's paths.
  - An unknown purpose returns no reader.
  - The reader's L1–L4 pipeline, Core gate and response seal are unchanged.
  - Authority is rechecked before and after every read, with no fallback.
- **Core gate.** `core.basic_rest_read`, `core.basic_websocket_read` and
  `core.non_device_registry_read` (HAMCP-164).
- **Audit.** Counts and source outcomes, never entity IDs or integration
  names.

## Corrections found during implementation

The implementer's own testing found and fixed these before the PR. They are
listed for the independent reviewer:
1. A database URL with userinfo inside an allowlisted text field, such as a
   SQL query, passed the projection. The final sanitizer does not cover
   non-HTTP userinfo. It is now withheld as `credential_uri`.
2. An unavailable entity with an ambiguous or unmatched definition was
   classified `entity_level`. It is now `undetermined` with
   `definition_not_matched`.
3. `YAML_FAILURE_CODES` was unused and inconsistent. It is now the shared
   reader codes plus `yaml_reader_not_admitted`.
4. Three problems in the service:
   - An unrecognized registry `disabled_by` was echoed.
   - The response-budget trim re-sanitized once per dropped row. That was
     quadratic, about 20 s of event loop for 210 definitions.
   - `coverage.definitions_listed` was stale after a trim.

## Compatibility and rollback

- **Additive.** One new tool; no existing descriptor or schema changes.
  `yaml_config_reader` keeps `recorder_settings` as its default purpose.
- **Stored formats.** No stored format changes.
- **Rollback.** Reinstall the beta.29 artifact. No state needs migration.
  beta.29's own rollback notes for plan records still apply to anything
  older.
- **Known limitations:**
  - Configuration comes from disk, as parsed by the YAML reader (YAML 1.2).
    It can differ from what Core loaded until a restart.
  - `!include` targets, package contents and labelled keys are not read.
  - UI-defined definitions cannot be read without a flow; only their entry
    identity and state are reported.
  - A heuristic match is evidence, not proof, and is labelled with its
    confidence. Non-ASCII names and names with `&` derive no ID.
  - With the component not used, a loaded `ha_mcp_tools` component's
    problems stay `undetermined`.
