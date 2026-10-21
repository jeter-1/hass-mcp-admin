# Engineering 2.4.1-beta.28 release notes

Engineering 2.4.1-beta.28 makes exact automation and script changes that touch
a protected physical object actionable with an owner acknowledgement instead
of refusing them (HAMCP-213 PR 1, policy `f2-v3`). It compares the before and
after physical effects of each operation, asks for plan approval only when
they are equivalent, and adds a per-object safety-critical acknowledgement
when they changed or cannot be determined. It applies Josh's owner decisions
OD-1 to OD-10, recorded in
[ADR-022](architecture/ADR-022-OWNER-AUTHORITATIVE-PRODUCT-DIRECTION.md#owner-decision-record-hamcp-213-safety-critical-acknowledgement).

Design: [`HAMCP_213_SAFETY_CRITICAL_APPROVAL_DESIGN.md`](HAMCP_213_SAFETY_CRITICAL_APPROVAL_DESIGN.md).
Acceptance: [`V2_4_1_BETA28_ACCEPTANCE.md`](V2_4_1_BETA28_ACCEPTANCE.md).
Contract: [External approval](EXTERNAL_APPROVAL.md#safety-critical-acknowledgement).

beta.28 follows 2.4.1-beta.27 (HAMCP-132, recorder settings and unregistered
entities), which follows 2.4.1-beta.26 (HAMCP-217, certifi 2026.10.19) and
2.4.1-beta.25 (HAMCP-212, loaded script configuration). It includes
everything in them. It adds no dependency change: both hashed locks are
beta.27's.

No tool, input schema, tool description, signed registry, HOLD, support
window, workflow or stable-v1 change is included. The tool catalog is
unchanged from beta.27 (90 tools, 91 with the optional YAML reader). Plan and
approval outputs gain additive values, listed under Compatibility.

**What it fixes.** Under `f2-v2`, any update that kept or reworded a step near
a lock, alarm panel, valve, water control or garage cover was `prohibited`,
including a wording-only notification change (audit symptom E013). A
logbook mention of a garage cover counted as a target, a notification routed
by service name was reported as an omitted target, and plans outside the
retry shape carried a retry warning.

## Changes

1. **Physical-effect comparison** (`governance/physical_effects.py`, model
   `physical-effect-comparison-v1`). Each automation or script create or
   update is compared before and after, per protected object, with verdict
   `none`, `equivalent`, `changed` or `unresolved`.
   - Reference-only services (the closed set `logbook.log`,
     `system_log.write`, `notify.*` and `persistent_notification.*`) never
     name an effect.
   - Run mode is compared. Waits and delays after the last effect keep a run
     occupied and count.
   - Conservative OD-10: a reference step marked `continue_on_error` is
     ignored only when no effect follows it. Added ahead of an effect, it is
     `changed`, because it can still abort the run.
   - An unknown service on a protected object counts as an effect. Unknown
     structure is `unresolved`.
   - An effect-capable call (`cover`, `lock`, `alarm_control_panel`,
     `valve`, `siren`, `switch`, `water_heater`, `homeassistant.turn_on`,
     `turn_off` or `toggle`, an unknown service, or a device action of those
     domains) whose target is templated, variable-driven, or an area, device,
     floor or label selector is `unresolved`. So is any call whose service
     name is templated (`service_template` included). Registry selectors are
     not resolved.
   - A reference-only step that can run before a protected effect is
     compared in full: nested data, target, `continue_on_error`, `enabled`
     and position relative to conditions, waits and branches. Any difference,
     wording included, is `changed`. Wording after the last effect stays
     `equivalent`.
   - The comparison is an allowlist. Only the keys in the design's
     ignorable-differences table (D2 item 8) are ignored, such as an
     `alias` on a step, option or branch. Anything else that differs on an effect
     path is `changed`, including an `enabled` flag (on a step, a `choose`
     option or a `parallel` branch) and a key Engineering does not know.
   - Review bounds (nesting depth 24, 4096 steps, called scripts 8 deep or
     16 distinct, 256 callee analyses) are a floor, not a verdict. A change
     past them is `prohibited` on every route, reason
     `safety_critical_review_bounds_exceeded`, with the unlock or disarm
     floor computed first.
2. **Policy mapping** (`f2-v3`):

   | Verdict | Policy | Required actions |
   | --- | --- | --- |
   | `none` | as before | as before |
   | `equivalent` | `elevated_admin` | `plan_approval` |
   | `changed` or `unresolved` | `elevated_admin`, risk delta `high` | `plan_approval`, `safety_critical_acknowledgement` |
   | any, with an incomplete execution contract | `prohibited` | none |

   An unlock or disarm effect always requires the acknowledgement (OD-6(b),
   which supersedes part of ADR-012). If the comparison finds no protected
   effect while the risk classifier does, the plan is `prohibited`. The Beta 33
   retained-effect proof, the garage inverse and the retry-transfer paths are
   unchanged and are evaluated first, except where an unlock or disarm effect
   is or may be present: none of them applies there. On the legacy
   contract-v1 route, such an unlock or disarm change is `prohibited`. Script updates join automation updates
   as exact writes.
3. **Safety-critical acknowledgement** (Ingress panel only, action authority
   version 4). After plan approval, the panel lists each protected object with
   its verdict and before/after effects, each with a required checkbox. The
   submitted object set and comparison hash must match the recomputed values
   exactly, or nothing is recorded (`safety_critical_acknowledgement_mismatch`).
   The same administrator, single-use CSRF, expiry and stale-page rules apply.
   Dispatch rechecks the acknowledgement independently of the bundle state
   (`safety_critical_acknowledgement_required`), so plan approval alone never
   dispatches such a plan. No MCP tool can grant it. The F3 operational adapter
   refuses a bundle bound to it. A plan with more than 32 objects to
   acknowledge is `prohibited`
   (`too_many_protected_objects_to_review_split_the_change`).
4. **Risk precision (E013).** A logbook or system-log mention is not a target.
   A notify call routed by service name has no omitted target. The retry
   warning appears only for a real retry candidate. The recipient reason
   appears only when the recipient set changes.
5. **Disclosure rename (OD-4).** `principal_separation_status.reason` reports
   `no_decision_recorded`, or `not_approvable` for a prohibited plan, instead of
   `no_external_approver_exists`. Stored records are not rewritten.
6. **Audit and errors.** New events `safety_critical_acknowledgement_requested`,
   `_granted` and `_failed`. New stable error codes
   `safety_critical_acknowledgement_required` and
   `safety_critical_acknowledgement_mismatch`. The advisory push notification
   names the acknowledgement step; it carries no authority.

## Compatibility and rollback

- **More acknowledgements by design.** A cover, switch, siren, water heater,
  generic `homeassistant` or unknown-service call with an area, device,
  floor, label or templated target now needs the acknowledgement, as does a
  wording change to a notification that runs before a protected effect.
  `light`, `fan` and `climate` calls with such targets, and unknown
  services with no target, are not flagged (residual).
- **Called scripts.** A call to a script that contains a protected effect,
  directly or through further scripts, is compared as that effect, reached at
  the calling step. A guard, trigger, mode or wait change before
  `script.close_…` therefore needs the acknowledgement. A call to a script
  that cannot be resolved (templated name, unknown, unreadable or
  blueprint-backed, cycle, more than 8 levels) is `unresolved` and needs it
  too. An unchanged call stays `equivalent`. Each called script's
  fingerprint is bound to the plan. It is re-read before approval is
  consumed and again before each write. A callee edited outside the plan, or
  unreadable, refuses as `stale_target_state` (reason `stale_called_script`)
  with no write, on the service path and in F3; replan.
  `automation.*` and `scene.*` calls are not followed.

- **Policy snapshots.** `f2-v1` and `f2-v2` stay readable. A pending `f2-v2`
  plan fails its policy snapshot under `f2-v3` and must be replanned; terminal
  records are not rewritten. Two read-only exceptions admit an exact `f2-v2`
  snapshot. F3 rollback creation admits an applied source as evidence; the
  inverse is evaluated under `f2-v3`. `reverify_configuration_task` admits
  the consumed plan of the task it reverifies, as beta.27 does; it reads
  and records receipts and never dispatches.
- **Upgrade with work in flight.** A task still executing at upgrade under an
  `f2-v2` plan cannot regain authority: startup reconciliation moves it to
  manual review (the existing fail-closed path). Let in-flight tasks finish
  before upgrading.
- **Stored records (additive).** Plans may carry the new action kind in
  `required_acknowledgements`, and an acknowledgement record with
  `kind: safety_critical_acknowledgement`, `authority_version: 4`,
  `safety_critical_projection_hash` and `acknowledged_objects`. It uses the
  historical `elevated_risk_acknowledgement` slot. Authority version 3
  bundles stay valid for plans that do not need it. Task approval evidence
  records the acknowledged set as a digest and a count.
  A configuration operation that calls a script also stores
  `called_scripts`, the called-script snapshot bound into the plan hash. The
  key is absent otherwise, so other records are byte-identical.
- **Public output (additive).** The new `ApprovalActionKind` value, reason
  codes, the `safety_critical_acknowledgement` review section, the OD-4 values,
  the audit events and the two error codes.
- **Sanitizer.** The acknowledgement review is a seventh allow-listed
  `rescan_stored_plan` call site (governance family), like the plan review
  summary.
- **Health.** The elevated-acknowledgement counters do not count safety-critical
  acknowledgements; their keys are unchanged.
- **Rollback** is reinstalling the beta.27 artifact. beta.27 cannot read any
  plan record that beta.28 writes. Every new plan carries policy version
  `f2-v3`, and beta.27 accepts only `f2-v1` and `f2-v2`. At load it moves
  each such record, pending or terminal (applied included), to the plan
  store's quarantine directory and counts it as corrupt. A record that names
  the new action kind, or carries `called_scripts`, fails on that too.
  Reinstalling beta.28 does not restore a quarantined record. Execution-task
  records stay readable. Before a rollback, apply, reject or let expire every
  pending plan, and keep the plan store backup if the plan history matters.
  Executed offline for configuration plans; by source, every plan creation
  path stamps `f2-v3`.
- **Boundaries.** No new tool, provider, upstream dispatch, direct-HA fallback
  or forwarding. The only new write reachability is the existing governed
  configuration apply for exact plans that were `prohibited` before and now
  carry the owner's acknowledgement.
