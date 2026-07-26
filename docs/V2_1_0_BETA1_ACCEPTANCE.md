# 2.1.0-beta.1 2.1A Dev1 acceptance contract

Version: `2.1.0-beta.1`

Status: source and CI acceptance authority only. Do not access a live Home
Assistant instance or trigger a real backup, reload, or restart during PR
validation.

## Provenance and catalog

- Require base/tag target
  `4942770a2fd80fed613eb1f42ed84ba9fa1c134c` and annotated tag object
  `60969502d63e0926c956b179dcad987058dece2b`.
- Require 42 Engineering tools, 26 delegated reads, and 68 total with complete
  admission.
- Require the unchanged 7.14.1 and 7.14.2 release registries, 78 tool records
  per release, exact catalog and per-tool fingerprints, zero unreviewed
  delegation, and zero fallback.

## Source and disposable acceptance

1. Prove planning reads exact provider and inventory evidence but performs no
   write.
2. Prove safe name bounds and the absence of arbitrary provider arguments.
3. Prove exact hash-bound external approval, expiration, rejection, one-time
   consumption, and mutation invalidation.
4. Prove persistence precedes exactly one dispatch.
5. Prove independent completed-state, identifier, name, date, size, and
   inventory verification.
6. Prove definitive failures remain terminal and transport ambiguity enters
   verification-only recovery.
7. Prove concurrent, repeated, and restarted apply paths never redispatch.
8. Prove audit redaction, health source labels, zero fallback, and
   `rollback_available=false`.
9. Run exact-image lanes against digest-pinned 7.14.1 and 7.14.2. Each lane
   must invoke the real upstream `ha_manage_backup` snapshot-create path
   against the disposable Home Assistant fixture, observe exactly one
   internally constrained request, and independently verify it through
   `backup/info`.
10. Retain all public schema, compatibility-ledger, dashboard, governance,
    dependency, disposable-HA, image, multiarchitecture, metadata, security,
    protected-path, whitespace, and stable-v1 gates.

## Runtime acceptance plan

Do not execute this plan as part of source review. After an independently
approved publication and deployment:

1. Verify version, source SHA, image digest, clean build, Home Assistant
   connectivity, 42 Engineering tools, 26 delegated reads, and 68 total.
2. Verify exact active 7.14.1 or 7.14.2 admission and zero fallback.
3. Create a uniquely named backup plan and confirm proposal-only evidence.
4. Review and approve the exact hash through administrator Ingress.
5. Apply once; require one provider dispatch and a completed, newly identified
   backup in independent inventory readback.
6. Inspect bounded audit and operational health counters.
7. Repeat `apply_change_plan`; require `already_applied` with no redispatch.
8. Exercise ambiguous recovery only in a specifically authorized disposable
   environment. Never induce it on production merely for acceptance.

Rollback of this candidate is source/image rollback to exact 2.0.1 with
retained `/data`. Contract-v3 operational records remain byte-preserved in the
separate `operational-administration-v3` namespace. Exact 2.0.1 does not
display, process, quarantine, modify, or delete that namespace; its legacy
configuration plans remain readable and operational. Reinstalling 2.1 restores
the exact plan IDs, hashes, approvals, dispatch evidence, lifecycle state, and
readback-only recovery.

Operational plans cannot be approved, applied, or recovered while 2.0.1 is
running. Never move their files into the legacy governance namespace or
manually recreate a pending or `verification_required` operation. Re-upgrade
to 2.1 before resuming operational recovery. A created backup is not deleted
automatically, and no storage migration is required merely to downgrade.
