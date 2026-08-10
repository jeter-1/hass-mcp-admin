# Engineering 2.2.0-beta.31 workstream 1 acceptance

This procedure applies only after the draft is independently reviewed, merged,
promoted, published, and separately authorized for deployment. Source
validation must not access the deployed Home Assistant environment. Do not
apply or reuse the pending Beta 30 notification canary.

## Pre-deployment gates

1. Confirm source, generated add-on metadata, runtime version, image labels,
   tag, and accepted commit all identify `2.2.0-beta.31`.
2. Confirm the complete unit suite, Full and Evidence gates, exact-image bake,
   exact add-on runtime acceptance, and both supported Home Assistant lanes
   pass.
3. Inspect the exact-image notification result. It must show a Supervisor
   self-info payload larger than 32 KiB, one self-info request, exactly one
   allowlisted notification dispatch, the exact Ingress-link check passing,
   authority `none`, and fallback count zero.
4. Confirm the public MCP catalog, ha-mcp 8.1.1 admission, dashboard routes,
   governance authority, and stable v1.1.2 are unchanged.
5. Preserve the configured service `notify.mobile_app_pixel_9_pro_xl`. Do not
   change add-on permissions or add an identity fallback.

## Read-only smoke checks

1. Confirm `server_info` and build provenance identify the accepted Beta 31
   artifact.
2. Confirm health reports approval notifications configured, the worker
   running, authority `none`, delivery semantics `best_effort_advisory`, and
   fallback `none`.
3. Confirm no notification has guessed an identity. Before a new challenge,
   identity may be unresolved because resolution is lazy.

## Fresh notification canary

Use a new small, reversible, independently acceptable plan. Do not apply or
reuse the Beta 30 challenge.

1. Request external approval once. Confirm the result remains
   `approval_pending`, notification authority is `none`, and a new notification
   is queued.
2. Confirm one notification is delivered through
   `notify.mobile_app_pixel_9_pro_xl`. It must contain only the normal advisory
   message and **Open Approval Panel** URI action, with no Approve/Reject action,
   challenge ID, plan hash, CSRF nonce, credential, diff, or configuration.
3. Open the notification and confirm the exact fresh plan review appears only
   through authenticated administrator Ingress. The Ingress page must resolve
   current persisted authority and issue its existing one-time CSRF token.
4. Exercise one ordinary decision through Ingress. Confirm the notification is
   independently cleared by its deterministic tag and the decision was governed
   exclusively by persisted plan authority, principal checks, exact hash,
   policy, sequence, and CSRF validation.
5. Confirm health reports truthful queued, delivered, failed, clear-queued, and
   cleared counters. The installed identity status must be
   `verified_supervisor_self_info`, the identity failure category must be empty,
   and fallback must remain zero.
6. Confirm audit and logs contain only safe categories and bounded identifiers,
   with no Supervisor response content, add-on options, configuration,
   translations, configured mobile service, notification payload, challenge ID,
   authenticated URL, credential, or token.

## Failure acceptance

Source and exact-image tests must prove that an over-512-KiB response, non-200
status, malformed response, timeout, and transport failure produce their exact
safe category, no Home Assistant notify call, no guessed identity, and no
authority change. Do not weaken authentication, networking, or the deployed
Supervisor response to manufacture these failures live.

## Acceptance result

Workstream 1 passes only when a fresh notification is delivered and opened,
Ingress alone governs the decision, clearing succeeds independently, health and
audit remain truthful, and all fallback counters remain zero. A guessed slug,
raw Supervisor content in any observable or persisted surface, notification
authority, or failure to resolve a legitimate bounded response is a release
blocker.
