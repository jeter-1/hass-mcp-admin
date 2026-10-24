# Engineering 2.4.1-beta.35 release notes

Engineering 2.4.1-beta.35 adds governed deletion of UI-managed automations
and scripts, shipped **inactive** (HAMCP-155). It follows 2.4.1-beta.34
(HAMCP-165) and includes it. It adds no dependency change; both hashed locks
are beta.34's. **The release activates nothing new.**

Design, Core contract and owner decisions:
[`HAMCP_155_GOVERNED_DELETION_DESIGN.md`](HAMCP_155_GOVERNED_DELETION_DESIGN.md).
Acceptance: [`V2_4_1_BETA35_ACCEPTANCE.md`](V2_4_1_BETA35_ACCEPTANCE.md).

**Release target.** This PR first staged beta.33. PRs #294 (HAMCP-132
slice 2, beta.33) and #303 (HAMCP-165, beta.34) merged first, so this PR
merged the new `main` with a merge commit (never a rebase) and restaged as
beta.35, renaming these two documents. The merge keeps both sides of every
shared runtime seam: the single-planning-family check, the per-family diff
and risk selection, and the unadmitted-entry and dispatch-time admission
checks now cover deletion, labels and both HAMCP-069B profiles together.

## Governed deletion, inactive

- **New tool** `create_configuration_delete_plan`. Input:
  - `deletions`: one to eight `{resource_type: automation | script,
    target_id}` items;
  - `reason`;
  - `expiration_minutes`.
- **New runtime-only Core profile** `core.configuration_delete_operation`
  (adapter `compiled-core-configuration-delete-operation-v1`, contract
  fingerprint
  `sha256:5688af5d141b97ca33eadf251ecfbb8f3b1f833024b0ee1acea4973f558f887d`).
  It is not a signed probe profile. The shipped signed registry (sequence
  12) does not reference it, so it reports
  `pending_signed_registry_admission`.
- **New F3 identities** `delete_automation_configuration` and
  `delete_script_configuration`, bound to that profile only.

While the profile is unadmitted:
- the routing layer refuses before planning
  (`CoreCapabilityUnavailable` / `pending_signed_registry_admission`);
- the governance service refuses first, before any Home Assistant read and
  before storing anything. This covers the tool, the generic builder and the
  internal builder;
- a first F3 apply of a retained or approved plan refuses at the F3 entry,
  before any plan, task, child or lock state is stored and before any Home
  Assistant request (`PROVIDER_UNAVAILABLE`,
  `pending_signed_registry_admission`, `execution_state_persisted: false`).
  This includes F3 composed without Core;
- dispatch still re-checks admission on every F3 path (resume, recovery and
  re-apply of an existing task). Each refuses with zero writes.

## Behavior once a signed registry admits the profile

- **The only write** is `DELETE /api/config/<automation|script>/config/<id>`
  with no body. It is sent once, by the new gateway method, and only for an
  approved plan. The generic configuration write still refuses any action
  other than create or update. `create_configuration_plan` still refuses
  `delete`, legacy `delete_automation` stays prohibited, and upstream
  `ha_config_remove_*` stays unrouted.
- **Drift at dispatch.** Immediately before the DELETE, the live config is
  compared with the bound pre-image fingerprint. Any difference, or an item
  that is already absent, is refused as `stale_target_state` with zero
  writes. A changed consumer-evidence fingerprint is also stale.
- **Proof of absence.** Verification polls, within a bound:
  - the config GET returns 404;
  - the registry entry is gone;
  - the state is gone.
  Config gone but the entry or state still present after the bound is
  `manual_review_required` with `config_removed_runtime_unverified`, never
  success. A config still present after an `ok` DELETE is a readback
  mismatch.
- **Removed effects decide the policy.** A delete is classified by the
  effects it removes. Each protected effect of the pre-image (the HAMCP-213
  physical-effect model, including the unlock/disarm floor) becomes a
  `removed` row with an empty after side; an effect that cannot be resolved
  is `unresolved` and treated as protected. Either requires `ELEVATED_ADMIN`
  and the safety-critical acknowledgement, with the consequence statement
  "Deleting <kind> <id> removes these protected effects: …". Incomplete
  dependency evidence, or a protected or unresolved consumer, also elevates
  ("impact could not be fully determined"). A delete can never be classified
  as having no effect; an ordinary delete is `standard_admin` at medium risk.
- **Disclosed on every plan:** the item stops triggering or being callable;
  any current run stops; references from other automations, scripts and
  dashboards break; dependents found are listed and unavailable coverage is
  stated as unavailable.
- **Rollback** (`rollback_change`) creates a new, separately approved plan.
  For each `succeeded_verified` delete whose target is still absent, it
  recreates the exact captured pre-image under the existing create
  identities. Core restores registry metadata from its `DeletedRegistryEntry`
  only within 30 days; that is disclosed, not promised.

## Shared change

The F3 executor now stores every code of an adapter's pre-intent dispatch
refusal, not only the first. The outcome (`failed_pre_dispatch`, nothing
sent) is unchanged for every adapter.

The F3 entry admission check covers every runtime-only profile the shipped
signed registry lacks. Besides the delete profile, that is the dormant
HAMCP-069B availability and exposure profiles: an unadmitted first apply of
those plans now refuses before storing anything, where it previously stored
a `failed_pre_dispatch` task with zero writes. Signed profiles keep their
existing dispatch-time checks.

## Interim disclosures until HAMCP-181 PR2

- A call refused after intent but before sending can appear as
  send-uncertain or `failed_post_dispatch`.
- On success the top-level `verification.status` can read `not_run`
  (HAMCP-214); `execution_task.verification_summary` is authoritative.
- Recovery from a failed or partial delete is a new governed plan.

## Compatibility

- One new tool. The catalog is **96 tools, 97 with the optional YAML reader
  admitted** (68 static plus 28 delegated).
- Every existing descriptor and input schema is unchanged.
- The 17 signed Core capability profiles, the probe fingerprints and the
  owner-signed Core registry (sequence 12) are unchanged.
- No persisted format changes.

**Protected runtime files.** Josh authorized the HAMCP-155 Engineering
runtime surface: the new `f3_configuration/delete.py`,
`governance/configuration_delete.py` and
`governance/configuration_delete_planning.py`, and changes to the F3
executor, runtime and registry, the configuration adapter and gateway,
governance policy, physical effects, semantic projection, resources, service
and runtime, the Core readmission profiles, models, routes and source, the
capability catalog, routing and provider routing, the MCP server, the
governance tools and tool registry, and the version.

**Dormant workflow wiring.** Josh authorized one `ci.yml` change for the
dormant seq-13 deletion lane, joined to #303's lanes: the `real-ha` job keeps
its single `REAL_HA_SEQ13_LANES: dormant` entry, the shared stage step adds
the delete receipt path, and one new upload step carries the bounded delete
receipt. Both run only when the flag is `enabled`. No other step, condition,
artifact, permission or action changes. The harness calls each seq-13 lane
only when the flag is exactly `enabled`.

No add-on permission, Supervisor route, add-on configuration, signed
registry entry, HOLD, support window, dependency or stable-v1 file changes.

## Post-signing canary

Only after an owner-signed sequence 13 references
`core.configuration_delete_operation`, and with Josh's separate approval: on
one test automation, delete it under an approved plan and read back its
absence, then roll it back and read back that the recreate matches exactly.

## Rollback

Reinstall the 2.4.1-beta.34 artifact. No stored format changes.
