# HA MCP Engineering Server 2.2.0-beta.14 release notes

## Release boundary

Beta 14 is the special-provider runtime-admission correction deferred from the
Beta 13 dependency-security release. It preserves the Beta 13 pins
`aiohttp==3.14.3` and `cryptography==50.0.0`, strict dependency auditing,
stable v1.1.2, and the exact Beta 12 automatic-read admission boundary. It adds
no Engineering-local tool, upstream release, protocol, provider action,
approval mode, dispatch argument, held-tool promotion, or fallback.

The Beta 12 production canary admitted all 24 reviewed automatic reads for
exact `ha-mcp` 8.0.0 with two held reads and zero fallback. Dashboard list/get
then failed closed because Dashboard v3 fingerprinted deployment-specific
policy values. Backup and lifecycle planning each failed closed because a
stale raw whole-catalog equality gate compared standalone review evidence with
the valid add-on runtime. No plan or execution task was created by those live
failures, and no backup, reload, or restart was dispatched.

## Exact release/model-aware catalog validation

Backup and lifecycle now call one shared full-catalog validator after exact
release and protocol selection. It requires the exact reviewed tool-name set,
rejects duplicate, missing, additional, and unreviewed tools, and validates all
78 policy entries—not only automatic reads. Each entry retains its reviewed
classification, name and description semantics, input schema, security
annotations and policy metadata, output contract, and runtime descriptor under
the exact model declared by the selected release.

The validator derives the versioned
`ha-mcp-reviewed-normalized-catalog-v1` aggregate identity from sorted exact
per-tool validation results. Raw standalone and observed add-on catalog
fingerprints remain bounded diagnostic evidence; raw equality is not an
authoritative gate when the exact release's reviewed runtime model validates
and normalizes deployment policy state. Unknown releases, unsupported models,
wrong protocols, changed tool sets, malformed policy metadata, classification
drift, or component drift remain fail-closed.

The immutable 8.0.0 add-on runtime reproduces raw fingerprint
`c61b0959e766f3900300dd4dd69a6d799fc113186d91983f21be69f1bc6b8768`
and normalized aggregate
`3bad86b86400807ceddf68805cf4ed86d1243f201104e18ed8d3c15e560a1d53`.
The reviewed standalone and add-on descriptors differ in the observed values
of policy `deployment`, `enabled`, and `live`; `rules` remains zero in both
captures, while the reviewed v2 model validates and projects all four dynamic
members.

## Dashboard v3 and typed failures

Dashboard v3 remains its own argument-constrained contract family. Its
security and runtime projections now reuse the exact four-member policy
validator and bounded projection used by the reviewed v2 operational model.
The policy object is not discarded: invalid shape, keys, types, deployment, or
rule bounds alter the fingerprint and fail closed. Name, description,
input/output schemas, annotations, tags, pinning, LLM exposure, and the exact
list/get non-screenshot argument surface remain fingerprinted. The exact
reviewed normalized runtime fingerprint is
`fb7f3789c8c020d8636a96b85a207635e94eefe9e0944c8814de59aba17e532e`.

Recognized typed dashboard errors now survive nested exception groups.
Homogeneous groups retain their category, heterogeneous groups use documented
security/admission-first precedence, and only groups without a safely
recognized category become `internal_error`. Messages and category lists stay
bounded and sanitized; raw descriptors, policy values, and exception content
are not exposed.

Dashboard inventory remains `list_only=true` with screenshots disabled, and
configuration reads remain bound to one exact canonical URL path with
screenshots disabled. Preference writes, rendering, mutation, service calls,
and arbitrary arguments remain prohibited.

## Immutable add-on runtime acceptance

CI has a distinct planning-only exact add-on-runtime job. It pulls the exact
8.0.0 amd64 and arm64 add-on indexes by reviewed immutable digest, verifies
their platform manifests, and executes the real amd64 add-on runtime with
synthetic non-secret settings. It requires protocol `2025-03-26`, 78
advertised tools, the exact raw and normalized catalog identities, 24
automatic reads, and the two held reads. Against a disposable Home Assistant
fixture it exercises dashboard inventory and exact configuration reads plus
governed backup, controlled-reload, add-on-restart, and Home Assistant-restart
planning. It requires four disposable proposal records, zero provider
dispatch, zero fixture mutation, and zero fallback.

Native arm64 and arm/v7 add-on execution is not claimed. Existing architecture
jobs verify their layers, source, configuration, metadata, and build identity;
the immutable add-on runtime is executed natively only on amd64 in CI.
Production behavior is not inferred from source or CI evidence.

## Preserved contracts and acceptance status

Exact 7.14.2 remains 78 advertised tools, 26 delegated reads, and 74 total
tools. Exact 8.0.0 remains 78 advertised, 24 delegated, two held, and 72 total;
the held set is exactly `ha_search` and `ha_get_operation_status`, and neither
is registered or callable. The 48 Engineering-local tools, protocol
`2025-03-26`, dashboard no-write boundary, backup and lifecycle authorization
and arguments, F1/F2 storage, task schema 1, approval authority version 3,
restart reconciliation, unknown-8.x refusal, secure dependency pins, stable
1.1.2, and zero fallback remain unchanged.

Beta 14 has not passed a production canary. A later separately authorized
deployment must first validate Beta 14 against 7.14.2, then perform the
controlled 8.0.0 acceptance in
[`V2_2_0_BETA14_ACCEPTANCE.md`](V2_2_0_BETA14_ACCEPTANCE.md). This source
release does not authorize merge, publication, deployment, an upstream update,
or a live canary.
