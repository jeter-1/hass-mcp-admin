# Engineering 2.4.1-beta.27 release notes

Engineering 2.4.1-beta.27 adds two read-only tools (HAMCP-132 slice 1,
definition visibility):
- `get_recorder_settings` reports the recorder's effective settings and where
  each one comes from;
- `get_unregistered_entities` lists entities that have a state but no
  registry entry.

Neither tool can write, purge, reload or call a service.

Design and Part 1 verification against the pinned Core 2026.10.1 source:
[`HAMCP_132_DEFINITION_VISIBILITY_DESIGN.md`](HAMCP_132_DEFINITION_VISIBILITY_DESIGN.md)
(draft PR #291). Implementation notes and the deviations from the design:
[`HAMCP_132_SLICE1_IMPLEMENTATION_NOTES.md`](HAMCP_132_SLICE1_IMPLEMENTATION_NOTES.md).
Acceptance: [`V2_4_1_BETA27_ACCEPTANCE.md`](V2_4_1_BETA27_ACCEPTANCE.md).

beta.27 follows 2.4.1-beta.26 (HAMCP-217, certifi 2026.10.19), which follows
2.4.1-beta.25 (HAMCP-212), and includes everything in both. It adds no
dependency change; both hashed locks are beta.26's.

The catalog grows from 88 to **90** tools (91 with the optional YAML reader
admitted): 62 static plus 28 delegated. No existing tool, input schema,
description, signed registry entry, HOLD, support window, workflow, add-on
permission or stable-v1 file changes.

## `get_recorder_settings`

Input: optional `entity_ids`, 0 to 50 strict entity IDs (owner decision (iv)).

It answers "what will the recorder keep, and why?" from four sources, each
listed in `sources` with its own status and reason:

| Source | Read | What it gives |
| --- | --- | --- |
| `configuration_yaml:recorder` | admitted `ha_config_get_yaml`, `yaml_path: recorder` | written settings and filters |
| `configuration_yaml:homeassistant.packages` | the same reader, `homeassistant.packages`, and `homeassistant` only to classify a missing path | whether packages could add recorder keys |
| `recorder/info` | Core WebSocket, its own batch | live recording state, backlog, migration, database location |
| `recorder/entity_options/get` | Core WebSocket batch, one command per entity (admin, read-only) | Core's own "is this entity recorded" answer |

- **Settings.** Each scalar setting has an `origin`:
  - `configured` means written in `configuration.yaml`, after Core's own
    validators;
  - `core_default` means Core 2026.10.1's schema default, labelled with
    `pin`, and is used only when every source that could set the key is
    complete;
  - `unknown` carries a reason: `packages_present`, `packages_unreadable`,
    `include_not_followed`, `yaml_truncated` or `yaml_unavailable` (owner
    decision (v)).

  Deprecated settings appear only when written. The YAML reader parses
  YAML 1.2, while Home Assistant loads YAML 1.1, and the reader's value
  cannot show how a value was spelled. When the YAML 1.1 readings of the
  same text would change Core's verdict, the setting is
  `validity: parser_dependent` with each reading and `value: null`. Examples
  are unquoted `off` or `yes`, sexagesimal `1:20` and an exponent float.
  Only a value that fails Core's schema under every reading is `invalid`
  with `fails_core_schema`. It is never replaced by the default. Under the
  owner decisions of 2026-10-20:
  - a plain integer such as `10`, `14`, `30` or `365` is definite.
    `coverage.yaml_numeric_spelling` always says that the spelling is not
    observed, and that a leading-zero integer such as `010` would be octal
    in YAML 1.1;
  - a string the reader could only have returned because it was quoted
    (`"010"`, `"true"`, `"1.5"`) is a definite string under both versions;
  - `y` and `n` are plain strings, because PyYAML's bool resolver does not
    include them.
- **`db_url` is never returned.** The report gives only
  `value_withheld: true`, whether it is configured, and `recorder/info`'s
  `default_location`. A `!secret` or `!env_var` reference is withheld,
  including its name. Unrecognized recorder keys are counted, never copied.
- **Filters.** The `include` and `exclude` lists are reported with
  `filter_case`, using Core's case numbering: `1`, `2`, `3`, `4a`, `4b` or
  `6`. In case 4a, Core ignores `exclude.domains`, and the report sets
  `exclude_domains_ignored: true`. An `exclude.event_types` entry for
  `state_changed` is flagged with `state_changed_event_type_ignored`.
- **Runtime.** `recording`, `thread_running`, `backlog`, `max_backlog`,
  migration flags and `db_in_default_location`. `disable_service_state` is
  `not_observable`. If `recorder/info` returns `unknown_command`, the
  recorder is not set up: runtime is `unavailable` with
  `recorder_not_set_up`, never "not recording".
- **Entities.** For each requested entity the report gives:
  - Core's answer (`core_recorded`);
  - the filter computed from the written configuration (`computed_recorded`);
  - `agreement`.

  A disagreement is `filter_drift` and is never resolved silently; it means
  the configuration on disk and the running recorder differ, for example
  before a restart.
- **Completeness.** Any source that is not complete, any unknown or
  parser-dependent setting, drift, redaction or response-size trim makes the
  report `partial`, with each cause in `omissions`. The tool fails only when
  every source is unavailable.
- **Sanitization.** Both tools build the complete response first, including
  omissions and every identifier. They then run the strict sanitizer, with
  the known secrets, once on that final envelope. Nothing is added after
  that call, and any redaction makes the report partial.

Without the optional YAML reader admitted, the tool still runs. The YAML
sources are then `unavailable` with `yaml_reader_not_admitted`, settings and
filters are `unknown` (`yaml_unavailable`), and the report is partial. Runtime
and Core's entity answers are still reported.

## `get_unregistered_entities`

Input: optional `domains` (1 to 50) and `limit` (1 to 500, default 200).

It reports each entity that has a current state but no entity registry entry:
- REST `GET /api/states`, minus `config/entity_registry/list`;
- `entity/source` gives the integration platform;
- batched `search/related` (`item_type: entity`) gives the config entry, only
  for entities that have a platform.

Per entity it reports:
- `platform`, with `platform_status`. `directly_written_state` means no
  integration added the entity, so its state was written directly.
- `config_entry_id`, with `config_entry_status`: `resolved`, `none_related`,
  `ambiguous` (more than one entry; never guessed), `not_applicable`,
  `command_failed` or `invalid_response`.
- `definition_location: not_determinable`. Core records no file or package
  for a registry-less entity, and no candidate is offered.

`counts` gives totals for states, registry entries, unregistered entities, the
in-scope subset, listed rows and directly written states. A limit or
response-size truncation sets `truncated` and makes the report partial. If
the states or registry read fails, the tool fails with `sources_unavailable`;
a partial difference is never reported.

## Boundaries

- **Reads only.** The commands are:
  - REST `GET /api/states`;
  - WebSocket `recorder/info`, `config/entity_registry/list` and
    `entity/source`;
  - in the pipelined batch, `recorder/entity_options/get` (exact keys, strict
    entity ID) and `search/related` with `item_type: entity`;
  - the admitted YAML reader for three fixed paths.

  An AST test pins the new modules to these reads.
- **Allowlist.** The batch allowlist gains exactly two shapes:
  - `recorder/entity_options/get`;
  - `search/related` for `item_type: entity`.

  `recorder/info` is admitted only with no keys besides `type`.
- **Core gate.** `get_recorder_settings` requires `core.basic_rest_read` and
  `core.basic_websocket_read`. `get_unregistered_entities` also requires
  `core.non_device_registry_read`.
- **Arguments.** Arguments are validated before the SDK can coerce them. A
  string is never split into a list of characters.
- **Audit.** The audit records counts and source outcomes, never entity IDs.

## Compatibility and rollback

- **Additive.** Two new tools; no existing descriptor or schema changes.
- **Stored formats.** No stored format changes.
- **Rollback.** Reinstall the beta.26 artifact; no state needs migration.
- **Known limitations** (design section 9):
  - the `recorder.disable` state is not observable;
  - configured values come from disk and can differ from the running recorder
    until a restart, so the `filter_drift` cross-check exists;
  - `!include` targets and package contents are not read;
  - YAML 1.2 parsing can differ from Home Assistant's YAML 1.1. A value
    where this can change Core's verdict is reported `parser_dependent`,
    never invalid. A plain integer is reported as the reader parsed it, so
    `purge_keep_days: 10` is definite even though it may have been written
    `010`, which YAML 1.1 reads as 8. A `0o`-prefixed integer cannot be
    detected either. `coverage.yaml_numeric_spelling` discloses both;
  - registry-less entities cannot be traced to a file.
