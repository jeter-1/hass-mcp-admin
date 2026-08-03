# HA MCP Engineering Server 2.2.0-beta.11 acceptance

## Source and release boundary

- source base: `97f4848dbccc44ec0e0c1bc4f0edbe6aa5491449`
- Engineering version: `2.2.0-beta.11`
- stable source version: `1.1.2` (unchanged and operationally retired)
- Engineering-local tools: 48 (25 canonical plus 23 Engineering-native)
- task schema: 1
- approval authority: 3
- fallback: 0

The exact reviewed PR head and later merge/build SHA must be recorded before
publication or deployment. Source validation must not access Home Assistant.

## Required source validation

1. Validate metadata, compilation, YAML, dependencies, strict dependency
   audit, secret scanning, protected paths, whitespace, compatibility registry
   and deterministic evidence regeneration.
2. Run restart-reconciliation, governance, durable-task, provider, dashboard,
   backup and lifecycle suites with a controllable clock.
3. Run full unit discovery twice buffered and once verbose, then the repository
   Full and Evidence tiers.
4. Validate source fixtures and immutable exact images for 7.14.2 and 8.0.0
   using MCP initialization and `tools/list` only. Record unavailable container
   lanes explicitly; do not alter Docker permissions.
5. Require a clean worktree, unchanged stable source and no workflow,
   protocol, provider, tool or fallback expansion.

## Restart-reconciliation assertions

Require an expired durable restart task and an expired historical taskless
plan to terminalize without Core, Supervisor or provider requests. Missing
dispatch timing must fail closed. Startup must preserve deadline, attempt count,
next attempt and backoff. Unchanged evidence must follow capped increasing
backoff and never schedule beyond the deadline. Duplicate workers must not
overlap; one malformed record must not block other records; batches and
timeouts must remain bounded; terminalization must be idempotent.

Simulate several hours with a controllable clock and prove a bounded probe
count and no fixed sub-minute loop. Reconciliation must never dispatch or
redispatch a restart. Health must show the exact active plan/task and clear both
identifiers after completion, with timing and cumulative avoidance/failure
evidence intact.

## Exact 7.14.2 assertions

Require source commit `904c14ebbe76de700f7c3535f5cc71c017dca12e`,
the committed immutable artifact identities, operational fingerprint
`c6bd074d9ee1e832bd90318398c00efd9a9ffd983d5444817bc830208cbfc47c`,
78 advertised/accounted tools, 26 admitted reads, 74 total tools and zero
missing, unreviewed, contract mismatch or fallback. Dashboard, backup,
lifecycle, governance and durable-task regressions must remain green.

## Exact 8.0.0 assertions

Require source commit `9dd3ac620e3149cd34ec3c990b6ee81e778191f2`,
the committed immutable standalone and add-on artifact identities, operational
fingerprint
`0bc81aa7bd94416385520b9c4c4f7d9ccbc6a49f8f65b8a2a599135463327316`,
and strict fingerprint
`ff18cda3ca27abc8cca69685fb5240942cbe24a1508f73b9a26e57e1afe44d5a`.

Require exactly 78 accounted tools: 24 automatic reads, 14 mixed/wrapper,
32 persistent writes, four physical/high-risk actions, one prohibited, one
unsupported and two held. Expose exactly 24 delegated reads and 72 total
tools. The held set must be exactly `ha_search` and
`ha_get_operation_status`; neither may be registered or callable, and neither
is missing or unreviewed. Catalog and tool-level mismatches fail closed with
bounded observability. Exact 8.0.0 must use the existing Engineering-permitted
protocol path.

## Unknown-release refusal

An unknown later 8.x release must not select the 8.0.0 profile, expose any
delegated read or write, retry indefinitely, or use fallback. Health must
report an exact-release unreviewed condition without implying partial trust.

## Provider constraints

Dashboard validation permits only reviewed list inventory and exact get by
canonical `url_path`; it refuses fuzzy selection, `view_path`, screenshot and
preference-write paths. Backup remains exact full-backup creation with bounded
arguments and no restore/delete/download. Lifecycle remains controlled reload,
exact add-on restart and Home Assistant restart with existing approval,
identity, readiness and no-blind-redispatch constraints. Static tests must not
be reported as live acceptance.

## Exact-head CI and independent review

Fresh push and PR workflows must be successful at the exact draft-PR head.
Independent review must focus on absolute restart deadlines, cheap eligibility,
persisted backoff, single flight, no redispatch, active health identity, exact
release selection, complete tool accounting, held-tool non-reachability,
fingerprint-model separation, artifact provenance, provider constraints,
unknown-8.x refusal, historical governance compatibility and zero fallback.
The PR remains draft and unmerged until separately approved.

## Post-deployment canary handoff (not authorized by this document)

After a separate merge, publication and deployment authorization, begin with
read-only server identity, capabilities, health, configuration and upstream
admission checks. Confirm version/build/dirty identity and the expected 74-tool
7.14.2 or 72-tool 8.0.0 result before any governed operation.

The canary must separately observe production architecture and running add-on
digest; held `ha_search` and `ha_get_operation_status`; dashboard output and
not-found behavior; Supervisor backup response/progress; controlled reload;
add-on identity/readiness; Home Assistant restart outage/recovery; connector
reconnection; no-blind-redispatch through a real outage; and bounded stale
restart behavior after deployment. Stop on any unexpected probe cadence,
deadline extension, active-identity mismatch, fallback, catalog mismatch,
unreviewed release, missing manifest or storage/governance failure. The held
tools remain unavailable regardless of canary results until a later reviewed
release explicitly admits them.
