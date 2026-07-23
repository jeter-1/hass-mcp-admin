# ADR-006: Contract-level upstream compatibility admission

Status: accepted for Dev15

## Supersession

This decision supersedes only the exact-version and shared all-or-nothing
admission rules in:

- [ADR-003](ADR-003-REVIEWED-ARGUMENT-CONSTRAINED-DASHBOARD-READS.md);
- [ADR-004](ADR-004-SIGNED-UPSTREAM-CONTRACT-ADMISSION.md); and
- [ADR-005](ADR-005-READONLY-UPSTREAM-GATEWAY.md).

Those records remain the history of the releases that implemented them. Their
fixed tool names, argument constraints, classification boundaries, response
validation, no-fallback rule, and prohibition on generic writes remain in
force.

## Context

The first read gateway used `ha-mcp` 7.14.1 as both reviewed evidence and a
global runtime identity gate. The dashboard provider separately required a
version-specific release attestation. The application then reused dashboard
admission as a prerequisite for the generic read gateway.

That design failed closed, but it made unrelated compatibility decisions
dependent on one version string and one dashboard tool. A release that changed
only its version could remove 26 otherwise exact reads. One dashboard mismatch
could remove every generic read, and stable catalog differences used the same
rapid retry loop intended to recover from upstream boot order.

The committed 7.14.1 review remains valuable. It defines the stock inventory,
the 26 approved automatic-read contracts, the blocked classifications, and the
baseline evidence against which later observations are explained. It does not
need to make a version string executable authority.

## Decision

Engineering admits upstream capability at the smallest independently reviewed
contract boundary.

### Identity and protocol

Server identity and supported MCP protocol remain global prerequisites. A
different server name, malformed discovery response, unsupported protocol, or
transport failure cannot be repaired by a matching tool name or schema.

The observed upstream version is bounded evidence. `version_status` reports it
relative to the reviewed 7.14.1 baseline as:

- `reviewed_exact` when it equals the reviewed evidence version;
- `observed_contract_only` when it differs; or
- `not_observed` before a valid catalog identity is available.

A changed version is not itself a compatibility failure. This applies to
patch, minor, and major changes. Exact contracts from an otherwise valid
`ha-mcp` identity may be admitted even when no version-specific release record
exists.

Health retains the bounded observation separately in
`observed_upstream_server_name`, `observed_upstream_server_version`,
`observed_protocol_version`, and `observed_identity_status`. The status is
`accepted` only when the name, version evidence, and protocol pass their global
checks; rejected or unparseable observations are sanitized and cannot become
compatibility authority.

### Generic automatic reads

Each of the 26 reviewed `automatic_read` entries is evaluated independently.
Admission requires the exact upstream tool name and the complete
dispatch-relevant contract owned by Engineering, including:

- the complete canonical input-schema fingerprint;
- normalized description semantics;
- the reviewed safety-annotation projection;
- output-schema presence and, when present, its complete canonical
  fingerprint; and
- any other bounded top-level semantic metadata included by the reviewed
  contract projection.

Descriptions remain untrusted and are never published as remote model-facing
instructions. Their normalized Unicode/case-insensitive word sequence is part
of generic semantic admission, so a word-level meaning change quarantines that
tool while punctuation or case alone does not. Titles, display grouping, and
other explicitly excluded presentation data cannot authorize a tool.
Engineering continues to publish its own bounded reviewed descriptions and
annotations after admission.

The result for each reviewed read is one of:

- **admitted**: the exact contract is dynamically registered;
- **quarantined**: the name is present but its schema, safety annotations, or
  semantic contract differs;
- **missing**: the reviewed tool is not advertised.

A quarantined or missing read has no route. If a previously admitted read
becomes missing or incompatible after a successful catalog evaluation, its
dynamic registration and capability entry are removed atomically. Other exact
matches remain available.

Before each dispatch, the transport obtains `tools/list` in the same MCP
session that would issue `tools/call`. Engineering rechecks global identity and
protocol, requires the selected target exactly once, and compares that target
with the complete contract bound to the current registration generation.
Missing, duplicate, or changed selected-target evidence stops before
`tools/call` and removes or quarantines only that route.

A valid bounded version movement during this check is evidence, not contract
drift. When the selected target remains exact, the dispatch proceeds once, all
admitted routes remain registered, the observation becomes
`observed_contract_only`, aggregate compatibility becomes `reconciling`,
admission becomes `compatibility_reprobe_pending`, and slow compatibility
reprobe is triggered. This retains the last admitted routes without claiming
the complete catalog was verified at the newly observed version. A
different server name, malformed version, unsupported protocol, stale route
generation, or changed selected-target contract still fails before dispatch.

### New, mixed, action, and write tools

An unlisted tool is unavailable regardless of its advertised name,
annotations, or apparent read-only behavior. A reviewed entry classified as
mixed, persistent write, physical or high-risk action, prohibited, or
unsupported remains unavailable through the generic provider.

New or newly visible tools do not reduce the availability of exact reviewed
matches. They are reported as bounded observed differences, not inferred into
the policy. A signed record, catalog count, version match, or annotation cannot
promote them.

Malformed or duplicate descriptors for names outside the reviewed policy are
also isolated. They are never exposed and are represented only as bounded,
redacted unreviewed anomalies during reconciliation. Because call-time
validation is target-local, such unrelated entries do not block dispatch to a
selected reviewed target whose exact contract is present once. A malformed or
duplicate descriptor for the selected reviewed target does block that target.

The whole-catalog fingerprint is best-effort diagnostic evidence. If
unreviewed data cannot be canonically fingerprinted, the field becomes
unknown; independently verified reviewed-tool decisions remain authoritative.

There is no arbitrary name forwarding, direct Home Assistant fallback,
alternate provider fallback, service execution, or generic write dispatch.

### Dashboard independence

The dashboard provider and generic read gateway evaluate the same upstream
catalog independently:

- a generic-read mismatch cannot disable dashboard inventory or exact
  dashboard configuration evidence when the compiled dashboard contract
  matches;
- a missing or incompatible dashboard tool cannot disable an exact generic
  read; and
- `ha_config_get_dashboard` remains excluded from generic admission because it
  is a mixed-operation upstream tool.

The dashboard provider still constructs only the two fixed non-screenshot
argument shapes and enforces its response and dual-hash contracts. When no
exact-version attestation exists, an exact compiled contract is reported with
admission status `admitted_compatible_contract` and makes no
release-attestation or provenance claim. When the optional signed dashboard
registry is enabled, a currently usable signed registry must also establish
that no exact entry applies before this compatible-family path is available.

An exact-version built-in or verified signed attestation, when present, remains
authoritative. A revoked entry or contract mismatch blocks that exact release;
the provider must not fall back to an older release contract or to the
unattested compatible-contract path. An expired cached remote entry is retained
as deny-only exact-release evidence until a valid higher-sequence registry
supersedes it; it can never authorize compatibility. With the registry enabled,
expiry or unavailability after the hard cache limit blocks compatible-family
admission rather than silently reviving older binary evidence.

The two Engineering dashboard wrappers remain part of the static catalog.
Their registration is not a claim that the independently evaluated provider is
currently available.

### Compatibility state

The generic gateway reports one bounded aggregate compatibility state:

- `exact`: all reviewed automatic-read contracts are admitted at the reviewed
  evidence version;
- `compatible`: all reviewed automatic-read contracts are admitted at a
  different observed version;
- `partial`: at least one reviewed read is admitted and at least one is missing
  or quarantined;
- `incompatible`: a stable catalog was evaluated but no reviewed automatic
  read can be admitted;
- `reconciling`: a selected target matched at changed bounded version
  evidence, but aggregate compatibility at that version is not yet known; or
- `unavailable`: the provider is unconfigured, global identity/protocol
  validation failed, or a catalog cannot currently be obtained.

Unreviewed additions and known blocked tools are counted separately. They do
not change `exact` or `compatible` to `partial` when all 26 reviewed reads
still match.

Health and capability output distinguish at least:

- reviewed and observed versions plus `version_status`;
- `observed_upstream_server_name`, `observed_upstream_server_version`,
  `observed_protocol_version`, and `observed_identity_status`;
- reviewed, exact-matched, exposed, missing, quarantined, and unreviewed
  counts;
- `schema_mismatch_count`, `description_semantics_mismatch_count`,
  `annotation_mismatch_count`, `output_contract_mismatch_count`, and
  `runtime_contract_mismatch_count`;
- bounded quarantine entries containing only tool identity, a stable reason,
  and expected/observed contract fingerprints;
- generic compatibility from dashboard compatibility;
- fast transport `retry_count`, `next_retry_delay_seconds`, and
  `reconciliation_status` separately from
  `compatibility_reprobe_interval_seconds`,
  `last_compatibility_reprobe_at`, `next_compatibility_reprobe_at`, and
  `compatibility_reprobe_status`; and
- zero write and fallback authority.

Each generic delegated-call audit record includes only the bounded,
same-session upstream version evidence and accepted/rejected identity status,
in addition to the reviewed route and argument field names. It never includes
the raw catalog, remote description, schema, endpoint, or credentials.

The accounting for the reviewed automatic-read set is:

```text
exact matched + missing + quarantined = reviewed automatic reads
```

Health never includes raw schemas, remote descriptions, registry bodies,
signatures, endpoint material, credentials, or raw exceptions.

### Recovery and reprobe cadence

Two supervised cadences serve different failure classes:

1. **Fast transport startup recovery** handles upstream boot order and transient
   connection availability using bounded exponential delays. A transient
   endpoint/session-not-ready classification receives at most the 600-second
   full-host-reboot grace before falling to the slow cadence. It re-establishes
   discovery after the fixed endpoint becomes reachable.
2. **Slow compatibility reprobe** handles a successfully observed but stable
   missing or incompatible contract. It avoids a continuous 1-to-30-second
   probe loop for a condition that normally requires an upstream or policy
   change.

Dev15 serializes generic delegated calls behind the same-session
catalog-validation/dispatch barrier and adds bounded paginated `tools/list`
work before every `tools/call`. Candidate acceptance must re-characterize
`ha_search` latency and concurrent delegated-read throughput.

Background discovery and delegated calls may overlap. Engineering fingerprints
only the admission-relevant identity, protocol, version evidence, and sorted
reviewed-read outcomes from the newer same-session observation. An equal token
may prove that the discovery is safe to publish; a different or unknown token
keeps the discovery stale. This concurrency token is never admission authority
and excludes unreviewed descriptor content and whole-catalog diagnostics.
The first stale mismatch may request one immediate retry. Further mismatch is
coalesced into the normal slow cadence until a stable discovery publishes and
resets the bound.

The fast lane retains bounded retry count, next delay, and reconciliation
status. The slow lane reports its fixed interval, last and next timestamps, and
status separately. A safely admitted partial subset remains registered while
waiting for or performing a compatibility reprobe. A successfully evaluated
catalog replaces the prior dynamic subset atomically.

Neither lane retries an upstream tool call semantically. A delegated operation
performs one bounded dispatch and returns its classified result.

## Capability accounting

Dev14 established 41 static Engineering tools: 25 canonical and 16
Engineering-native. The reviewed generic set contains 26 reads.

- All exact reviewed reads: 41 static + 26 delegated = 67 registered tools.
- One missing or quarantined read: 41 static + 25 delegated = 66.
- New or newly visible blocked tools: the registered count does not increase.

Clients that cache `tools/list` must re-list or reconnect after a dynamic
subset changes. This decision does not claim `tools/list_changed` notification
delivery.

## Acceptance examples

Deterministic validation covers:

1. a version-only change retaining all 26 generic reads and compatible
   dashboard operations;
2. one schema, annotation, or semantic change quarantining only its tool;
3. new reads and writes remaining unavailable without harming exact matches;
4. a missing reviewed read being removed while the other reads remain;
5. generic and dashboard admission succeeding or failing independently;
6. an unknown major version with exact contracts being compatible;
7. truthful bounded incompatibility and version state;
8. same-session selected-target revalidation before `tools/call`, with
   missing, duplicate, and changed targets retired independently;
9. valid call-time version movement retaining routes while triggering a slow
   reprobe;
10. unrelated malformed or duplicate unreviewed descriptors remaining blocked
    without preventing an exact reviewed target call; and
11. separate fast transport recovery and slow compatibility reprobe cadence;
    and
12. sustained equivalent calls cannot starve discovery, while repeated
    incompatible observations receive at most one immediate stale retry.

The pinned 7.14.1 exact-image gate remains an immutable regression for the
reviewed source/image and full stock catalog. It is evidence for the baseline,
not a global version gate for later compatible releases.

## Deferred work

Dev15 does not add generic signed-registry authority or release automation.

- **Dev16** may define a signed, data-only evidence and revocation format for
  generic reviewed-read contract families, including cache/expiry behavior,
  rollback and replay protection, revocation, and runtime refresh. The binary
  must continue to own all executable classifications, normalization, routes,
  and bounds.
- **Dev17** may automate immutable source/image resolution, disposable runtime
  extraction, catalog/annotation diffing, semantic fixtures, dashboard
  contract testing, zero-write verification, compatibility reports, and draft
  evidence updates for reviewed releases.

Neither follow-on may activate a new tool, write, action, argument, provider,
or fallback from signed data alone. Those require separately reviewed runtime
and governance changes.
