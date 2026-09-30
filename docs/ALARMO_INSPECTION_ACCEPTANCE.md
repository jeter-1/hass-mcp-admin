# Alarmo inspector acceptance

This is an acceptance definition for the source-only increment, not a record of
executed or installed acceptance. Use exact candidate revision and saved results.
Do not restart completed beta.5, dashboard or household device campaigns.

## Offline source gates

Run the seven `test_*integration_inspection*` / `test_alarmo_inspection_*` modules
with the pinned repository environment. Their cases cover useful master/area
membership, all five modes, nullable times, disabled/always-on/unregistered
sensors, cross-area groups, page-local evidence, malformed/partial sources,
unsupported identity, strict arguments, cancellation and concurrency, cursor
binding/expiry/eviction/revocation, byte/record/node/edge limits, and no fallback.
Run transport tests with synthetic loopback peers only. Test privacy through the
public tool and gateway, including rejected argument names/values and audit.

Include the valid 512-sensor/128-group report with bounded synthetic source
latency: complete within the 30-second fresh-request deadline or return a fixed
timeout, with responsive cancellation and no collector/snapshot leak. At a
16,000-byte response limit, traverse and replay the positive fixture: one omitted
sensor, 15 emitted rows, stable partial assessment/omission counts on every page,
and zero continuation reads. Check missing/malformed final manifest evidence
through the public envelope, preserving useful partial facts; a validated version
conflict still refuses. Test contradictory master observations and known-inside,
known-outside, unresolved and mixed group joins. Unavailable placeholders alone
must fail while observed false/zero/null or exact empty membership can succeed.

Prove beta.10 baseline 55 static descriptors and 19 Core fingerprints remain exact;
source adds one native descriptor and one runtime-only authority reference.
Use ephemeral signatures for absent, correct and mismatched/revoked applicability;
old authority must refuse feature dispatch while unrelated capabilities remain.
Run the authorized current-catalog expectation updates and complete Full/Evidence.
Unchanged release metadata is an explicit release-readiness failure, not permission
to skip that validator or create an unapproved version.

Reproduce synthetic fixtures with:

```text
python scripts/alarmo_inspection_contract_acceptance.py --alarmo-source <exact component source>
```

Source getters and callbacks are executed from hash-verified official source.
This proves fixture provenance; it is not real Core/Alarmo integration acceptance.
The exact Core source/tests are listed in the source-review record; inspection
alone must not be reported as execution of those upstream tests.

## Disposable Core/Alarmo gate

The retained harness below targets Core 2026.9.3. Deployment on Core 2026.9.4
requires source comparison for the added metadata reads and a separately prepared,
exact 2026.9.4 disposable execution. The existing 19-profile admission and the
beta.10 logbook lane do not establish this new capability. No installed-Core pin
or compatibility expansion is introduced by this reconciliation.

Required pairing: existing digest-pinned Core 2026.9.3 and ha-mcp 8.5.0 lane,
official Alarmo commit `169e134f4b70d87aae36ba54a72a398ecc960afd`. No production
endpoints, credentials or installation are used. The owned container is destroyed
by the existing outer harness; preserve its cleanup result on failure too.

Stage an overlay in a new directory, before starting the disposable Core:

```text
python scripts/alarmo_inspection_contract_acceptance.py --prepare-archive <verified archive> --destination <new disposable overlay>
```

The helper verifies the archive and every component file against the committed
profile, generates an inert store through the exact source writer with no users
or actions, and writes an explicit fixture receipt. It refuses an existing
destination. It does not install, start, restart or reconfigure Core itself.

The existing runner's Core 2026.9.3 branch invokes the new harness when
`REAL_HA_ALARMO_FIXTURE_RECEIPT` identifies the prepared overlay receipt. The
overlay's component/storage must have been incorporated into that disposable
Core configuration before its startup. The harness creates the singleton entry
through a normal config flow during separately identified fixture setup, then
uses the real public Engineering tool/transport for the measured nine reads and
a no-read continuation. It proves missing new authority, then uses an ephemeral
test-signed 20-reference journal. This is never production signing/activation.

The new result marker is `Alarmo disposable inspection contract:` in the existing
runner log. Without preparation, its result is **NOT_RUN**, never PASS. Existing
lanes keep their existing behavior. The workflow currently lacks this fixture
preparation: enabling it requires a separately approved minimal workflow change
that stages the verified archive/overlay before Core startup, passes the receipt
environment variable, retains the marker, and requires its PASS result. No
workflow permission expansion is needed or authorized here. A source-only hook
does not establish execution or CI coverage.

Acceptance additionally needs command-ledger confirmation, no feature-side
write/event/service/options-flow dispatch, exact useful settings comparison,
and cleanup evidence. The current harness's closed command capture is feature
boundary evidence; independently instrument Core to corroborate side effects
before closing the real-integration gate. No mock or AST hook check substitutes
for that run. Docker permission failures are an unavailable environment, not a
feature failure or authority to elevate host access.

## Independent review and later installation

Review the final source delta, transport, privacy, authority, fixtures and test
results independently after Full/Evidence. Record outstanding gates by stage.
Before activation: resolve R3 with separate authorization; review/sign/activate
applicability, reconcile P0b installed Alarmo files or the owner's explicit
local-modification declaration at its actual assurance level, and approve release.

A later explicitly authorized installed pass binds publication/image identity,
checks authority and permissions, records counters, reads one verified existing
panel, compares configured fields with sanctioned independent configuration
evidence, follows a cursor, and checks no-probe settlement. Retain fresh 81-tool
raw enumeration when required, in a separate zero-tools/call session. No arming,
disarming, sensor changes, script/device actions, plans, approvals or restarts
belong to this reader's acceptance. Concurrent unrelated activity is attributed.
