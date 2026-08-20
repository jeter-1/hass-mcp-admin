# Promotion regression manifest

This directory implements Consolidated Deficiency Register (HAMCP-089) item
#22 as a versioned, reviewable set of promotion sentinels and a transport-free
offline evaluator.

The pack evaluates evidence supplied and attested by an authorized operator. It
does not call Home Assistant or MCP, authenticate to a live system, run a
subprocess, modify a repository, or enforce promotion. Running the live reads is
a separate, manual authorization decision.

## Files

| File | Purpose |
| --- | --- |
| `promotion_regression_manifest.yaml` | Exact target, read-only observations, versioned projection and optional-canary declarations, desired contracts, and bounded known-failure signatures. |
| `manifest_schema.json` | Strict schemas for the manifest and operator capture. |
| `../scripts/promotion_regression_check.py` | Offline validator, planner, template generator, and classifier. |
| `../tests/test_promotion_regression_manifest.py` | Offline safety, fidelity, and classification tests. |
| `../tests/fixtures/promotion_regression/` | Synthetic, sanitized evidence; never a production capture. |

## Safety boundary

The default plan contains only tools whose authoritative reviewed metadata
classifies them as read-only. In particular, it does not contain
`create_helper_state_plan`. A state preread cannot make that proposal tool
structurally read-only because the state can change before invocation.

The manifest records required write-classified contracts separately from the
default plan. They appear only as `not_captured` requirements in the capture
template and affect promotion completeness; the checker never invokes them.
The helper no-change canary is executable only after separate authorization.
The Beta 39 Jinja/helper-dependency family is explicitly unavailable until a
separate reviewed live-fixture protocol exists. Any unperformed required canary
makes `promotion_eligible=false`. A preread cannot remove the race in which
`create_helper_state_plan` creates a plan.

The checker itself has no live transport, MCP client, credential access,
subprocess invocation, or file-writing path. Tests verify both that property and
the default manifest's tools against the repository's native and exact upstream
read classifications.

## Manual workflow

The only live step below is step 3. It must be performed separately by an
authorized operator or interactive agent with appropriate read-only access.

1. Validate the committed pack:

   ```bash
   python scripts/promotion_regression_check.py validate
   ```

2. Print the exact calls and allowlisted capture paths:

   ```bash
   python scripts/promotion_regression_check.py plan
   python scripts/promotion_regression_check.py template > /path/outside/repo/capture.json
   ```

3. Manually invoke each declared read-only tool against the exact target. For a
   captured observation, bind every operator-local argument in the capture to
   the value actually invoked. If an operator-local identity cannot be resolved,
   leave the observation `not_captured`, omit that unresolved local argument,
   and give a bounded reason. Fixed arguments remain mandatory in either state.
   Project only the allowlisted paths shown by `plan` into each observation's
   flat `evidence` mapping. Record a known-absent field in `absent_paths`.

4. Evaluate offline:

   ```bash
   python scripts/promotion_regression_check.py evaluate --capture /path/outside/repo/capture.json
   ```

   Add `--format json` for a bounded machine-readable report.

Keep captures outside the repository. `promotion/captures/` is ignored only as
a local safety net. A capture is operator-attested evidence, not cryptographic
provenance. The checker binds it to:

- capture and manifest schema versions;
- the deterministic manifest digest;
- exact target release and build SHA;
- a timezone-aware timestamp;
- non-placeholder operator and session attribution;
- exact observation IDs, tool names, and fixed arguments;
- every resolved operator-local argument for captured observations.

A mismatched, malformed, placeholder, conflicting, oversized, or incorrectly
targeted capture is rejected before classification.

## Capture minimization and bounds

Do not retain complete Home Assistant or MCP responses. Capture only the values
used by manifest checks. In particular, do not dump complete automation,
dashboard, entity-attribute, log, trace, or configuration bodies.

The capture contract enforces:

- a 256 KiB total input limit;
- a 24 KiB limit per observation;
- a 2 KiB limit per string value;
- exact allowlisted evidence paths;
- no duplicate JSON keys or undeclared observations;
- no sensitive field names or recognizable credential values;
- bounded diagnostics and a 96 KiB report limit.

Where material content need not remain readable, record the bounded digest
specified by the observation procedure. A content change must still change the
digest. Do not place secrets into a digest preimage retained in the capture.
Missing required evidence is `NOT_CAPTURED`; it is never inferred as success.

## Deterministic projection derivation

Every non-native `projection.*` field used by a sentinel is covered by exactly
one versioned `projection_contracts` entry in the manifest. The offline checker
derives those fields from a minimized, sanitized, source-shaped JSON file:

```bash
python scripts/promotion_regression_check.py project \
  --observation native_dependency_read \
  --source /path/outside/repo/dependency-source.json

python scripts/promotion_regression_check.py project \
  --observation long_wait_template_automation_read \
  --source /path/outside/repo/automation-source.json

python scripts/promotion_regression_check.py project \
  --observation f3_orphan_task \
  --source /path/outside/repo/execution-task-source.json
```

The source file is temporary operator input, not capture evidence. Keep it
outside the repository and discard it after checking the derived fields. The
checker accepts at most 128 KiB, rejects credential-shaped values and sensitive
field names, and outputs only the declared bounded projection.

`dependency_public_evidence_v2` consumes the actual public
`entity_dependency_analysis` response. It reads only the public overview,
automation/blueprint `source_coverage`, pagination, and refresh/cache fields.
For both sources it separately binds legacy `completeness` and
`failed_item_count` plus authoritative `obligation_ledger_completeness` and
`obligation_ledger_failed_item_count`; missing or unsupported ledger evidence
fails projection. Both fallback values are material.
The response does not expose failed-blueprint identities or reason codes, so
the pack does not invent them. The bounded human summary states only observable
coverage and failure counts. The fingerprint preimage is a separate versioned
structured object serialized as canonical compact JSON with sorted keys and
finite typed values; delimiter-joined strings are never hashed. Warnings,
durations, metadata, and input key order are nonmaterial.

`wait_template_structure_v1` resolves the reviewed `/action/2` JSON Pointer.
The parent must be the automation's `action` sequence and the selected action
must contain a string `wait_template`. Mappings under variables, action data,
metadata, or unrelated nested configuration are ignored. The template is
normalized from CRLF/CR to LF and Unicode NFC without trimming. Length is the
normalized UTF-8 byte count. The digest preimage is the UTF-8 algorithm name
`wait_template_structure_v1`, NUL, pointer, NUL, and normalized template,
hashed with SHA-256. A missing, moved, malformed, sensitive, or oversized target
fails without emitting a projection.

`f3_child_lifecycle_v1` consumes the bounded public `get_execution_task`
response for the exact orphan parent. It requires the complete child list and
uses parent task ID plus operation ID and ordinal as the deterministic child
identity grounded by authorized historical evidence. State, normalized
outcome, and dispatch count are fingerprinted using canonical structured JSON.
The projection also reports child count, all-zero-dispatch, all-terminal, and
all-cancelled-pre-dispatch aggregates. Duplicate operations or ordinals,
non-contiguous ordinals, missing children, unsupported states, and overflow
fail closed. Child events, lock bodies, prepared hashes, and task configuration
are never retained.

The sanitized source-shaped fixtures under
`tests/fixtures/promotion_regression/` independently derive every current
projection twice and require byte-identical results. They are synthetic and do
not retain live automation or blueprint response bodies.

## Classification

Every sentinel declares desired passing checks. An `expected_fail` sentinel
also declares an exact bounded signature for today's known deficiency.

| Expected status | Desired checks | Known-failure checks | Outcome |
| --- | --- | --- | --- |
| `expected_pass` | pass | N/A | `CONFIRMED` |
| `expected_pass` | fail | N/A | `REGRESSION` |
| `expected_fail` | pass | any | `UNEXPECTED_PASS` |
| `expected_fail` | fail | exact match | `KNOWN_FAILING` |
| `expected_fail` | fail | different or worse | `REGRESSION` |
| either | insufficient evidence | unknown | `NOT_CAPTURED` |

`KNOWN_FAILING` means the exact recorded failure signature matched. It does not
mean merely that something failed. A new, missing, or materially different
failure is a `REGRESSION`.

`UNEXPECTED_PASS` requires human confirmation and a reviewed manifest change.
The checker never changes status automatically. Every `expected_fail` also
declares a closed promotion disposition. Deficiency #1, deficiency #2/#14,
and deficiency #4 are `blocking`; an exact match remains `KNOWN_FAILING` but
makes this regression-manifest gate promotion-ineligible. The separately
represented top-level taxonomy part of deficiency #19 is
`tracked_nonblocking` and does not independently block an otherwise complete
pack. A disposition does not change classification, and there is no separate
deficiency #3 sentinel.

Exit codes:

| Code | Meaning |
| --- | --- |
| 0 | Complete accepted evidence; promotion eligible. |
| 1 | At least one regression. |
| 2 | Evidence incomplete, mandatory human review required, or an exact blocking known failure remains. |
| 3 | Invalid manifest, capture, or invocation. |

JSON and text use the same decision contract. `regression_present` reports any
regression, `evidence_complete` is false for `NOT_CAPTURED`, `review_required`
is true for `UNEXPECTED_PASS`, and `blocking_known_failure_present` plus
`blocking_known_failure_count` report exact known promotion blockers.
`promotion_eligible` is true only for complete evidence with no regression,
review requirement, or blocking known failure. It represents this regression-
manifest gate only; it does not bypass release-specific acceptance, deployment
authorization, publication policy, or any other promotion gate. The
compatibility fields `run_complete` and
`promotion_blocked` are exact aliases of `evidence_complete` and the inverse of
`promotion_eligible`; they cannot contradict eligibility.

## Sentinel fidelity notes

- Dependency evidence is forced fresh with `refresh_index: true`. The capture
  records refresh/cache state, automation and blueprint legacy and obligation-
  ledger completeness, both failure counts, bounded observable dependency-
  source count, pagination, and fallback.
  A zero source count proves there is no consequential source for this canary;
  exact failed-blueprint identities and per-source consequence classes are not
  public and are not claimed.
- The stale-state sentinel is tied to the exact operator-supplied task ID,
  operation, and target. It requires `failed_pre_dispatch`, zero provider
  attempts, a null dispatch timestamp, the exact stale-state reason, and no
  success or post-dispatch verification state.
- The long-automation sentinel records only the selected automation ID and
  bounded evidence that exactly one expected action is a `wait_template`,
  including its RFC 6901 action path, normalized byte length, and semantic
  digest. It does not retain the automation body.
- Approval authority version 3 is read from server health. Durable task schema
  version 1 is independently bound to the exact orphan execution-task read.
- F3 known-failure evidence binds exact parent `failed_pre_dispatch`, terminal
  outcome, zero attempts, null dispatch, causal error, exactly two children,
  `create_fp2_pending_helper` at ordinal 0 in `preflight`, and
  `update_vanity_automation` at ordinal 1 in `not_started`, each with zero
  dispatch and no normalized terminal outcome. Desired recovery preserves the
  parent failure and requires both children to become
  `cancelled_pre_dispatch` with zero dispatch. Inventory and health must agree.
  F3 known-failure evidence allows only this documented orphan difference.
  Normal locks, corruption, recovery coordinator, recovery failures, fallback,
  readiness, and conflict holds must remain at their desired values; any new
  failure is a regression even while the orphan persists.
- The historical Beta 31 map update and cleanup and the historical long-template
  execution use exact operator-local task identities from approved acceptance
  evidence. If an identity cannot be resolved, the observation is
  `NOT_CAPTURED`; a different task must never be substituted.
- Home Assistant configuration validity comes from the reviewed read-only
  `check_config` capability, not Engineering process health.
- Held-read status is an expected-pass contract independent of the currently
  incorrect top-level error taxonomy. Nested upstream `RESOURCE_NOT_FOUND`
  evidence does not claim the top-level taxonomy is already correct.

## Updating the pack

For a new target, update the target release/build and observations in one
reviewed change. Regenerate the template so the capture contains the new
manifest digest. Repeated generation must be byte-identical.

Do not automatically flip an `expected_fail` after one unexpected pass. Confirm
the deployed behavior, update the deficiency register, then review the desired
and known-failure contracts together.

This pack is not wired into GitHub Actions or mandatory promotion validation.
Adding live credentials, CI enforcement, or an automated gate is a separate
security and product decision. No live run is performed as part of source
correction.
