# Native automation-baseline capture acceptance

This is a feature contract, not an advertised release or permission to access HA.
Beta.14 is the materialized candidate; obtain independent review, complete exact-
head gates and prepare signed applicability separately. Preserve completed beta.13
acceptance. See [release acceptance](V2_4_0_BETA14_ACCEPTANCE.md).

## Source and disposable prerequisites

- Preserve all existing 81 descriptors and 20 Core fingerprints. Candidate catalog
  is 82 = 57 static + 25 delegated; capability set is 21. The signed journal is
  unchanged. Old authority must withhold the new capability with zero collection.
- Run the baseline tests, unchanged comparator regressions, gateway/audit/privacy
  checks and full repository Full/Evidence on the final materialized release head.
- Prove source-derived semantics using the exact Core source archive and record
  the distinction between isolated functions and the assembled disposable lane.
- Require every evidence class in the matrix below. The assembled lane proves
  actual Core reads and capture/export interoperability; deterministic offline
  controls prove adversarial conditions that are not manufactured in that lane.
- Use ephemeral test signing only in the isolated lane. Verify absent/wrong/exact
  capability references. Production key use, signing and activation are separate.

### Required case-to-evidence matrix

This allocation separates the approved adversarial proving gates from the
assembled exact-source read gate. It supersedes the earlier wording that grouped
every negative case under disposable Core. It changes no runtime behavior or
required refusal outcome. All receipts must identify the final candidate; an
unavailable receipt leaves its row open, and a generic CI PASS is insufficient.

| Required cases | Execution boundary and named evidence |
| --- | --- |
| Exact Core admin REST inventory, connection-principal semantics, built-in MFA read behavior, registry omissions and persisted anchor representation | Verified Core archive plus [test_automation_baseline_core_source.py](../tests/test_automation_baseline_core_source.py). Retain archive hash and named-test results; isolated source functions are not a Core container run. |
| Actual fixed commands, normal predecessor acquisition refusal with zero reads, exact-profile admission, non-admin/missing-anchor refusal, restored identity preservation and changed config-entry lineage | Existing Core 2026.9.4 lane running [automation_baseline_contract_acceptance.py](../scripts/automation_baseline_contract_acceptance.py). Retain `alarmo-integration-ha-2026-9-4.json` / `automation_baseline`, including `predecessor_admission`, and its bound image/source receipt. The Hass.io entry is synthetic metadata, not a running Supervisor. |
| More than 100 loaded objects, off versus registry-disabled scope, unreadable lookup, two complete public-tool exports, all five unchanged-CLI classifications, zero-read continuation | Same assembled receipt: both baseline objects and hashes, comparison counts and continuation reads. An unreadable synthetic lookup does not prove a real package configuration was exercised. |
| No unexpected command, flow, service or command-origin storage write during successful captures; selected stores unchanged; owned fixture cleanup | Same receipt's two observations plus `alarmo-cleanup-ha-2026-9-4.json`. Require exact command order, intact hooks, hashes and cleanup binding. Setup/reload/cleanup stays outside these intervals. This is bounded observer coverage, not proof about every possible filesystem write. |
| Omitted/duplicate mappings, package-only lookup unavailability, ambiguous anchors, principal/authority/identity/mapping/inventory drift, empty inventory, cursor ownership/expiry/capacity | Deterministic synthetic-provider tests in [test_automation_baseline_capture.py](../tests/test_automation_baseline_capture.py): `test_unmapped_registry_omission_duplicate_id_and_unreadable`, `test_identity_missing_ambiguous_wrong_binding_and_nonadmin_refused`, `test_authority_principal_anchor_and_mapping_fence_changes`, and `test_cursor_frozen_caller_expiry_capacity_and_empty`. These are not assembled-Core executions. |
| Maximum inventory, output sizing/responsiveness, deadlines, cancellation/draining; byte/frame/request limits, malformed responses, authentication refusal, redirect/retry/fallback prevention | Capture tests `test_maximum_capture_bounded_pages_responsive`, `test_deadline_partial_no_new_reads`, `test_cancel_stops_config_collection_and_drains`, plus [test_automation_baseline_transport.py](../tests/test_automation_baseline_transport.py) using owned loopback servers. Retain measured gaps and named results, distinguishing synthetic budgets from live timing guarantees. |
| Exact hashing/privacy, compatible export, chronology/duplicate refusal, rename/enabled-state annotations, legacy incomparability, public schema/audit/authority continuity | Remaining capture tests and unchanged [test_automation_audit_baseline.py](../tests/test_automation_audit_baseline.py), together with compatibility suites in exact-head full discovery. Retain the Full/Evidence report and full named-test transcript. |

The assembled lane does not currently manufacture mid-capture drift, omitted
registry mappings, package definitions, exhausted budgets or cancellation. Those
outcomes require the named offline controls above; they must not be reported as
assembled-Core PASS. If a required source test is skipped in CI because its
archive is unavailable there, retain its exact-head local archive-bound result.

## Installed prerequisites (separately authorized)

Verify exact source/build and running-container -> architecture/configuration ->
published OCI index. Verify supported Core/ha-mcp identity, the separately signed
new profile, healthy authority/storage/F3 and read continuity. Retain a fresh raw
82-descriptor public catalog session with zero `tools/call` and a render-only panel
observation. Internal counts are not raw enumeration evidence.

The scoped installation assurance requires a uniquely loaded Hass.io entry and
its normal persisted Core device record. Record the restored-clone limitation;
no fallback identity, raw user identifier or owner label may replace the anchor.

## Bounded useful installed capture

Run one `capture_automation_baseline(limit=25, cursor="")`, then only the returned
continuations until complete. Do not create a second live scan solely to produce
a comparison. Verify header/digest/offset/count continuity and zero extra HA reads
on continuation. Reconstruct and validate the baseline offline with the unchanged
validator. Retain sanitized evidence only, no raw configuration bodies.

Verify exact config IDs, explicit on/off versus registry-disabled scope, readable
hashes, unmapped omissions, source/provider attribution and no fallback. Treat
partial capture as partial. A registry-only preview is not proof of inventory
completeness. No deletion claim may be based on an uncovered source.

Compare to a later naturally needed compatible capture. Require distinct ordered
intervals, same verified lineage/scope/fingerprint model, continuous capture
fences and complete per-record evidence. Retained legacy baseline hashes remain
incomparable. `REMOVED` refers to loaded-scope membership, never disk deletion.

Finish with existing no-probe health settlement; explain concurrent authorized
work separately. Stop on authority/identity drift, transport failure, unexplained
counts, hash mismatch or nonterminal work. Preserve failed evidence; no automatic
retry, restart, configuration write, forced refresh or manufactured event.
