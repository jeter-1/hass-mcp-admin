# 2.2.0-beta.6 acceptance contract

Version: `2.2.0-beta.6`

Source baseline:
`99920c2a1f94c59a2e1559283659fa03bbcfb78c`

This is the source and later operator-controlled acceptance contract for F2
policy, risk, and elevated administrator approval. Source implementation and
validation must not access deployed Home Assistant, publish an image, create a
tag or release, merge, deploy, or trigger an operational action.

## Immutable boundaries

- Policy is derived only from the normalized immutable plan. The caller cannot
  supply or downgrade the policy class.
- Risk delta and physical consequence remain independent dimensions.
- `standard_admin` requires one administrator plan approval.
- `elevated_admin` requires plan approval followed by a separate elevated-risk
  acknowledgement from the same authenticated administrator. This is not
  two-person control.
- `prohibited` grants no approval, task, provider, write, or fallback path.
- Plan and policy hashes are revalidated before approval and dispatch.
- Authority-v2 history remains readable but cannot satisfy authority version
  3. Historical terminal records are not rewritten.
- Task schema remains 1 and one F1 task remains the only execution owner.
- The source catalog remains 25 canonical plus 23 Engineering-native tools,
  48 local registered tools. Exact configured admission may add 26 delegated
  reads for 74 live tools. Stable v1.1.2 remains unchanged.

## Source policy acceptance

Require deterministic classification for every existing writable operation:

| Existing operation | Required policy |
| --- | --- |
| full local backup | `standard_admin` |
| controlled approved-domain reload | `standard_admin` |
| exact add-on restart | `elevated_admin` |
| Home Assistant restart | `elevated_admin` |
| supported low/moderate configuration without direct consequence | `standard_admin` |
| supported high-risk or direct-consequence configuration | `elevated_admin` |
| critical, safety-critical, unsupported, destructive, arbitrary, evasive, or unknown | `prohibited` |

Multi-operation order must not affect the aggregate result. Strictest risk and
physical consequence win independently. A prohibited operation makes the
whole plan prohibited.

Changing a normalized operation or policy field must change the corresponding
hash. Stored/recomputed policy mismatch must fail with
`policy_snapshot_mismatch`. Missing F2 policy on an active legacy plan must
fail with `policy_snapshot_required`; authority version 2 must not satisfy an
authority-version-3 bundle.

## Approval and transaction acceptance

For a standard plan, require one hash-bound plan approval, an unexpired bundle,
atomic durable task reservation before approval consumption, exactly one
dispatch lineage, verified completion, and duplicate apply reuse without
redispatch.

For an elevated plan, require:

1. one request creates the bundle and exposes only `plan_approval`;
2. elevated acknowledgement cannot precede plan approval;
3. plan approval alone returns `elevated_risk_acknowledgement_required`;
4. another administrator receives `approval_principal_mismatch`;
5. the same administrator can complete the second action;
6. both records bind the same plan hash and policy-decision hash, and the
   acknowledgement also binds displayed risk and physical consequence;
7. both actions are consumed once with the durable task; and
8. duplicate apply neither consumes nor dispatches again.

Expiry, rejection, supersession, stale state, policy drift, malformed bundle
state, task-storage failure, and materialization interruption must fail closed.
Task creation failure must leave approval unconsumed. A failure after task
reservation must retain that sole task owner and never create a replacement
dispatch opportunity. Startup rehydration may read and reconcile but never
invoke a provider.

## Ingress, audit, and health acceptance

Require admin-only Ingress, Supervisor-peer validation, one-time CSRF, separate
buttons and challenges, stale-action rejection, and same-administrator
enforcement. The review must show bounded server-owned plan, hash, policy,
risk, consequence, reason, operation, target, and expiration fields. Proposed
content remains untrusted escaped data.

Audit must record policy and approval transitions without raw principals,
tokens, full configuration, provider payloads, or authenticated URLs. Health
must report persisted policy-class, pending-action, consumed-bundle,
prohibited, mismatch, and sequence counters without mutating records during a
read or rehydration.

## Disposable pinned-Core scenarios

The established `real-ha-contract-tests` job must run these cases against only
the throwaway pinned Home Assistant container:

1. Standard helper update: classify `standard_admin`, approve once, apply one
   exact configuration write, verify readback, retain one task and one provider
   attempt, then duplicate apply with no second write.
2. Elevated automation configuration: embed a future physical action without
   firing its trigger; planning and both approval actions must perform no
   mutation. One approval is insufficient, a different administrator is
   rejected, the same administrator may acknowledge, apply changes only the
   stored configuration, trace identity remains unchanged, and duplicate
   apply does not redispatch.
3. Prohibited safety-critical fixture: approval returns
   `prohibited_change`, apply returns `prohibited_change`, no task exists, and
   the configuration gateway records no provider mutation.

All disposable configuration and credentials must be removed in the existing
`always()` cleanup. No step may contact deployed Home Assistant.

## Required validation

Run and record:

- focused F2 policy, approval, persistence, Ingress, and adversarial tests
  twice;
- two buffered and one verbose complete unittest discoveries;
- compilation, metadata, YAML, dependency consistency, secret, PowerShell,
  protected-path, whitespace, Full, and exact-head Evidence gates;
- strict Engineering dependency audit;
- deterministic compatibility-registry validation and regeneration/drift;
- disposable Home Assistant contracts;
- exact-image `ha-mcp` 7.14.1 and 7.14.2 lanes;
- stable-v1 and Engineering image builds; and
- amd64, arm64, and arm/v7 no-push builds where supported.

No failing test may be weakened, skipped, or converted to expected failure.
The Engineering image must report `2.2.0-beta.6`, the exact build SHA, and
`dirty=false`. Stable packaging must remain `1.1.2`.

## Later operator acceptance and rollback

Deployment and live acceptance require separate explicit authorization. A
later operator should use one harmless standard configuration plan, one
elevated configuration plan whose future action is not triggered, and one
bounded prohibited fixture. Verify exact Ingress sequencing, one task and one
dispatch per applied plan, consumed authority-v3 evidence, truthful health and
audit counters, and zero fallback. Do not use a live restart merely to test F2.

F2 adds no new resource types, update/recovery execution, shared lock policy,
transaction graph, compensation, generalized verification, arbitrary provider
access, or MCP-native task behavior. Those remain separately reviewed work.
