# Dashboard integrity analysis — implementation checkpoint

`dashboard_integrity_analysis(url_path: str, limit: int = 25, cursor: str = "")`
inspects one exact dashboard's literal entity references and potential configured
controls. It never calls those controls, evaluates templates, renders a browser,
repairs a dashboard, or certifies that an entity can be deleted.

This local checkpoint implements the analyzer and registers its public tool.
It is **not a completed release candidate**: the central provider capability enum
and routing/policy entries need two protected paths omitted from the approved
file boundary (`providers/models.py` and `providers/routing.py`). Their exact
additive patch is prepared for owner approval. Current routing-policy tests
correctly fail until that scope is resolved. No release/version/deployment is
included. Exact frontend execution, disposable exact Core/ha-mcp acceptance,
exact-head CI and independent dashboard review remain pending.

## Evidence collection and authority

The first request performs three logical evidence reads: one admitted upstream
`ha_config_get_dashboard` call with the exact path, `force_reload=true`,
`list_only=false` and `include_screenshot=false`; native `GET /api/states`; and
native WebSocket `config/entity_registry/list`. Initialization, bounded catalog
discovery, authentication and MCP session cleanup are additional wire exchanges.
No other Home Assistant command, retry, redirect, provider refresh or fallback
is reachable from this path. Existing provider entry points retain their behavior.

The new path requires the conjunction of `core.dashboard_configuration_read`,
`core.basic_rest_read`, `core.non_device_registry_read`, and existing upstream
admission. Current Core leases and upstream identity are checked at dispatch,
return, projection, snapshot commit and page export. A missing, retired or
changed authority refuses the request. A hash, path, identity or access
contradiction never becomes a successful partial report.

Native inventory failure can retain useful static findings and valid positive
evidence from the other inventory. Absence requires complete, successful states
and registry inventories without contradictory identities. Categories are
`present`, `unavailable`, `state_unknown`, `registry_only`, `registry_disabled`,
`absent_from_observed_inventories`, and `unassessed`. Absence means absent from
those observations, not globally deleted. Duplicate/malformed/sanitized identities
prevent authoritative absence; state values and registry names are discarded.

## Rule profile and output

Rules were traced through exact Core 2026.9.4 source
`9212531f40a0b7b23229a90d688dd79d9dfccff4`, which pins frontend 20260826.7 at
`380e9b5a81ada29a1d187b4123c54fb3d6fbcc89`. The supplied complete reference's
3,614 regular files were verified against SHA-256 and Git blob identities.
`docs/evidence/dashboard-analysis-source-review.json` records file/line bindings,
relevant upstream tests inspected, and explicit execution limitations. Source
inspection and original synthetic tests are not upstream TypeScript execution.

The closed walker follows views, sections, supported stack/grid/conditional
containers, button/tile cards, supported Entities rows, literal action targets,
and supported condition entity selectors. It preserves original array indices
and never recursively searches arbitrary strings, payloads or custom components.
Conditional visibility does not suppress configured descendants. Unsupported
components, strategies, badges, features, dynamic selectors and unreviewed
semantics produce gaps while supported siblings remain visible.

Button/tile defaults, distinct tile icon slots, six helper widgets, display row
overrides, header-toggle membership and call-service rows follow the pinned
source. Explicit `none` overrides its action slot; it does not disable a separate
inline helper widget. `perform-action` is an action name, not a supported row-type
alias in this frontend. Both `data.entity_id` and `target.entity_id` can be literal
references, but their downstream merge is not certified. Header group membership
is conditional. Potential controls are not current service eligibility, physical
scope or authorization. Confirmation is only absent/present/unresolved;
`more-info` may expose controls, and Assist/custom events have unresolved scope.
Unmapped Core versions retain literal observations and mark defaults unsupported;
`default_rules_applicable` states the rule profile's applicability.

The deterministic item stream contains entity references, controls and coverage
gaps. IDs bind the model, source configuration fingerprint, safe original pointer,
kind and rule/slot. Inferred defaults use their real card/row pointer. Reference
occurrences and unique identifiers are counted separately. Processing truncation
marks counts as lower bounds; pagination alone does not. Independent reference,
availability and control coverage cannot be globally complete while a branch or
source remains unassessed.

Each page repeats compact source identities, collection interval, counters,
failures, sanitized projection hash, and the original dashboard reader's two
verified hashes. A source hash does not certify a sanitized field. Collection
is non-atomic and makes no assertion that the dashboard stayed unchanged later.

## Limits, privacy and lifecycle

The process-owned service admits one analysis or continuation at a time with
immediate `capacity_busy`, keeps at most two live snapshots without evicting them,
and expires snapshots after five minutes. Only allowlisted projected bytes are
frozen, up to 512 KiB each. Private ephemeral HMAC cursors bind caller, path,
snapshot and offset. Continuations revalidate in-memory authority and perform
zero provider reads. Pages reconstruct the same frozen report despite changing
the requested limit. The final envelope is at most the configured response limit
or 32 KiB, whichever is smaller; successful pages never use lossy response fitting.

Collection is bounded to 30 seconds and each evidence read to 10 seconds or the
remaining collection budget. Dashboard payloads are at most 2 MiB, each native
inventory 4 MiB, aggregate wire evidence 10 MiB, and auth messages 64 KiB. Stricter
existing limits still apply. Catalog discovery is capped at 20 pages and 500
descriptors. JSON bytes and lexical depth are checked before decoding, duplicate
keys and nonfinite values refuse, and inventories have 10,000-entry / depth-16 /
100,000-node ceilings. Scanning stops at 10,000 examined nodes, depth 32, 512 unique
references or 1,024 retained items; pointers/IDs are bounded to 256 characters.

Pure admission, decoding, hashes, projections, scanning, fitting and serialization
run in finite owned workers. Network/session tasks and authority callbacks remain
on the owning event loop. Cancellation drains detached work before capacity is
released, including continuation pages. Tests measure the assembled admission/
read/hash/scan/export path with natural GC, loop-thread CPU, wall gaps and a
controlled contention run. The tests use a fresh child to isolate suite heap
history, without disabling GC or raising performance bounds.

No raw configuration, helper value, action payload, URL, confirmation text,
user/registry names, template body or provider message is exported. Audit retains
only rule/tool identity, validated numeric limit, cursor presence, fixed outcome,
counts, provider attribution and an opaque report fingerprint. It omits raw path,
cursor, argument values and unknown keys, including on invalid requests. Errors
use fixed codes; only capacity/timeout/source-unavailable failures may be retryable.
The server itself never retries.

## Preservation, validation and recovery

The 57 existing native descriptors and 25 admitted delegated descriptors are
captured by executing exact checkpoint `c82b56d9e78db16aa7020b81e5a0d103567524d1`
with the locked SDK and existing synthetic provider fixtures. The two fixtures in
`tests/fixtures/dashboard_analysis/` record provenance. Tests compare complete
descriptors, not just counts. The only intended catalog addition is this analyzer.

Reviewed maintenance `7bfea9f66d6b949d192c86ebba814bf802bb99c3` remains integrated.
Its four exclusively owned files are unchanged; the transferred shared baseline
test changes only the registered native count from 57 to 58. Its retry/failure
attribution and refusal assertions are preserved. Earlier checkpoint evidence is
immutable; current command/results and limitations are saved in the assigned
RESULT and a separate `completion-002` artifact directory.

Recovery is a reviewed reversal of dashboard changes while preserving maintenance.
There is no live rollback because this task changed no live system. Stable v1,
versions, dependencies, workflows, signed profiles/journals, generic clients and
other analyses are outside scope and remain unchanged.
