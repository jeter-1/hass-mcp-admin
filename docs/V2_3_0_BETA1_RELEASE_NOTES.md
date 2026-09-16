# Engineering 2.3.0-beta.1 release notes

Engineering 2.3.0-beta.1 adds `control_fan` for exact, ordinary fan requests
through the existing authenticated Engineering connector. It is a prerelease
feature milestone on the retained **HA MCP Engineering Server Beta** installation
path. It does not introduce a second server or a generic service-call tool.

## Typed fan actions

Supported actions are `turn_on`, `turn_off` and `set_percentage`, targeting one
fan entity. Percentage is a strict integer from 0 through 100; zero requests OFF.
The assistant generates an operation ID automatically and reuses it with identical
arguments to reconcile the action. The owner need not manage operation IDs.
An already satisfied request is a verified no-op with no provider attempt.

An exact fan request uses ordinary authenticated connector authorization, without
a configuration plan or panel approval. Existing governed operations retain their
authenticated approval requirements. The wrapper dispatches only the reviewed
internal ha-mcp fan service contract; public generic service calls, bulk targets,
arbitrary forwarding and fallback remain closed.

Fan changes can cause motion and automation reactions. Consumer coverage remains
incomplete, including integration-defined groups behind a single entity.
Verification establishes Home Assistant's reported state and exact requested
percentage, not independent physical feedback. Integration rounding does not
become verified success. See [typed fan control](TYPED_FAN_CONTROL.md) for the
complete arguments, bounds, consequences and recovery contract.

## Execution and reconciliation

The reviewed implementation requires exact target/arguments, fresh state, Core
authority, provider admission and shared execution ownership before dispatch.
Durable intent permits at most one mutating invocation. Lost acknowledgements,
timeouts and cancellation lead to read-only reconciliation, never blind retries
or provider switching. Neither attempt count nor durable intent alone proves
remote delivery. Inspect the authoritative task state and verification outcome;
a successful task retrieval is not proof that the action succeeded.

An unresolved outcome retains protection for the exact fan target. Core and
ha-mcp availability locks are released atomically when that target hold is
settled, so unrelated targets and governed work are not indefinitely blocked.
Active executions retain their dependency locks. Failed local settlement remains
fenced for reconciliation. No automatic hold override or restoration is provided.

Bounded receipts and audit records preserve provider attribution, original
request correlation and available execution facts, including background recovery.
Audit replay deduplication is limited by retained audit logs; it is not an unlimited
permanent ledger. Audit failure does not create new dispatch authority. Ordinary
fan records occupy a separate namespace under the existing governance storage
root; older plan, task and lock formats are unchanged.

## Exact compatibility and installation

Fan semantics are compiled for **Core 2026.9.2 / ha-mcp 8.4.3**, compatibility
entry `ha-mcp-v8.4.3-d5cea47a`, internal protocol `2025-03-26`. Separately
configured, valid signed Core authority is still required for this Core version.
The existing 17 capability references and probe fingerprints are unchanged;
signed read authority alone does not grant the compiled fan action contract.
Other supported Core pairings retain their existing behavior without gaining
this fan contract. No blanket future-Core or future-ha-mcp compatibility is implied.

Healthy registration grows to **52 static tools plus 25 delegated reads, 77 total**.
Existing descriptors are preserved; `ha_get_operation_status` remains absent from
ordinary registration. Actual authority and admission still determine availability.

The directory/slug `hass_mcp_engineering_beta`, add-on name, image repository
`ghcr.io/jeter-1/hass-mcp-engineering-beta`, ports 8100/8110, ingress identity,
configuration and controlled build inputs remain unchanged. Supported images
remain `linux/amd64` and `linux/arm64`. Frozen stable-v1 1.1.2 and signed trust
data are unchanged. Preserve existing Host/Origin and authentication configuration;
the private observer remains dormant unless separately authorized and armed.

## Evidence and acceptance limits

Independent implementation rereview covered commit
`f9bbd9ff4f5b20203bb3699291d09740a553f49c`, tree
`dc89804c2956b045052dd447249c83eaffa57a88`, with no new actionable findings.
Both target-hold and audit-attribution findings were closed. The reviewer ran
158 focused test executions, eight original probes and two additional controls,
all passing. These selections overlap and are not a count of unique scenarios.

Those results establish reviewed source behavior. Final-candidate validation,
actual disposable Core/ha-mcp fan execution, architecture builds and publication
have separately bound evidence. Publication must identify the actual release
source and produced artifacts; it does not establish installed acceptance.
[2.3.0-beta.1 acceptance](V2_3_0_BETA1_ACCEPTANCE.md) requires fresh installed
identity/catalog evidence and separately authorized fan verification. Historical
2.2.x and RC acceptance remains attributed to its original pairing and time.

Do not repeat an uncertain mutation to recover information. Reconcile the
operation/task and current target first; any restoration requires a separate
exact owner request. Installed recovery must account for retained ordinary
records/holds and compatible artifact, Core, configuration and backup state.
Reverting source cannot undo a dispatched action. Complete Android navigation,
cache-only startup during an outage and independent backup-content verification
remain outside the established historical acceptance evidence.
