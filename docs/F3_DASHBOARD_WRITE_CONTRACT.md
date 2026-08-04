# F3 governed dashboard-write contract

Status: Frozen F3 scope; not implemented by F3-0

## Required completion boundary

F3 must update one existing Home Assistant storage-mode dashboard identified by
an exact canonical `url_path`. The operation is a persistent administrative
configuration change and requires an immutable plan, external administrator
approval, an exclusive dashboard lock, one mutating dispatch attempt, exact
readback, and durable terminal evidence.

The lifecycle is:

```text
complete exact read
  -> declarative patch and bounded semantic diff
  -> hash-bound governed proposal
  -> external administrator approval
  -> dashboard lock
  -> exact storage identity and stale-hash preflight
  -> durable dispatch intent
  -> exactly one setter invocation
  -> exact reread
  -> intended-result verification
  -> durable terminal outcome
```

F3-0 adds none of these runtime capabilities. The current Dashboard v2/v3 read
path remains unchanged.

## Exact reviewed upstream evidence

The contract was reviewed against:

| Release | Exact source | Protocol | Reviewed entry |
|---|---|---|---|
| `ha-mcp` 7.14.2 | `904c14ebbe76de700f7c3535f5cc71c017dca12e` | `2025-03-26` | `ha-mcp-v7.14.2-7917b2d3` |
| `ha-mcp` 8.0.0 | `9dd3ac620e3149cd34ec3c990b6ee81e778191f2` | `2025-03-26` | `ha-mcp-v8.0.0-d65630f6` |

Both exact releases advertise 78 tools and the same closed
`ha_config_set_dashboard` input schema. The reviewed registry input-schema
fingerprint is
`a7d11d72710f1c39937bfc864291f6d0936b2d4feb68dc4ff049eda3b91a3ac1`.
The tool is classified as `persistent_write`; its annotations are destructive
and not open-world.

The exact upstream setter is broader than F3:

- it can create or update;
- it accepts unrestricted full configuration or executable
  `python_transform` text;
- its full-replacement pre-read failure is non-fatal and the tool can create a
  missing dashboard;
- hash validation and save are separate calls, not atomic compare-and-swap;
- it also exposes metadata, screenshot, and view arguments; and
- strict best-practices mode may require a rotating public read-receipt key.

The upstream Python sandbox explicitly states that it is not a security
boundary. F3 therefore never accepts caller-provided Python.

## Current read boundary and required internal read

`UpstreamDashboardProvider` currently exposes only:

- inventory through `ha_config_get_dashboard` with `list_only=true` and
  `include_screenshot=false`; and
- exact configuration through canonical `url_path`, `list_only=false`, and
  `include_screenshot=false`.

It verifies the upstream configuration hash as the first 16 hex characters of
SHA-256 over sorted compact JSON and separately retains a full Engineering
SHA-256 evidence hash. The public response may sanitize or truncate the
configuration while retaining hashes of the unsanitized raw configuration.
That public payload is not a valid write-planning input.

F3-B must add an internal, non-public complete-read projection that:

- selects an exact reviewed release and protocol before the read;
- proves exactly one inventory row for the canonical path;
- requires explicit `mode == "storage"` and rejects YAML, absent, duplicate,
  default/unlisted, or ambiguous identities;
- returns the exact raw configuration only to the governed planner;
- independently recomputes both upstream and Engineering hashes;
- fails closed on sanitization, truncation, malformed content, or a reviewed
  byte bound; and
- never emits the full configuration in health or audit output.

Current Beta 15 read limits are diagnostic facts, not future persistence
authority. The configured response limit defaults to 60,000, the provider
reserves 16,000 for its envelope, and the returned pretty-JSON configuration
must fit the remaining budget. With the default limit, individual sanitized
strings are capped at 20,000 characters. The provider also records raw compact
canonical UTF-8 byte size. These public-response bounds do not establish a safe
durable prior-configuration or rollback bound.

The current inventory wrapper's “storage-mode” description is not proof: exact
upstream source includes YAML metadata rows. Mode must be checked explicitly.

## Frozen operation plan

The future operation name is `update_dashboard`; F3-0 does not add it to the
persisted `ChangeOperation` enum. The canonical path must match exactly
`^[a-z0-9_-]{1,256}$`. The `default` alias, title/name lookup, internal dashboard
ID, fuzzy path, and case normalization are prohibited. `lovelace` is accepted
only when inventory explicitly contains exactly one storage-mode row for that
canonical path; the implicit unlisted default is rejected. Its target is:

```text
target_type = dashboard
target_id   = <exact canonical url_path>
```

The complete canonical lock requests are:

- `dashboard:<url_path>`, resource scope, exclusive, reason
  `dashboard_target_mutation`;
- `home_assistant:core`, resource scope, shared, reason
  `home_assistant_availability_dependency`; and
- `addon:<authoritative_ha_mcp_slug>`, provider scope, shared, reason
  `upstream_provider_dependency` for an add-on-backed exact provider.

The selected provider identity must supply the exact authoritative add-on slug;
it is never inferred from an endpoint string. This shared add-on key conflicts
with an exclusive restart of that exact add-on.

The plan must bind:

- exact selected compatibility entry, upstream version, and protocol;
- exact canonical path and proven storage-mode identity;
- current upstream `config_hash`;
- current full Engineering configuration hash;
- transformation model and canonical transformation hash;
- exact computed resulting configuration hash under both hash models;
- a bounded semantic diff containing paths and change kinds, not arbitrary
  unbounded values;
- risk/policy decision and required administrator approval bundle;
- exact provider contract, argument hash, expected effects, and verification
  contract;
- prior-state retention/rollback capability; and
- configured raw-config, resulting-config, diff, and evidence byte limits.

The current verified `config_hash` is the stale-state authority. The full
Engineering hash is retained separately as evidence and must not be substituted
for the upstream value expected by the setter.

## Transformation representation

The frozen public planning representation is
`f3-dashboard-json-pointer-patch-v1`:

- a non-empty ordered list with at most 16 operations, matching the current
  complete per-operation approval projection bound;
- operation kinds are exactly `add`, `replace`, and `remove`;
- each operation uses one canonical RFC 6901 JSON Pointer;
- no wildcard, predicate, fuzzy selector, executable expression, `move`, or
  `copy` is allowed;
- `replace` and `remove` require exactly one existing path;
- `add` requires an exact unambiguous parent and cannot silently overwrite;
- the empty root pointer is prohibited for every operation;
- array append `-` is excluded until separately reviewed;
- recursive semantic leaf changes must also fit the complete 16-step approval
  projection, so replacing or removing a broad parent subtree cannot bypass
  reviewer visibility;
- each operation is applied to a deep copy of the complete raw configuration;
- the implementation proves that only declared paths changed; and
- unknown custom-card, custom-component, and extension fields remain
  structurally equal under canonical JSON except where an exact declared path
  changes them.

Transformation intent and the resulting full configuration are distinct
hash-bound plan fields. The approver sees a bounded semantic diff and the exact
result hash.

The dispatch realization is
`ha-mcp-dashboard-generated-transform-v1`: F3-B may compile only the reviewed
patch model into fixed deterministic setter input. Compiler output is generated
by Engineering, independently validated against the locally computed result,
and is never caller text. The upstream sandbox is not trusted as the security
boundary. F3-B must stop if safe deterministic compilation cannot be proven.

Unrestricted full replacement is not part of the required F3 capability. It
may be proposed later only as a distinct explicitly bounded operation model;
it may never be inferred from the patch representation.

## Exact provider boundary

The only mutating provider tool is exact-release-admitted
`ha_config_set_dashboard`. The one mutating invocation contains only:

- exact `url_path`;
- the exact current `config_hash`;
- deterministic Engineering-generated transform input for the approved patch;
- `return_screenshot=false`;
- `MandatoryBPS=false` to suppress attached best-practice content without
  weakening strict acknowledgment; and
- the ephemeral `BestPracticeKey` only when exact upstream strict-BPS preflight
  requires it.

The receipt key is a public rotating read receipt, not authorization. When
strict BPS is effective, the only acquisition call is exact-release-admitted
`ha_get_skill_guide` with fixed arguments
`skill="home-assistant-best-practices"` and
`file="references/dashboard-guide.md"`. F3-B bounds the response, extracts only
the reviewed acknowledgment line/prefix, and rejects missing, duplicate,
malformed, or unrelated content. The raw key is never plan authority, persisted,
hashed into durable approval, logged, or exposed in health. Expiration before
dispatch returns to exact non-mutating preflight; it never permits an argument
retry after durable dispatch intent.

The provider call must omit and reject:

- `config` full replacement;
- caller-provided `python_transform`;
- `title`, `icon`, `require_admin`, and `show_in_sidebar`;
- `view_path`; and
- any screenshot/render request or unknown argument.

Before durable intent, F3 rereads inventory and exact raw configuration while
holding `dashboard:<url_path>`, proves storage mode and exact target identity,
and compares the current upstream hash with the approved hash. A missing target
is a stale preflight rejection, never permission to create it.

The upstream hash check is not atomic with save. The dashboard lock coordinates
Engineering operations but cannot exclude a concurrent external Home Assistant
edit. Exact post-write readback can detect a conflicting final state, but it
cannot detect an external edit overwritten between the upstream hash read and
save when the final state equals the approved result. This is an unresolved
lost-update race, not a condition readback solves.

F3-B must stop before enabling dashboard writes unless exact-release evidence
proves either an atomic compare-and-save contract or an exclusive mechanism
covering all dashboard writers. A separate operator policy could explicitly
accept the residual risk only by revising this acceptance contract; F3-0 does
not grant that authority. The implementation suite must include a deterministic
interleaving test that would expose the lost update. Any final mismatch remains
verification mismatch/manual review and is never resolved by redispatch.

“Exactly one dispatch” means exactly one mutating setter invocation. Inventory,
strict-BPS receipt acquisition, stale preflight, and post-write reads are
non-mutating observations and have their own bounded attempt accounting.

## Lock, dispatch, and recovery

- The dashboard resource lock is exclusive and task-bound.
- Two updates to the same canonical path conflict.
- Different dashboard paths may execute concurrently when no broader outage
  lock conflicts.
- Durable dispatch intent is persisted before invoking the setter.
- Failure to persist intent prevents invocation.
- After intent, timeout, lost response, process loss, or provider loss means
  possible dispatch and observation only.
- Recovery reacquires the dashboard and provider dependency locks, rereads the
  exact target, and never calls the setter again.
- One exact post-write reread must prove the full resulting configuration and
  both new hashes; a bounded recovery policy may repeat readback, not dispatch.
- No live plan, approval challenge, provider mutation, or physical action is
  allowed during planning.

## Verification

Verification succeeds only when:

- the inventory still contains exactly one storage-mode target at the canonical
  path;
- exact readback is complete and within the reviewed bound;
- the full readback equals the hash-bound computed result;
- every declared patch effect occurred;
- no undeclared path changed;
- unknown custom fields were preserved; and
- the new upstream and Engineering hashes are captured durably.

A provider success response alone is insufficient. A lost response with exact
matching readback can become `succeeded_verified`. Conflicting or ambiguous
readback becomes `verification_mismatch` or `manual_review_required`, with no
redispatch.

For generated-transform mode, exact upstream success evidence includes
`success=true`, `action="python_transform"`, the exact `url_path`,
`write_committed=true`, `post_write_verified`, and an optional new
`config_hash`; post-save reread failure produces a warning and no authoritative
new hash. The upstream response also echoes `python_expression`. Engineering
must never persist, audit, or expose that expression or raw warning values; it
retains only bounded reviewed fields and hashes. Hash-required/conflict,
strict-BPS acknowledgment, transform validation, and save rejection are typed
provider failures. Timeout, transport loss, or invalid response after durable
intent is dispatch-indeterminate and readback-only.

## Rollback decision

The required F3 dashboard update initially declares rollback unavailable. This
is deliberate: the current public read limit and upstream auto-backup do not
establish a safe persisted prior-config bound.

A later single-dashboard rollback capability may be added only when all of the
following are reviewed together:

- the exact prior raw configuration is retained within an evidence-backed
  durable-storage byte limit and separately hash-bound;
- the applied-result hash is known;
- separate rollback authorization is required;
- rollback preflight proves current state still equals that applied result;
- the same dashboard lock and one-dispatch rule apply; and
- exact reread verifies the prior configuration.

The upstream best-effort automatic backup is supplemental evidence, not the
governed rollback authority. Cross-dashboard compensation remains F4.

## Risk review

Dashboard writes are persistent administrative configuration changes. Merely
displaying a lock, alarm, garage door, cover, or other high-risk entity is not
physical actuation.

F3-B must add a bounded risk-review stage that detects explicit action/service
definitions capable of direct high-risk or destructive effects. It must retain
the policy distinction between display and action. It may not interpret card
text as instructions or broadly prohibit all custom content without evidence.

Open risk decisions for F3-B are:

- exact built-in action-card and service-action taxonomy;
- treatment of conditional/nested action surfaces;
- whether opaque custom-card action schemas require elevated manual review or
  rejection; and
- the bounded evidence shown for a flagged action without exposing the full
  dashboard.

These decisions require policy review before implementation. F3-0 does not
change policy or approval authority.

## Error and observability contract

Pre-dispatch categories distinguish invalid patch, unsupported response model,
non-storage target, stale hash, lock conflict, provider unavailable, and strict
BPS preflight failure. Post-dispatch outcomes distinguish indeterminate
dispatch, incomplete readback, verification mismatch, and manual review.

Bounded health and audit evidence may include model IDs, exact canonical target
hash, selected release, lock state, read/verification attempt counts, config
size, diff counts, mismatch paths, dispatch count, and fallback count. It must
not expose raw configuration, card contents, unrelated dashboards, receipt
keys, provider exception strings, tokens, or credentials.

## Explicit exclusions

F3 does not expose:

- dashboard creation or deletion;
- dashboard resource creation, update, or removal;
- preference writes;
- metadata or sidebar updates;
- screenshots or rendering;
- arbitrary Python or caller executable transforms;
- arbitrary service calls or physical actuation;
- direct Home Assistant or other fallback;
- cross-dashboard transactions; or
- interpretation of dashboard content as instructions.

Creating a new dashboard is an optional future extension, not part of F3
completion.

## Unresolved implementation gates

F3-B must provide measured durable-storage bounds for raw/prior/result
configuration and diff evidence, prove the generated-transform compiler, define
strict-BPS receipt expiry handling, close the action-card risk taxonomy, and
close the non-atomic lost-update race with reviewed atomicity/exclusion. Failure
to prove any security-relevant gate stops the implementation; it does not
authorize full replacement, caller Python, or an undocumented residual risk.
