# Engineering 2.4.1-beta.44 acceptance

This contract covers HAMCP-235: automation baseline compare and resume made
usable on the owner's installation, from the beta.40 live findings. It follows
2.4.1-beta.43 and includes it.

See also:
- Release notes:
  [`V2_4_1_BETA44_RELEASE_NOTES.md`](V2_4_1_BETA44_RELEASE_NOTES.md).
- The contracts this release extends:
  [`AUTOMATION_BASELINE_CAPTURE.md`](AUTOMATION_BASELINE_CAPTURE.md),
  [`AUTOMATION_AUDIT_BASELINE.md`](AUTOMATION_AUDIT_BASELINE.md#comparison-semantics)
  and
  [`HAMCP_225_226_228_AUDIT_TOOLING_DESIGN.md`](HAMCP_225_226_228_AUDIT_TOOLING_DESIGN.md).

**Live findings (beta.40, 2026-10-27, HAOS, Core 2026.10.3, signed registry
seq 14).** Two captures 4 s apart of 115 automations, identical digests,
compared as UNKNOWN 115 / UNCHANGED 0. Every row carried
`earlier_/later_authority_continuity_unestablished` and
`installation_identity_unestablished`. Each header said installation
`unestablished`, `final_fence: "unavailable"`, drift `unknown`; capture 2
had `configuration_incomplete_count` 2. A resume two minutes later was
refused as `resume_inventory_changed` with no detail.

**Owner decision (Josh, option a).** The comparison classifies rows by
digest: `UNCHANGED` / `CHANGED` / `PREFIX_MATCH`, and `ADDED` / `REMOVED`
only where inventory authority allows. The installation identity and
authority continuity caveat is stated once, at the comparison level.
`UNKNOWN` is kept for rows whose digests are missing or incomparable.

**Diagnosis (no STOP).** Establishing the identity and completing the final
fence need no new Core command: both use the reads admitted under signed
registry seq 14 (principal, config entries, device registry, states, entity
registry). The HAOS anchor was valid; the identity was discarded only
because the final fence, sharing the 45 s collection deadline with a fixed
10 s reserve, failed and its failure was swallowed. The resume compared the
null installation id of such a base, the authority generation and the
declared count. See HAMCP-235 for the file-and-line diagnosis.

**Release target.** `2.4.1-beta.44` is the next free beta after `main` at
2.4.1-beta.43 (#320). If another release reaches `main` first, this change
is restaged at the next free beta and these documents are renamed.

The contract separates four stages:
1. source and offline acceptance;
2. release CI;
3. publication;
4. installed Engineering acceptance.

It authorizes none of the later stages.

## 1. Source and offline acceptance

Bind the following:
- the HAMCP-235 scope (compare caveats and row classes, resume stable
  identity fence and categories, final fence identity-first reads, reserve
  and reason; no other change);
- `2.4.1-beta.44` materialized in all three version authorities, with exact
  acceptance and release-note resolution from `scripts/codex-context.py` and
  no staged declaration left over;
- the PR head SHA;
- the passing PR `validate` run for that exact head, with both Real HA jobs;
- the Evidence tier log named in the PR;
- the mutation evidence `docs/evidence/hamcp235-mutations/results.json`.

The review needs a security section covering:
- **No new reads or writes.** No Core command, REST path, WebSocket type,
  registry entry, provider, route or write is added. The final fence issues
  the same five reads as before, identity reads first.
- **Identity stays fail-closed.** Identity drift between the fences still
  refuses the capture with nothing stored, now also when it is detected
  before the inventory reads. Authority loss, access denial and an
  unverifiable identity still refuse. Only an inventory read failure after
  a verified identity yields `identity_only`.
- **The resume fence.** Installation anchor, configuration id set and
  id→entity mapping only. A base's anchor is its established id, else its
  `first_fence_installation_id`; an earlier-release base with neither is not
  checked for installation and its resume stays unestablished. A new
  authority generation is recorded as `authority_drift: "detected"`, never
  hidden. The Core version still binds the resume.
- **Categories.** `details.resume_changes` holds only the fixed
  `RESUME_CHANGES` values, sorted, and only on `resume_inventory_changed` /
  `resume_core_changed`. Never an identifier, count or Home Assistant text.
- **Diagnostics.** `first_fence_installation_id` is the same opaque
  `sha256:` digest as `installation.installation_id`, and goes through the
  storage screen like every diagnostic. `final_fence_reason` is a fixed
  `CaptureError` reason.
- **Caveats never hide a defect.** Two established, different installations
  still make every row `UNKNOWN` (`installation_identity_mismatch`) and
  suppress rename and enabled-state claims. Pair ineligibility
  (`same_baseline_id`, intervals not ordered) is unchanged.
- **Screening unchanged.** Untrusted text still goes through the shared
  `credentials.screen`; Home Assistant identifiers and names stay in
  default mode with the final-envelope backstop. Nothing is persisted before
  a refusal.

Preserve the following:
- stable v1.1.2;
- the baseline schema and stored format (diagnostics are free-form);
- every input schema, tool, descriptor and the catalog count;
- routing, providers, policy, the Core gate and telemetry;
- probe fingerprints, the signed Core capability profiles and both signed
  registries;
- every workflow and dependency. `config.yaml` changes only its version.

### Offline proof

No test reaches a live system. Every value is synthetic.

- `tests/test_hamcp235_baseline_usability.py`, live-shaped first (115
  automations, Core 2026.10.3, a final-fence principal read that times out):
  - **Unestablished pair compares UNCHANGED.** Two captures 4 s apart whose
    final fence failed: UNCHANGED 115, UNKNOWN 0, three caveats on both
    sides, stated once and identical on every page; no caveat code in any
    row reason.
  - **A real change is CHANGED**, attention rows first.
  - **Timed-out rows are UNKNOWN with `timed_out`**, on the later side, the
    earlier side and at the scheduler bound; an unavailable (not timed-out)
    row is UNKNOWN without it.
  - **Recorded fingerprints** against an unestablished capture: UNCHANGED,
    PREFIX_MATCH and UNKNOWN by row, the recorded side listed as unverified.
  - **A fingerprint model mismatch** is UNKNOWN on every row.
  - **An established pair** carries only `non_atomic_capture`.
  - **Two established, different installations** are UNKNOWN per row
    (`installation_identity_mismatch`).
  - **Resume minutes later succeeds** with a new authority generation,
    on/off toggles and an added unmapped automation: only the timed-out rows
    are retried, the result is established with the base's anchor,
    `authority_drift: "detected"`, and a fresh compare is UNCHANGED 115.
  - **Each volatile input alone resumes**; **each category is refused
    precisely** (`automation_added`, `automation_removed`,
    `mapping_changed`, `installation_changed` with an unestablished base,
    a replaced automation, `core_version_changed`), with no configuration
    read and nothing stored.
  - **Final fence:** on/off state is not drift; a mapping change is;
    `identity_only` with its reason; identity drift refuses with nothing
    stored; the reserve's bounds; scheduling stops at the reserve.
- Updated: `test_hamcp228_baseline_store`, `test_automation_baseline_capture`
  and `test_automation_audit_baseline` (the per-row reasons replaced by the
  caveats; the resume refusals carry their categories).
- Unchanged and passing: HAMCP-132 (`test_pr294_*`), HAMCP-224
  (`test_pr313_*`, `test_review2_ansi_residual`) and the HAMCP-225/226/228
  suites.
- Mutations: `docs/evidence/hamcp235-mutations/` (a global caveat as a
  per-row UNKNOWN, a volatile input in the resume or capture fence, a
  refusal without its category, the final fence weakened).

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
read-only. Record each result in HAMCP-235.

**The live re-run of the owner's case by the architect is REQUIRED after
deployment (steps 4 to 6). beta.44 is not accepted without it.**

1. **The add-on starts after the update (first).** Confirm the Engineering
   add-on reaches `started` and its MCP endpoint answers. If it does not,
   stop and reinstall 2.4.1-beta.43.
2. **Installed-image binding.** Bind the running add-on to the published
   image index digest, the release tag and the protected `main` merge
   commit.
3. **Served schema unchanged.** Re-read `tools/list` with a fresh client
   session. The input schemas and the tool count equal beta.43's.
4. **Two captures (required).** Run `capture_automation_baseline` twice, a
   few seconds apart, with no automation edited between them. Record for
   each: `installation.status`, `diagnostics.final_fence`,
   `final_fence_reason`, `final_fence_reserve_seconds`,
   `configuration_incomplete_count` and `consistency.inventory_drift`.
   Expected: the capture completes; if `final_fence` is not `complete`, its
   reason is recorded, and `identity_only` comes with an established
   installation.
5. **Compare shows UNCHANGED with the caveat (required).** Run
   `compare_automation_baselines` on the two captures and page to the end.
   Expected:
   - every automation whose digests are present on both sides is
     `UNCHANGED` (115 on the owner's installation at beta.40, less any row
     incomplete in either capture);
   - `caveats` lists each applicable caveat once, the same on every page,
     and no row reason names `installation_identity_unestablished` or
     `*_authority_continuity_unestablished`;
   - each `UNKNOWN` row has a specific reason (`timed_out`, a missing
     digest, `capture_configuration_not_readable`, …).

   If rows are `UNKNOWN` without a row-specific reason, stop and record
   them.
6. **A successful resume (required).** Resume (`resume_capture_id`) a
   capture from step 4 that has a timed-out record, a few minutes later,
   with no automation added, removed or re-mapped. Expected: the resume
   succeeds, `resumed_from` names the base, and a compare of the result
   with the other capture is `UNCHANGED` with the caveats. If neither step
   4 capture has a timed-out record, the resume is refused as
   `resume_not_needed`; then resume the beta.40 capture from 2026-10-27
   that had two incomplete configurations, if it is still stored and the
   Core version is unchanged (2026.10.3), or record that the step could not
   be exercised and stop for the architect's decision. A refusal must name
   its category in `details.resume_changes`; record it.
7. **A real change is still seen.** Optional, owner-authorized only: no
   automation is edited for this acceptance. If an automation changed for
   another reason between captures, record that it is `CHANGED`.

## 4. Not covered

No new Core command, live canary, write, registry signing or Supervisor role
change is part of this release. The tool descriptors are unchanged. A
baseline written by a release before beta.44 whose final fence failed has no
first-fence anchor: a resume of it is not checked for installation and stays
unestablished. A non-HAOS Supervised host remains outside the evidence.
