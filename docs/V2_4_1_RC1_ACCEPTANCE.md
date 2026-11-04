# Engineering 2.4.1-rc.1 acceptance

This contract (HAMCP-302) accepts the 2.4.1-beta.63 Engineering runtime as
release candidate 2.4.1-rc.1 with a version-only promotion. It defines the
source, CI, publication and installed gates. Each later stage needs its own
authorization; this contract supplies none.

See also the release notes:
[`V2_4_1_RC1_RELEASE_NOTES.md`](V2_4_1_RC1_RELEASE_NOTES.md).

## 1. Exact source, scope and review

Preparation base is protected `main`
`58ce6b81b2b504221e2bfcde26b43f1842e93b60`, tree
`815c5a8c962819dcfb9085d49b8027406c88c6c8` (beta.63 plus the HAMCP-303
documentation consolidation). Record the actual PR head, merged release SHA,
tree and build identity when they exist; do not infer them from this base.

Bind Josh's direction as recorded in HAMCP-302 (2026-11-03): cut 2.4.1-rc.1 after beta.63, with the
HAMCP-303 documentation PR first, and carry Codex's HAMCP-303 findings R1–R3
and the add-on store README refresh in this PR.

The source delta is restricted to:
- the three authoritative Engineering versions (`config.yaml`, `version.py`,
  `scripts/validate_addon_metadata.py`), materialized from
  `.release/next-version` by `scripts/promote_next_release.py --apply` in this
  PR, with no staged declaration left;
- this acceptance contract and the RC1 release notes, flat in `docs/`;
- `hass_mcp_engineering_beta/CHANGELOG.md`, `docs/RELEASES.md` and the root
  README **Current release** section;
- the R1–R3 documentation corrections (root README, the Core registry
  one-tap runbook) and the add-on store README pointer.

Required source evidence:
- `git diff 58ce6b8 HEAD -- hass_mcp_engineering_beta/ha_mcp_engineering`
  shows only `SERVER_VERSION` in `version.py`;
- no change to tools, schemas, routing, providers, the upstream or Core
  registries, signed profiles, persisted formats, dependency locks, build
  inputs, workflows or test logic;
- canonical `2.4.1-beta.63 -> 2.4.1-rc.1` sequencing accepted by
  `scripts/release_transition.py`;
- `scripts/codex-context.py` reports Engineering `2.4.1-rc.1`, documents
  `resolution_status: exact`, with this contract and the RC1 release notes as
  the active pair;
- 80 static plus 28 delegated reads, 108 expected connector tools, unchanged
  from beta.63;
- stable v1.1.2 under `hass_mcp_admin/` unchanged;
- focused suites (release transition, promotion, add-on metadata, docs index,
  context, PR evidence, instruction files, Core registry runbook and Core
  release evidence documents), copied-tree mutation of the release gates, and
  one complete Evidence tier run on the final head against the pinned base.

Review: one bounded independent release-delta review (Codex) of the exact
materialized head. Reuse beta.63's source, review and offline evidence for
the unchanged runtime with its original attribution; a release label alone
does not require rerunning it.

## 2. Release CI

The exact materialized head's `validate` check passes, including the release
transition, metadata validation and the dependency audit. Every skipped job is
named with its condition; a skip is not a pass. Reruns use the full workflow
rerun only.

## 3. Publication

Publication happens only through Josh's Ready action and the repository's
head-bound merge and publication workflow; agents do not mark the PR Ready.
Require `v2.4.1-rc.1` to target the exact merged release SHA, a GitHub
prerelease carrying the RC1 notes, and immutable version and commit image tags
resolving to one OCI index with linux/amd64 and linux/arm64 manifests, clean
source, version and build identity, SBOM and provenance, checked with the
shipped verifiers. Retain the workflow run and attempt and the digest chain.
On refusal, partial publication or ambiguous ownership, preserve the evidence
and reconcile before any further attempt; no blind rerun or moved tag.

## 4. Deployment preparation and recovery

Deployment needs Josh's separate authorization. Before deploying, record the
running version and build, the Core registry state, and the open plans, tasks,
approvals, locks and acknowledgement counts. Settle in-flight work through
supported paths first.

- **Upgrade** from 2.4.1-beta.63 is version-only: update the existing add-on
  in place; options, storage, plan and task formats and the acknowledgement
  ledger are unchanged.
- **Rollback** to 2.4.1-beta.63 is safe: same runtime and storage contracts.
  Do not treat 2.4.1-beta.57 or earlier as a safe rollback: releases before
  beta.61 refuse plans written under the beta.61 helper bindings, and beta.57
  and earlier read the frozen `main` registry copy and learn no later sequence
  or revocation.
- An image rollback does not restore household configuration or physical
  state.

## 5. Installed acceptance (separately authorized, after deploy)

One bounded campaign by the architect, at Josh's direction. Endpoint and
tokens stay private interactive inputs.

1. **Identity and health.** `server_info` and `get_server_health` report
   `2.4.1-rc.1`, the merged release SHA as build SHA and `dirty: false`; bind
   the running image to the published RC1 index digest through the existing
   private operator route. Health is otherwise unchanged from the pre-deploy
   record.
2. **Core authority at sequence 15.** `release_registry` shows
   `registry_id_status: accepted`, `freshness_status: current`, a
   `refresh_status` of `accepted` or `idempotent`, and the `registry` branch
   sequence (15 for Core 2026.10.3, or a later reviewed and published
   sequence, recorded as observed). Core-backed profiles are compatible for
   the installed Core version.
3. **Tool catalog.** A fresh `tools/list` with zero `tools/call` shows the
   same tool names and descriptors as beta.63: 108, or 109 with the optional
   `ha_config_get_yaml` reader for an admitting ha-mcp version (beta.63
   reported 109 on Josh's installation, observed live by the architect,
   2026-11-03).
4. **Reads.** One Engineering-native and one admitted delegated read of the
   same non-action target, with attribution, completeness and zero fallback.
5. **Disclosure regressions**, read from plan creation responses and health
   only:
   - HAMCP-270: a governed plan's create response carries the informational
     `global_source_consumers` block, outside the plan binding;
   - HAMCP-277: the dependency index's dashboard source reads the household's
     dashboards within its phase budget, with completeness reported;
   - HAMCP-285: helper plans carry the evidence-based labels and group
     membership disclosure (known issue HAMCP-301 still applies).
6. **Approved helper write round trip, two plans, both required.** On the
   acceptance helper used for beta.61
   (`input_boolean.engineering_acceptance`), record its pre-state by an
   independent state read before the first plan. Then:
   1. **Change:** create one value plan that sets the helper from its
      recorded pre-state to the other value;
   2. **Restore:** create a second, separate value plan that sets it back to
      the recorded pre-state.

   Each plan is approved by Josh on the Ingress panel for that exact plan,
   applied once, its execution task read, and its result confirmed by an
   independent state read that is not the task's own readback. The step
   passes only if the final independent read equals the recorded pre-state.
   If either plan is not approved or applied, the step is INCOMPLETE. If
   restoration fails, the step is FAIL: record the observed state, and Josh
   decides recovery.
7. **No-op control.** Request the helper's current value. Expect
   `status: no_change`, `outcome: already_in_desired_state`,
   `plan_created: false` and `provider_dispatch_occurred: false`.
8. **Acknowledgement state unchanged.** `governance.record_acknowledgement`
   counts and `inspect_governance_record` acknowledgement states match the
   pre-deploy record; the upgrade itself changes no acknowledgement.

PASS needs every step. Report NOT RUN or INCOMPLETE for missing evidence and
FAIL for an observed failure, with the response recorded in HAMCP-302. Stop on
unexplained drift, provider loss, unexpected dispatch or persistent faults.
Whether to roll back is Josh's decision.

## 6. Known issues carried

Documented, not fixed in this release:

- HAMCP-301 (High): unreferenced helpers still plan as `elevated_admin` on
  Josh's installation.
- HAMCP-288 (High, security queue): the production ha-mcp 8.5.0 provider writes
  its secret-path URL to its logs; exposure is unknown.
- HAMCP-300: stored plan and task identifiers are not yet screened in every
  remaining output.
- HAMCP-304 (Low): never-approvable prohibited operational plans are reported
  as projection failures.
- HAMCP-305 (Low): the Evidence secret scanner reports a false positive on one
  ADR filename.

## 7. RC exit

RC1 is accepted only when every source, review, CI, publication and installed
gate above has matching evidence. Any code change after rc.1 is a new release
with its own review and sequencing; it is not folded into this promotion.
Development continues as 2.4.2-beta.1; no stable 2.4.1 is planned (Josh,
2026-11-03, recorded in HAMCP-302).
