# F3 completion acceptance contract

Status: Future F3 milestone gate; amended by Beta 17 dashboard deferral

F3 is complete only when every requirement below is demonstrated with
deterministic tests and exact-release evidence. Documentation or a successful
provider response alone is not acceptance.

## Source and release boundary

- The final F3 source is an exact reviewed commit with a clean build identity.
- Stable v1.1.2 has no diff.
- `aiohttp==3.14.3` and `cryptography==50.0.0` or later separately reviewed
  secure exact pins remain installed.
- Strict dependency audit reports no known vulnerabilities and has no broad
  suppression.
- Protocol support and exact upstream release trust have not broadened.
- Public-schema changes have an explicit compatibility and security review.
- Persisted task/plan format changes have a separately authorized migration
  decision and fixtures produced by the exact shipped writer; hand-reconstructed
  historical records are not evidence.

## Shared adapter execution

- Every executable configuration or operational adapter implements the
  accepted `f3-operation-adapter-v1` lifecycle. Dashboard execution is not an
  included capability while the authoritative atomicity gate is unresolved.
- Planning binds exact target, current fingerprint, proposed hash, policy,
  approval, effects, rollback capability, and verification contract.
- Preflight runs under the complete acquired lock set and repeats exact
  provider, operation, identity, stale-state, and argument checks.
- Approval consumption and durable dispatch intent are persisted before any
  mutating provider invocation.
- Each prepared operation has at most one mutating provider invocation;
  separately bounded non-mutating preflight/readback calls do not create a
  dispatch lineage.
- Failure to persist intent prevents invocation.
- A possible dispatch can never return to dispatch after timeout, response
  loss, process loss, provider loss, or task rehydration.
- Exact readback, not a provider response alone, decides verification.
- Normalized outcomes map deterministically to existing persisted task states.
- Rollback availability is explicit; unavailable adapters cannot infer it from
  snapshots or upstream backups.
- No adapter adds fallback or arbitrary forwarding.

## Locks and conflicts

- Canonical resource/provider keys are deterministic and validated.
- Canonical key is the lock identity. Duplicate declarations are merged before
  acquisition by unioning sorted scopes/reasons; exclusive mode dominates
  shared mode. Each unique key is acquired exactly once in sorted-key order and
  released in reverse order.
- Lock records are durable, task/plan/owner bound, leased, renewed, and safely
  recoverable after process loss.
- Lease duration and renewal interval are evidence-backed and safe for the
  longest accepted operation.
- Terminal pre-dispatch and verified terminal outcomes release locks.
- Possible-dispatch recovery retains or safely reacquires locks without
  redispatch. An unresolved deadline/manual-review outcome becomes a durable
  target-conflict hold; it cannot silently release the target for another
  incompatible write.
- Dashboard planning models the complete lock set, and test-only F3-A
  conformance proves its normalization and pre-intent atomicity rejection.
  These Engineering locks are not external-writer exclusion.
- Home Assistant restart conflicts with configuration writes and reloads.
- Exact add-on restart conflicts with operations that depend on that add-on.
- Backup compatibility edges are evidence-backed rather than assumed.
- Deadlock, stale-owner, renewal-failure, and duplicate-apply tests pass.
- Locks do not authorize an operation.

## Process recovery and durable evidence

- Process loss is tested at planning, preflight, lock acquisition, before and
  after durable intent, during provider call, after response, during readback,
  during verification, and during separately authorized rollback.
- Plan/task reconciliation repairs only from authoritative durable evidence and
  performs no provider mutation.
- Backup, configuration, reload, add-on restart, and Home Assistant restart
  have bounded readback/manual-review outcomes. Dashboard verification has
  bounded outcomes only when supplied complete exact readback evidence; it is
  not reached through a Beta 17 dispatch.
- Post-dispatch deadlines are fixed at first intent and cannot be extended by
  restart or retry.
- Storage corruption is quarantined and cannot create a replacement authority.
- Governance and task storage health, write failures, corruption, nonterminal
  tasks, lock health, reconciliation, and audit failures are bounded and
  observable.
- Health and audit surfaces never expose provider payloads, secrets, full
  dashboard config, or unbounded lock/validation objects.

## Governed dashboard update

- One existing dashboard is selected by exact canonical `url_path`.
- Inventory proves exactly one explicit storage-mode target; YAML, absent,
  duplicate, and ambiguous targets are rejected.
- An exact complete internal read verifies upstream `config_hash` and separate
  Engineering evidence hash.
- The approved operation uses `f3-dashboard-json-pointer-patch-v1` and a
  bounded reviewer-visible semantic diff.
- The exact computed result is hash-bound before external administrator
  approval.
- Unknown custom-card/component fields are preserved except at declared paths.
- Ambiguous selectors, undeclared paths, arbitrary executable code, and
  unbounded data are rejected.
- The complete lock set is acquired before the final exact reread:
  exclusive `dashboard:<url_path>`, shared `home_assistant:core`, and shared
  provider `addon:<authoritative_ha_mcp_slug>` when add-on backed.
- A stale hash is rejected before durable intent or dispatch.
- Before write enablement, exact-release evidence proves atomic compare/save or
  an exclusive mechanism covering all dashboard writers. A deterministic
  interleaving test proves that the current non-atomic upstream sequence permits
  an undetectable lost update. Without authoritative proof, dashboard execution
  remains deferred; an Engineering-only lock or final reread is insufficient.
- No dashboard transport or setter realization exists. Generated
  `python_transform` and unrestricted full-configuration replacement are not
  accepted alternatives.
- The target cannot be created when missing.
- Exact complete readback evidence can verify the full intended result and no
  unintended field loss without claiming that a write occurred.
- Resulting upstream and Engineering hashes are retained in immutable planning
  evidence.
- F3-0, planning-only, exact-release, and F3-A conformance lanes perform zero
  mutating dispatch, fixture mutation, approval grant, or physical action.
- Dashboard-specific setter invocation and fixture mutation counts are zero.
- Initial dashboard rollback remains unavailable. If later declared available,
  exact prior config is bounded/hash-bound and a separate stale-safe approved
  one-dispatch rollback is proven.
- Dashboard deletion, creation, resource writes, preference writes, metadata
  writes, screenshots, rendering, arbitrary Python, arbitrary services, and
  direct physical action remain unreachable.
- Dashboard content is never interpreted as instructions.

## Configuration conformance

- Automation, script, input_boolean, and input_number preserve exact existing
  create/update operations, schemas, endpoints, identity checks, and stale
  fingerprints.
- Caller order, earlier-only dependencies, duplicate-target rejection, and
  stop-on-first-failure remain deterministic.
- There is at most one mutating provider invocation per prepared configuration
  operation.
- Existing no-change, ambiguous-response, verification, configuration-check,
  contract-v1 compatibility, and rollback boundaries remain explicit.
- No delete or arbitrary WebSocket/REST forwarding becomes reachable.

## Operational conformance

- Backup remains exact full local snapshot creation with existing approval,
  name/expiration handling, verification, and no fallback.
- Controlled reload remains limited to the reviewed domain allowlist.
- Add-on restart remains exact `action=restart` plus authoritative slug,
  endpoint, installed-version, repository, release, and response-contract
  binding.
- Home Assistant restart remains exact `confirm=true` plus configuration,
  outage, readiness, upstream, storage, audit, and fallback verification.
- Start, stop, install, uninstall, update, options mutation, arbitrary
  Supervisor commands, service shortcuts, and blind redispatch remain
  unreachable.

## Exact `ha-mcp` 7.14.2 compatibility

- protocol: `2025-03-26`;
- advertised tools: 78;
- delegated reads: 26;
- held reads: 0;
- Engineering-local tools: 48;
- total tools: 74;
- selected entry: `ha-mcp-v7.14.2-7917b2d3`;
- schema, description, annotation, output, and runtime mismatch: zero;
- quarantine, missing, unreviewed, and fallback: zero;
- Dashboard inventory/config reads unchanged;
- backup, reload, add-on restart, and Home Assistant restart planning valid;
- governed dashboard update planning valid under the reviewed exact write
  contract;
- mutating provider dispatch and fixture mutation in exact-release planning:
  zero; and
- dashboard setter invocation and fixture mutation remain zero.

## Exact `ha-mcp` 8.0.0 compatibility

- protocol: `2025-03-26`;
- advertised tools: 78;
- delegated reads: 24;
- held reads: 2;
- held exactly `ha_search` and `ha_get_operation_status`;
- Engineering-local tools: 48;
- total tools: 72;
- selected entry: `ha-mcp-v8.0.0-d65630f6`;
- schema, description, annotation, output, and runtime mismatch: zero;
- quarantine, missing, unreviewed, and fallback: zero;
- Dashboard v3 inventory/config reads unchanged;
- backup, reload, exact add-on restart, and Home Assistant restart planning
  valid with Beta 15 response-envelope behavior;
- governed dashboard update planning valid under the reviewed exact write
  contract;
- mutating provider dispatch and fixture mutation in exact-release planning:
  zero; and
- dashboard setter invocation and fixture mutation remain zero.

## Dashboard risk review

- Display-only references to locks, alarms, covers, garage doors, or other
  high-risk entities are not mislabeled as physical actuation.
- Explicit direct high-risk/destructive action definitions are detected and
  handled by reviewed policy.
- Opaque custom action surfaces have a fail-closed, documented outcome.
- Risk evidence is bounded and does not expose the full dashboard.
- No broad content prohibition is introduced without evidence.

## Test and CI gate

- Focused adapter, lock, dashboard, configuration, operational, recovery,
  negative-reachability, schema, and tool-count suites pass.
- Fast, Full, and Evidence tiers pass with only documented expected skips.
- Stable and Engineering packaging pass.
- amd64, arm64, and arm/v7 validation pass.
- Exact standalone-image 7.14.2 and 8.0.0 acceptance pass.
- Existing exact immutable 8.0.0 read/provider-planning acceptance passes with
  zero mutation/dispatch.
- Exact-release dashboard lanes perform planning and supplied-readback
  verification only, with zero setter invocation, fixture mutation, provider
  dispatch, physical action, live action, or fallback.
- Real-Home-Assistant contract tests pass against the pinned disposable image.
- Secret, protected-path, YAML, whitespace, and PowerShell checks pass.
- Exact-head push and pull-request CI are green before review completion.

## Explicit non-goals

F3 completion does not require dashboard execution while authoritative
external-writer protection is unavailable. It also excludes dashboard
creation/deletion/resources, cross-dashboard transactions, arbitrary
transforms, multi-operation graph scheduling, and generalized compensation.
Those require separate scope; graph execution and compensation remain F4.
