# HAMCP-089 exact helper-state action

Status: Beta 54 owner-authoritative execution source staged; promotion,
deployment, and live acceptance are not part of this change.

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
target-specific downstream automation/action-consequence evidence from the
whole-template obligation ledger. Exact or proven evidence creates a low-risk
`standard_admin` contract-v3 operational plan only
when every relevant action is mechanically proven benign, such as a bounded
ordinary static notification containing only a message and optional title, or
has no effect action. A closed reviewed subset of bounded, nonphysical
Companion UI controls—notification or badge clearing, widget or complication
refresh, and kiosk screensaver display—also remains low risk with distinct
effect attribution. Location or sensor refresh, device control, TTS,
notification-channel mutation, other kiosk controls, dynamic content, and
unreviewed notification extensions are unknown/incomplete rather than
harmless. Direct physical actions elevate the existing governance policy
proportionally. Generic broad targets, scene or script activation, custom
action domains and other unknown effects are never described as harmless.

Bounded semantic opacity is distinct from technical execution uncertainty. An
opaque source may remain owner-actionable only when the exact helper provider
contract and a complete conservative lock graph are independently established.
Incomplete, high, safety-critical, direct, or unknown downstream consequences
remain elevated/high and visibly disclosed. Invalid helper targets or states,
provider ambiguity or drift, stale state, approval/evidence drift, lock-graph
failure, unsafe concurrency, missing durable intent, duplicate dispatch,
inconclusive readback, or unverifiable completion remain non-actionable.

Target relevance is projected from one of five explicit obligation terminals:
exact dependency, proven target exclusion, proven dependency-neutral result,
bounded semantic opacity, or coverage failure. A readable exact reference to
another helper or a finite candidate set/domain that excludes this exact helper
does not disable it. An unknown Jinja node or callable, imported template,
unconstrained entity selector, or state-bearing context without exact
provenance remains explicitly opaque. A missing action profile or unreadable
automation configuration is a coverage failure, not an empty graph. AST work,
depth, bindings, abstract-value/container growth, candidates, external
references, obligation count, retained
identities, profiles, evidence, and output are deterministically bounded.

Configuration context follows Home Assistant scope and ordering. Root
automation/script defaults retain the possible caller-supplied run-variable
override; action-level variables are rendered sequentially in insertion order;
literal disabled actions are unreachable; and parallel branches do not transfer
locals laterally. Exact zone-trigger and repeat/loop provenance is retained.
Event/payload values and ordinary variable metadata remain neutral when only
formatted, but become bounded opacity if subsequently used as entity selectors.
An unused data member named `entity_id` is not a causal dependency merely because
of its key name.

Fresh Beta 54 plans use `helper-dependency-risk-v13` and `f2-v2`. The immutable
plan binds the exact target, desired state, state baseline, normalized
dependency/consequence evidence and fingerprint, exact downstream automation
resource identities, effect-relevant services, targets and selectors, action
data, and normalized action structure. Sensitive or oversized values contribute
through bounded hashes and unbounded automation bodies are not retained.
Display-only aliases and descriptions do not invalidate approval. The plan also
binds the code-owned direct-provider contract and no-fallback policy. Approval
uses one existing external Home Assistant administrator owner decision and the
exact plan hash. Exact standard and elevated owner-authoritative operations both
require `plan_approval`; severity alone does not create a second challenge. The
MCP caller cannot self-approve, and principal, CSRF, hash, sequence, and expiry
checks remain mandatory.

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
Every helper execution holds the exact
`helper_dependency:input_boolean.<object_id>` key and the shared conservative
introduction guard. Automation configuration work derives exclusive dependency
keys from its current and proposed content: exact references take the exact
key, bounded opacity takes the conservative key, and proven exclusions take
neither. Exact dependencies add the relevant automation locks; bounded opacity
adds every known potentially relevant automation lock. External-template
opacity also binds a deterministic
custom-template reload identity without adding a reload tool. Engineering
configuration analysis treats raw `use_blueprint` content as bounded external
opacity until the exact source has been resolved and analyzed; blueprint
create, update, and removal therefore take the conservative helper-dependency
guard when F3 has only the raw automation body. Engineering
automation create/update operations derive exclusive dependency keys from both
current and proposed obligation ledgers, including added, retained, removed,
or materially changed dependencies or opacity. This blocks newly relevant automation
changes across preflight, dispatch, readback, and verification while keeping
statically proven unrelated automation updates concurrent. The final dependency
refresh occurs only after the complete lock set is held.

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
  affirmatively misclassified as harmless, while ordinary static notifications
  and reviewed bounded UI controls remain low risk; location/sensor updates,
  device control, TTS, channel mutation, other kiosk controls, templates, and
  extensions do not;
- unconstrained dynamic or unreadable automation evidence stays
  non-conclusive, while extractor-proven non-helper dynamic domains and exact
  references to another helper remain unrelated;
- compound dynamic expressions cannot claim a non-helper domain or omit the
  conservative dependency lock; dynamic-reference overflow is bounded,
  fingerprinted, and non-conclusive regardless of provider ordering;
- automation create/update operations that add, retain, remove, or materially
  alter the helper dependency conflict with helper execution after exact lock
  acquisition; relevant reloads conflict and unrelated automation work remains
  concurrent;
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
