# HA MCP Engineering Server 2.2.0-beta.17 acceptance

## Source boundary

- direct base: merged Beta 16 main
  `e2861b646d0e09effdcc1e6955f1f1ab7951980e`
- Engineering version: `2.2.0-beta.17`
- stable version: `1.1.2`
- adapter contract: `f3-operation-adapter-v1`
- canonical contract module: `ha_mcp_engineering.f3.contracts`
- task schema: 1
- configuration plan contract: 2
- operational plan contract: 3
- Engineering-local tools: 48
- exact protocol: `2025-03-26`
- secure pins: `aiohttp==3.14.3`, `cryptography==50.0.0`
- fallback: 0

No source or validation step may access production Home Assistant, HAOS,
Supervisor, credentials, tokens, deployed MCP endpoints, or live provider
responses. Nothing in this document authorizes publication, deployment,
tagging, merging, runtime activation, or dashboard mutation.

## Canonical contract acceptance

1. `ha_mcp_engineering.f3.contracts` is the sole runtime definition of
   `f3-operation-adapter-v1` and is present in the built image.
2. Accepted constants, enum values, frozen dataclass fields and order, protocol
   method names, and keyword-only boundaries match the F3-0 declaration.
3. Repository-root `f3_contracts` is only a compatibility/test facade and
   exports the exact canonical objects by identity.
4. No shipped module imports repository-root contracts, test support, or the
   repository-root dashboard foundation.
5. Every shipped module imports in a copied container-equivalent package from
   a fresh subprocess whose working directory and `sys.path` exclude the
   checkout.

## Dashboard foundation acceptance

Retain exact storage identity and complete raw preread evidence, bounded
`f3-dashboard-json-pointer-patch-v1` compilation, 16-leaf semantic bounds,
unknown-field preservation, semantic diff, risk evidence, immutable private
artifacts, stale-state checks, and exact complete-readback verification.

The reviewed Home Assistant and `ha-mcp` interfaces have separate hash-check
and save operations. They provide no atomic compare-and-save, expected-hash
enforcement at the save boundary, transaction receipt, or authoritative
exclusion of all external Lovelace writers. Engineering locks coordinate only
cooperating Engineering operations. Final reread cannot reveal an external
edit overwritten before the approved result became final.

Therefore require:

- atomicity mechanism: none;
- dashboard execution rejected before durable dispatch intent;
- dashboard setter invocation count: zero;
- synthetic dashboard-fixture mutation count: zero;
- generated `python_transform`: rejected setter candidate;
- full-configuration replacement: prohibited workaround;
- dashboard public tool and persisted `update_dashboard`: absent;
- dashboard provider transport, dispatch, rollback, and fallback: absent; and
- exact verification accepted only from complete readback evidence, never a
  provider response or short hash alone.

Reconsideration requires independently reviewed evidence of atomic
compare-and-save, expected-hash enforcement at the authoritative save boundary,
or authoritative exclusion of every dashboard writer.

## F3-A conformance acceptance

Require the canonical-contract synthetic adapter to remain accepted by
`SharedOperationExecutor`, canonical lock requests to normalize through the
F3-A model, complete lock sets to acquire atomically, and fencing generations
to remain enforced. Persistence failure before intent must invoke accepted
synthetic mutations zero times. Every possible-intent recovery is observation
only. Duplicate, cancellation, lease, reverse-release, manual-review, and all
15 process-loss boundary tests remain required.

The dashboard-specific conformance adapter must request exact
`dashboard:<url_path>`, `home_assistant:core`, and `addon:<slug>` locks and then
reject the atomicity gate before intent. Its dispatch method must remain
unreachable.

## Runtime and compatibility acceptance

No current application startup, registry, service, provider route, governance
apply route, or recovery coordinator may import or instantiate the dashboard
foundation. No plan/task schema or current public input/output schema changes.
Dashboard reads and operational providers remain unchanged.

Exact 7.14.2 remains 78 advertised, 26 delegated, zero held, 48 local, and 74
total. Exact 8.0.0 remains 78 advertised, 24 delegated, exactly two held, 48
local, and 72 total. The held set is exactly `ha_search` and
`ha_get_operation_status`. Require zero missing, quarantine, unreviewed,
automatic-read mismatch, and fallback counts. Stable-v1 source has an empty
diff.

## Validation tiers

Require compilation, YAML and add-on metadata, canonical-contract tests,
built-image import closure, repository-wide F3 import boundaries, focused F3-A
and F3-B suites, task/plan compatibility, Fast, Full, clean exact-head
Evidence, fresh `pip check`, strict `pip-audit` with no known vulnerabilities,
secret and whitespace checks, PowerShell parsing, stable and Engineering
packaging, amd64/arm64/arm-v7 no-push builds, exact-image 7.14.1/7.14.2/8.0.0,
immutable 8.0.0 add-on runtime, and disposable pinned Home Assistant contracts.
Compatibility and acceptance tests perform zero real provider mutation.
