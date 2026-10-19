# Engineering 2.4.1-beta.21 release notes

Engineering 2.4.1-beta.21 verifies that every Ingress approval page is used by
a Home Assistant administrator (HAMCP-205, absorbing HAMCP-199).
- On every review and every decision it looks up the requesting user's role in
  Home Assistant and requires an administrator, failing closed when the lookup
  fails or is ambiguous.
- It removes the anonymous default approver. A stored approval under the
  anonymous principal must be re-approved.
- The design, the pinned upstream sources and the owner decisions are in
  [`HAMCP_205_ADMIN_VERIFICATION_DESIGN.md`](HAMCP_205_ADMIN_VERIFICATION_DESIGN.md).

It builds on beta.20 (HAMCP-191).

No tool, input schema, description, provider route, signed registry, HOLD,
support window, workflow or stable-v1 change is included. The tool catalog is
unchanged from beta.20. No new dispatch, service-call or fallback path is
reachable; the only new Home Assistant call is one fixed, read-only
`config/auth/list` WebSocket command. Acceptance:
[`V2_4_1_BETA21_ACCEPTANCE.md`](V2_4_1_BETA21_ACCEPTANCE.md).

**What was wrong (beta.20 and earlier).**
- The Ingress approval pages trusted the Ingress session alone. Supervisor
  2026.10.0 and 2026.10.1 let a non-administrator reach an add-on's Ingress
  URL, and Supervisor forwards no administrator flag, so a non-administrator
  could review and decide approvals, F3 reconciliations and typed hold
  releases.
- When Home Assistant supplied no `X-Remote-User-Id`, a decision was recorded
  under the anonymous principal `home_assistant_admin_ingress` (HAMCP-199).
  Since HAMCP-184 it reported `principal_separation_enforced: false`, but a
  standard-class anonymous approval still authorized apply.

## Changes

1. **One administrator check for every approval page.**
   - All ten Ingress routes (plan inbox, review, approve and reject; F3 inbox,
     review and reconcile; typed inbox, review and release) run the same gate
     first, after the existing Ingress origin check.
   - The gate needs exactly one well-formed `X-Remote-User-Id` value. Zero,
     repeated or malformed values are refused as "Approver identity missing"
     (403) without a lookup.
   - It then reads `config/auth/list` once and applies Core's `is_admin` rule
     to the single matching row. A non-administrator is refused as
     "Administrator required" (403). A failed, slow, unknown or ambiguous
     lookup, or an unconfigured verifier, is refused as "Could not verify
     administrator role" (503).
   - The role is never cached, so a role removed between a review and its
     decision is refused at the decision.
   - Each refusal happens before any governance, F3, typed-service or device
     access, issues no CSRF token, writes one
     `ingress_approval_identity_refused` audit entry and increments a health
     counter. The user list is never stored, rendered or logged.
2. **Inactive users never decide (review L1).** Core's `is_admin` keeps an
   inactive owner an administrator. Engineering is stricter: an inactive user,
   the owner included, is not an administrator for approval. System-generated
   accounts are never administrators for approval.
3. **No anonymous approver (owner decision).**
   - Governance no longer defaults a missing principal. A missing or anonymous
     principal is refused with `external_approval_invalid`, and no decision is
     recorded.
   - A stored approval under the anonymous principal, whatever its recorded
     `principal_separation_enforced` flag, no longer authorizes apply or
     rollback for any risk class. Both refuse it with
     `external_approval_required` and `details.reason:
     anonymous_approval_requires_reapproval`, before any dispatch, snapshot,
     approval consumption or task, recording only the usual refusal.
   - An approved plan cannot be approved twice, so re-approval means planning
     the change again and approving the new plan as a verified administrator.
4. **Health.** `get_server_health` gains `approval_identity`:
   `administrator_role_source`, `refused_attempts`, `refused_by_reason`
   (`identity_missing`, `not_administrator`, `lookup_failed`),
   `last_refusal_reason` and `counter_semantics:
   cumulative_process_events`. These are counts with no user IDs.

## Visible changes

- A non-administrator, or a request without one Ingress user identity, sees a
  403 page instead of the approval panel. If Home Assistant cannot be asked,
  every approval page shows a 503 page.
- Every page view and decision makes one Core WebSocket round trip.
- Approvals, F3 reconciliations and typed hold releases are recorded under
  `home_assistant_admin_ingress:<user id>`.
- A plan approved under the anonymous principal before this release is
  refused at apply and rollback with the re-approve reason. Plan reads still
  show it truthfully (`principal_separation_enforced: false`, reason
  `anonymous_fallback_principal`).

## Compatibility and rollback

- **Stored formats** are unchanged. Records are not rewritten; anonymous
  approvals are refused when used, not migrated.
- **Responses** gain only the additive health `approval_identity` object and
  the `details.reason` on the anonymous-approval refusal.
- **Rollback** is reinstalling the beta.20 artifact, which reads every record
  this release writes. Rolling back restores non-administrator access to the
  approval pages on Supervisor 2026.10.0 and 2026.10.1, and restores the
  eligibility of stored standard-class anonymous approvals.
