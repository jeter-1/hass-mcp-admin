# HAMCP-089 exact helper-state action

Status: Beta 37 source increment; deployment and live acceptance are not part
of this change.

## Responsibility and scope

HAMCP-089 adds one Engineering-native governed runtime action for a virtual
Home Assistant helper. The public proposal tool is
`create_helper_state_plan`. It accepts one exact lowercase
`input_boolean.<object_id>` and an explicit desired state of `on` or `off`.

This is an Engineering MCP responsibility because the action requires an
evidence-bound plan, external Home Assistant administrator approval, durable
single-dispatch execution, exact readback, recovery, and truthful result
reporting. It is not a generic replacement for ordinary device control in the
standard Home Assistant MCP.

The following are deliberately unavailable:

- `toggle` or an inferred desired state;
- physical device domains such as `light`, `switch`, `lock`, or `cover`;
- arbitrary services, targets, service data, or forwarding;
- delegated `ha-mcp` dispatch or direct-provider fallback;
- automatic rollback or compensation.

## Plan and execution contract

Planning reads `/states/<exact_entity_id>` and captures the authoritative
`entity_id`, `state`, and `last_changed` baseline. If the helper already has
the desired state, planning returns a verified no-change result, creates no
plan, and performs no dispatch.

Otherwise planning reads the shared dependency index and projects bounded,
target-specific downstream automation/action-consequence evidence. Complete
evidence creates a low-risk `standard_admin` contract-v3 operational plan only
when every relevant action is mechanically proven benign, such as a bounded
notification, or has no effect action. Direct physical actions elevate the
existing governance policy proportionally. Generic broad targets, scene or
script activation, custom action domains, relevant dynamic references, and
other effects that cannot be resolved are classified as unknown/incomplete;
they are never described as harmless. Incomplete, stale, failed, unsupported,
or truncated target-relevant evidence cannot claim conclusive low risk and is
not dispatch-eligible.

Completeness is target-specific. Unrelated dynamic references and unrelated
configuration-read failures remain visible as coverage diagnostics but do not
automatically disable a helper whose relevant evidence is complete. A dynamic
reference, missing action profile, or unreadable source tied to the exact
helper remains non-conclusive. Collection, retained profiles, dynamic evidence,
and output are bounded.

The immutable plan binds the exact target, desired state, state baseline,
normalized dependency evidence and fingerprint, exact downstream automation
resource identities, effect-relevant services, targets and selectors, action
data, and normalized action structure. Sensitive or oversized values contribute
through bounded hashes and unbounded automation bodies are not retained.
Display-only aliases and descriptions do not invalidate approval. The plan also
binds the code-owned direct-provider contract and no-fallback policy. Approval
uses the existing external Home Assistant administrator challenge and exact
plan hash. The MCP caller cannot self-approve.

Immediately before the sole mutation opportunity, F3 reads the exact state
again:

- an unchanged baseline permits one dispatch;
- the desired state already present terminalizes as verified success with zero
  dispatch and without consuming the approval;
- any other state fingerprint change fails as stale before dispatch; and
- a material normalized dependency-risk change invalidates the prior approval
  and fails separately before dispatch.

The action holds the same canonical
`helper:input_boolean.<object_id>` resource used by helper configuration and a
shared `reload:input_boolean` dependency. It also holds each approval-bound
`automation:<internal_id>` resource and shared `reload:automation` dependency.
This serializes same-helper state and configuration operations, blocks relevant
automation update or reload drift across preflight, dispatch, readback, and
verification, and keeps unrelated helpers and unrelated automation updates
concurrent. The final dependency refresh occurs only after the complete lock
set is held.

The only permitted WebSocket command shapes are:

```json
{"type":"call_service","domain":"input_boolean","service":"turn_on","target":{"entity_id":"input_boolean.<object_id>"}}
```

```json
{"type":"call_service","domain":"input_boolean","service":"turn_off","target":{"entity_id":"input_boolean.<object_id>"}}
```

The executor commits durable intent before the command. After dispatch it
reads the exact state through Home Assistant REST and reports success only when
the desired state is observed. A lost provider response is reconciled by
readback only. Duplicate apply and recovery paths never redispatch.

## Result truthfulness and recovery

The provider identity is `direct_home_assistant_state` with contract
`direct-ha-exact-input-boolean-v1`; fallback is always `none`. Results preserve
whether dispatch occurred, whether a response was received, and whether exact
readback verified the state.

A provider rejection is a confirmed post-dispatch failure. A response loss is
indeterminate until readback. A mismatched authoritative readback is a
post-dispatch verification failure and is never reported as success. Provider
unavailability during recovery remains readback-only and cannot create another
dispatch opportunity.

No automatic rollback is available. Reversing a successful change requires a
new `create_helper_state_plan` request for the opposite exact state, new current
evidence, and separate approval.

## Acceptance contract

Source acceptance must prove:

- an approved exact `off` to `on` or `on` to `off` change dispatches once and
  passes authoritative readback;
- planning and final-preflight already-desired cases dispatch zero times;
- stale state fails before dispatch;
- material dependency-risk drift fails before dispatch while irrelevant index
  generation changes do not;
- effect-relevant service, exact target, broad selector, action-data, or action
  structure changes cause dependency drift while display-only alias changes do
  not;
- switches, lights, fans, scenes, generic Home Assistant actions, physical
  domains, transitive actions, broad selectors, and custom domains are never
  affirmatively misclassified as harmless, while proven-benign notifications
  remain low risk;
- relevant dynamic or unreadable dependency evidence stays non-conclusive,
  while unrelated uncertainty does not globally disable helper execution;
- relevant automation configuration and reload operations conflict with helper
  execution after exact lock acquisition while unrelated automation work
  remains concurrent;
- duplicate apply and lost-response recovery never redispatch;
- verification mismatch is reported as a post-dispatch failure;
- `toggle`, non-`input_boolean` targets, arbitrary service data, ha-mcp
  delegation, and fallback cannot reach a mutation transport;
- catalog schemas, capability metadata, audit attribution, provider identity,
  static tool counts, and F3 registry declarations remain consistent.

The disposable Home Assistant contract runs the production gateway/F3 path on
2026.7.2, 2026.8.0, and 2026.8.1 for off-to-on, duplicate, separately approved
on-to-off, and response-loss/readback cases. It does not constitute household
or deployed-runtime acceptance.

Live acceptance requires separate deployment authorization and a named test
helper with recorded pre-state, exact expected state, readback, and a separately
approved cleanup plan. No live Home Assistant action is authorized by this
document.
