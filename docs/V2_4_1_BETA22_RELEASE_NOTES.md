# Engineering 2.4.1-beta.22 release notes

Engineering 2.4.1-beta.22 builds the dependency index from the automation
configuration Home Assistant has **loaded**. It no longer makes one serialized
REST fetch per automation (HAMCP-209, break-fix). This applies Josh's
HAMCP-171 decisions D3 (prefer Home Assistant's API for the loaded
configuration, in a small bounded number of requests) and D4 (no direct read
of `automations.yaml` or any configuration file).

The design, with the Part 1 verification against the pinned Core source, is
in [`HAMCP_209_FAST_DEPENDENCY_INDEX_DESIGN.md`](HAMCP_209_FAST_DEPENDENCY_INDEX_DESIGN.md).
Acceptance: [`V2_4_1_BETA22_ACCEPTANCE.md`](V2_4_1_BETA22_ACCEPTANCE.md).

No tool, input schema, description, provider route, signed registry, HOLD,
support window, workflow or stable-v1 change is included. The tool catalog
and every public descriptor are unchanged from beta.21.

**What was wrong (beta.16–beta.19 live).**
- Index builds took 33.7–41.5 s on 113 automations.
- The provider read each automation through
  `GET /api/config/automation/config/{id}`. Core serializes that route on one
  lock and re-parses the whole `automations.yaml` for every request
  (`config/view.py:86-97`), so total cost grows with the square of the
  automation count. The average queue wait was about 15.6 s, with a maximum of
  about 33 s.
- Writes right after a restart took 35–44 s, because the helper-dependency
  preflight joined the startup prewarm build.

## Changes

1. **Loaded configuration over one WebSocket connection.** Each build opens
   one authenticated connection. It pipelines two fixed, read-only commands
   per automation, with at most 16 in flight:
   - `automation/config {entity_id}` returns the in-memory `raw_config`, with
     no lock and no file read;
   - `search/related {item_type: "automation", item_id}` detects blueprint
     instances.

   Core 2026.10.x has no list-all command, so this is the smallest bounded
   form. The command set is enforced inside the WebSocket client. Any other
   type or shape is refused before connecting, and Core dispatch authority is
   re-checked before every message.
2. **Stored read only where the loaded form is not equivalent, always
   reported.** In these cases an automation is read through the unchanged
   typed REST `Route`, and the reason is recorded:

   | Reason | When |
   | --- | --- |
   | `blueprint_instance` | the loaded form is the substituted configuration, not `use_blueprint` |
   | `loaded_entity_unavailable` | the automation failed validation |
   | `loaded_config_unreadable` | the loaded read failed or was malformed |
   | `loaded_identity_mismatch` | the loaded ID differs from the state's ID |
   | `pending_write_reload` | a governed write has not been reloaded yet |
   | `loaded_source_unavailable` | the whole WebSocket batch failed |

   The reasons appear in the automation coverage `policy` string and in the
   build profile. A whole-source failure also adds a coverage warning. No
   fallback is silent.
3. **Pending-write journal.** Both governed automation write boundaries record
   the automation ID before dispatch. This is needed because Home Assistant
   reloads an automation after the write response.

   Until the loaded and stored forms agree, the index uses the stored form, as
   before. A written automation that Home Assistant has not loaded is the
   coverage failure `automation_pending_reload_not_loaded`, and the index is
   partial. The journal holds at most 256 IDs. On overflow, the index compares
   every automation and reports `pending_write_journal_overflow` until a clean
   build.
4. **Profile fields (additive).** Health `last_build_profile` adds:
   - `automation_config_source` (`loaded_websocket`, `stored_rest`, `mixed`
     or `none`);
   - `automation_config_source_counts`;
   - `stored_config_fallback_reasons`;
   - `loaded_config_connection_count`;
   - `loaded_config_command_count`;
   - `pending_write_journal`.

   Existing fields (`request_count`, `request_count_by_operation`,
   `build_wall_clock_ms`, queue-wait fields, coverage) keep their meaning.

TTLs (600 s soft, 3600 s hard), governance invalidation and Core-authority
invalidation are unchanged. There is no reload-event subscription and no
incremental hashing; the design note gives the reasons.

## Measured effect (synthetic 113-automation model)

| | beta.19 base | beta.22 |
| --- | --- | --- |
| Build wall time | 34.8 s | 0.42 s |
| Provider requests | 116 | 4 |
| Per-automation REST reads | 113 | 0 |
| Max request queue wait | 31.9 s | 0 |
| Automation write issued during the build | 2.7 s | 0.32 s (idle: 0.30 s) |
| Index reader during the build | 34.5 s | 0.32 s |

The model reproduces the live beta.19 figures. The live check is in the
acceptance document. Extraction parity with beta.19 is proven on a
representative fixture set and a 113-automation set. Both give identical index
views and snapshot fingerprints.

## Compatibility and rollback

- **Index content.** The index is identical to beta.19 for automations stored
  in `automations.yaml` that Home Assistant has loaded.
  - An automation defined outside `automations.yaml` (packages,
    `configuration.yaml`) returned 404 on the stored route and was reported
    unreadable. It is now indexed from its loaded configuration. Coverage
    improves for these automations.
  - Because the loaded form can include resolved `!secret` values, bounded
    `detail_level=evidence` excerpts pass through the existing sanitizer. See
    the design note's security section.
  - Out-of-band edits that Home Assistant has not reloaded are reported as
    loaded, which is the configuration that runs.
- **Stored formats.** No stored format changes. The journal is in memory only.
- **Rollback** is reinstalling the beta.21 artifact, which returns to the
  per-automation REST reads and their slow builds. No state needs migration.
- **HAMCP-200.** Every REST call still goes through the typed `Route`. The two
  WebSocket commands are fixed and read-only. No write, service call, reload
  or subscription becomes reachable.
