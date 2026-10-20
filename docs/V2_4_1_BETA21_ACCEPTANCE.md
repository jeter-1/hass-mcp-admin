# Engineering 2.4.1-beta.21 acceptance

This contract covers HAMCP-205 (absorbing HAMCP-199): one shared
administrator check for every Ingress approval page, backed by a read-only
Home Assistant role lookup, and removal of the anonymous default approver. It
builds on 2.4.1-beta.20 (HAMCP-191).

See also:
- Release notes:
  [`V2_4_1_BETA21_RELEASE_NOTES.md`](V2_4_1_BETA21_RELEASE_NOTES.md).
- Design and pinned upstream sources:
  [`HAMCP_205_ADMIN_VERIFICATION_DESIGN.md`](HAMCP_205_ADMIN_VERIFICATION_DESIGN.md).

**Release target.**
- This PR was brought up to `2.4.1-beta.20` by a merge commit (base
  `538a7c73f54096a637b43205f82a09457db92486`).
- It was staged as `2.4.1-beta.21` by one owner-authorized run of
  `scripts/promote_next_release.py --apply`, which consumed the declaration.

The contract separates four stages:
1. source and offline acceptance;
2. release CI;
3. publication;
4. installed Engineering acceptance.

It authorizes none of the later stages.

## 1. Source and offline acceptance

Bind the following:
- `2.4.1-beta.21` materialized in all three version authorities, with exact
  acceptance and release-note resolution from `scripts/codex-context.py` and
  no staged declaration left over;
- the PR head SHA;
- the passing PR `validate` run for that exact head;
- the Evidence tier log named in the PR.

### Offline proof

No test reaches a live system. The role lookup runs against a synthetic
`config/auth/list` transport, and approval flows use the offline verifier in
`tests/approval_identity_support.py`. No committed file holds secret-shaped
text.

**`tests/test_hamcp205_admin_verification.py` (39 tests):**
- `IngressIdentityTests` and `AdministratorVerdictTests`:
  - exactly one well-formed `X-Remote-User-Id` value is an identity;
  - Core's `is_admin` rule, with an inactive user (the owner included) and a
    system-generated account never an administrator (review L1);
  - unknown, duplicated or malformed results fail closed.
- `AdministratorVerifierTests`: the only command sent is the fixed
  `config/auth/list` as a bounded single read; a missing identity never looks
  up; every lookup failure and a slow lookup refuse; nothing is cached.
- `SharedGateRefusalTests`: a non-administrator, missing, malformed or
  repeated identity, a lookup failure, a verifier exception or unknown
  verdict, and an unconfigured verifier are each refused on all ten routes
  with no governance, typed-service, CSRF or device side effect. Non-Ingress
  peers and paths are refused before any identity check. A failed audit write
  does not turn a refusal into an acceptance. Health counts refusals by
  reason.
- `NoAnonymousApproverSourceTests`: the Ingress application has no default
  principal, production wiring installs the real verifier, and the verifier
  module reads through one fixed command.
- `PlanApprovalAdministratorTests`, `F3ReconciliationAdministratorTests` and
  `TypedHoldReleaseAdministratorTests`: an administrator reviews and decides
  with the verified principal stored (useful authorized success); a
  non-administrator sees and changes nothing; a role revoked between review
  and decision is refused at the decision; one audit entry without secrets.
- `AnonymousApproverRemovalTests` (owner cleanup):
  - governance has no default principal;
  - a stored anonymous approval, with either recorded separation flag, is
    refused at apply with `external_approval_required` and
    `details.reason: anonymous_approval_requires_reapproval`, with no Home
    Assistant read or write, no task, and only the refusal recorded (one
    `change_apply_rejected` plan event and audit entry);
  - the same approval is refused at rollback with the reason, recorded as
    `rollback_failed`;
  - re-planning and approving as a verified administrator applies normally;
  - the service refuses an empty, missing or anonymous principal.

**`tests/test_hamcp184_principal_separation_honesty.py` (11 tests):**
HAMCP-184's truthful projection is kept for stored anonymous records, which
are now planted with plain repository saves. No decision is recorded for the
anonymous principal (standard and elevated, approve and reject), a stored
anonymous rejection is not enforced, and stored anonymous approvals with
either flag need re-approval.

**Updated approval suites.** `test_beta8_persisted_prohibited_compatibility`,
`test_beta10_legacy_expired_automation_compatibility`,
`test_beta25_external_approval`, `test_beta30_approval_notifications`,
`test_beta35_approval_theme`, `test_f2_policy_approval`,
`test_f3_runtime_integration`, `test_hamcp181_surface_consistency` and
`test_hamcp192_observing_fix` inject an administrator verifier. The beta25
HAMCP-199 test now expects a refusal, and its refusal tests use an
identified principal so they keep testing their own reasons.

### Mutation proof

Each mutation was applied to a disposable copy of the worktree, and the
HAMCP-205 module (plus the HAMCP-184 and beta25 approval modules for the
governance and L1 mutations) was run. All 20 were killed:
- the role check skipped on each of the ten pages;
- the identity header trusted instead of the lookup;
- the default principal accepted again in the Ingress gate;
- the role cached in the verifier, and in the gate;
- a stored anonymous approval accepted again (any flag), and a stored
  anonymous approval with a pre-HAMCP-184 `true` flag accepted;
- the anonymous decision fallback restored in governance;
- the re-approve reason dropped from the refusal;
- an inactive owner treated as an administrator (Core's rule restored), and
  the owner check moved before the inactive check.

## 2. Release CI and publication

Require exact-head CI before release acceptance. Pending or unavailable jobs
are not passing evidence.

Publication needs Josh's Ready authorization and the protected merge
handoff. Verify the following with the shipped verifiers:
- the merged source;
- v2.4.1-beta.21;
- the immutable version and commit images;
- provenance and SBOM.

Never invent future image identities. Retain the beta.20 artifacts and the
known-good earlier artifacts for recovery.

No stored format changes. Rollback is reinstalling the beta.20 artifact; it
restores non-administrator access to the approval pages on Supervisor
2026.10.0 and 2026.10.1 and the eligibility of stored standard-class
anonymous approvals.

## 3. Installed Engineering

All installed checks below are read-only unless a step names Josh's explicit
authorization.

1. **Installed-image binding.** Separately authorize installation of the
   published Engineering artifact. Bind the running add-on to:
   - the published image index digest;
   - the v2.4.1-beta.21 tag;
   - the protected `main` merge commit that contains this PR.

   A source version alone does not prove the installed image.
2. **Catalog unchanged.** Compare the public descriptors with beta.20: the
   same tool count, input schemas and descriptions. Also compare:
   - Core signed selection;
   - storage and audit health;
   - F3 readiness;
   - zero fallback.
3. **Health: `approval_identity` (read-only).** Call `get_server_health`.
   Require `approval_identity` with `administrator_role_source`, integer
   `refused_attempts`, `refused_by_reason` keyed by `identity_missing`,
   `not_administrator` and `lookup_failed`, and `counter_semantics:
   cumulative_process_events`. Record the counts; nonzero counts are reported
   to Josh with the time since start, not treated as failure.
4. **An administrator sees the approval panel (read-only).** Josh, signed in
   as a Home Assistant administrator, opens the Engineering panel and its
   plan, F3 and typed inboxes. **Pass:** each page loads, and repeating step 3
   shows `refused_attempts` unchanged by these views. **Fail:** a 503 "Could
   not verify administrator role" page means the role lookup does not work in
   the installed environment; report it and do not work around it.
5. **Stored anonymous approvals (read-only).** Call `list_change_plans` and
   `get_change_plan` on any approved, unapplied plan. A plan whose approval
   reports `principal_separation_enforced: false` with reason
   `anonymous_fallback_principal` will be refused at apply; report it to
   Josh, who decides whether to re-plan it. Do not apply it to test the
   refusal; that path is proven offline (§1).
6. **A non-administrator is refused (needs Josh's explicit authorization,
   because it needs a non-administrator Home Assistant user and writes an
   audit entry).** Open the panel as that user. **Pass:** "Administrator
   required" (403) on the inbox, and `refused_by_reason.not_administrator`
   increases by one. On Supervisor 2026.10.2 or later, Supervisor itself may
   refuse first; record which layer refused.
7. **An authorized decision (needs Josh's explicit authorization, because it
   approves a plan).** Approving a real plan as an administrator records the
   principal `home_assistant_admin_ingress:<user id>` and reports
   `principal_separation_enforced: true`. Do not apply unless Josh
   authorizes that change separately.

No push, CI trigger, signing, publication, installation or live action is
authorized by source completion.
