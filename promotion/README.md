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

The manifest records the helper no-change contract as a separately authorized
canary so deficiency #22 remains complete, but that declaration is excluded
from the default plan, capture template, completeness calculation, and
classification. Any helper-state transition, no-change proposal, or other
write-capable canary requires separate authorization. A preread cannot remove
the race in which `create_helper_state_plan` creates a plan.

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
```

The source file is temporary operator input, not capture evidence. Keep it
outside the repository and discard it after checking the derived fields. The
checker accepts at most 128 KiB, rejects credential-shaped values and sensitive
field names, and outputs only the declared bounded projection.

`dependency_evidence_v1` accepts only a bounded list of failure records with
`source_type`, `source_identity`, `reason_code`, and positive `count`, plus the
native `unique_dependency_source_count`. It validates each value, sorts the
failure tuples lexicographically, serializes each as
`source_type:source_identity:reason_code:count=N`, joins multiple records with a
single LF byte, sums the counts, and hashes the exact UTF-8 signature with
SHA-256. The unique dependency-source count becomes the conservative
consequential-dependency count.

`wait_template_structure_v1` recursively walks a sanitized automation mapping.
Mapping keys are visited in lexical order and sequences in index order. The
walk is bounded to 4,096 nodes, 24 levels, 100 `wait_template` candidates, and
60,000 UTF-8 bytes per template. Each containing action is identified by an
RFC 6901 JSON Pointer; pointers are sorted and the first is selected. The
template is normalized from CRLF/CR to LF and Unicode NFC without trimming.
Length is the normalized UTF-8 byte count. The digest preimage is the UTF-8
algorithm name `wait_template_structure_v1`, NUL, pointer, NUL, and normalized
template, hashed with SHA-256. Unsupported, malformed, sensitive, or oversized
source input fails without emitting a projection.

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
The checker never changes status automatically. Current nonblocking known
failures are limited to the bounded signatures for deficiency #1, deficiency
#2/#14, deficiency #4, and the separately represented top-level taxonomy part
of deficiency #19. There is no separate deficiency #3 sentinel.

Exit codes:

| Code | Meaning |
| --- | --- |
| 0 | Complete evidence and no regression. |
| 1 | At least one regression. |
| 2 | No regression found, but evidence is incomplete. |
| 3 | Invalid manifest, capture, or invocation. |

## Sentinel fidelity notes

- Dependency evidence is forced fresh with `refresh_index: true`. The capture
  records refresh/cache state, automation and blueprint completeness, bounded
  failed-obligation identity, consequential-dependency count, and fallback.
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
