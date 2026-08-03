# HA MCP Engineering Server 2.2.0-beta.12 release notes

## Release boundary

Beta 12 is a narrow correction for exact `ha-mcp` 8.0.0 Home Assistant
add-on admission. It adds no Engineering-local tool, provider, write,
protocol, fallback, semver trust, backup-approval policy or garage safety
policy. Stable 1.1.2 and all F1/F2 durable-task and governance behavior are
unchanged.

## Corrected 8.0.0 admission

Beta 11 reviewed the standalone 8.0.0 tool surface, whose descriptors reported
the policy middleware as `standalone`/disabled/not-live. The exact add-on and
production canary reported `addon`/enabled/live. Those three members changed
on all 78 tools; `rules` remained `0` in both artifacts. The v2 model validates
and normalizes the complete four-member dynamic policy state—`deployment`,
`enabled`, `live` and `rules`—rather than only the three members that happened
to differ. Ordinary contract hashes were equal, but the separate raw
full-descriptor runtime hashes differed, so all 24 automatic reads were
quarantined together.

Beta 12 assigns exact 8.0.0 an explicit operational runtime model. It strictly
validates the policy object's field set, JSON types, deployment allowlist and
rule-count bound, then normalizes only the validated environment-dependent
values. Names, schemas, descriptions, annotations, output contracts, titles,
tags, LLM exposure, pinning and every other descriptor field remain exact.
Malformed metadata still quarantines. Exact 7.14.1 and 7.14.2 retain their
legacy raw descriptor model.

The immutable add-on amd64 artifact reproduced the live catalog fingerprint
`c61b0959e766f3900300dd4dd69a6d799fc113186d91983f21be69f1bc6b8768`.
The exact standalone artifact reproduced `0bc81aa7…`. Raw operational and
strict full-contract fingerprints remain visible evidence even where the
operational admission model deliberately normalizes the validated policy
runtime state.

## Diagnostics and accounting

Runtime mismatches now publish distinct ordinary and runtime expected/observed
hashes, the runtime model, bounded changed-field pointers and a sanitized
summary. Catalog health publishes reviewed and observed operational hashes and
model, reviewed and observed strict hashes and model, and bounded field-change
counts. It can no longer explain a runtime mismatch only with identical
ordinary hashes.

Exact 7.14.2 remains 78 advertised, 26 delegated and 74 total with zero
mismatch, quarantine or fallback. Corrected exact 8.0.0 is 78 advertised, 24
delegated, two held and 72 total. The held set remains exactly `ha_search` and
`ha_get_operation_status`. Unknown 8.0.1, 8.1.0 and other unreviewed versions
remain unavailable without inheritance or fallback.

## Evidence and deployment status

The production failure, rollback, root cause, exact artifact evidence,
fingerprint models and remaining canary are recorded in
[`V2_2_0_BETA12_CANARY_FAILURE.md`](V2_2_0_BETA12_CANARY_FAILURE.md). Source
and immutable-artifact investigation used no production access or credentials.

Beta 12 has not passed a post-correction production canary. After separate
merge, publication and deployment authorization, operators must use
[`V2_2_0_BETA12_ACCEPTANCE.md`](V2_2_0_BETA12_ACCEPTANCE.md). Do not deploy or
merge based solely on this source result.
