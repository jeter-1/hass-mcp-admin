# Engineering 2.4.1-beta.25 release notes

Engineering 2.4.1-beta.25 reads **script** configuration from what Home
Assistant has loaded, over the dependency index's existing single WebSocket
batch. It no longer makes one delegated ha-mcp read per script (HAMCP-212, fast
dependency index part 2). It applies Josh's decisions D3 (Home Assistant's API,
bounded requests) and D4 (no configuration-file read) to scripts, as beta.23
(HAMCP-209) did for automations.

Design, with the Part 1 verification against the pinned Core source:
[`HAMCP_212_LOADED_SCRIPT_CONFIG_DESIGN.md`](HAMCP_212_LOADED_SCRIPT_CONFIG_DESIGN.md).
Acceptance: [`V2_4_1_BETA25_ACCEPTANCE.md`](V2_4_1_BETA25_ACCEPTANCE.md).

beta.25 follows 2.4.1-beta.24 (HAMCP-130, `get_backup_inventory`) and includes
everything in it. It adds no dependency change: both hashed locks are
beta.24's.

No tool, input schema, description, signed registry, HOLD, support window,
workflow or stable-v1 change is included. The tool catalog and every public
descriptor are unchanged from beta.24.

**What was slow (beta.23 live).** The index build took 8.8 s. Automations took
1.19 s; the rest was the 5 script configurations, read one by one through
delegated `ha_config_get_script` (`delegated_script_configuration`: 5 requests,
9.3 s cumulative).

## Changes

1. **Loaded script configuration on the same batch.** For each available,
   identity-verified script, the build's one WebSocket batch (now
   `loaded_config_batch`, at most 16 in flight) adds two fixed, read-only
   commands:
   - `script/config {entity_id}` returns the in-memory `raw_config` (Core's
     pre-validation copy of the script's configuration);
   - `search/related {item_type: "script", item_id}` detects blueprint use.

   The WebSocket client's allowlist admits exactly these shapes. Unavailable
   and registry-only scripts are not commanded. The batch bound
   (`MAX_BATCH_COMMANDS`) rises from 2,048 to 4,096 so a build at the
   provider's limits (1,000 automations and 1,000 scripts, two commands each,
   4,000 in total) still fits; a batch over the bound is refused before
   dispatch and the whole build would read stored. At most 16 commands remain
   in flight.
2. **Stored read only where loaded is not equivalent, always counted.** In
   these cases the unchanged delegated reader is used and the reason recorded:

   | Reason | When | Partial |
   | --- | --- | --- |
   | `blueprint_instance` | Core reports the script's blueprint; loaded is the substituted body | no |
   | `blueprint_reference_nested` | Core's recursive search reports a blueprint through a targeted script | no |
   | `loaded_entity_unavailable` | the script is unavailable or registry-only | no |
   | `pending_write_reload` | a governed write has not been reloaded yet | no |
   | `loaded_source_unsupported` | the client has no batch read (the pre-beta.25 path) | no |
   | `loaded_config_unreadable` | the loaded read or search failed or was malformed, including a self-calling or cyclic script (Core's script search has no cycle guard) | **yes** |
   | `loaded_source_unavailable` | the whole batch failed | **yes** |

   Reasons appear in the script coverage `policy` and in the build profile. A
   partial reason adds `loaded_config_unavailable: N` to script coverage even
   when the stored read succeeds, so a failed loaded read is never reported as
   complete.
3. **Script pending-write journal.** The governed configuration-resource write
   records a script ID before dispatch, because Home Assistant reloads a
   script after the write response. Until loaded and stored agree, the stored
   form is analyzed. The journal works like beta.23's automation journal:
   - written but not loaded is `pending_write_unreconciled`;
   - a deleted script clears;
   - a rewrite during a build is retained;
   - at most 256 IDs, with a sticky overflow.
4. **Attribution.** Each script's evidence names its own source:
   - `direct_ha_api` for loaded scripts;
   - `upstream_read_gateway` for stored scripts.

   `script_call_graph.configuration_providers` lists the distinct providers.
5. **Profile fields (additive).** Health `last_build_profile` adds:
   - `script_config_source` (`loaded_websocket`, `stored_delegated`, `mixed`
     or `none`);
   - `script_config_source_counts`;
   - `script_stored_config_fallback_reasons`;
   - `script_loaded_config_unavailable`;
   - `script_pending_write_journal`;
   - `loaded_script_command_count`.

   `loaded_config_command_count` now includes script commands. The operation
   `loaded_automation_config_batch` is renamed `loaded_config_batch`.

## Measured effect (synthetic 113 automations + 5 scripts)

| Model | beta.24 | beta.25 |
| --- | --- | --- |
| Delegated reads serialized (reproduces live 8.8 s) | 9.80 s, 5 delegated reads | 0.51 s, 0 delegated reads |
| Delegated reads concurrent (optimistic) | 2.35 s | 0.49 s |

Script parity with beta.24 holds on two fixture sets:
- a representative set: plain, templated, dynamic, calling, blueprint,
  nested-blueprint, unavailable and categorized scripts;
- 113 automations with 5 scripts.

Both give identical analysis, automation index, script evidence and snapshot
fingerprint (`docs/evidence/hamcp212/`).

## Compatibility and rollback

- **Content.** Script evidence is identical for scripts in `scripts.yaml` that
  Home Assistant has loaded.
  - A package or YAML-only script previously returned `resource_not_found` on
    the stored route. It is now analyzed from its loaded configuration, which
    improves coverage.
  - Loaded configuration can contain resolved `!secret` values. Extraction and
    every output use the same strict sanitizer and registry-backed display
    allowlist as before. Tests plant secret-shaped values in loaded scripts and
    check every analysis family, plan response and saved plan file.
- **Fingerprints.** The script-diagnostic fingerprint and script-aware query
  fingerprints fold in per-script attribution, so they change once on upgrade.
  Existing script-aware cursors become stale and fail closed. The
  DependencyIndex snapshot fingerprint and helper plan evidence are unchanged.
- **Stored formats.** No stored format changes. The journal is in memory only.
- **Rollback** is reinstalling the beta.24 artifact. That returns to
  per-script delegated reads. No state needs migration.
- **Boundaries.** No write, service call, reload, `execute_script` or
  subscription becomes reachable. The stored fallback is the same admitted
  delegated reader. There is no direct-HA REST script read and no file read.
