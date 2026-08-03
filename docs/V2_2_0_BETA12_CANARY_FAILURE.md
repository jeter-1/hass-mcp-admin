# Beta 12 ha-mcp 8.0.0 canary-failure record

## Scope and outcome

The 2026-08-02 Beta 11 production canary correctly failed closed when the live
Home Assistant add-on catalog for exact `ha-mcp` 8.0.0 did not match the
standalone-derived runtime-contract evidence. Production was returned to
`ha-mcp` 7.14.2. This record explains and corrects the source-side evidence and
comparator defect for Engineering `2.2.0-beta.12`; it does not claim a
post-correction production deployment or successful canary.

No production Home Assistant, Engineering server, HAOS SSH endpoint, secret,
or production configuration was accessed during this investigation. Immutable
public artifacts were run or inspected locally with synthetic non-secret
values. Stable add-on 1.1.2 was not changed.

## Live failure and rollback evidence

The live canary selected exact release entry `ha-mcp-v8.0.0-d65630f6`, server
`ha-mcp` 8.0.0 and protocol `2025-03-26`. The reviewed standalone operational
catalog was
`0bc81aa7bd94416385520b9c4c4f7d9ccbc6a49f8f65b8a2a599135463327316`;
the observed live catalog was
`c61b0959e766f3900300dd4dd69a6d799fc113186d91983f21be69f1bc6b8768`.
The retained reviewed strict full-contract fingerprint was
`ff18cda3ca27abc8cca69685fb5240942cbe24a1508f73b9a26e57e1afe44d5a`.

The server advertised and accounted for all 78 tools: 24 automatic reads, two
held reads, 14 mixed/wrapper-required, 32 persistent writes, four
physical/high-risk actions, one prohibited and one unsupported. Nothing was
missing or unreviewed and fallback was zero. Nevertheless, all 24 automatic
reads were quarantined as `runtime_contract_mismatch`, leaving 48
Engineering-local tools, zero delegated reads and
`blocked_incompatible_upstream`. The held set remained exactly `ha_search` and
`ha_get_operation_status`.

A bounded live `ha_get_state` probe, request
`63d2c76690544b349464b8ca5566e16a`, returned `provider_error` with 8.0.0
identity evidence and no fallback. After the separately authorized production
rollback to 7.14.2, the operational fingerprint returned to
`c6bd074d9ee1e832bd90318398c00efd9a9ffd983d5444817bc830208cbfc47c`,
26 reads were readmitted, the total returned to 74, and mismatch and quarantine
counts returned to zero.

## Deterministic reproduction

The protected evidence manifest validated all 27,831 retained entries before
use. The immutable 8.0.0 standalone amd64 artifact reproduced 78 tools,
operational fingerprint `0bc81aa7…` and strict wire-order fingerprint
`ff18cda3…`. The immutable Home Assistant add-on amd64 artifact, run through
its bundled Python and FastMCP entry point with protocol `2025-03-26`,
synthetic tokens, an unreachable loopback Home Assistant URL and documented
non-secret add-on policy settings, reproduced 78 tools and the exact live
`c61b0959…` fingerprint. Its strict wire-order fingerprint is
`f061e48a5d049a2fe84f8b46451a8c2928e0eb5fc68181cf0cbbe71ae5025727`.

Every immutable add-on arm64 layer was verified and extracted. Native arm64
execution was unavailable, so 168 tracked `ha_mcp` source files were compared
with the executed amd64 payload; all were byte-identical, with aggregate
fingerprint
`41da0e613baea315bf9ef0f618108478eb5c60a415a8e898b5260d5e132c4f63`.
The detailed artifact result is retained in
[`ha-mcp-8.0.0-exact-artifact-inspection.json`](evidence/upstream-read-compatibility/ha-mcp-8.0.0-exact-artifact-inspection.json).

## Exact root cause

The reviewed 8.0.0 fixture came from the standalone runtime. Every reviewed
descriptor contained:

```json
{"deployment":"standalone","enabled":false,"live":false,"rules":0}
```

Every exact add-on descriptor and the live reconstruction contained:

```json
{"deployment":"addon","enabled":true,"live":true,"rules":0}
```

These are the only field differences for `ha_get_state`,
`ha_config_get_automation`, `ha_get_history` and `ha_list_services`; the same
three changes occur on all 78 descriptors. Input schemas, descriptions,
annotations, output contracts, titles, tags, LLM exposure and pinning are
unchanged. Machine- and human-readable comparisons are in
[`ha-mcp-8.0.0-live-addon-field-diff.json`](evidence/upstream-read-compatibility/ha-mcp-8.0.0-live-addon-field-diff.json)
and
[`ha-mcp-8.0.0-live-addon-field-diff.md`](evidence/upstream-read-compatibility/ha-mcp-8.0.0-live-addon-field-diff.md).

The ordinary Engineering contract comparator never included these policy
diagnostics, so its expected and observed hashes were equal. The separate
automatic-read runtime comparator originally hashed the complete raw tool
descriptor, while the 8.0.0 registry's expected runtime hash came from the
standalone fixture. Because the shared add-on policy block changed on every
tool, all 24 automatic reads failed together. Health then published only the
ordinary hashes, which is why every failure misleadingly showed two identical
fingerprints.

Exact-image validation of the correction then found two additional consumers
of the same reviewed release contracts. The operational backup provider was a
second missed legacy-v1 consumer, and the operational lifecycle provider was
the final missed legacy-v1 consumer. Both called `schema_fingerprint(tool)`
directly while exact 8.0.0 stored v2 fingerprints. Backup validation failed
closed before planning until its consumer selected the release model;
lifecycle validation then failed closed with `operational_contract_mismatch`
until its consumer did the same. These were comparator-selection defects, not
authorization or dispatch defects: the failures occurred before any backup,
reload or restart dispatch.

The `0bc81aa7…` and `c61b0959…` catalog hashes differ for the same reason: the
operational catalog model hashes the sorted raw descriptors, including the
environment-dependent policy block. This was not a protocol-negotiation,
null/omission/default, stale-7.14.2, strict-versus-operational substitution, or
unknown-release selection error.

## Security classification and correction

The policy block reports an upstream restrictive middleware's runtime state;
it does not grant Engineering authority or create write reachability. The
standalone state with the middleware inactive and the add-on state with it
active are both exact immutable 8.0.0 artifact surfaces. Beta 12 therefore
uses the explicit version-scoped
`ha-mcp-operational-tool-descriptor-v2` admission model only for exact 8.0.0.
It requires the policy object to contain exactly `deployment`, `enabled`,
`live` and `rules`; requires `standalone` or `addon`, real JSON booleans, and a
non-boolean integer rule count from zero through 10,000; then replaces only
those validated dynamic values with a stable model marker before hashing.

Missing, null, empty, extra-field, wrong-type, unknown-deployment and
out-of-range policy values produce a different fingerprint and quarantine.
Every other raw descriptor field remains exact. Exact 7.14.1 and 7.14.2 keep
the legacy full-descriptor model. Raw operational catalog and strict
full-contract fingerprints remain visible evidence and are not admission
aliases. There is no version special case that suppresses comparison.

The automatic-read gateway, operational backup provider and operational
lifecycle provider now all call the shared runtime-contract fingerprint API
with `release.runtime_contract_fingerprint_model` (or the exact selected
release model carried into the comparison). Unknown or unsupported releases
and models remain fail-closed. Dashboard validation intentionally remains on
its separate reviewed dashboard-attestation model; it is not a consumer of
the release runtime fingerprint model. No authorization, accepted argument,
planning, dispatch, verification, reconciliation or fallback behavior changed.

## Observability correction

A quarantine record now distinguishes ordinary
`expected_contract_fingerprint`/`observed_contract_fingerprint` from
`expected_runtime_contract_fingerprint`/`observed_runtime_contract_fingerprint`,
names the runtime model, and reports bounded constant JSON-pointer differences
and a sanitized summary. Catalog health separately reports reviewed and
observed operational fingerprints and model, reviewed and observed strict
fingerprints and strict model, and bounded catalog changed-field counts.
Diagnostic values and raw descriptors are not emitted.

## Hypothesis disposition

- H-1 and H-3 were confirmed: the fixture was standalone-derived and the
  add-on emits documented environment-dependent policy metadata.
- H-5 was confirmed: health displayed ordinary hashes after a separate raw
  runtime hash failed.
- H-4 and H-7 identified the evidence-model defect: expected runtime hashes
  and catalog hashes used raw environment-dependent descriptors without an
  explicit operational normalization boundary.
- H-2, H-6 and H-8 were rejected by exact protocol and field-level evidence.
- H-9 was partly confirmed with a stricter classification: the difference is
  safe diagnostic/security state, not merely presentation metadata.
- H-10 was rejected for the three reviewed values, while malformed or
  structurally unreviewed policy metadata remains security-relevant and
  fail-closed.

## Preserved boundaries and remaining canary

Exact 7.14.2 remains 78/26/74. Corrected exact 8.0.0 is 78 advertised, 24
automatic reads exposed, two held and 72 total. Unknown 8.0.1 and 8.1.0 expose
no delegated reads; no semver inheritance, protocol broadening, fallback,
write, dashboard expansion, backup/lifecycle argument expansion, governance
change, backup-approval change, or garage safety policy is included.

Production remains on `ha-mcp` 7.14.2. Corrected 8.0.0 support is local and
exact-image evidence only and is not production-accepted until a later
authorized controlled canary passes.

After a separately approved merge, publication and deployment, a new
controlled production canary must still confirm exact Engineering version and
build, exact live upstream identity and catalog, 24/2/72 accounting, held-tool
non-registration, zero quarantine and fallback, a representative delegated
read, dashboard constraints, health diagnostics and successful rollback
readiness. Production credentials and production access are not authorized by
this document. The pull request must remain draft and unmerged until the
normal review and release process authorizes otherwise.
