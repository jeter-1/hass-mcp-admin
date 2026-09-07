# Engineering 2.2.0-rc.2 release notes

Engineering 2.2.0-rc.2 is the materialized source candidate for exact Home
Assistant Core 2026.9 capability readmission. The advertised Engineering source
is 2.2.0-rc.2, `.release/next-version` has been consumed, and stable remains
1.1.2.

## Core capability-scoped readmission

Engineering now observes two stable authenticated Core snapshots, requires
REST/WebSocket identity agreement, and publishes binary-owned per-capability
authority. Core, ha-mcp, and transport generations are independent. Registered
single-use route leases bind the exact generation, session, observation,
profile, adapter, capability, and mutation target. Drift or duplicate commit
fails before dispatch; recovery after durable intent remains readback-only.

Exact Core 2026.9.0 and the newer 2026.9.1 patch are reviewed. With exact
ha-mcp 8.4.3, all 25 delegated reads remain available. In particular, the five
device-dependent reads now use ha-mcp 8.4.3's shipped child-device and
effective-area correction rather than inheriting the obsolete 8.4.1
quarantine. A legacy 8.4.1 adapter on Core 2026.9 still withholds only those
five reads.

Template/Jinja behavior, Probatio validation, dependency/helper planning,
typed-helper execution/readback, F3 verification, dashboard reads, and governed
dashboard writes retain exact capability authority. Unknown releases,
malformed or unstable observations, adapter/identity drift, stale plans, and
unbounded evidence remain fail-closed.

Automation trace list/get reads now have an independent binary-owned Core
contract instead of borrowing automation-configuration authority. A failed
service-inventory probe withholds only service discovery, and multi-call tools
revalidate the same active Core commit before every provider interaction so a
generation retired between calls cannot dispatch again.

State-inventory failure is likewise capability-scoped: independent WebSocket
registry, automation, trace, and dashboard reads remain eligible while
state-dependent routes are withheld. Provider-backed backup and lifecycle
planners now require their exact compiled Core route sets before entering the
application. Background dependency prewarming uses the same single-generation
Core lease discipline and invalidates cached evidence whenever Core authority
changes.

The final mutation boundary revalidates consumed Core authority after durable
intent and immediately before provider invocation. F3 readback and add-on
lifecycle identity reads likewise revalidate before each provider interaction;
a retired generation therefore enters observation-only recovery or fails before
the next call rather than using stale authority.

Core 2026.9 helper and dependency authority is now bound to exact official
template source blobs, including the `states.py` implementation used by the
reviewed globals and State-object semantics. Its generated registry is
byte-deterministic and remains fail-closed for any unlisted Core release.

The current Core 2026.8.1 plus ha-mcp 8.4.3 combination is unchanged: 51
Engineering-native tools, 25 delegated reads, and 76 total client-visible
tools. `ha_get_operation_status` remains the only held read, task schema remains
1, approval authority remains 3, and fallback remains zero. Held-read
`RESOURCE_NOT_FOUND` continues to project as non-retryable
`resource_not_found`.

## Immutable validation

The disposable HA matrix now includes exact Core 2026.9.0 and 2026.9.1 images
paired with exact ha-mcp 8.4.3 source and image identities. It uses a dedicated
non-internal Docker bridge, synthetic credentials and data, bounded waits, and
unconditional cleanup. The exact-image, packaging, architecture, dashboard,
helper, and historical compatibility lanes remain required.

Local Docker was unavailable during source authoring, so disposable runtime
evidence must come from exact-head CI. This candidate does not update Core,
publish or deploy RC2, access a live system, take a backup, create a provider,
add a public schema or write route, or enable fallback.

The rollback source is Engineering 2.2.0-rc.1 at commit
`6340f2024d2c667251ea4b635565a0d5020141bb`. Live deployment, full-backup
verification, Core upgrade, and post-upgrade canaries remain separate
owner-authorized operations.
