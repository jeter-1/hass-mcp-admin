# HA MCP Engineering Server 2.2.0-beta.20 acceptance

## Source and compatibility boundary

- direct base: Beta 19 main `51943e11cc5290b1bf8db75474982193463044f5`;
- exact merged F3-0/A/B/C1/C2 heads:
  `77d8f19b3dc12ec94eef134375ddcbd5baeb2670`,
  `94392e31b2dd1892889ca643e8cabb157085ffc1`,
  `d76badbf2263541c33b07cd366a3ed77bc0902aa`,
  `f26328c0f95769b3893ee650ce4abcfe976d3397`, and
  `bcb6d93bfe3010722942ec566f2a17f8d6014e97`;
- Engineering/stable: `2.2.0-beta.20` / `1.1.2`;
- protocol: `2025-03-26`; task/configuration/operational schemas: 1/2/3;
- pins: `aiohttp==3.14.3`, `cryptography==50.0.0`;
- public tool delta zero; local tools 48; fallback zero; and
- dashboard execution excluded, unregistered, and unreachable.

## Required functional evidence

Acceptance requires exact activation of all eight C1 and four C2 capabilities
through one closed registry and executor. Test every operation's immutable
projection, complete locks, stale preflight, approval, durable intent, single
provider boundary, readback, outcome, public projection, duplicate apply, and
cancellation rule.

Prove one schema-1 task and deterministic child authority under concurrent
processes and crashes before/during/after initialization. A failed task/child
write invokes the provider zero times. Journal replay may resume exact
authorized pre-intent work, but creates one task, child attempt, intent, and at
most one mutation.

Prove same-target and update/rollback conflicts; configuration/reload and
core/restart conflicts; duplicate backup/reload/add-on conflicts; exact
provider self-restart conflict; and safe independent-resource concurrency.
All sets are atomic and use no process-local fallback.

Prove 120/20/0/0.05-second lease/renewal/wait/poll timing. Stale transfer must
preserve fencing. Every post-intent path keeps `dispatch_count == 1`, provider
redispatch zero, recovery mutation zero, and the original deadline.

Prove selective promotion retains the target generation, releases dependencies,
never expires, reconstructs after process loss, and needs authenticated
generation-bound reconciliation. Crash-interrupted authorized release must
finish without provider access.

Prove startup fails closed on invalid child/lock/ownership storage or registry.
The startup sweep precedes listener creation; `/ready` requires catalog and F3
readiness. Health and audit remain bounded and sanitized.

Prove upgrade behavior for empty, terminal, approved-unapplied, active legacy
pre/post-dispatch, legacy manual-review, corrupt, expired, duplicate-startup,
provider-outage, and HA-outage cases. Reading history must not mutate. Prove
downgrade restrictions without deleting F3 data or Beta 19 redispatch.

Prove rollback Option A for supported current and historical configuration
evidence. The request performs no mutation; the reverse plan requires separate
approval and the same F3 lock/intent/verification path. Operational/dashboard
rollback stay unavailable.

Prove dashboard setter, planning tool, operation vocabulary, full replacement,
and Python transform are unreachable. Dashboard v3 reads and inert F3-B
infrastructure regress cleanly.

## Validation gate

Require compilation, YAML, all F3/sibling/integration suites, existing
configuration/operational/dashboard regressions, exact 7.14.2/8.0.0 acceptance,
Fast, Full, Evidence, fresh `pip check`, strict audit with “No known
vulnerabilities found,” deterministic registry regeneration, stable and
Engineering packaging, no-push architecture builds, exact-image lanes,
immutable add-on acceptance, disposable pinned-HA contracts, secret/protected
path/PowerShell/whitespace/publication guards, and green push and draft-PR
exact-head CI. Record exact counts and explain every skip.

Production Home Assistant, deployed MCP endpoints, credentials, Supervisor
tokens, publication, deployment, merge, and live restart are prohibited.
