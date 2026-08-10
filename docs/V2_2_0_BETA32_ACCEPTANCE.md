# Engineering 2.2.0-beta.32 acceptance

This procedure applies only after the draft is independently reviewed, merged,
promoted, published, and separately authorized for deployment. Source
validation must not access the deployed Home Assistant environment. Use a fresh
plan and challenge; do not apply or reuse the failed Beta 31 notification
canary.

## Pre-deployment gates

1. Confirm source, generated add-on metadata, runtime version, image labels,
   tag, and accepted commit all identify `2.2.0-beta.32`.
2. Confirm the complete unit suite, Full and Evidence gates, exact-image bake,
   exact add-on runtime acceptance, and both supported Home Assistant lanes
   pass.
3. Inspect exact-image notification evidence. Require a realistic Supervisor
   self-info response larger than 32 KiB, exactly one allowlisted notification
   submission, a matching `url`, `clickAction`, and URI action path, no
   `authenticationRequired` action property, authority `none`, and fallback
   zero.
4. Confirm runtime health reports `submitted` separately from `delivered`,
   `clear_submitted` separately from `cleared`, and both handset-observability
   flags false.
5. Confirm the public MCP catalog, ha-mcp 8.2.0 admission, dashboard routes,
   governance/F3 authority, and stable v1.1.2 are unchanged.
6. Preserve the configured service `notify.mobile_app_pixel_9_pro_xl`. Do not
   change add-on permissions, identity authority, or add a fallback.

## Read-only smoke checks

1. Confirm `server_info` and build provenance identify the accepted Beta 32
   artifact.
2. Confirm health reports approval notifications configured, the worker
   running, authority `none`, submission semantics
   `home_assistant_service_response_only`, and fallback `none`.
3. Confirm self identity is `verified_supervisor_self_info` after the first
   fresh submission and no identity fallback or guessed slug exists.

## Fresh notification canary

Use a new small, reversible, independently acceptable plan. Do not apply or
reuse an earlier challenge.

1. Request external approval once. Confirm the result remains
   `approval_pending`, notification authority is `none`, and a fresh
   notification is queued.
2. Confirm Engineering makes exactly one allowlisted Home Assistant notification
   service submission. Health and audit must report `submitted`, not
   `delivered`, with a provider response received and no fallback.
3. Independently confirm the Pixel receives the notification. This handset
   observation is live acceptance evidence and must not be inferred from the
   Engineering submission counter.
4. Open the notification. Confirm the exact fresh plan review opens through
   authenticated administrator Ingress and that the URL contains no decision
   authority or sensitive plan material.
5. Exercise either approval or rejection through the existing Ingress form.
   Confirm persisted plan authority, exact hash, principal separation, policy,
   sequence, and one-time CSRF validation exclusively govern the decision.
6. Confirm Engineering submits exactly one tagged clear request and reports
   `clear_submitted`, not `cleared`.
7. Independently confirm the notification disappears from the Pixel. Again,
   this handset observation is acceptance evidence, not a runtime receipt.
8. Confirm health and audit contain only safe categories and bounded
   identifiers, with no configured device service, notification payload,
   challenge ID, Supervisor response, add-on options, authenticated URL,
   credential, or token.

## Failure acceptance

Prove a structured Home Assistant rejection increments `failed`, keeps
`submitted` and `delivered` at zero, reports `provider_rejected` with
`provider_response_received: true`, and leaves approval authority unchanged.
Timeout and transport failures must not claim a provider response. Failure to
resolve authoritative Supervisor self identity must produce no notification,
no guessed identity, and no fallback.

## Acceptance result

Beta 32 passes only when a fresh notification is independently observed on the
Pixel, opens the exact Ingress review, the governed decision remains exclusively
Ingress-authorized, and tagged clearing is independently observed. Source and
runtime evidence must remain truthful about service submission versus handset
delivery. Any direct notification approval authority, payload rejection,
guessed identity, fallback, or false delivery claim is a blocker.
