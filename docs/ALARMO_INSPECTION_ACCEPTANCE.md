# Alarmo inspector acceptance

This is an acceptance definition for the bounded inspector increment, not a record of
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
The exact Core 2026.9.4 source/tests are listed in the source-review record,
which is kept as a historical record of that review (HAMCP-159). It is no longer
the CI basis: each Alarmo run now records mechanical per-slot evidence (see
below). Inspection alone must not be reported as execution of upstream tests.

## Disposable Core/Alarmo gate

Required pairing (HAMCP-159): every non-empty Core slot in
`ci/support-window.yaml` that the run's tier covers. Pull requests run
`core.current` and, when one exists, `core.candidate`; the full tier adds
`core.rollback`. Each slot is its own parallel Real HA matrix job, flagged by
`alarmo: true` from `scripts/support_window.py`. An explicitly empty candidate
(`candidate: null`) creates and requires no candidate job. Each slot binds, by
exact string with no prefix, family or wildcard:

- the Core version and image index digest from the window, which must equal
  that Core's exact entry in the one reviewed binding list,
  `scripts/core_lane_bindings.py` (a window Core without one refuses with
  `core_<slot>_binding_unreviewed` before any matrix row is generated);
- the linux/amd64 descriptor, source commit and source-archive SHA-256 from
  `tests/fixtures/core_<version>_lane_provenance.json`;
- the ha-mcp `current` slot; and
- Alarmo commit `169e134f4b70d87aae36ba54a72a398ecc960afd`.

A version outside those slots refuses (`alarmo_core_not_in_window_slot`). The
image receipt records `core_version`, `support_slot` and the SHA-256 of the three
Core read handlers (`core_read_sources_sha256`), hashed from the digest-verified
source archive. That receipt is the per-slot CI evidence. The automation-baseline
proof travels with Alarmo to the same slot under the same rules, and its retained
result must carry the exact nested `support_slot` of the job (a missing or
different nested slot refuses, as do a different Core or outer slot) (an owner-approved
deviation recorded in the HAMCP-159 pull request). Lanes keep the 50-minute job
bound and unchanged permissions/action pins. No ARM execution, production
endpoint or key is implied.

Rotation among Cores already in the window edits only `ci/support-window.yaml`;
a brand-new Core first adds its exact binding to `scripts/core_lane_bindings.py`
as one deliberate review step (contract amendment, Josh 2026-10-11). The
real-HA runner routes every Core that a window slot advertises for Alarmo to the
child-device contract that runs Alarmo and the baseline proof; there is no other
route for such a Core.

Failure triage per slot: harness breakage is fixed in the harness. A
production-path difference on a slot, including the candidate, is not absorbed
by adjusting fixtures, expectations or receipts. The job stays failing and is
reported as a compatibility finding (candidate-blocking on the candidate slot),
and Josh decides whether to adopt that Core.

The workflow stages the hash-verified Alarmo archive and synthetic source-written
store **after** the historical migration writer stops and **before** target Core
starts. The fixture has no users or actions. It also installs the test-only
`alarmo_interval_observer`, never included in either shipped add-on. The helper
refuses an existing overlay destination:

```text
python scripts/alarmo_inspection_contract_acceptance.py --prepare-archive <verified archive> --destination <new disposable overlay>
```

The publication check verifies the exact Core source archive, index and platform
manifest bytes/digests/size, then container/image/container configuration-digest
continuity. It records architecture and never exports container environment.

The required scenario creates its Alarmo singleton through a normal config flow
outside the measured interval and waits for setup/storage settlement. Exact Core
2026.9.4 `helpers/registry.py:13-14,74-79` schedules registry writes after 180
seconds during startup (10 seconds while running). A setup-only readiness read
therefore allows at most 41 polls over 200 seconds, requiring Core running,
no queued or active fixed-store writes, and intact observer hooks. It does not
force saves or retry feature reads. The 45-second observation and 30-second
feature collection bounds are unchanged; missing settlement still fails. It then:

1. Captures an actual extra harmless `get_states` request as a negative observer
   control and proves that a zero-command expectation rejects it.
2. Uses a 19-reference ephemeral signed journal and proves the public inspector
   refuses with no feature commands, independently observed inside Core.
3. Extends that **same registry** to 20 through `extend-capabilities`, preserving
   its authenticated predecessor. No production journal is used.
4. Runs the real public tool/transport: nine fixed commands in order, two pages,
   no continuation reads, useful configured membership/modes/delays, explicit
   false/zero/null values and absent registry membership. Page-local evidence
   resolves; partial coverage is preserved rather than called complete.
5. Requires all observer hooks intact, no flow init/configure/abort, service call,
   command-origin storage work, unexpected command or changed fixed store hashes.
   Captures are bounded to 128 events/45 seconds; overflow/expiry fail.
6. Reconciles Core leases/commits/fallback and retires/joins the test-owned Core
   connection monitor before writing a success receipt. It validates owned
   container/network removal after the outer always-cleanup step. Missing
   execution, NOT_RUN, failed monitor cleanup, Docker enumeration failure,
   residue or invalid interval proof fails the job.

The observer hooks exact Core `ActiveConnection.async_handle`, config-entry and
generic flow managers, service calls and Store save/delayed-save/write/remove.
Context propagation associates asynchronous callbacks with their command.
Independent background auth/restore-state bookkeeping is explicitly retained;
it is not a global zero-write claim. This finite interval cannot prove absence
of future arbitrary tasks or writes bypassing Core Store. Hashes of the fixed
Alarmo/config-entry/entity/device stores supplement the hooks without retaining
store contents. The observer retains no arguments, config bodies or credentials.

The required result marker is `Alarmo disposable inspection contract:`. Missing
fixture receipt or a non-PASS result fails that slot's Real HA runner. The final
artifact includes independently verified interval observations, image identity
and cleanup receipts. Unit/synthetic hook checks do not close this assembled gate.
Run `tests.test_alarmo_interval_observer`, the seven inspector modules,
`tests.test_core_registry_preparation` and relevant Core/workflow checks before
complete Full/Evidence and independent review. Record actual CI at the final
candidate, including failures or unavailable execution.

## Independent review and later installation

Review the final source delta, transport, privacy, authority, fixtures and test
results independently after Full/Evidence. Record outstanding gates by stage.
Before activation: verify the extension and disposable-lane review/CI; review/sign/activate
applicability, reconcile P0b installed Alarmo files or the owner's explicit
local-modification declaration at its actual assurance level, and approve release.

A later explicitly authorized installed pass binds publication/image identity,
checks authority and permissions, records counters, reads one verified existing
panel, compares configured fields with sanctioned independent configuration
evidence, follows a cursor, and checks no-probe settlement. Retain fresh 81-tool
raw enumeration when required, in a separate zero-tools/call session. No arming,
disarming, sensor changes, script/device actions, plans, approvals or restarts
belong to this reader's acceptance. Concurrent unrelated activity is attributed.
