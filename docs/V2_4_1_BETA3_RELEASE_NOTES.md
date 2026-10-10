# Engineering 2.4.1-beta.3 release notes

Engineering 2.4.1-beta.3 adds exact ha-mcp **8.6.0** compatibility on top of
beta.2's **Core 2026.10.0** support. It retains the existing 8.5.0 provider and
September contracts. The same release integrates cooperative deep-health
collection and bounded Core reconciliation diagnostics. Installing Engineering
does not upgrade upstream, select new signed authority or admit an unreviewed
operation.

Explicit asynchronous health now reads each retained plan, parent, child and
manifest once per call, yields during collection, and reuses validated records
for F3 reconciliation. In the offline 200-parent/three-child fixture, this reduces
5,000 cold / 4,800 repeated reads to 1,000 per call. These are synthetic operation
counts, not installed latency results. Every explicit async request still makes
a fresh integrity collection; historical warm-cache acceptance criteria do not
apply to this path. Routine request readiness continues without a history scan.
Per-file I/O remains synchronous and external-process snapshot checks are
optimistic. See [health behavior and limits](HEALTH_PERFORMANCE.md).

Initial consistency-fence failures retain their storage attribution and execution
readiness protection. Collected parent records must match their canonical
plan/task filenames before they can certify settlement. Concurrent writer
movement remains a local `health_snapshot_superseded` refusal; cancellation does
not publish partial navigation or transfer worker ownership prematurely. No
durable format, approval, dispatch or recovery authority is changed.

Core reconciliation now records fixed, sanitized early-failure reasons through
the existing audit sink and identifies successful `failure_retry` events.
The 300-second failure pacing, signed authority, retirement and stale-plan
controls remain unchanged. The diagnostics cannot reconstruct discarded
historical details, and neither correction proves the cause of the October
disconnect. See [diagnostic fields and limits](CORE_AUTHORITY_DIAGNOSTICS.md).

The healthy catalog remains **83 tools: 58 native and 25 delegated reads**.
On 8.6, `ha_get_skill_guide` takes `file` (default `SKILL.md`) instead of the
8.5 `skill` selector. Other existing public descriptors are preserved. Closed
blueprint list/get and scene/template adapters exclude newly advertised writes,
search modes and template options. The dashboard guide uses the exact new
transport while retaining strict acknowledgement and external approval.

Both fixed search catalogs are supported: reference and the pinned optional
`ha_mcp_tools` 2.2.1 component. Their complete raw contracts and compiled policy
are bound independently of observation. A changed variant retires stale search
before dispatch; signed denial/revocation still controls admission. Search
config budgets retain their documented per-ID legacy scope. Upstream can
internally fall back to its legacy reads; Engineering adds no fallback or retry.

Typed fan/light/switch and governed dashboard operations preserve target,
authority, stale-state, durable ownership, independent readback and recovery
checks. Core 2026.10.0 with 8.6.0 has an exact adapter binding to beta.2's unchanged
October probe. Its fingerprint and all 21 Core capability contracts are retained;
a signature cannot supply a missing parser or adapter. Future versions are not
covered. Backup, lifecycle and operation-status remain held. Govee package work
is shelved; HAMCP-151 automated add-on restart is owner-deferred with admission
held. Neither is included in this release.

The prior 8.6 source review and September amd64 disposable receipts are retained.
This release adds fixed September/October CI rows for native amd64 and arm64,
each with both catalog profiles and both packaging forms, immutable pins,
bounded evidence and unconditional owned cleanup. Local October results and
revision-bound validation are recorded in the
[compatibility evidence](HA_MCP_8_6_0_COMPATIBILITY.md). CI has not been triggered
by local preparation; native arm64 and exact-head CI remain release gates.
Disposable add-on execution uses a Core relay, not a real Supervisor. Synthetic
state/readback is not physical or household acceptance.

Stable v1.1.2, dependency locks, build inputs, durable formats, Core probe
fingerprints and workflow permissions remain unchanged. The unresolved RC1
projection/approval-sequence warning retains its prior disposition. Garage/D2
acceptance is preserved; no household drill is required by this change.

Follow the [beta.3 acceptance contract](V2_4_1_BETA3_ACCEPTANCE.md). Upgrade
Engineering before upstream; preserve the exact working beta.2/8.5 artifacts,
options, journals and independent management access. An Engineering beta.1
rollback alone cannot recover October compatibility. Inspect uncertainty without
redispatch and never clear journals or locks to force recovery. The pinned
8.6 startup can refresh HACS repository information in the background; its
household consequence needs a separate owner decision before activation.
