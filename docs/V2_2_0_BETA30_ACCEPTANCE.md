# Engineering 2.2.0-beta.30 acceptance

This procedure applies only after the Beta 30 draft is independently reviewed,
merged, promoted, published, and separately authorized for deployment. Source
validation in the draft PR must not access the deployed Home Assistant system.

## Pre-deployment gates

1. Confirm source, add-on metadata, runtime version, generated release state,
   image labels, tag, and accepted commit all identify `2.2.0-beta.30`.
2. Confirm the complete unit suite, Full and Evidence gates, exact-image bake,
   and both supported Home Assistant compatibility lanes pass.
3. Confirm the public MCP catalog and provider/tool accounting are unchanged
   from Beta 29 and stable v1.1.2 has no diff.
4. Set `approval_notification_service` to the exact existing Companion App
   service for the one intended operator device, in the form
   `notify.mobile_app_<device>`. This live options change requires separate
   deployment authorization. Do not enter a person name, device display name,
   URL, token, or generic notify group.

## Read-only smoke checks

1. Confirm `server_info` and build provenance identify the accepted Beta 30
   artifact.
2. Confirm health reports the approval notification route as
   `direct_home_assistant_rest_allowlisted_notification`, authority `none`,
   delivery semantics `best_effort_advisory`, and fallback `none`.
3. Open the Ingress approval panel and verify the **Approvals** and
   **F3 reconciliation** navigation links work. No notification is required for
   these checks.

## Governed notification canary

Use a small, reversible, already-understood Home Assistant configuration change
whose governed plan and rollback are independently acceptable. Creating and
applying that plan are live actions and require their normal separate
authorization.

1. Request external approval once. Confirm the MCP result remains
   `approval_pending` and reports notification status separately with authority
   `none`.
2. Confirm one normal-priority notification arrives and exposes only **Open Approval Panel**.
   It must not contain Approve or Reject actions, a plan hash,
   challenge ID, configuration content, credential, or secret.
3. Open it. Confirm the exact review appears only through authenticated Home
   Assistant Ingress and displays the current persisted challenge. A signed-out
   or non-administrator session must not gain review or decision authority.
4. Use the review page to approve or reject. Confirm the decision page links to
   the freshly resolved approval inbox. For an elevated test, confirm the first
   decision links to the exact second acknowledgement rather than a stale
   previous/next snapshot.
5. Confirm the tagged mobile notification clears after the challenge reaches an
   approved, rejected, expired, invalidated, or consumed lifecycle state being
   tested.
6. Confirm audit and health record truthful delivery/clear status, provider
   response status, and zero fallback without exposing the configured mobile
   service, notification payload, challenge ID, or secrets.

## Failure canary

Only if a reversible, approved test method is available, temporarily select an
invalid-but-allowlisted nonexistent mobile service and create a fresh bounded
approval request. Confirm the challenge is still persisted and actionable,
notification delivery reports a normalized failure, no approval authority
changes, and no arbitrary service or fallback is attempted. Restore the exact
approved option afterward. Do not weaken Home Assistant authentication or
network controls to manufacture this failure.

## Acceptance result

Beta 30 notification acceptance passes only if the governed approval lifecycle
remains authoritative and usable whether notification delivery succeeds or
fails. A delivered notification is a navigation convenience, not evidence of
approval. Any notification action that directly decides a plan, any generic
service-call reachability, any authority change caused by delivery status, or
any nonzero fallback is a release blocker.
