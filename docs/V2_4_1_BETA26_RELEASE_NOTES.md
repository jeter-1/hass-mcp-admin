# Engineering 2.4.1-beta.26 release notes

Engineering 2.4.1-beta.26 makes exact automation and script changes that touch
a protected physical object actionable with an owner acknowledgement instead
of refusing them (HAMCP-213 PR 1, policy `f2-v3`). It compares the before and
after physical effects of each operation, asks for plan approval only when
they are equivalent, and adds a per-object safety-critical acknowledgement
when they changed or cannot be determined. It applies Josh's owner decisions
OD-1 to OD-10, recorded in
[ADR-022](architecture/ADR-022-OWNER-AUTHORITATIVE-PRODUCT-DIRECTION.md#owner-decision-record-hamcp-213-safety-critical-acknowledgement).

Design: [`HAMCP_213_SAFETY_CRITICAL_APPROVAL_DESIGN.md`](HAMCP_213_SAFETY_CRITICAL_APPROVAL_DESIGN.md).
Acceptance: [`V2_4_1_BETA26_ACCEPTANCE.md`](V2_4_1_BETA26_ACCEPTANCE.md).
Contract: [External approval](EXTERNAL_APPROVAL.md#safety-critical-acknowledgement).

beta.26 follows 2.4.1-beta.25 (HAMCP-212, loaded script configuration) and
includes everything in it. It adds no dependency change: both hashed locks are
beta.25's.

No tool, input schema, tool description, signed registry, HOLD, support
window, workflow or stable-v1 change is included. The tool catalog is
unchanged from beta.25 (88 tools, 89 with the optional YAML reader). Plan and
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
   unchanged and are evaluated first. Script updates join automation updates
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
   refuses a bundle bound to it. More than 32 objects are refused at creation.
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

- **Policy snapshots.** `f2-v1` and `f2-v2` stay readable. A pending `f2-v2`
  plan fails its policy snapshot under `f2-v3` and must be replanned; terminal
  records are not rewritten. The one exception admits an applied `f2-v2`
  source as read-only evidence when F3 creates a governed rollback inverse.
  The inverse is evaluated under `f2-v3`.
- **Stored records (additive).** Plans may carry the new action kind in
  `required_acknowledgements`, and an acknowledgement record with
  `kind: safety_critical_acknowledgement`, `authority_version: 4`,
  `safety_critical_projection_hash` and `acknowledged_objects`. It uses the
  historical `elevated_risk_acknowledgement` slot. Authority version 3
  bundles stay valid for plans that do not need it. Task approval evidence
  records the acknowledged set as a digest and a count.
- **Public output (additive).** The new `ApprovalActionKind` value, reason
  codes, the `safety_critical_acknowledgement` review section, the OD-4 values,
  the audit events and the two error codes.
- **Health.** The elevated-acknowledgement counters do not count safety-critical
  acknowledgements; their keys are unchanged.
- **Rollback** is reinstalling the beta.25 artifact. beta.25 cannot parse the
  new action kind: it moves any plan record that names it to the plan
  store's quarantine directory and counts it as corrupt. Reinstalling beta.26
  does not restore a quarantined record. Before a rollback, apply, reject or
  let expire each plan that needs the safety-critical acknowledgement. Other
  `f2-v3` plans fail their policy snapshot on beta.25 and must be replanned.
- **Boundaries.** No new tool, provider, upstream dispatch, direct-HA fallback
  or forwarding. The only new write reachability is the existing governed
  configuration apply for exact plans that were `prohibited` before and now
  carry the owner's acknowledgement.
