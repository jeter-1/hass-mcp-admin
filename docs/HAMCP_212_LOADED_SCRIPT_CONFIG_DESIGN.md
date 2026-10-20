# HAMCP-212 Loaded Script Configuration: Design Note

Register: HAMCP-212 (fast dependency index, part 2; next beta). Parent:
HAMCP-209,
[`HAMCP_209_FAST_DEPENDENCY_INDEX_DESIGN.md`](HAMCP_209_FAST_DEPENDENCY_INDEX_DESIGN.md)
section 3.3 ("Moving [scripts] to `script/config` is a provider-routing
change"). Owner decisions in force:

- D3: read the loaded configuration through Home Assistant's API with bounded
  requests.
- D4: no direct read of any configuration file.
- Truthfulness must not get worse.
- The registry-backed display allowlist applies to script-derived values too.
- No new permissions.

Base: `origin/main` `f69bf8cca4e4d6a1af9c4fdfd95476f62d7c601b` (2.4.1-beta.24).

## 1. Problem

Live beta.23 index build: 8.8 s. Automations took 1.19 s (113 loaded over one
WebSocket connection, 226 commands, 0 stored fallbacks). Almost all of the
remainder was the 5 script configurations read one by one through delegated
ha-mcp `ha_config_get_script`: `delegated_script_configuration` made 5
requests for 9.3 s cumulative.

## 2. Part 1: verification against pinned Core source (ADR-021)

Authority: `ci/support-window.yaml` (current `2026.10.1`, rollback `2026.10.0`,
candidate none). The pins are unchanged since HAMCP-209.

| Lane | Tag | Commit |
| --- | --- | --- |
| current | `2026.10.1` | `abe1fdd7cd6b4de1715c87b6fd87d92699514ef9` |
| rollback | `2026.10.0` | `6a811d3359c7b2076dc9e1cf900843a129c044af` |

Every file cited is byte-identical between the two tags (`script/__init__.py`,
`script/config.py`, `config/script.py`, `search/__init__.py`). Paths are
relative to `homeassistant/components/`.

### 2.1 The command

- **WebSocket `script/config`** (`script/__init__.py:784-807`, registered at
  `:282`):
  - takes `{"type": "script/config", "entity_id": str}` and is `@require_admin`;
  - returns `{"config": script.raw_config}` from the in-memory entity, with no
    lock and no file read;
  - returns `ERR_NOT_FOUND` when no script entity is loaded for the id.

  The command exists, so the STOP condition does not apply.
- **No list-all command exists.** Like `automation/config`, it is one command per
  entity. The bounded route reuses the HAMCP-209 batch: one authenticated
  connection per build, fixed command shapes, at most 16 commands in flight.
- **Authority.** Engineering's administrative token already dispatches the
  identically shaped `automation/config` (HAMCP-209) and `search/related` with
  `item_type: automation`. `script/config` and `search/related` with
  `item_type: script` are read-only. No write, service call, reload,
  `execute_script` or subscription becomes reachable. The batch allowlist
  (`clients/websocket.py batch_command_allowed`) admits exactly these two
  shapes with the key set fixed and the identity matched by
  `script\.[a-z0-9_]{1,121}\Z`.

### 2.2 Where loaded differs from stored

| Case | Core source | Loaded content | HAMCP-212 handling |
| --- | --- | --- | --- |
| Plain, templated or dynamic script | `script/config.py _async_validate_config_item` `:118` keeps the pre-validation `dict(config)` as `raw_config` (the validated config is held separately, `:220-232`); `_create_script_entities` `:348-353` passes it to the entity | Same keys and values as `scripts.yaml`; templates stay unrendered | Loaded; parity proven |
| Blueprint instance | `referenced_blueprint` `:503`, `:632-638`; `raw_config` is the **substituted** body | The expanded sequence, not the `use_blueprint` input form | `search/related` reports `script_blueprint`, so the stored read is used: `blueprint_instance` |
| Script that targets a blueprint script | `search/__init__.py _async_search_script` `:556-615` recurses into referenced scripts | Its own config, but the search reports the nested script's blueprint | Stored read: `blueprint_reference_nested`. This is conservative; the config itself is plain |
| Validation failure | `UnavailableScriptEntity` `:460-480`, state `unavailable`, `raw_config` is the original | Possibly invalid; no blueprint reported | Not commanded; stored read: `loaded_entity_unavailable` |
| Registry-only or disabled script | No entity loaded | `ERR_NOT_FOUND` | Not commanded; stored read: `loaded_entity_unavailable` |
| Package or YAML-only script (no `scripts.yaml` entry) | Loaded from configuration packages | Loaded config; `!secret` values are **resolved** | Loaded; the stored route alone would report `resource_not_found` |
| Governed write before reload | `config/script.py` reloads after the POST response | Stale until the reload lands | Pending-write journal (section 3.3) |
| Self or cyclic script reference | `_async_search_script` `:613-614` recurses with no cycle guard until `RecursionError`; `websocket_api/connection.py:249-252` returns it as `unknown_error` | A per-command search error (`script/config` itself succeeds) | `loaded_config_unreadable`, therefore partial (disclosed limitation); `test_cyclic_script_search_error_is_partial` |
| Registry category | Not part of `raw_config` | Absent | ha-mcp 8.5.0 `ha_config_get_script` (`tools_config_scripts.py:117`, commit `311d6dc`) injects `category` into the stored form; `_comparable_stored` ignores it for the journal comparison |

Blueprint helpers: `blueprint_in_script` `:196-207` and `scripts_with_blueprint`
`:181-193` confirm the search result is the authoritative blueprint signal.

## 3. Design

### 3.1 Source selection, per build

1. **Candidates.** `select_script_candidates` keeps the existing registry-backed
   identity proof (canonical `unique_id`, sanitizer refusal, conflicting
   identities withheld). Only candidates with a loaded state that is not
   `unavailable` and that pass the allowlist get batch commands.
2. **Batch.** The provider appends `script/config` and `search/related` for each
   such candidate after the automation commands in the same `command_batch`
   (operation `loaded_config_batch`, renamed from
   `loaded_automation_config_batch`). The batch also runs when there are scripts
   but no automations.
   - If the client has no `command_batch`, every script reads stored
     (`loaded_source_unsupported`), which is the pre-HAMCP-212 path.
   - If the whole batch fails or returns the wrong number of outcomes, both
     outcome maps are cleared and every script reads stored
     (`loaded_source_unavailable`).
3. **Route.** For each candidate, `loaded_script_route`:
   - returns the loaded config when both outcomes are well-formed and no
     blueprint is involved;
   - otherwise returns a closed fallback reason.
4. **Stored fallback.** The existing delegated reader (8 workers, same 60 s
   phase deadline, same authority checks) is used only for the fallback reasons.

### 3.2 Fallback accounting

Each stored read is counted with exactly one reason in
`script_stored_config_fallback_reasons` and in the coverage policy text
(`script_config_source=X; loaded=N; stored=M; stored fallback reasons: ...`).
The vocabulary is closed (`SCRIPT_LOADED_FALLBACK_REASONS`):

| Reason | Partial? | Why |
| --- | --- | --- |
| `blueprint_instance`, `blueprint_reference_nested` | no | Stable, expected; the stored input form is the analyzed source, as before |
| `loaded_entity_unavailable` | no | Core has no usable loaded config |
| `loaded_source_unsupported` | no | No batch client; pre-HAMCP-212 behavior |
| `pending_write_reload` | no | The journaled write is read from stored, which is authoritative until reload |
| `loaded_config_unreadable` | **yes** | A per-command error, malformed config or malformed search |
| `loaded_source_unavailable` | **yes** | The whole batch failed |

A partial reason adds `loaded_config_unavailable: N` to the script warnings even
when the stored read succeeds, so a failed loaded read is never reported as
complete. When the stored read fails too, its failure category is counted as
before.

### 3.3 Pending-write journal (scripts)

`DirectHaDependencyProvider.note_script_write` keeps a sequence-numbered
journal (at most `MAX_PENDING_SCRIPT_WRITES = 256`; overflow is sticky).

- **Write hook.** `governance/resources.py _write_rest_configuration` journals a
  script write before the POST, after the path check. An invalid identifier is
  neither journaled nor dispatched.
- **Pending candidate.** A journaled candidate is read from stored and compared
  with loaded (ignoring the injected `category`).
  - Equal: the entry is verified, cleared, and the script is analyzed from
    loaded.
  - Different: the script is analyzed from stored (`pending_write_reload`).
- **Pending non-candidate.** A journaled script that is not a candidate is
  read from stored.
  - 404 (deleted): the entry clears.
  - Present but not loaded: `pending_write_unreconciled`.
- **Settling.** `_settle_pending_script_writes` clears only entries whose
  sequence is unchanged since the build snapshot, so a rewrite during a build is
  retained.
- **Overflow.** Every candidate reads stored, and the build stays
  `pending_write_unreconciled`. A package script with no stored form keeps its
  loaded config.

### 3.4 Health and attribution

`last_build_profile` (`DependencyIndex.health`) carries:

- `script_config_source` (`loaded_websocket`, `stored_delegated`, `mixed`,
  `none`);
- `script_config_source_counts`;
- `script_stored_config_fallback_reasons`;
- `script_loaded_config_unavailable`;
- `script_pending_write_journal` (`pending_at_build`, `overflow_at_build`,
  `unreconciled`, `verified_cleared`, `overflow_write_count`);
- `loaded_script_command_count`.

`loaded_config_command_count` now includes script commands.

Attribution:

- Loaded scripts are attributed to `direct_ha_api` and stored scripts to
  `upstream_read_gateway`, per script (`ScriptDiagnostics.provider_for`).
- `script_call_graph.path_evidence[].provider` names each script's own source.
- `configuration_providers` lists the distinct providers.
- A mixed build's coverage provider is the joined
  `direct_ha_api+upstream_read_gateway`.

### 3.5 Protected-file list (final)

Authorized by the brief (dependency modules and the WebSocket client):

- `hass_mcp_engineering_beta/ha_mcp_engineering/clients/websocket.py`:
  - the allowlist (two more fixed shapes);
  - the batch bound `MAX_BATCH_COMMANDS`, raised from 2,048 to 4,096. Each
    script adds two commands, like each automation, so a build at the
    provider's limits (`MAX_AUTOMATION_SOURCES` 1,000 and
    `MAX_SCRIPT_SOURCES` 1,000) needs up to 4,000 commands. At 2,048, such a
    home would exceed the bound and lose the loaded route for the whole build
    (`loaded_source_unavailable`). The bound is still a fixed refusal limit
    checked before dispatch; the in-flight window (`BATCH_WINDOW` 16) is
    unchanged. `test_delegated_fallback_reads_stay_bounded` asserts the bound
    covers both limits, and HAMCP-209's refusal test still rejects
    `MAX_BATCH_COMMANDS + 1` payloads before dispatch.
- `dependency/provider.py`: batch assembly, journal and profile.
- `dependency/runtime.py`: `note_script_write`.
- `dependency/script_sources.py`: routing, accounting, attribution.
- `dependency/service.py`: per-script provider attribution.

Reported first and authorized by Josh:

- `hass_mcp_engineering_beta/ha_mcp_engineering/governance/resources.py`: one
  pre-dispatch `note_script_write` call.

Release files are changed only through `promote_next_release.py --apply`.

Not changed:

- `dependency/index.py` (health already copies the build profile);
- the delegated reader and its admission;
- `governance/service.py`, whose automation-only write path is unchanged;
- the signed registry, workflows, the support window and stable v1.

## 4. Security

- **No new reachability.** The batch admits two more fixed read-only shapes.
  The refusal tests (`ScriptBatchAllowlistTests`) cover:
  - extra keys, `id` and the wrong identity domain;
  - uppercase, overlong and newline identities;
  - list identities and `search/related` for other item types;
  - `call_service` `script.reload` and `script.turn_on`;
  - `execute_script`, `config/script/delete` and `script/trace/list`.

  Mutation S7 (open identity pattern) is killed.
- **No fallback widening.** Stored fallback uses the same admitted delegated
  reader and catalog/Core/upstream authority checks as before. There is no
  direct-HA REST script read and no file read.
- **Resolved secrets.** A package script's loaded config contains resolved
  `!secret` values, which the stored route never returned. Extraction and every
  output pass through the same strict sanitizer as before.
  `ScriptResolvedSecretDisclosureTests` builds planted values at runtime and
  covers:
  - JWT, bearer, password and API-key data;
  - URL userinfo and query tokens, webhook ids and headers;
  - template text, label templates, target device and label ids;
  - data keys, variables, field defaults and aliases.

  The planted values appear in two scripts: a package script, and a script whose
  stored form holds only the reference. The test runs dependency, impact,
  integrity, incident, handoff (structured and markdown), helper risk and health
  at evidence detail. It also creates and saves a helper-state plan
  (`input_boolean.beta37_exact_action`) and checks the response and every
  persisted plan file. No marker appears anywhere. Both tests assert that the
  scripts were actually analyzed from loaded config, so the absence of a marker
  is not vacuous.
- **Display allowlist.** Script-derived values reach saved plans only through
  the existing registry-backed helper risk path. The plan test extends planted
  label ids and transitions into loaded script sequences.

## 5. Tests, parity and mutations

- `tests/test_hamcp212_loaded_script_config.py` (43 tests: the 42 reviewed at
  `180b1d3` plus the cyclic-search test):
  - parity;
  - bounded requests (one connection, 236 commands for 113 + 5, 0 delegated
    reads, fixed shapes, unavailable and registry-only scripts not commanded,
    a scripts-only batch, no reader means no script command);
  - fallback accounting for each reason;
  - partial on failure (batch failure, short batch, per-command error, malformed
    config or search, both sources failing, a self-calling or cyclic script's
    search error);
  - analysis disclosure;
  - journal (until reload, verified, written-not-loaded, deleted, unreadable,
    rewrite during build, overflow sticky, overflow package);
  - runtime and the resources hook;
  - allowlist;
  - health profile;
  - attribution;
  - planted secrets.
- `tests/hamcp212_fixtures.py` extends the HAMCP-209 synthetic model with
  loaded and stored scripts. Core's recursive script search is modelled as
  Core implements it: no cycle guard (a cycle is a per-command
  `unknown_error`), and an unavailable or unloaded nested script references
  nothing.
- Cross-tree parity (`docs/evidence/hamcp212/`): base `f69bf8c` (every script
  read delegated) and head are **IDENTICAL** for the representative and
  113 + 5 fixtures, covering the analysis, automation index, script view and
  snapshot fingerprint. The script fingerprint and query fingerprint change by
  design (they fold in per-document provider attribution).
- Mutations (`docs/evidence/hamcp212-mutations/`): 12 of 12 killed.

  | Mutation | Killed by |
  | --- | --- |
  | S1: per-script delegated loop restored (brief a) | request-count test |
  | S2a: failed loaded read reported complete (brief b) | fallback-accounting test |
  | S2b: batch failure reported as unsupported (brief b) | analysis-disclosure test |
  | S3: silent fallback | |
  | S4: journal ignored | |
  | S5: blueprint search ignored | |
  | S6: unreconciled write hidden | |
  | S7: open allowlist | |
  | S8: unavailable script batched | |
  | S9: write hook removed | |
  | S10: settle ignoring the rewrite sequence | |
  | S11: attribution forced | |

- `tests/test_hamcp191_shared_sanitizer.py`: the one script sanitizer call moved
  from `collect_script_diagnostics` to `select_script_candidates`. It is the same
  single identity check, so only the committed table key changed.

## 6. Measurements

Synthetic (`docs/evidence/hamcp212/measurement.json`). Each delegated stored
read costs 1.86 s (the beta.23 live average); each WebSocket command costs
0.5 ms.

| Model | beta.24 | HAMCP-212 |
| --- | --- | --- |
| Serialized delegated reads (matches live 8.8 s) | 9800.2 ms; 5 delegated reads | 505.5 ms; 0 delegated reads |
| Concurrent (optimistic) | 2348.7 ms | 488.5 ms |

Projected live effect: the index build falls from about 8.8 s to about the
automation phase (about 1.2 s) plus 10 more in-memory commands. The live
acceptance check records the real number.

## 7. Limitations

- A whole-batch failure affects automations and scripts together (shared
  connection), and both report it.
- Cyclic script references make the search fail (Core has no cycle guard).
  Every script that reaches the cycle, including a self-calling script, is
  `loaded_config_unreadable` and partial, while its stored evidence is still
  reported.
- A `blueprint_reference_nested` script reads stored even though its own config
  is plain (conservative).
- Script identity is still bound through the registry `unique_id`. Discovery
  cannot enumerate every package script, so script coverage stays `partial`.
- `authority_current` for cached script evidence still follows the delegated
  reader's authority. If no reader is configured, no script is read (no loaded
  shortcut).
- When both the loaded and stored reads fail, the stored failure category is
  counted and the script is not reported.
- The script and query fingerprints change once on upgrade, so existing
  script-aware cursors become stale.
- `scripts/real_ha_contract_tests.py`'s capture has no batch client, so its
  real-HA script check keeps exercising the stored route.
