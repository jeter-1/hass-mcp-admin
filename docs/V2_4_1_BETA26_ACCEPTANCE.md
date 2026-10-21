# Engineering 2.4.1-beta.26 acceptance

This contract covers HAMCP-213 PR 1: the physical-effect comparison, policy
`f2-v3` and the safety-critical acknowledgement. It builds on 2.4.1-beta.25
(HAMCP-212).

See also:
- Release notes:
  [`V2_4_1_BETA26_RELEASE_NOTES.md`](V2_4_1_BETA26_RELEASE_NOTES.md).
- Design: [`HAMCP_213_SAFETY_CRITICAL_APPROVAL_DESIGN.md`](HAMCP_213_SAFETY_CRITICAL_APPROVAL_DESIGN.md).
- Owner decisions: [ADR-022](architecture/ADR-022-OWNER-AUTHORITATIVE-PRODUCT-DIRECTION.md#owner-decision-record-hamcp-213-safety-critical-acknowledgement).

**Release target.** `2.4.1-beta.26`. It is staged by one owner-authorized run
of `scripts/promote_next_release.py --apply` on a base that already carries
2.4.1-beta.25. The run consumes the declaration.

The contract separates four stages:
1. source and offline acceptance;
2. release CI;
3. publication;
4. installed Engineering acceptance.

Completing one stage grants nothing for the next.

## 1. Source and offline acceptance

Bind the following:
- Josh's HAMCP-213 objective and owner decisions OD-1 to OD-10 (register row
  HAMCP-213);
- `2.4.1-beta.26` materialized in all three version authorities, with exact
  acceptance and release-note resolution from `scripts/codex-context.py` and
  no staged declaration left over;
- the PR head SHA;
- the passing PR `validate` run for that exact head;
- the Evidence tier log named in the PR.

The review needs a security section covering:
- the new reachable writes: exact automation and script plans that were
  `prohibited` under `f2-v2` now reach the existing governed apply, only after
  plan approval and, where required, the acknowledgement;
- a false `equivalent` (main risk), and the mutation evidence for each
  comparison rule;
- acknowledgement forgery: exact object set and hash, same administrator,
  single-use CSRF, expiry, Ingress-only grant and the independent dispatch
  check;
- the two read-only `f2-v2` exceptions (F3 rollback creation, and
  reverification of a consumed task) and their confinement;
- the beta.25 rollback quarantine of records that name the new action kind.

Preserve the following:
- stable v1.1.2;
- every tool descriptor and input schema (88 tools, 89 with the optional YAML
  reader admitted, as in beta.25);
- the Beta 33 retained-effect proof, the garage inverse and the retry-transfer
  paths, except that none applies where an unlock or disarm effect is or may
  be present;
- probe fingerprints, Core capability profiles, owner-signed Core registry
  entries and the signed ha-mcp registry;
- the HOLD and the support window;
- workflows and dependencies.

### Offline proof

No test reaches a live system.

- `tests/test_hamcp213_safety_critical_acknowledgement.py` (16 tests): useful
  success (exact acknowledgement dispatches once and verifies), plan approval
  alone never dispatches, forged or drifted object sets, other principal,
  replayed CSRF, expiry, stale state without a write, the 32-object bound, no
  tool grant, one required checkbox per object, notification text and
  version 3 hash stability.
- `tests/test_hamcp213_pr001_fixtures.py` (8 tests): the anonymized PR001
  plan gives op1 `none` (standard), op2 `changed` (elevated with the
  acknowledgement), op3 `equivalent` (plan approval only) and op4 `none`; the
  whole plan is approvable and dispatches only after the acknowledgement; the
  seven design mutations each give `changed`.
- `tests/test_hamcp213_physical_effect_regressions.py` (11 tests): one test per
  E013 symptom, the unlock floor, the unresolved target, and the abort-point,
  mode, run-occupancy and reference-only rules.
- `tests/test_hamcp213_review_corrections.py` (16 tests): every
  independent-review reproduction through both the configuration service and
  the F3 runtime. H1: templated, variable-driven, area, device, floor and
  label targets and templated service names are `unresolved`. H2: reference
  steps before an effect compared in full. H3: unlock and disarm always
  require the acknowledgement, and no shortcut applies to them. Each needs
  the acknowledgement, plan approval alone writes nothing, and the exact
  acknowledgement dispatches once.
  Controls keep plan approval only. The PR001 verdicts are unchanged, and
  comparisons are deterministic.
- `tests/test_hamcp213_called_script_effects.py` (16 tests): protected
  effects reached through called scripts, through both the configuration
  service and the F3 runtime. A condition, trigger, mode, wait, branch or
  pre-effect reference change before a garage-closing script call is
  `changed`, as is an added or removed call; the `script.turn_on` and
  `homeassistant.turn_on` entity forms resolve through the registry. A
  templated or variable script name, a selector target, an unknown,
  unreadable or blueprint-backed script, a non-script registry row, a cycle
  and a chain deeper than 8 are `unresolved`. A script calling a script that
  unlocks keeps the OD-6(b) floor, even when the call is `equivalent`. An
  unchanged call stays `equivalent` with plan approval only; a non-protected
  callee and `script.reload` are `none`. A callee changed in the same plan is
  compared by its own operation. The PR001 verdicts are unchanged, and the
  snapshot is bound into the plan hash and the policy subject.
- `tests/test_hamcp213_f2_v2_rollback_confinement.py` (8 tests): only F3
  rollback creation, and reverification of a consumed task, admit an `f2-v2`
  plan; every other path refuses `f2-v2` plans without a state change or a
  write.
- `tests/test_configuration_reverification.py`: a task applied by the shipped
  beta6 writer under `f2-v2` still reverifies after the upgrade.
- Mutations, each killed: the reference-only table, the abort-point rule (both
  directions), the mode rule, the run-occupancy rule, the object-set check,
  the dispatch check, and three widenings of the `f2-v2` exception. The
  review corrections add: a templated target or service treated as `none`,
  unresolved targets ignored, reference steps compared by presence and order
  or signature only, the unlock and disarm floor disabled or bypassed, and an
  id-only comparison memo. The called-script comparison adds: script calls
  ignored, and an unresolvable called script treated as `none`. The PR lists
  counts and logs.

## 2. Release CI and publication

Require exact-head CI before release acceptance. Pending or unavailable jobs
are not passing evidence.

Publication needs Josh's Ready authorization and the protected merge
handoff. Verify the following with the shipped verifiers:
- the merged source;
- v2.4.1-beta.26;
- the immutable version and commit images;
- provenance and SBOM.

Never invent future image identities. Retain the beta.25 artifacts and the
known-good earlier artifacts for recovery.

Rollback is reinstalling the beta.25 artifact, after applying, rejecting or
expiring each plan that needs the safety-critical acknowledgement or calls a
script (beta.25 quarantines those records).

## 3. Installed Engineering

Steps 1 to 4 are read-only. Step 5 needs its own owner authorization.

1. **Installed-image binding.** Separately authorize installation of the
   published Engineering artifact. Bind the running add-on to:
   - the published image index digest;
   - the v2.4.1-beta.26 tag;
   - the protected `main` merge commit that contains this PR.
2. **Catalog.** Using fresh protocol enumeration, confirm:
   - 88 tools, or 89 with the optional YAML reader admitted;
   - every descriptor unchanged from beta.25;
   - Core signed selection, storage and audit health, and zero fallback.
3. **Planning only.** Create, without approving, a wording-only update plan
   for an automation that closes a garage cover. Confirm `elevated_admin`,
   required actions `["plan_approval"]` and the comparison verdict
   `equivalent`. Create, without approving, an update that adds a step ahead
   of the close. Confirm `["plan_approval", "safety_critical_acknowledgement"]`.
   Create, without approving, an update that adds a condition ahead of a call
   to a garage-closing script. Confirm the same required actions and a row
   for the garage cover. Planning does not write or trigger anything.
4. **Review page.** Open the Ingress panel for the second plan. Confirm one
   required checkbox per listed object, and that submitting plan approval
   alone leaves the plan at `pending_safety_critical_acknowledgement`. Reject
   both plans afterwards.
5. **Authorized success (owner-authorized only).** If Josh authorizes a real
   change, apply one acknowledged plan and confirm readback verification and
   the `safety_critical_acknowledgement_granted` audit event.

No push, CI trigger, signing, publication, installation or live action is
authorized by source completion.
