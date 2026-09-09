# Engineering 2.2.0-rc.4 acceptance

RC4 prepares the independently reviewed post-Core corrections for release.
Final review state requires all three authoritative Engineering versions to be
`2.2.0-rc.4` and `.release/next-version` to be consumed. Stable remains 1.1.2.
Materialization, source review and candidate CI do not establish publication,
installed identity or live acceptance.

The last reported household Core is **2026.9.1**, already updated. This is the
acceptance path for the corrected Engineering build on that installation,
subject to fresh authorized identity readback. It does not prescribe another
Core update. The exact household triggering device payload remains unobserved;
the two reproduced source defects are not proof of the incident's complete cause
or its resolution after deployment.

## Source, release and compatibility authority

Published RC3 / complete-PR release-validation base:
`a4c18864b1b6092f7f70255a6b7f37192a5bf100`.
Independently reviewed correction:
`a56ba457ee40ca32c904f63f139e43cfbd2efd36`.
Record the final RC4 head and tree in delivery evidence. Compare the complete PR
with published RC3 and the release-preparation delta with the reviewed correction.
Keep `ha_core_readmission/device_registry.py`, `ha_core_readmission/runtime.py`
and `tests/test_rc3_postcore_regressions.py` byte-identical to that correction.
Preserve the prior failed RC3-version Evidence records and independent review.

Resolve this document using `python scripts/codex-context.py --format json`.
Require exact version-matched document resolution before dependent release or
deployment work. The RC4 preparation delta changes release declarations,
current documentation references and necessary version-specific test expectations
only. It does not change runtime behavior or the reviewed correction.

The compiled compatibility profiles and immutable authority remain unchanged:

| Core | Exact source commit | Reviewed OCI index |
| --- | --- | --- |
| `2026.9.0` | `dfb5a9e690daaf204b542896e4b595e61a11a401` | `sha256:372d991e58882a1d8c68c07e9aa3f3b509276e695355f73ccdb03baa70407293` |
| `2026.9.1` | `fc034572d0216a04ed40a07154394908a594dfed` | `sha256:612d76760b544cb40b7ba01387fdac964c59a6a550a50a4d30b4773c822d2918` |

These are retained compatibility authorities, not an upgrade proposal or proof
of the installed Core image. Previously admitted Core releases remain unchanged.
An unlisted patch cannot admit itself. Use the
[immutable authority fixture](../tests/fixtures/ha_core_2026_9_authority.json),
compiled profiles and semantic registry as exact source evidence.

The intended internal pairing is exact reviewed ha-mcp 8.4.3, entry
`ha-mcp-v8.4.3-d5cea47a`, source
`eac7a3aa7063432e9af17e7d7726040e909c7b8f`, OCI index
`sha256:d5cea47a0115e5d161c2b319ee637b1b0a5bcfafe1597cb490299bbbc6329456`.
Engineering remains the one public endpoint and catalog through Nabu Casa;
provider selection and admission stay internal. No second client connector,
arbitrary forwarding or direct-HA/upstream fallback is authorized.

## Correction requirements and retained boundaries

**RC3-PC-1 — device display text.** Validate `name` and `name_by_user` as
string-or-null display values independently of identifier restrictions. Null,
ordinary, empty, long and Unicode strings must not withdraw device/dependency
authority solely because they are unsuitable identifiers. Preserve the exact
text and the registry byte/count bounds. Wrong types, invalid identities,
duplicates, parent/cycle errors, invalid timestamps and unsupported versions
must retain their existing capability consequences.

Prove the actual probe/projector/coordinator and registration path restores
`core.delegated_device_effective_area`, `core.dependency_helper_planning` and
`core.direct_device_registry_read` with compatible evidence. Malformed device
evidence must still withhold its dependent capabilities while preserving
independently valid capabilities. With the exact admitted provider, the five
reads `ha_get_device`, `ha_get_entity`, `ha_get_entity_exposure`,
`ha_get_overview` and `ha_search` must return through ordinary registration with
unchanged descriptors and truthful attribution.

**RC3-PC-2 — recurring failed reconciliation.** Failed supervisor observation
or monitor attachment must retire unusable authority immediately and wait the
existing **300-second reconciliation interval** before retrying. Coalesce
redundant dependency invalidations while authority is absent; notify real
published-authority transitions. Healthy connection changes remain prompt.
External connection changes during a failure delay still retire authority
immediately and are collected for the next current-epoch probe.

Recovery can take one interval plus probe/attachment time; further failures
extend it. This is not a global request-rate limit: startup retries and direct
reconciliation are separate. It is not a remote-abort guarantee. Preserve fresh
authentication, epoch/version binding, current-generation read/publication
checks, cancellation propagation, exactly-once cleanup and automatic recovery
without a manual restart.

Use synthetic network/session boundaries and a controlled scheduler for success,
wrong-type and malformed evidence, HTTP/connection/authentication failures,
timeouts, monitor failure, repeated hints, retirement, shutdown and cancellation.
Do not replace the authority boundary being tested. Check attempts, transition
notifications, retained leases/commits, refused stale/late callbacks and useful
unrelated authority after recovery. Do not inject these faults into the household.

Preserve [RC3's shared-build requirements](V2_2_0_RC3_ACCEPTANCE.md) and
[RC2's earlier corrections](V2_2_0_RC2_ACCEPTANCE.md): manager-owned
`core.dependency_helper_planning` authority; one build for shared waiters;
startup prewarm, foreground and retained background refresh; cancellation and
late-callback protection; source-epoch and governed post-lock freshness fences;
the separate 300-second cooperative build deadline; unchanged 600/3600-second
soft/hard TTLs and stale disclosure. Already-dispatched reads may finish after
cancellation. Invalidated, retired or expired evidence cannot become current.

Retain raw REST NaN/Infinity/-Infinity rejection and authority-local consequences,
conservative connection failures, useful valid responses, per-read composite
authority, capability-local `/services` timeout, discarded timed-out WebSockets
and bounded freshly authenticated replacement at the same version/epoch.
Retain R182-1 cleanup and unrelated read capacity after 64 cancelled canaries.

The healthy reviewed pairing requires **51 static tools plus 25 delegated reads,
76 total**, and all 17 expected Core capabilities. `ha_get_operation_status`
remains held and absent from ordinary registration. Public schemas, provider
contracts/attribution, admission policy, routing, governance, permissions,
dependencies, workflows, Dockerfiles, TTLs and stable-v1 remain unchanged by
release preparation. Task schema remains 1 and approval authority remains 3.
No new write, forwarding or fallback surface is introduced.

## Evidence stages and source validation

Record PASS, FAIL, BLOCKED or NOT RUN per check, with UTC time, exact source or
deployed identity, sanitized arguments, request IDs where available, expected
and actual results, provider, completeness/truncation, verification and recovery.

| Stage | Required evidence |
| --- | --- |
| Independent correction review | Verdict bound to `a56ba457ee40ca32c904f63f139e43cfbd2efd36`; it does not cover the new release delta automatically. |
| Materialized candidate | Clean final head, both comparisons, unchanged correction hashes, consistent RC4 declarations, consumed staging file and exact RC4 documents. |
| Local validation | Focused release tests and complete Evidence gate against published RC3, with actual counts/skips and all applicable steps passing, including metadata. |
| Independent release review | Separate reviewer verifies both ranges, preservation, documentation, release authority, security scope and candidate evidence. |
| CI/build | Exact run/attempt IDs, job conclusions, relevant logs/artifacts and checkout revisions; bind synthetic merge parents and tree to candidate/base. Historical CI is separate. |
| Publication | Future authorized release commit, immutable index/platform manifests, labels and provenance. Candidate build evidence is not publication. |
| Installation | Future authorized running-container/image binding to the published architecture manifest and configuration digest, with clean source/build labels. Tags or version display alone are insufficient. |
| Live acceptance | Fresh catalog, useful reads, automatic refresh and separately authorized canaries on the corrected installation. Earlier RC3 receipts remain historical evidence. |

Run `python -m unittest -v tests.test_rc3_postcore_regressions` and the retained
selection covering `tests.test_held_read_canary`,
`tests.test_readonly_upstream_gateway`, `tests.test_ha_core_2026_9_integration`,
`tests.test_ha_core_capability_auto_readmission`,
`tests.test_ha_2026_8_device_compatibility`, `tests.test_real_ha_contract_gate`,
`tests.test_dependency_build_authority` and `tests.test_beta39_fenced_refresh`
as applicable source evidence. Full discovery must include these tests on the
final RC4 candidate. Record independent executions separately from inspected logs.

## Corrected-build acceptance on the already-updated installation

After separate publication, deployment and bounded live-read authorization:

1. Read actual Core, Supervisor, HAOS, Engineering and ha-mcp identities. Expect
   RC4 and last-reported Core 2026.9.1, subject to readback. Record any drift and
   resolve affected compatibility before dependent work. Bind the running
   Engineering image to the published RC4 index, correct architecture manifest,
   image/configuration digest, source revision, build timestamp and clean labels.
   Existing RC3 image evidence does not establish an RC4 installation.
2. Use a fresh client session through the existing Engineering Nabu Casa
   endpoint. Capture initialization and every raw `tools/list` page, preserving
   complete descriptors and cursor continuity. Compare all public schemas and
   metadata with the exact RC4 source and dependencies. Require 76 unique tools,
   including the five restored reads, and no ordinary operation-status tool.
   Missing raw catalog or installed-image access is BLOCKED; cached inventories
   and health counts cannot substitute. Use existing authorized facilities.
3. Capture `server_info`, `get_server_health(check_ha=true)` and
   `list_capabilities`. Verify REST/WebSocket identity agreement, all 17 expected
   Core capabilities, exact provider admission, dashboard authority, healthy
   storage/audit and F3 readiness. Explain any withheld capability.
4. Exercise each restored delegated read using confirmed existing entity/device
   fixtures and small bounds; compare IDs and effective-area/exposure semantics
   with authoritative registry evidence. Do not demand a parent/child relationship
   where the fixture has none. Also perform bounded entity/state, service,
   automation-configuration and storage-dashboard reads. Record truthful provider
   attribution, useful results, completeness, errors and zero fallback.
5. Establish settled health and dependency generation/build time, age, source
   epoch and invalidation baseline. Let evidence age naturally past its reported
   soft expiry while below hard expiry. Issue one bounded ordinary dependency
   query with `refresh_index=false`. It may return stale evidence explicitly.
   Record that request's completion; observe health without repeated dependency
   queries for up to 330 seconds to avoid obscuring a failed build with a retry.
6. Require retained refresh completion after the initiating request returns,
   a newer build/generation and fresh coherent replacement evidence on the next
   ordinary query. An unchanged configuration fingerprint is permissible;
   `refreshed=false` alone does not refute automatic replacement. Missing timing
   or generation evidence is BLOCKED. Preserve the original diagnostic and
   distinguish a probe/readmission failure from a dependency-build failure.
7. After work settles, compare issued leases, active commits, exhaustion and
   fallback counters with baseline. Attribute legitimate background work;
   unexplained persistent retention is a failure. Finish with an unrelated
   successful Core read. Preserve existing household issues separately from new
   regressions, including any unchanged governance projection failures.

Complete representative templates, one configuration validation, bounded
dependency/reliability analysis, dashboard rereads, and a validly shaped missing
read target followed by a successful unrelated read. Use naturally existing
automation traces; do not trigger an automation to manufacture evidence.

## Canaries, recovery and final disposition

Resume mutation canaries only after the corrected-build identity, catalog,
authority, useful-read and automatic-refresh gates succeed. Prepare exact
targets, pre-state, arguments, effects, incomplete consumer coverage, verification
and restoration before requesting bounded authorization. Retain every shipped
external plan approval; `approve_change_plan` requests approval, not grants it.

The held-read missing-resource canary needs its exact reviewed entry and a
synthetic operation ID. Require `resource_not_found`, `retryable=false`,
`promotion_performed=false`, unchanged registration, settled resources and zero
fallback. Do not create a device operation for a positive example.

For an existing dedicated test helper, inspect consumers, record original state,
approve one opposite-state plan, apply once, inspect task status and independently
read back the change. Use a fresh approved restoration plan and verify the
original state. For an existing dedicated storage dashboard, capture complete
configuration and both hashes, exclude concurrent edits, approve one supported
cosmetic patch, apply once, verify authoritative configuration and actual-client
rendering, then approve the supported inverse plan and verify full restoration.
Dashboard writes are non-atomic against external editors. Uncertain dispatch or
external drift requires task/status and authoritative-state reconciliation before
further mutation; never blindly retry or overwrite unexpected edits.

Android navigation remains separately authorized, with an exact harmless plan,
bounded notification count, authenticated inbox navigation, rejection/clearing
and no execution. Restart recovery requires an exact component and count, no
active mutations or unfinished restoration, and verified recovery access/backup.
After an authorized restart, verify the same artifact, fresh catalog and
authority, healthy storage/F3, prewarm or initial build, useful reads and settled
resources. Do not reuse pre-restart plans for new dispatch.

Before any authorized deployment recovery or restart, establish a usable full
backup and off-host availability, selected contents including Engineering and
ha-mcp data, completion, timestamp and size. Account for external databases,
local access that survives connector failure, the emergency kit without exposing
its key, overwrite consequences and restored Core/configuration/database
consistency. Backup creation and restart are separate operations, not granted by
this document. No further Core update is part of RC4 acceptance; any later change
needs its own exact-target review and authorization.

[Published RC3](https://github.com/jeter-1/hass-mcp-admin/releases/tag/v2.2.0-rc.3)
at `a4c18864b1b6092f7f70255a6b7f37192a5bf100` remains immutable. Returning to RC3
restores the known display-validation and reconciliation defects. RC2 additionally
has the prior shared-refresh ownership defect. Neither reinstalling an old
Engineering artifact nor stable-v1 is a complete recovery plan for the updated
Core/configuration/database state. Select and verify a compatible recovery
artifact and backup under separate authority.

Before merge, keep the draft unused or revert only release preparation without
rewriting the reviewed correction. Later corrective release, rollback or
deployment decisions must preserve published artifacts and establish exact
verification/recovery. Stop dependent mutations for identity drift, lost required
authority, fallback, uncertain dispatch, failed verification/restoration,
persistent retention or unusable recovery. Continue unaffected authorized reads.

Issue separate conclusions for identity/image binding, fresh catalog, restored
reads/authority, automatic refresh, regression, each canary/restoration,
navigation and restart recovery. Keep unresolved household cause explicit.
Leave the delivery PR draft. Josh's later Ready action may authorize the
repository's controlled merge/publication; deployment and live acceptance remain
separate.
