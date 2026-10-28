# Engineering 2.4.1-beta.44 acceptance

This contract covers HAMCP-242. `get_server_health` itemizes each withheld
Core capability by name, disposition, reason and affected Engineering tools.
It also states the count semantics. The counts are unchanged. It follows
2.4.1-beta.43 and includes it.

See also:
- Release notes:
  [`V2_4_1_BETA44_RELEASE_NOTES.md`](V2_4_1_BETA44_RELEASE_NOTES.md).
- How to read the block:
  [`CORE_AUTHORITY_DIAGNOSTICS.md`](CORE_AUTHORITY_DIAGNOSTICS.md).
- The contract this release extends:
  [`V2_4_1_BETA43_ACCEPTANCE.md`](V2_4_1_BETA43_ACCEPTANCE.md).

**Release target.** `2.4.1-beta.44` is the next free beta after `main` at
2.4.1-beta.43 (#320). HAMCP-235 is in progress in parallel and may claim the
same number. If another release reaches `main` first, this change is
restaged at the next free beta and these documents are renamed.

The contract separates four stages:
1. source and offline acceptance;
2. release CI;
3. publication;
4. installed Engineering acceptance.

It authorizes none of the later stages.

## 1. Source and offline acceptance

Bind the following:
- the HAMCP-242 scope: the three additive health fields, the consistency
  checks and `routes.static_tools_requiring`, with no other runtime change;
- `2.4.1-beta.44` materialized in all three version authorities, with exact
  acceptance and release-note resolution from `scripts/codex-context.py` and
  no staged declaration left over;
- the PR head SHA;
- the passing PR `validate` run for that exact head, with both Real HA jobs;
- the Evidence tier log named in the PR.

The review needs a security section covering:
- **Engineering identifiers only.** Every list value comes from a profile
  id, `CoreDisposition`, a coordinator reason code or
  `STATIC_TOOL_CORE_REQUIREMENTS`. No observation text, entity, session or
  endpoint value is read into it. The response still passes the
  final-envelope sanitizer.
- **No decision change.** `withheld_capability_entries` reads the assessment
  `update_assessment` already builds. It does not touch selection,
  admission, reason codes, leases, retirement, the registry or recovery.
  The two new consistency checks only refuse an inconsistent projection
  (`core_authority_projection_inconsistent`). They never admit.
- **No new reach.** No new Core, Supervisor or upstream call, no persisted
  state, no tool, route, provider, write or fallback.
- **Bound.** At most 64 entries, `withheld_capabilities_truncated` past
  that, and the whole projection within `MAX_REPORT_BYTES`.

Preserve the following:
- stable v1.1.2;
- persisted formats (none change);
- every input schema, tool and the catalog count;
- `list_capabilities` and `server_info`;
- routing, providers, policies, the Core gate and telemetry;
- probe fingerprints, the signed Core capability profiles and both signed
  registries (the registry stays at sequence 14);
- every workflow and dependency. `config.yaml` changes only its version.

### Offline proof

No test reaches a live system. Every value is synthetic, apart from the
committed signed registry entry, which is read and re-signed with an
ephemeral test key.

- `tests/test_hamcp242_core_authority_itemization.py`, live case first:
  - **The beta.43 live case.** The committed sequence-14 entry for Core
    2026.10.3 goes through the production `CoreRuntime`,
    `CoreReleaseRegistry`, the gate, `HealthRegistry` and the
    `run_structured` envelope, with evidence for all 29 runtime profiles.
    Expected: `compatible_count` 26, `held_count` 0, `quarantined_count` 0,
    `unavailable_count` 3, `withheld_count` 3. The list holds exactly three
    `unavailable` / `positive_authority_missing` entries:
    `core.config_entry_helper_options_operation`
    (`create_helper_options_plan`), `core.script_trace_read`
    (`get_script_trace`, `list_script_traces`) and
    `core.typed_input_select_option_operation`
    (`create_input_select_option_plan`). The block key order is the
    beta.43 order with the three new keys inserted, and the three profiles
    are still refused.
  - **After sequence 15.** The same entry naming all 29 profiles: an empty
    list and `compatible_count` 29.
  - **No new reach.** Reading health makes no Core capture call and writes
    no file.
  - **Each disposition.** `held` (deny-only authority), `quarantined`
    (changed capability contract), `unavailable` (missing authority and
    missing evidence), a mixed case in one sorted list, and the
    all-compatible empty list. Every case checks that each disposition's
    entries equal its count.
  - **Global refusal.** A Core version disagreement lists all 29 profiles
    and stays within `MAX_REPORT_BYTES` (under 12 000 bytes).
  - **Bound and order.** Sorting across dispositions; 64 entries not
    truncated; 65 bounded to the first 64 with the flag set; the limit
    equal to `MAX_CAPABILITIES`.
  - **Consistency.** A disagreeing count, a duplicate id and an
    unclassified (`verifying`) decision each refuse.
  - **`affected_tools`.** It matches a hand-pinned table for every runtime
    profile, including the script-trace pair. It agrees with
    `STATIC_TOOL_CORE_REQUIREMENTS` both ways and names only Engineering
    static tools, never a delegated tool.
- One existing test is updated deliberately:
  `tests/test_ha_core_2026_9_integration.py`
  `test_public_health_uses_bounded_core_projection`. Its 4 096-byte budget
  still covers exactly the beta.43 counts-only block. The whole block is
  checked against `MAX_REPORT_BYTES`. It now pins the 12 later-batch
  profiles that a 2026.9 snapshot without their evidence itemizes as
  `unavailable` / `capability_evidence_missing`.
  `tests/test_governance_async_health.py` passes the projection through
  unchanged and needs no edit.
- Mutations (`docs/evidence/hamcp242/run_mutations.py`, result
  `mutations.json`), run against the new suite and the updated 2026.9 test
  over a 25-test baseline. A mutant counts as killed only when a named test
  fails and no import or syntax error occurs. All 25 are killed:
  - a disposition class omitted, with and without the consistency guards;
  - `affected_tools` empty, missing an element, reversed, including
    delegated tools, or absent;
  - the list and counts disagreeing: an entry dropped or duplicated with the
    guards removed, each guard removed alone, and the assessment invariant
    removed;
  - a wrong disposition label, a dropped reason, no sort;
  - the truncation flag never set or off by one, no bound, the limit 63;
  - `count_semantics` missing.

## 2. Release CI and publication

Require exact-head CI, both Real HA jobs included, before release
acceptance. Pending or unavailable jobs are not passing evidence.

Publication needs Josh's Ready authorization and the protected merge
handoff. Verify the merged source, the release tag, the immutable version
and commit images, provenance and the SBOM with the shipped verifiers. Never
invent future image identities. Keep the beta.43 artifacts and the
known-good earlier artifacts for recovery.

No stored format changes. **Rollback is reinstalling the 2.4.1-beta.43
artifact.**

## 3. Installed Engineering

Installation is separately authorized. Every check after step 1 is
read-only. Record each result in HAMCP-242.

1. **The add-on starts after the update (first).** Confirm the Engineering
   add-on reaches `started` and its MCP endpoint answers. If it does not,
   stop and reinstall 2.4.1-beta.43.
2. **Installed-image binding.** Bind the running add-on to the published
   image index digest, the release tag and the protected `main` merge
   commit.
3. **(a) On beta.44, before sequence 15 (required).** Call
   `get_server_health` with `check_ha: false`. In
   `home_assistant_core_authority`, expect:
   - `observed_core_version` `2026.10.3`;
   - counts unchanged from beta.43: `compatible_count` 26, `held_count` 0,
     `quarantined_count` 0, `unavailable_count` 3, `withheld_count` 3;
   - `withheld_capabilities` lists exactly three entries, in this order,
     each `disposition` `unavailable` and `reason_code`
     `positive_authority_missing`:
     - `core.config_entry_helper_options_operation`, `affected_tools`
       `["create_helper_options_plan"]`;
     - `core.script_trace_read`, `affected_tools`
       `["get_script_trace", "list_script_traces"]`;
     - `core.typed_input_select_option_operation`, `affected_tools`
       `["create_input_select_option_plan"]`;
   - `withheld_capabilities_truncated` `false`, and `count_semantics`
     present.

   If the counts differ from beta.43, or the list disagrees with them, stop
   and record the block. That is a defect in this release, not a reason to
   sign.
4. **(b) After signing sequence 15 and an add-on restart (required, when
   that separately authorized signing happens).** Call `get_server_health`
   again. Expected: `withheld_capabilities` `[]`,
   `withheld_capabilities_truncated` `false`, `compatible_count` 29,
   `withheld_count` 0. Signing sequence 15 is not part of this release.

## 4. Not covered

Signing registry sequence 15 and enabling the three dormant profiles are
separate tasks. Delegated `ha-mcp` tool names in `affected_tools`, a
`list_capabilities` or `server_info` mirror of the list, and any change to
admission, reason codes or recovery are out of scope.
