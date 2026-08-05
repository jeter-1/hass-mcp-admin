# HA MCP Engineering Server 2.2.0-beta.19 release notes

Beta 19 is based directly on merged Beta 18 main
`cca0d5e00d75398ec66bca0c9c2f568d11f7497e`. It adds runtime-inert F3
operational-adapter conformance for governed full backup, controlled reload,
exact installed add-on restart, and Home Assistant restart. It does not
activate an adapter or change a current planning, approval, apply,
cancellation, rollback, restart-reconciliation, provider-routing, or public MCP
route.

The operational modules now consume the sole shipped canonical
`ha_mcp_engineering.f3.contracts` objects. They bind one existing public task
to one future durable child execution and use the merged executor's accepted
order: complete durable locks, final mutable-state preflight, caller-owned
idempotent authorization consumption, durable intent, then one reviewed
provider mutation. C2 performs no evidence write between intent and provider.

Independent rereview corrections add
`f3-operational-prepared-authority-v1`, one canonical payload used both to
create and revalidate the prepared hash. Every execution-relevant target,
plan, policy, provider, argument, baseline, reporting, verification, recovery,
hold, and deadline field is bound at every adapter boundary. Final preflight
also compares the recomputed value with the authoritative durable child hash,
so a caller-supplied recomputed checksum cannot replace the stored authority.
Tampering fails before approval, intent, or provider invocation.

The former independent operational recovery ledger is removed. C2 defines
only a frozen bounded read-only projection that F3-D must map from the
authoritative child execution record. JSONL remains secondary audit evidence;
corrupt or contradictory projections fail closed and missing optional provider
IDs never allow retry. Post-intent recovery is readback-only with one reserved
dispatch and an immutable intent-relative deadline.

The executor binding now rejects any configured duration other than the exact
prepared value before execution claim: 86,400 seconds for backup, 900 for
reload, and 1,800 for either restart. The evidence projection normalizes UTC
timestamps and requires durable deadline equality with intent time plus that
duration. Reconstruction cannot shorten, extend, round, or replace it. The
threshold is inclusive: unresolved evidence enters manual review exactly at
deadline, while selective holds remain non-expiring.

Exact complete lock sets now use canonical lock objects. Reload takes the same
exclusive `reload:<domain>` key that Beta 18 configuration writes take shared.
All operations bind shared core/provider dependencies as applicable; HA
restart takes the core exclusively; provider self-restart unions provider and
resource scopes with exclusive dominance. Only the affected resource key is a
future manual-review hold candidate. Evidence/escalation deadlines do not
automatically release holds; selective promotion and authenticated release
remain F3-D.

Verification is intentionally conservative after response loss. Reload
readiness alone cannot prove a reload. An add-on's unchanged running state
cannot prove restart. Home Assistant requires persisted outage/reconnect and
full identity/storage/catalog/admission/dependency/configuration recovery.
Backup may verify from a completed exact-name new identifier outside the
approved baseline and bounded from authoritative intent time. Recorder
inclusion and archive integrity remain unsupported.

Exact `ha-mcp` 7.14.2 and 8.0.0 admission, protocol `2025-03-26`, normalized
78-tool catalog checks, reviewed operational descriptors/fingerprint models,
the 7.14.2 legacy lifecycle response, and the 8.0.0 structured-content model
remain unchanged. Unknown releases and fallback fail closed; generic response
bounds are unchanged.

Beta 19 preserves 48 Engineering-local tools, task schema 1, configuration
plan contract 2, operational plan contract 3, F2 policy outcomes, approval
authority, dashboard-execution deferral, exact 7.14.2 78/26/74 accounting,
exact 8.0.0 78/24/72 accounting, held tools `ha_search` and
`ha_get_operation_status`, `aiohttp==3.14.3`, `cryptography==50.0.0`, stable
v1.1.2, and zero fallback.

F3-D still owns durable child execution, production authority-reader and
operation-evidence mapping, exact per-operation executor construction, one
central reconciliation coordinator, selective hold promotion and release,
private authenticated manual reconciliation, governed rollback Option A, and
runtime route migration. Issue #92 remains separate. This source
declaration authorizes no merge, tag, release, image, attestation, provenance,
deployment, production access, or live operational action.
