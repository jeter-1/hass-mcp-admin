# Engineering 2.4.1-beta.23 release notes

Engineering 2.4.1-beta.23 builds the dependency index from the automation
configuration Home Assistant has **loaded**. It no longer makes one serialized
REST fetch per automation (HAMCP-209, break-fix). This applies Josh's
HAMCP-171 decisions D3 (prefer Home Assistant's API for the loaded
configuration, in a small bounded number of requests) and D4 (no direct read
of `automations.yaml` or any configuration file).

The design, with the Part 1 verification against the pinned Core source, is
in [`HAMCP_209_FAST_DEPENDENCY_INDEX_DESIGN.md`](HAMCP_209_FAST_DEPENDENCY_INDEX_DESIGN.md).
Acceptance: [`V2_4_1_BETA23_ACCEPTANCE.md`](V2_4_1_BETA23_ACCEPTANCE.md).

beta.23 follows 2.4.1-beta.22 (HAMCP-211, the aiohttp 3.14.4 security
release) and includes everything in it, including HAMCP-205's administrator
verification. It adds no dependency change: both hashed locks are beta.22's.

No tool, input schema, description, provider route, signed registry, HOLD,
support window, workflow or stable-v1 change is included. The tool catalog
and every public descriptor are unchanged from beta.22.

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
   | `loaded_source_unsupported` | the client has no batch read (test clients only; the stored-only path, complete) |

   The reasons appear in the automation coverage `policy` string and in the
   build profile. No fallback is silent.

   For `loaded_config_unreadable`, `loaded_identity_mismatch` and
   `loaded_source_unavailable` the running configuration could not be read,
   so a stored read does not prove what is loaded. Each such automation is
   the coverage failure `loaded_config_unavailable`, the index is partial,
   and health reports `automation_partial_reasons` and the count.
   `blueprint_instance` and `loaded_entity_unavailable` stay complete.
3. **Pending-write journal.** Both governed automation write boundaries record
   the automation ID before dispatch. This is needed because Home Assistant
   reloads an automation after the write response.

   Until the loaded and stored forms agree, the index uses the stored form, as
   before. A journaled write that a build cannot account for (not loaded yet,
   or its stored read failed) stays journaled, and every build is partial
   with the coverage failure `pending_write_unreconciled` until a build
   accounts for it. The journal holds at most 256 IDs. A write beyond that
   sets an overflow flag and count that no build clears: every build
   compares every automation and is partial with `pending_write_unreconciled`
   until the add-on restarts.
4. **Profile fields (additive).** Health `last_build_profile` adds:
   - `automation_config_source` (`loaded_websocket`, `stored_rest`, `mixed`
     or `none`);
   - `automation_config_source_counts`;
   - `stored_config_fallback_reasons`;
   - `loaded_config_connection_count`;
   - `loaded_config_command_count`;
   - `pending_write_journal` (with `overflow_write_count` and `unreconciled`);
   - `automation_partial_reasons` and `loaded_config_unavailable`.

   Existing fields (`request_count`, `request_count_by_operation`,
   `build_wall_clock_ms`, queue-wait fields, coverage) keep their meaning.

TTLs (600 s soft, 3600 s hard), governance invalidation and Core-authority
invalidation are unchanged. There is no reload-event subscription and no
incremental hashing; the design note gives the reasons.

## Measured effect (synthetic 113-automation model)

| | beta.19 base | beta.23 |
| --- | --- | --- |
| Build wall time | 34.6 s | 0.45 s |
| Provider requests | 116 | 5 |
| Per-automation REST reads | 113 | 0 |
| Max request queue wait | 31.9 s | 0 |
| Automation write issued during the build | 2.7 s | 0.32 s (idle: 0.30 s) |
| Index reader during the build | 34.5 s | 0.35 s |

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
  - The loaded form can include resolved `!secret` values. The index keeps
    no configuration excerpt, and its identity fields and label selectors pass
    through the existing sanitizer. The helper and entity-metadata effect
    projection (domains, services, targets and data values) now admits a
    value for verbatim display by registry membership:
    - a target must be a registered entity, device, area, floor or label ID
      (state machine, entity registry or `config/<kind>_registry/list`);
    - a service or domain must be in the service registry (`GET /services`);
    - a data key must be in that service's schema; its value must be a
      number, a boolean or `null`, or a fixed enum word that was configured
      literally.

    Every value must also pass the sanitizer and the known-secret check,
    numbers and enums included. Anything else is a `redacted_sha256:`
    digest. When registry data is unavailable, values are digested and the
    profile is marked partial for display. Before, a secret-shaped value
    (including prefixed, delimited, lowercased or high-entropy lowercase
    values) could reach helper-state plan disclosures and plan files.
    Tests plant secret-shaped values and check every analysis output, plan
    response and persisted plan file. See the design note's security
    section.
  - **Plan evidence.** The raw effect, profile and index fingerprints are
    unchanged. The aggregate helper evidence fingerprint covers the displayed
    values, so it changes when a value is digested, including when registry
    availability changes for an unchanged configuration. A plan bound to the
    earlier evidence is then stale and must be re-planned; this fails
    closed.
  - **Requests.** Each build with automation profiles makes one more read,
    `GET /services`. It makes a device, area, floor or label registry read
    only when a target of that kind is not already known.
  - Out-of-band edits that Home Assistant has not reloaded are reported as
    loaded, which is the configuration that runs.
- **Stored formats.** No stored format changes. The journal is in memory only.
- **Rollback** is reinstalling the beta.22 artifact, which returns to the
  per-automation REST reads and their slow builds. No state needs migration.
- **HAMCP-200.** Every REST call still goes through the typed `Route`. The two
  WebSocket commands are fixed and read-only. No write, service call, reload
  or subscription becomes reachable.
