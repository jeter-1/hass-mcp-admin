# Dashboard integrity analysis — incomplete implementation checkpoint

This branch contains independently testable inventory and snapshot primitives
for the approved exact-dashboard analyzer. **The analyzer is not registered or
available.** This checkpoint does not reduce the approved contract to a smaller
feature and must not be released as its completion.

## Implemented internal components

- Closed exact-path/limit/cursor argument validation, fixed errors, bounded JSON
  bytes and nesting, rejection of duplicate keys/nonfinite values, and owned
  worker cancellation.
- Separate native states and entity-registry transport using existing configured
  settings, one attempt per fixed read, no redirects or compression, bounded
  authentication, fixed command shapes and authority callbacks at dispatch and
  return. It is not composed into a runtime or reachable from an MCP tool.
- Internal `McpDashboardTransport.execute_analysis_read` uses the locked SDK's
  public HTTP-client factory. It bounds raw JSON/SSE bytes before decoding,
  refuses redirects/compression and extra application calls, blocks all SDK GETs
  locally, and retains one exact dashboard read plus bounded discovery and
  session cleanup. Strict JSON decoding, SSE normalization and result/catalog
  serialization run in owned workers. Network/session tasks and admission
  callbacks remain on the owning event loop, avoiding per-call worker-loop DNS
  shutdown delays. Existing transport entry points
  keep their original behavior. The provider's no-refresh integration is pending.
- Projection to validated entity identifiers and closed availability/disabled
  categories. Raw values, attributes, registry names and options are omitted.
  Partial, malformed, duplicate or failed inventories cannot prove absence.
- Allowlisted snapshot projection and two retained immutable snapshots per
  service instance, five-minute expiry, caller/path-bound ephemeral HMAC cursors,
  authority revalidation, bounded pages and zero recollection on continuation.
  Runtime singleton composition, which will establish the process-wide limit,
  is still pending.

These components have synthetic/unit and local-loopback tests. They have no
source-derived dashboard semantics or household fixtures. Maximum-size native
inventory transport/decoding/projection and the maximum-size upstream transport
are measured with wall-gap, loop-thread CPU and natural-GC attribution, serially
and under controlled contention. The upstream test uses a synthetic admission
callback, not the final provider/collector. These measurements do not establish
responsiveness of the unfinished full analyzer.

## Required source gate

The approved rule profile is Core 2026.9.4 at
`9212531f40a0b7b23229a90d688dd79d9dfccff4`, with frontend 20260826.7 at
`380e9b5a81ada29a1d187b4123c54fb3d6fbcc89`. Seventeen retained frontend files
match their recorded SHA-256 and Git blob identities. The retained source is
not a complete frontend checkout.

The following exact-version inputs were not found in the searched local
evidence locations:

- `src/panels/lovelace/entity-rows/hui-input-select-entity-row.ts`
- `src/panels/lovelace/entity-rows/hui-input-datetime-entity-row.ts`
- `src/panels/lovelace/entity-rows/hui-input-button-entity-row.ts`
- Relevant upstream frontend tests, including discovery of their actual paths.

`create-row-element.ts` explicitly imports the missing helper implementations.
Further relevant dependencies include `create-element-base.ts`,
`process-config-entities.ts`, `migrate-card-config.ts`, `hui-entities-toggle.ts`,
and the generic row/action implementations needed to establish overrides and
reachable interactions. Their relevance must be traced from the pinned source;
failed filename guesses are not proof that an upstream test does not exist.

The approved offline batch prohibits fetching these inputs. No rule semantics
are encoded while that gate is unresolved. No unsupported helper is silently
treated as supported and no required helper has been removed from scope.

## Remaining implementation and proving work

The rule walker, explicit/inferred controls, six helper domains, exact source
pointers, coverage accounting and rule evidence remain unimplemented. The
isolated upstream transport now has byte-bound and one-call tests, but the
upstream provider still needs its no-refresh integration, strict inner-payload
validation, hash/projection provenance and authority binding. Its existing
reader may refresh/retry and still uses the original transport. The new method
must not be composed through that old refreshing path.

The earlier proposed private-SDK scope expansion is superseded by the tested
public-factory design. The installed SDK may schedule local GET attempts; none
are forwarded. The HTTP adapter reduces a bounded POST SSE response to one
strict matching JSON-RPC response before the SDK sees it, so incomplete SSE
cannot trigger wire resumption. It accepts only a response event, refusing
other data events. No private SDK override, compatibility-module change or
dependency change was needed. Full provider and exact upstream acceptance
remain required; synthetic protocol peers do not establish deployed support.

The final collector must compose exactly the admitted upstream dashboard read,
states and entity-registry reads, hold all three Core profiles plus upstream
authority, preserve source/projection hash distinctions, and bind authority
through return and snapshot commit. Public router/tool validation, registration,
composition, metadata and value-free audit handling remain pending. No raw path,
cursor, helper value or provider message may enter the new audit path.

The future public tool remains
`dashboard_integrity_analysis(url_path: str, limit: int = 25, cursor: str = "")`.
The eventual catalog delta is one native tool with all old descriptors
preserved. This checkpoint adds no tool and makes no installed catalog claim.

The complete synthetic integration suite, exact-source semantic verification,
full-analyzer responsiveness, disposable exact Core/frontend/ha-mcp execution,
CI, independent review and release decisions remain separate requirements.
The coordinator's reconciliation-001 handoff transferred the shared test after
accepting maintenance commit `7bfea9f66d6b949d192c86ebba814bf802bb99c3`. That
complete commit is integrated with all five reviewed files unchanged. Its public
retry guidance and states/configuration failure attribution corrections are
preserved. No dashboard catalog-count edit is needed until registration occurs.

## Recovery

The checkpoint has no live-system effect. Recovery is a reviewed reversal of
the relevant local commits; preserve the accepted maintenance correction when
reversing dashboard work. No live rollback, restart, deployment, source-profile
change, credential access, dependency change or external communication is part
of this work.
