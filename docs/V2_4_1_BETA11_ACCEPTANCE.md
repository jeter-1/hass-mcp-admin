# Engineering 2.4.1-beta.11 acceptance

This contract covers HAMCP-184, the audit-hardening batch: F-05 (provider
dispositions fail closed), F-01 (F3 re-apply hash guard), F-02 (truthful
principal-separation reporting) and F-06 (runner guard covers matrix runners).
It builds on beta.10 (HAMCP-181 PR1). It separates four stages: source and
disposable acceptance, release CI, publication, and installed Engineering
acceptance. This document authorizes none of the later stages.

## 1. Source, review and CI

Bind the following:
- the design note `docs/HAMCP_184_AUDIT_HARDENING_DESIGN.md` and its final
  protected-file list;
- Josh's owner decisions of 2026-10-14/15 on HAMCP-184:
  - F-02 is honest reporting only. A named approver is not required, and who
    can approve, and how, is unchanged.
  - F-05 also covers the five legacy compiled entries (7.14.1–8.2.0).
  - Following the independent review (R1), ha-mcp 8.2.0 backup and lifecycle
    are `held`: they have no capability-specific review evidence. 7.14.1,
    7.14.2, 8.0.0 and 8.1.0 stay admitted on every surface.
- the base `main` (2.4.1-beta.10 at `b249738`), and every later main merge
  into the branch;
- the final reviewed commit and tree.

Earlier review does not cover changed code.

Materialize 2.4.1-beta.11 in all three version authorities through the existing
staging tool. Require exact acceptance and release-note resolution, with no
staged declaration left over. Complete Full/Evidence on clean source and the
bounded independent review.

The review needs a security section covering:
- zero new write, dispatch, service-call, fallback or forwarding reachability;
- F-05: no surface admitted by omission, an unknown value read as `held`, and
  every compiled entry explicit;
- F-01: the existing-task refusal occurring before any dispatch, task event or
  plan change;
- F-02: approval eligibility identical to beta.10 for every stored record,
  including the refusal of a forged `false` flag with an identified principal;
- F-06: no workflow edit, with the guard reading generated runner rows.

Require exact-head CI before release acceptance. Pending or unavailable jobs
are not passing evidence.

Preserve the following:
- stable v1.1.2;
- historical persisted fixtures and durable formats. No stored approval
  record is rewritten, and F3 approval-bundle hashes are unchanged;
- probe fingerprints, the Core capability profiles, every owner-signed Core
  registry entry and the signed ha-mcp registry;
- the 8.6.0 HOLD and the support window;
- dependency locks, build inputs, pinned runners and workflow permissions
  (`ci.yml` unchanged).

The public catalog stays at **87 tools (59 native, 28 delegated)**, and every
beta.10 descriptor is unchanged.

Offline source evidence must prove each of the following.

**F-05.**
- Every compiled registry entry declares exactly the four provider surfaces.
- The effective dispositions of all ten entries equal the after table in the
  design note. The only difference from the pre-HAMCP-184 table is 8.2.0
  backup and lifecycle, now `held`; both providers refuse 8.2.0 before
  dispatch. 8.5.0 and 8.4.3 are exactly read gateway and dashboard
  `admitted`, backup and lifecycle `held`, with byte-identical entries.
- An absent entry, null, missing surface, extra surface, unknown value or
  non-object is refused at load.
- The accessor returns `held` for absent and unknown values.
- A provider refuses a release stripped of its dispositions before dispatch.

**F-01.**
- A re-apply of a terminal task with an empty or wrong hash is refused with
  `approval_hash_mismatch`, with zero dispatch and unchanged task and plan.
- An undispatched initialized task with a wrong hash is refused the same way,
  then dispatches exactly once with the matching hash.
- A matching replay of a terminal task returns the same task without
  redispatch.

**F-02.**
- An anonymous-fallback standard approval stores and projects `false` with
  reason `anonymous_fallback_principal`, and still applies.
- An identified principal stores and projects `true` with
  `external_administrator_distinct`, for standard and elevated plans.
- Elevated plans still refuse the fallback with `approval_principal_mismatch`.
- A pre-beta.11 anonymous record stored `true` projects `false`, is not
  rewritten and stays eligible.
- The plan record, `get_change_plan` and apply results agree. The audit and
  the task carry no separation claim.

**F-06.**
- The guard resolves the `ci.yml` matrix runner to the generator's rows for
  every tier.
- An unpinned generated runner, or an unpinned runner constant, fails the
  guard.

**Mutation checks.** Each of the following fails a test:
- restoring the `admitted` default or making dispositions optional;
- removing the F-01 hash check;
- writing `true` for every principal;
- trusting the stored flag in the projection;
- requiring a truthy flag at the apply gate;
- resolving the runner guard from the constant instead of the rows;
- unpinning a runner.

## 2. Publication and recovery preparation

Publication needs Josh's applicable Ready authorization and the protected merge
handoff. Verify the following with the shipped verifiers:
- the merged source;
- v2.4.1-beta.11;
- the immutable version and commit images;
- provenance and SBOM.

Never invent future image identities. Retain the known-good beta.10 and beta.9
artifacts and options for recovery.

No new persisted format is introduced. A beta.11 anonymous-fallback approval is
stored with `principal_separation_enforced: false`, which beta.10 refuses to
apply. Before a downgrade, apply or reject pending approved plans, or decide
them again after the downgrade.

## 3. Installed Engineering

All installed checks below are read-only unless a step names Josh's explicit
authorization.

1. **Installed-image binding.** Separately authorize installation of the
   published Engineering artifact. Bind the running add-on to the published
   image index digest, the v2.4.1-beta.11 tag and the protected `main` merge
   commit. A source version alone does not prove the installed image.
2. **Catalog and health.** Compare all 87 public descriptors, Core signed
   selection, storage and audit health, F3 readiness and zero fallback.
3. **F-05 admission unchanged (read-only).** With production ha-mcp 8.5.0:
   - upstream admission must report the same 28 delegated reads as beta.10;
   - power, fan, dashboard and entity-metadata capabilities must report the
     same admission as beta.10;
   - backup and lifecycle must stay held for 8.5.0;
   - no surface may report a missing-disposition refusal.
4. **F-02 display (one owner-approved canary).** On a plan Josh chooses and
   approves through the Ingress panel, with Josh's explicit authorization of
   any apply, read `get_change_plan`. Record the approver principal. Then:
   - **Anonymous fallback:** the approval must show
     `principal_separation_enforced: false` and `principal_separation_status`
     `{evaluated: true, enforced: false, reason: anonymous_fallback_principal}`.
   - **Identified user:** the approval must show `true` with
     `external_administrator_distinct`.

   Read one approval decided before the upgrade the same way. It is expected
   to display `false` if it was anonymous.

   The approval must remain applicable exactly as on beta.10. No approval
   behavior may change.
5. **F-01 refusal probe (read-only/offline).** Do not re-apply a live plan to
   probe F-01. Run the offline source test
   `tests/test_hamcp184_f3_reapply_hash_guard.py` against the exact released
   tree, and record its result with the commit. Optionally read
   `get_execution_task` for a completed F3 task to confirm it still reports a
   single dispatch intent.

No push, CI trigger, signing, publication, installation or live action is
authorized by source completion.
