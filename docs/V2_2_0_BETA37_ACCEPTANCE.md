# Engineering 2.2.0-beta.37 acceptance

This procedure applies only after independent review, protected merge,
promotion, publication, separately authorized deployment, and separately
authorized live mutation. Source validation must not access the deployed MCP or
Home Assistant. Use a dedicated disposable `input_boolean`; never use a helper
that controls safety-critical or broad household behavior.

## Pre-deployment gates

1. Confirm the accepted source baseline is the Beta 36 tag commit, the
   advertised source before promotion remains `2.2.0-beta.36`, and
   `.release/next-version` stages only `2.2.0-beta.37`.
2. Confirm stable v1.1.2, workflows, container build inputs, add-on deployment
   configuration, provider compatibility artifacts, and immutable upstream
   admission policy are unchanged.
3. Require the focused HAMCP-089 positive, no-change, stale-state,
   dependency-risk, dependency-drift, response-loss, verification-failure,
   invalid-target, consequence-family, effect-projection, target-specific
   completeness, cross-family lock, downstream-automation lock, routing,
   metadata, schema, audit, persistence, recovery, and no-fallback tests.
4. Require the complete unit, Fast, Full, Evidence, dependency, vulnerability,
   YAML, PowerShell, secret, whitespace, packaging, exact-image, and stable-v1
   gates applicable to the promotion candidate.
5. Confirm source reports 51 static tools: 25 canonical and 26
   Engineering-native. Exact reviewed totals are 77 for 7.14.2, 75 for 8.0.0
   and 8.1.0, and 76 for 8.1.1 and 8.2.0. Require exact equality for the
   selected release; do not substitute a lower-bound assertion.
6. Confirm no test or source path can dispatch `toggle`, a non-`input_boolean`
   target, arbitrary service data, generic service forwarding, ha-mcp write
   delegation, or fallback.

## Post-deployment read-only entry gate

Before any live mutation, confirm through read-only runtime inspection:

- Engineering identifies exactly as `2.2.0-beta.37` and build/image provenance
  matches the approved artifact;
- the runtime catalog contains `create_helper_state_plan` with exact
  `entity_id`, `desired_state`, and `expiration_minutes` inputs;
- its annotations identify a proposal-only non-read-only tool, while
  `apply_change_plan` remains the only execution route;
- capability metadata identifies `direct_home_assistant_state`, contract
  `direct-ha-exact-input-boolean-v1`, no fallback, dependency-aware risk,
  external approval, exact readback, and separate reverse-plan recovery;
- Home Assistant connectivity, governance storage, external approval, F3,
  audit, and provider health are available; and
- the actual static/delegated counts and catalog fingerprint match the approved
  release contract or are truthfully explained by per-tool upstream admission.

Stop before mutation on a version, build, schema, provider, approval, fallback,
health, or catalog mismatch.

## Approved live helper test

Josh must separately approve the exact test helper and both state changes.
Record the helper's dependencies and confirm it has no critical physical
consequence.

1. Read and record the exact helper `entity_id`, current `on`/`off` state,
   `last_changed`, and complete bounded automation-dependency evidence.
2. Choose the opposite explicit desired state and create one
   `create_helper_state_plan` proposal. Require zero dispatch during planning,
   the exact baseline/provider evidence, and `awaiting_approval`. Require
   `standard_admin`/low/none only when complete bounded evidence finds no
   consequential downstream path and every relevant action is proven benign.
   An ordinary static notification with a bounded message and optional title is
   benign. A closed reviewed subset of bounded, nonphysical Companion UI
   controls may also remain low risk with distinct attribution. Location or
   sensor refresh, device control, TTS, notification-channel mutation, other
   kiosk controls, templated messages, and unreviewed notification extensions
   must be unknown/incomplete.
   Direct physical paths must elevate governance. Generic broad targets,
   transitive scene/script actions, custom effects, and relevant unresolved
   evidence must remain unknown/incomplete rather than being called harmless.
   Incomplete, stale, failed, unsupported, or truncated target-relevant evidence
   must not claim conclusive low risk or dispatch eligibility. An unreadable
   automation or unconstrained dynamic entity lookup is plausibly relevant and
   non-conclusive. Only readable static evidence or an extractor-proven
   non-`input_boolean` domain constraint may establish unrelatedness. The domain
   proof must cover the complete canonical expression; compound conditionals,
   boolean expressions, parentheses, filters, or dynamic-reference overflow
   remain non-conclusive.
3. Review and approve the exact plan hash through authenticated Ingress. The
   known Android cold-start body-navigation defect is outside Beta 37; do not
   treat notification navigation as this capability's acceptance criterion.
4. Apply once. Require one durable intent, exactly one fixed
   `input_boolean.turn_on` or `input_boolean.turn_off` dispatch, authoritative
   exact-state readback, terminal `succeeded_verified`, and zero fallback.
5. Apply the same plan again. Require `already_applied`, zero redispatch, and
   the same durable task identity.
6. Create a fresh proposal for the original state, approve it separately, apply
   it once, and require exact readback of the recorded pre-state. This is
   cleanup through a new governed change, not automatic rollback.

Source/CI acceptance also exercises this lifecycle against disposable Home
Assistant 2026.7.2, 2026.8.0, and 2026.8.1. It proves off-to-on, duplicate
suppression, a separately approved on-to-off plan, and response-loss readback
through production code. This is disposable CI evidence, not household/live
acceptance.

If the initial helper is already in the requested state, require a verified
no-change response, no plan, no approval, and no dispatch. To test final
preflight no-op separately, an authorized operator may place the disposable
helper into the desired state after approval but before apply; require verified
success, zero dispatch, and unconsumed approval.

## Failure and truthfulness acceptance

- Baseline drift to a state other than the desired state must fail before
  approval consumption or dispatch.
- A material normalized dependency-evidence change after approval must report
  dependency-risk drift and fail before dispatch. This includes service, exact
  entity target, device/area/floor/label selector, risk-relevant action data,
  or normalized action-structure changes. Sensitive and oversized values must
  contribute through bounded hashes. A generation, timestamp, alias, or other
  display-only change with identical normalized effect evidence must not cause
  a false rejection.
- Confirmed provider rejection must be reported as post-dispatch failure.
- Lost provider response may succeed only when independent readback observes
  the exact desired state; it must never trigger redispatch.
- A wrong readback must be `failed_post_dispatch`, not success or rollback.
- Readback unavailability must remain pending or fail according to its bounded
  evidence deadline without blind retry of the mutation.
- Audit and health must preserve direct-provider attribution, dispatch/response
  truth, verification result, request correlation, and zero fallback without
  retaining secrets or unbounded Home Assistant content.
- Location/sensor refresh, device control, TTS, notification-channel mutation,
  other unreviewed kiosk controls, dynamic notification messages, and custom
  effect-bearing notification data must never receive `none` consequence.
- Bounded notification/badge clearing, widget/complication refresh, and kiosk
  screensaver display may remain `standard_admin`/low/none only through the
  closed reviewed payload contracts and distinct nonphysical-control reason.
- A real provider-shaped partial automation result with any failed read must
  retain bounded failed identities and cannot become complete low-risk evidence.
- More than 1,000 dynamic references must produce deterministic retained
  evidence plus bounded overflow count/fingerprint evidence; the helper plan
  must be truncated and non-dispatchable regardless of provider ordering.

## Operational boundaries

- Do not use a physical device, lock, cover, alarm, climate entity, ordinary
  switch, light, or safety-relevant helper.
- Do not use `toggle`; both the forward and cleanup state must be explicit.
- The state path must use the same
  `helper:input_boolean.<object_id>` exclusive resource identity as helper
  configuration and a shared `reload:input_boolean` dependency. Same-helper
  configuration and controlled reload must conflict; unrelated helpers must
  remain concurrent.
- The state path must also hold every bound
  `automation:<internal_id>` resource and shared `reload:automation` dependency
  before its final dependency refresh. Relevant automation update/reload must
  conflict in both directions; unrelated automation configuration must remain
  concurrent.
- The state path must hold shared exact helper-dependency and unconstrained
  dynamic-template keys. Automation create/update lock calculation must inspect
  both current and proposed content so adding, retaining, removing, or
  materially altering this helper dependency conflicts through dispatch.
  Domain-constrained non-helper and statically unrelated automation changes
  must remain concurrent. Only a complete fixed-domain plus simple-name
  expression may omit the conservative dynamic lock; compound expressions must
  acquire it.
- Do not change dashboards, dashboard metadata, automations, notification
  navigation, integrations, registries, credentials, deployment configuration,
  or provider policy during this acceptance.
- Do not merge, promote, publish, deploy, restart, or execute the live helper
  test without the separate authorization required for that action.
