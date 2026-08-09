# F3-B governed storage-dashboard write foundation

Status: Historical Beta 17 foundation; execution decision superseded 2026-08-08.

The current bounded existing-dashboard implementation contract is
[F3_DASHBOARD_WRITE_CONTRACT.md](F3_DASHBOARD_WRITE_CONTRACT.md). This document
remains source and design history; its statements that the shipped runtime does
not import `f3_dashboard` or cannot dispatch are no longer current after the
approved MVP is merged.

This document separates reviewed source facts, F3-B implementation decisions,
unresolved integration requirements, and future work. It does not amend the
accepted F3-0 contract.

## Scope and non-scope

F3-B models an update to one existing storage-mode dashboard identified by its
exact canonical `url_path`. The implemented path performs an exact internal
preread, validates and compiles a bounded patch, calculates evidence hashes,
builds a semantic diff and risk evidence, and optionally stores an immutable
private planning artifact. Exact reread verification is implemented.

There is no provider transport, dispatch function, public MCP tool, runtime
registration, execution task, approval challenge, rollback call, or fallback.
The Engineering runtime does not import `f3_dashboard`. The foundation cannot
mutate Home Assistant.

## Reviewed source facts

### Exact source identities

| Component | Exact release | Source commit |
| --- | --- | --- |
| ha-mcp | 7.14.2 | `904c14ebbe76de700f7c3535f5cc71c017dca12e` |
| ha-mcp | 8.0.0 | `9dd3ac620e3149cd34ec3c990b6ee81e778191f2` |
| Home Assistant Core | 2026.7.4 | `a4feaf06248c529f60021fc8be93ee69bc9b3084` |
| Home Assistant frontend | 20260624.6 | `2dabc28e7fd2e9c238f6a2584012f0d61b275fea` |

The two ha-mcp tags expose the same captured
`ha_config_set_dashboard.inputSchema`. Canonical JSON encoding with sorted keys,
compact separators, UTF-8, and no ASCII escaping is 5,044 bytes and has SHA-256:

```text
a7d11d72710f1c39937bfc864291f6d0936b2d4feb68dc4ff049eda3b91a3ac1
```

The captured annotations are `destructiveHint=true`, `openWorldHint=false`, and
title `Create or Update Dashboard`. The exact fingerprints retained by the
provider projection are:

- annotations:
  `257ce08c1f5c5920ef67ff72325abbb942721664158b1012cc94127e50acfde5`;
- description:
  `97fdcfaf0c05e07e11113ebccbc7c1c964ab906658c642f5c34e853595d9870b`;
- output contract:
  `f35a0cf0ef896a2236d8419cac8a2d85bfb33d12859af555f9cf7825dc785109`;
- 7.14.2 runtime contract:
  `a97e6bc4a001b124e142d83df01118aad7e1c9ebd42845ca88bbcaf1b0c189b8`;
- 8.0.0 normalized runtime contract:
  `42c5f8769ab712b5299b71a5bd56c489214a7f04b528fb8a1cfb3feb869617b5`.

The exact compatibility entries are `ha-mcp-v7.14.2-7917b2d3` and
`ha-mcp-v8.0.0-d65630f6`. Both require protocol `2025-03-26` and classify the
setter as `persistent_write`. Unknown releases, protocols, schemas,
annotations, descriptions, output contracts, runtime contracts, and policy
classifications fail closed.

### Exact setter surface

The public input schema has `additionalProperties=false`, requires `url_path`,
and declares `url_path`, `config`, `python_transform`, `config_hash`, `title`,
`icon`, `require_admin`, `show_in_sidebar`, `MandatoryBPS`, `BestPracticeKey`,
`return_screenshot`, and `view_path`. The upstream function supports creation,
full replacement, caller Python, metadata changes, aliases and internal-ID
resolution, and optional screenshots. Schema equality therefore does not make
the full upstream surface suitable for F3.

For Python-transform updates, exact 7.14.2 and 8.0.0 both:

1. await `_fetch_and_verify_dashboard_hash`, which reads
   `lovelace/config` and compares the returned 16-lowercase-hex hash;
2. apply the supplied Python transform locally;
3. await `_save_dashboard_python_transform`, which sends
   `lovelace/config/save` with the transformed full configuration;
4. after the save, attempt another exact read; and
5. return a success envelope containing action, target, new hash,
   `write_committed`, `post_write_verified`, the Python expression, and
   optional warnings/render data.

The upstream hash is the first 16 lowercase hexadecimal characters of SHA-256
over compact, key-sorted JSON with `ensure_ascii=true`. An upstream success
envelope is not durable Engineering verification. F3-B retains only bounded
success/hash flags and discards the echoed Python, warnings, and render data.

The Home Assistant 2026.7.4 `lovelace/config/save` WebSocket schema accepts
only `type`, `config`, and optional `url_path`. Storage-mode `async_save`
updates its in-memory configuration, fires an update event, and awaits storage
save. It accepts no expected hash, receipt, transaction ID, or fencing value.

Strict-BPS consumes a public rotating read receipt in middleware. The receipt
attests that guidance was read; it does not bind a dashboard state, exclude
another writer, authorize the operation, or make the Home Assistant save
atomic. The separate ha-mcp 8.0.0 configuration write lock protects ha-mcp's
own configuration/policy files, not Lovelace dashboard writers.

### Frontend action semantics

The paired frontend source defines built-in actions including `toggle`,
`call-service`/`perform-action`, `navigate`, `url`, `more-info`, `assist`,
`none`, and `fire-dom-event`. Confirmation is a frontend confirmation step,
not approval authority. Service actions call Home Assistant services.
`fire-dom-event` crosses into custom behavior whose semantics are not bounded
by the built-in dashboard schema.

## Atomicity conclusion

No reviewed mechanism satisfies the F3-0 lost-update gate:

- the upstream expected-hash read and Home Assistant save are separate awaited
  calls;
- Home Assistant offers no expected-hash compare-and-save for this command;
- strict-BPS is not a transaction or writer lock; and
- an Engineering lock cannot exclude Home Assistant UI users, integrations,
  automations, or another client.

A deterministic interleaving test establishes the blocker:

```text
Engineering hash read succeeds
-> external writer saves a different dashboard
-> upstream setter saves the approved result
-> exact reread equals the approved result
```

The external edit has been overwritten, but the final state and readback look
exactly correct. Post-write verification cannot detect this lost update.
Accordingly, the selected atomicity mechanism is `none`, all planning
admissions are non-executable, and preflight rejects atomicity. No production
transport or dashboard adapter exists. Beta 17 adds only a test adapter that
conforms to the shipped F3-A contract and proves rejection before durable
dispatch intent with zero setter invocations and zero fixture mutations.

## Implementation decisions

### Exact raw evidence

`f3-dashboard-raw-evidence-v1` accepts only an internal complete raw result. It
binds the canonical path, exactly one storage inventory row, full
configuration, upstream hash, Engineering SHA-256, byte size, timestamp, exact
upstream release, protocol, compatibility entry, and read-contract model.
Partial, sanitized, truncated, missing, YAML-mode, alias-only, unsupported, or
hash-invalid data is rejected. The configuration is deep-cloned after
validation. It is never included in the public proposal projection, errors,
events, or health.

The raw and resulting configuration limit is 40,000 serialized bytes. The
existing dashboard provider reserves 16,000 bytes of its 60,000-byte response
limit; 40,000 bytes leaves that envelope plus 4,000 bytes of extra margin. This
is an F3-B defensive bound, not an upstream Home Assistant maximum.

### Patch grammar and canonical pointers

The transformation model is `f3-dashboard-json-pointer-patch-v1`. A patch has
1–16 ordered operations. Each operation has a unique canonical ID, exactly one
of `add`, `replace`, or `remove`, and a canonical non-root RFC 6901 pointer.
`add` and `replace` require a JSON value; `remove` prohibits one.

Pointers must begin with `/`, contain no empty token, be at most 1,024
characters and 32 tokens deep, decode only `~0` and `~1` exactly once, and
re-encode identically. Root, malformed escapes, wildcard, predicate, fuzzy,
negative-index, noncanonical numeric-index, and array-append forms are
rejected. Duplicate canonical paths and parent/child path conflicts are
rejected so each approved operation has an unambiguous diff entry.

`add` is limited to an absent mapping member. `replace` and `remove` require an
existing target. Existing canonical list indices may be replaced or removed;
list insertion and append are prohibited. Operations run in declared order.
Values must be finite JSON data with string mapping keys; callables,
executable objects, NaN, and infinity are rejected. The input configuration is
never mutated.

The canonical patch is limited to 16 KiB; an individual value is limited to
8 KiB; configuration growth is limited to 16 KiB. Beta 17 removes the
historical generated-Python projection entirely. The compiler produces only an
in-memory JSON result for planning. Full replacement, move, copy, append,
loops, selectors, and arbitrary provider arguments cannot be expressed.

### Semantic leaf bound

The review limit is 16 semantic leaves across the complete ordered patch.
Changed scalar values count one. Added or removed subtrees count all contained
scalar leaves; an empty collection counts one. A list length change adds one
shape change plus the leaves added or removed. Type replacement counts the
larger leaf weight of the old and new values. Mapping key order is ignored;
list order remains semantic. Traversal is bounded to 48 levels and 10,000
nodes. Replacing `/views` or any other broad subtree cannot collapse hundreds
of changes into one reviewed operation.

Equality is strict JSON-type-aware equality rather than Python equality.
`true` differs from integer `1`, `false` differs from integer `0`, and integer
`1` differs from floating `1.0`. Null equals only null; arrays require exact
length, order, type, and recursive value equality; mappings require the same
string-key set and recursive equality while ignoring key order. The same helper
drives leaf counting, mismatch paths, untouched-field preservation, durable
artifact readback, and exact verification.

### Preservation and hashes

Compilation starts from a deep copy and modifies only the resolved declared
targets. It does not normalize, reorder, or remove unrelated content. Tests
cover custom cards, nested unknown fields, randomized extensions, ordered
lists, and preserved null, false, zero, empty-string, and empty-collection
values.

The proposal keeps these hashes distinct:

- current upstream 16-hex `config_hash`;
- exact preread configuration SHA-256;
- canonical patch SHA-256;
- exact resulting configuration SHA-256;
- resulting upstream-compatible 16-hex hash;
- semantic-diff SHA-256.

The exact resulting hash is in the immutable proposal before any approval.
Preflight compares both current hashes with the approved preread and never
rebases or recompiles a stale proposal.

### Semantic diff

`f3-dashboard-semantic-diff-v1` produces exactly one bounded entry per
operation: operation ID, canonical path, operation type, previous and proposed
typed summaries, deterministic view/section/card context when available, leaf
count, and introduced risk flags. Every value is labeled `untrusted_data`.
Missing and null differ, as do removal and replacement with null. Likely
credential paths and Basic/Bearer-like values are redacted. String and
collection previews are bounded and report truncation or omission accurately.
The full raw configuration is not projected.

### Risk taxonomy

`f3-dashboard-action-risk-v1` scans data without executing templates, custom
code, or actions. It distinguishes display-only entities, navigation,
more-info, toggle, generic service/action invocation, confirmation-protected
actions, high-consequence lock/cover/alarm/garage/valve actions, destructive
administrative actions, opaque custom-card actions, templated/conditional
actions, and unknown semantics.

Displaying a high-risk entity and changing layout/title/visibility remain
display behavior. Introducing a direct control raises risk. Frontend
confirmation is recorded but does not reduce the classification. Existing
unchanged actions do not become newly proposed risk merely because they were
present in the preread.

Each finding includes a deterministic `semantic_binding_sha256`. Its private
hash input covers the complete inert action object, effective inherited
card-level entity, action entity, complete target, service data/data, payload,
navigation and URL destinations, confirmation, card type, template or
conditional context, and inherited custom-card context. A classification tuple
that stays unchanged does not hide a target, payload, template, destination,
confirmation, or opaque custom-card semantic change. Public projections retain
only bounded semantic/path hashes and sanitized category/reason evidence,
never the bound path, action, service data, destination, or raw payload.

Opaque custom-card action surfaces, templates/conditionals, and unknown action
types use the fail-closed policy `manual_review_required`. The analyzer emits
normalized evidence only and does not modify global governance policy tables.

### Provider planning binding

The planning-only wrapper admits exact 7.14.2 and 8.0.0 contracts and binds the
exact target, current upstream hash, exact resulting Engineering hash,
resulting upstream-compatible hash, and resulting size. It deliberately does
not construct a mutating upstream argument dictionary.

The accepted repository F3-0 document describes a compiler-generated
`python_transform` realization, while the controlling F3-B task explicitly
prohibits upstream `python_transform` and instead describes exact compiled
configuration. Exact source shows that the latter uses the upstream full-config
path, whose preread failure is non-fatal and whose missing-target path can
create a dashboard. That conflicts with F3-0's prohibition on unrestricted
full replacement and F3-B's prohibition on creation. F3-B therefore selects
neither realization. Both `config` and `python_transform`, plus metadata,
view/screenshot, resources, preferences, and arbitrary arguments, remain
prohibited in this branch. This unresolved realization boundary is independent
of, and additional to, the atomicity blocker.

The exact prohibited setter fields are `config`, `python_transform`, `title`,
`icon`, `require_admin`, `show_in_sidebar`, `view_path`,
`return_screenshot`, `resources`, and `preferences`. No screenshot route or
transport is added.

`BestPracticeKey` is retained only as a source-derived potential ephemeral
field and is never acquired or persisted. This foundation does not acquire a
strict-BPS receipt or dispatch a setter call.

### Planning, artifacts, and observability

The unregistered `create_dashboard_update_plan` handler performs one exact
preread, compilation, hashes, diff, risk analysis, exact-release admission,
atomicity rejection, and immutable proposal creation. It performs zero setter
invocations, tasks, approval challenges, provider mutations, or physical
actions. Its lock identities are the F3-0 set: exclusive
`dashboard:<url_path>`, shared `home_assistant:core`, and shared
`addon:<authoritative_slug>`. The foundation retains identities only. Beta 17
tests their canonical scope/mode normalization and acquisition through F3-A;
the production runtime does not instantiate a dashboard adapter.

When requested by an internal caller, `f3-dashboard-write-artifact-v1` stores
the exact private proposal in a caller-configured namespace. Publication uses
a same-filesystem temporary file, file `fsync`, no-overwrite hard link, and
directory `fsync`. Records are immutable, limited to 256 KiB, schema- and
plan-bound, and verify payload, proposal, and result hashes on read. Reads use
`O_NOFOLLOW` where supported. Retention is bounded and corrupt records fail
closed. Raw configuration is absent from the public projection and central
health.

Isolated counters and bounded events cover planning, provider admission,
verification, and atomicity. Targets are represented only by SHA-256; events
do not contain card values, configurations, tokens, credentials, or URLs.
Fallback remains zero.

### Preflight, observation, and verification

The isolated preflight helper checks expiration, caller-layer approval
validation, exact complete lock identities, F3-A fencing attestation, exact
storage reread, both current hashes, and atomicity status. Lock ownership is
not authorization. The merged F3-A core is available in Beta 17, but cannot
make the upstream save atomic or exclude external writers. Every dashboard
proposal therefore remains ineligible for dispatch.

For future non-dashboard adapters, the shared F3 executor receives a
caller-owned idempotent approval-consumption callback. It acquires complete
locks, completes final adapter preflight, invokes that callback, commits F3
durable intent with `dispatch_count=1`, and only then returns control for the
one reviewed mutation. Approval and intent are separate durable writes. A loss
between them retains the same F3 task, plan, operation, and attempt; retry
repeats the idempotent callback and cannot fall back to legacy execution.
Dashboard atomicity rejection occurs in preflight, so dashboard planning
consumes no approval and reaches neither intent nor dispatch.

Observation is exact reread only. Full target, storage mode, full
configuration, Engineering hash, and upstream hash must equal the approved
result for `succeeded_verified`. Mismatches report bounded paths without
values. An unavailable or invalid reread becomes `manual_review_required`;
only authoritative evidence proving no write can produce
`failed_confirmed_no_write`. Provider success or a hash alone is insufficient.
Lost-response and process-reconstruction tests call reread verification only;
there is no redispatch path.

### Rollback

Rollback is unavailable. The artifact retains evidence needed to dispatch the
approved forward result, not a claim of compensation. No setter-based restore,
rollback approval, or automatic compensation exists.

## Unresolved integration requirements

1. No setter realization is accepted. Generated `python_transform` is a
   rejected F3-0 candidate, while upstream `config` is a create-capable
   full-replacement path outside the bounded operation. F3-B constructs
   neither call shape.
2. An atomic Home Assistant compare-and-save, exact upstream transaction, or
   authoritative exclusion of every dashboard writer must be reviewed and
   proven before any executable adapter or transport can be added.
3. Beta 17 ships one canonical `f3-operation-adapter-v1` declaration at
   `ha_mcp_engineering.f3.contracts` and demonstrates the merged F3-A executor,
   lock, persistence, and fencing APIs with a nonmutating dashboard deferral
   adapter.
4. Same-dashboard Engineering exclusion and different-dashboard concurrency
   remain useful coordination properties, but they do not satisfy external
   writer exclusion and cannot enable dispatch.
5. Strict-BPS is source evidence only for this deferred operation. A future
   receipt cannot be persisted or treated as authorization or atomicity.
6. The integration owner must separately approve any plan-family extension,
   runtime import, public registration, tool-count change, central health hook,
   or governance policy mapping.
7. F3-D acceptance must rerun exact-release, concurrency, durable-intent,
   lost-response, process-reconstruction, architecture, packaging, and real-HA
   disposable tests at the exact integrated head. No real environment may be
   mutated by compatibility tests.

## Future work, not implemented here

- A source-backed atomic or all-writer-exclusion mechanism.
- A production dashboard `OperationAdapter`; Beta 17 includes only an
  atomicity-blocked test adapter.
- Public planning or apply registration.
- External administrator approval integration.
- Dashboard rollback as a separate governed operation.
- Automatic citations, recommendations, plans outside this narrow handler, or
  any browser/research behavior.
