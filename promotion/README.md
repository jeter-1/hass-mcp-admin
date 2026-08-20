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
| `promotion_regression_manifest.yaml` | Exact target, read-only observations, desired contracts, and bounded known-failure signatures. |
| `manifest_schema.json` | Strict schemas for the manifest and operator capture. |
| `../scripts/promotion_regression_check.py` | Offline validator, planner, template generator, and classifier. |
| `../tests/test_promotion_regression_manifest.py` | Offline safety, fidelity, and classification tests. |
| `../tests/fixtures/promotion_regression/` | Synthetic, sanitized evidence; never a production capture. |

## Safety boundary

The default plan contains only tools whose authoritative reviewed metadata
classifies them as read-only. In particular, it does not contain
`create_helper_state_plan`. A state preread cannot make that proposal tool
structurally read-only because the state can change before invocation.

Any helper-state transition, no-change proposal, or other write-capable canary
is outside this pack. It requires separate authorization and must not be added
to default capture completeness or classification.

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

3. Manually invoke each declared read-only tool against the exact target. Bind
   every operator-local argument in the capture to the value actually invoked.
   Project only the allowlisted paths shown by `plan` into each observation's
   flat `evidence` mapping. Record a known-absent field in `absent_paths`. If an
   observation was not performed, retain `status: not_captured` and give a
   bounded reason.

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
- exact observation IDs, tool names, fixed arguments, and resolved
  operator-local arguments.

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
  bounded evidence that the expected action is a `wait_template`, including a
  normalized semantic digest. It does not retain the automation body.
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
