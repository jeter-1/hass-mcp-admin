# Engineering 2.4.1-beta.6 acceptance

This contract covers the HAMCP-175 typed power and fan repair (F1
dependency-scoped admission, F2 truthful refusal, F3 exchange telemetry and F4
truthful task guidance) on top of beta.5. It distinguishes source and
disposable acceptance, release CI, publication and installed Engineering
acceptance. This document authorizes none of the latter operations.

## 1. Source, review and CI

Bind Josh's authorization: on 2026-10-13 he approved F1–F4 for power and fan
only. His release decision of the same day ("first ready, first served")
assigns this repair 2.4.1-beta.6 because PR #247 had not merged. Also bind the
base `main`, every later main merge into the branch, and the final reviewed
commit and tree. Earlier review does not cover changed code. The authorized
protected runtime paths, all under `hass_mcp_engineering_beta/ha_mcp_engineering/`,
are:

- `upstream_tool_policy.py`
- `providers/upstream_fan.py`
- `clients/upstream_read.py`
- `fan/contracts.py`
- `fan/service.py`
- `fan/audit.py`
- `tools/power.py`
- `tools/fan.py`

The two typed-control documents are a disclosed non-runtime deviation.

Materialize 2.4.1-beta.6 in all three version authorities through the existing
staging tool. Require exact acceptance and release-note resolution, with no
staged declaration left. Complete Full/Evidence on clean source. Complete the
bounded independent review, including a security section. That section must
show admission is narrower in what it inspects but not in what it grants, that
no new tool, write, service call or fallback is reachable, and that the
generic read gateway is not widened. Require exact-head CI before release
acceptance. Pending or unavailable jobs are not passing evidence.

Preserve the following:

- stable v1.1.2
- historical persisted fixtures, durable formats, and locks, holds and
  uncertainty
- production journals and probe fingerprints
- the Core capability profiles and every signed registry entry
- dependency locks, build inputs, workflow permissions and the support window

The public catalog stays 84 tools with every beta.5 descriptor unchanged.

Offline source evidence must prove the following through the shipped tools,
services and providers, using the pinned MCP client over a synthetic peer.

**Useful success**

- Power and fan each dispatch exactly once on the stock 77-tool catalog and on
  an 80-tool catalog carrying the three reported filesystem tool names.
- This holds on both signed Core versions, including fan `set_percentage`.
- The additional tools are never called.

**Admission refusal**

Each of the following refuses before any task or write:

- drift of any reviewed component of a required tool
- a missing or duplicated required tool
- a version or protocol change
- an incomplete catalog
- a HOLD or revocation
- authority retired between discovery and dispatch

Two further cases must hold:

- A call to any other tool name is refused.
- The whole-catalog validator still rejects the 80-tool catalog, and the
  generic read subset stays 25.

**Truthful refusal**

- Grouped and direct admission refusals surface as non-retryable
  `provider_unavailable` with the bounded category.
- A foreign or mixed group and a real transport failure keep transport
  semantics.
- Exception text is never exposed.

**Telemetry**

- Refused, successful, timed-out and cancelled exchanges are each counted and
  finished, and the active count returns to zero.

**Task guidance**

- A pre-save failure returns no `task_id` and records
  `preparation_failed_no_task`.
- A saved declaration, or unknown durable state, keeps the pointer.
- A fresh unsaved ID executes once, an expired ID refuses without a pointer,
  and a rebound saved ID never dispatches.

**Cancellation**

- Cancellation before the declaration is saved propagates unchanged and
  leaves no task pointer in the typed audit summary, for both providers.
- Cancellation after the save keeps the truthful pointer, and the single
  dispatch completes without retry authority.
- There are zero writes before the save.

**Mutation checks**

Each of the following fails a test:

- restoring whole-catalog admission
- dropping the sole-leaf unwrap
- restoring the generic refusal code
- suppressing the exchange count or its finish
- skipping the service-call fingerprint check
- returning the pointer before save
- restricting the audit cleanup to `Exception`
- always dropping the pointer
- swallowing cancellation

## 2. Publication and recovery preparation

Publication needs Josh's applicable Ready authorization and the protected merge
handoff. Verify the merged source, v2.4.1-beta.6, the immutable version and
commit images, provenance and SBOM with the shipped verifiers. Never invent
future image identities. Retain the known-good beta.5 and beta.4 artifacts and
options for recovery. Nothing new is persisted, so downgrade to beta.5 needs no
migration.

## 3. Installed Engineering

1. **Installed-image binding.** Separately authorize installation of the
   published Engineering artifact. Bind the running add-on to the published
   image index digest, the v2.4.1-beta.6 tag and the protected `main` merge
   commit. A source version alone does not prove the installed image.
2. Compare all 84 public descriptors, Core signed selection, storage and audit
   health, F3 readiness and zero fallback.
3. **Catalog-expansion check.** Record the `server_info` upstream catalog
   count, unreviewed count, reviewed-accounting validity and catalog
   fingerprint before the canary. Keep ha-mcp's filesystem tools disabled.
   Dashboard writes still apply whole-catalog admission until HAMCP-177 ships,
   so re-enabling them is a separate owner decision after that release. The
   expanded-catalog behavior of power and fan is established by the offline
   evidence in section 1, not by re-enabling those tools live.
4. **Power and fan canary.** Run this on an entity Josh chooses for each kind,
   with Josh's explicit authorization of each physical action:
   - Run `turn_off`, then `turn_on`, or restore the recorded pre-canary state.
     Each step must report exactly one provider attempt, a recorded dispatch
     intent, a received response and `succeeded_verified` by readback.
   - `get_execution_task` must resolve each returned `task_id`.
   - Replaying the same operation ID must reconcile without a second dispatch.
   - `upstream_attempted` must be true.
   - Record the observed latency and `dispatched_at` value. Both are known
     limitations outside this release, not acceptance criteria.
   - Leave each entity in its recorded pre-canary state.

The filesystem tools stay disabled until HAMCP-177 ships. This release does not
add the refusal warning log (HAMCP-172).

No push, CI trigger, signing, publication, installation or live action is
authorized by source completion.
