# Engineering 2.4.1-beta.25 acceptance

This contract covers HAMCP-212: script configuration read from Home Assistant's
loaded configuration (fast dependency index, part 2). It builds on
2.4.1-beta.24 (HAMCP-130).

See also:
- Release notes:
  [`V2_4_1_BETA25_RELEASE_NOTES.md`](V2_4_1_BETA25_RELEASE_NOTES.md).
- Design and Part 1 verification:
  [`HAMCP_212_LOADED_SCRIPT_CONFIG_DESIGN.md`](HAMCP_212_LOADED_SCRIPT_CONFIG_DESIGN.md).

**Release target.** `2.4.1-beta.25`. It is staged by one owner-authorized run
of `scripts/promote_next_release.py --apply` on a base that already carries
2.4.1-beta.24. The run consumes the declaration.

The contract separates four stages:
1. source and offline acceptance;
2. release CI;
3. publication;
4. installed Engineering acceptance.

It authorizes none of the later stages.

## 1. Source and offline acceptance

Bind the following:
- Josh's HAMCP-212 objective: read script configuration from Home Assistant's
  loaded configuration with bounded requests (D3), with no file read (D4), and
  with truthfulness not worse;
- `2.4.1-beta.25` materialized in all three version authorities, with exact
  acceptance and release-note resolution from `scripts/codex-context.py` and
  no staged declaration left over;
- the PR head SHA;
- the passing PR `validate` run for that exact head;
- the Evidence tier log named in the PR.

The review needs a security section covering:
- the Part 1 source choice and authority (design §2): Core WebSocket
  `script/config` and `search/related` (`item_type: script`) under the existing
  administrative token, with no new permission;
- the batch allowlist: exactly two more fixed read-only shapes, and zero new
  write, service-call, reload, `execute_script`, fallback or forwarding
  reachability (design §4);
- resolved `!secret` values in loaded package scripts: the same sanitizer and
  registry-backed display allowlist (design §4);
- every stored fallback counted with a closed reason, and a failed loaded read
  always partial (design §3.2);
- the script pending-write journal and its `governance/resources.py`
  pre-dispatch hook (design §3.3).

Preserve the following:
- stable v1.1.2;
- persisted formats;
- every descriptor and input schema (88 tools, 89 with the optional YAML
  reader admitted, as in beta.24);
- the DependencyIndex snapshot fingerprint and helper plan evidence;
- probe fingerprints, Core capability profiles, owner-signed Core registry
  entries and the signed ha-mcp registry;
- the HOLD and the support window;
- workflows and dependencies.

### Offline proof

No test reaches a live system.

- `tests/test_hamcp212_loaded_script_config.py` (43 tests) covers:
  - parity of loaded and stored scripts;
  - bounded requests: one batch, 236 commands for 113 automations and
    5 scripts, and 0 delegated reads;
  - fallback accounting for each reason;
  - partial on failure;
  - the journal and its write hook;
  - the allowlist;
  - health profile keys;
  - attribution;
  - planted secrets across every analysis family and a saved helper plan.
- Cross-tree parity against `f69bf8c` (beta.24): IDENTICAL
  (`docs/evidence/hamcp212/`).
- Mutations: 12 of 12 killed (`docs/evidence/hamcp212-mutations/`), including:
  - (a) the per-script delegated loop restored, which fails the request-count
    test;
  - (b) a failed loaded read reported as complete (S2a and S2b).
- Synthetic measurement: the build falls from 9.80 s to 0.51 s with delegated
  reads serialized, and from 2.35 s to 0.49 s with them concurrent.

## 2. Release CI and publication

Require exact-head CI before release acceptance. Pending or unavailable jobs
are not passing evidence.

Publication needs Josh's Ready authorization and the protected merge
handoff. Verify the following with the shipped verifiers:
- the merged source;
- v2.4.1-beta.25;
- the immutable version and commit images;
- provenance and SBOM.

Never invent future image identities. Retain the beta.24 artifacts and the
known-good earlier artifacts for recovery.

No stored format changes. Rollback is reinstalling the beta.24 artifact.

## 3. Installed Engineering

All installed checks below are read-only. None of them writes, reloads or runs a
script.

1. **Installed-image binding.** Separately authorize installation of the
   published Engineering artifact. Bind the running add-on to:
   - the published image index digest;
   - the v2.4.1-beta.25 tag;
   - the protected `main` merge commit that contains this PR.
2. **Catalog.** Using fresh protocol enumeration, confirm:
   - 88 tools, or 89 with the optional YAML reader admitted;
   - every descriptor unchanged from beta.24;
   - Core signed selection, storage and audit health, and zero fallback.
3. **Build time and script source counts (live).** After the startup prewarm
   build, read `get_server_health` and record the following from the
   dependency index `last_build_profile`:
   - `build_wall_clock_ms`; expected well below beta.23's 8.8 s, near the
     automation phase (about 1.2 s);
   - `request_count_by_operation`: `loaded_config_batch: 1`, and
     `delegated_script_configuration` equal to the stored count below;
   - `loaded_config_connection_count: 1`, `loaded_config_command_count` and
     `loaded_script_command_count`;
   - `script_config_source`;
   - `script_config_source_counts`: `loaded_websocket` plus
     `stored_delegated`, which should equal the 5 known scripts;
   - `script_stored_config_fallback_reasons`; every stored script needs a
     true reason, for example a blueprint script as `blueprint_instance`;
   - `script_loaded_config_unavailable`; expected `0`. Any nonzero value needs
     a true cause;
   - `script_pending_write_journal`.
4. **Evidence.** Call `entity_dependency_analysis` with `include_indirect: true`
   for an entity that a known script targets. Confirm:
   - the script's evidence is present;
   - its `script_call_graph.path_evidence[].provider` matches its counted
     source;
   - script coverage carries no `loaded_config_unavailable` warning.

   Compare with beta.24's result for the same entity, if recorded.
5. **No change.** No script, automation or helper is modified by steps 3 and 4.

No push, CI trigger, signing, publication, installation or live action is
authorized by source completion.
