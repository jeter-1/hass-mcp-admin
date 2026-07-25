# 2.0.1-rc1-dev2 release notes

Status: unpublished 2.0.1 development candidate

This candidate makes reviewed `ha-mcp` upgrades repeatable without changing
Engineering public schemas, adding upstream writes, or weakening exact
admission. It retains the accepted 2.0.1-rc1-dev1 SDK and dependency boundary.
The production FastMCP bind remains `0.0.0.0`; CVE-2025-66416 remains a
mitigated, deferred configuration risk tracked by issue #62.

## Reviewed upstream releases

The compiled, source-controlled release registry authorizes two exact entries:

| Evidence | ha-mcp 7.14.1 | ha-mcp 7.14.2 |
| --- | --- | --- |
| Release tag | `v7.14.1` | `v7.14.2` |
| Source commit | `255acec1affa6528004a122eb83e30aee9c77713` | `904c14ebbe76de700f7c3535f5cc71c017dca12e` |
| Image index | `sha256:68f386d9becfcc58476f1881a0025f4c6a3ae5874c15cdd61097b14156886292` | `sha256:7917b2d385e16e43f45f92fc72a757e5c0aec8d88b3cd69fe64f3b5106cbfe36` |
| amd64 image | `sha256:604ecd0d7d1aa102aa91c57c697e717a713549e97a2722b767161b1b56f3a9ee` | `sha256:e973144b62fbc873650c8d9c5aaf4d627d9e0a41b88df95178b2293f6d8026c0` |
| arm64 image | `sha256:86de5aa7036c4e7def19b56779e60c817f1cb09e261a42caed413fc2dc09ad43` | `sha256:5aa0814b9efc60c753d2acfed5b0fba37f09efe5aca08a5bb790807212f54c8d` |
| Advertised tools | 78 | 78 |
| Catalog fingerprint | `c6bd074d9ee1e832bd90318398c00efd9a9ffd983d5444817bc830208cbfc47c` | `c6bd074d9ee1e832bd90318398c00efd9a9ffd983d5444817bc830208cbfc47c` |
| Reviewed automatic reads | 26 | 26 |

Exact image capture and normalized comparison classify all 78 tools as
`unchanged_exact`: no input-schema, description, MCP annotation,
output-contract, runtime-contract, policy, delegation, or dashboard contract
change was found. The equal catalog fingerprint is explained by those identical
normalized wire contracts; it is not used by itself to authorize a tool.

Each registry entry includes complete per-tool fingerprints and Engineering
policy. Runtime selection requires exact server identity, version, reviewed
protocol, and release entry. Every read is then admitted independently. A
changed reviewed read is quarantined, a removed read is reported, and a new
tool remains unreviewed and unavailable. Unknown versions fail closed even
when they advertise an identical catalog.

One Engineering image can reconcile atomically between reviewed 7.14.1 and
7.14.2. The old generation is retired as one unit, so no stale route from the
previous version can remain registered. Rolling upstream back to 7.14.1 does
not require rebuilding Engineering.

## Reusable review workflow

`scripts/review_upstream_read_release.py` provides deterministic `capture`,
`normalize`, `fingerprint`, `diff`, `validate`, `report`, and `candidate`
operations. Candidate output is explicitly unapproved. It can prepare evidence
but cannot create policy authority. Approval still requires a human-reviewed
source change containing exact provenance, full per-tool contracts, explicit
classifications, and an exact dashboard decision.

The pinned-image CI gate retains 7.14.1 and adds 7.14.2 by immutable index
digest. Each matrix entry verifies capture reproducibility, exact admission,
normal and error calls, per-tool quarantine, dashboard constraints, outage and
recovery behavior, generic-write absence, and zero fallback.

The standalone 7.14.2 release asset was not used as admission evidence because
it self-reports 7.14.1. The official digest-pinned container reports 7.14.2 and
is the reviewed runtime artifact. Its image revision is
`c435dcb866a617da44e0527e0f4feca3b0612822`; the release tag source commit is
recorded separately above.

## `ha_get_entity` limitation

Both reviewed exact images successfully return a registry entity that exists.
For an existing state-only entity without a registry record and for a
nonexistent entity ID, both versions return `isError=true` with structured code
`SERVICE_CALL_FAILED`. The same code also represents genuine upstream service
failures. The remaining distinction is only free-form English prose.

Engineering therefore does not guess. It returns the existing bounded generic
`provider_error`/`upstream_error` outcome, marks it retryable under the existing
fail-closed provider policy, redacts the source text, counts it as operational,
and keeps fallback at zero. Regression and exact-image tests make this
limitation visible. A future non-retryable not-found normalization requires a
stable upstream code; an upstream issue is recommended.

## Preserved boundaries and rollback

With either exact catalog fully admitted, the runtime remains 41 Engineering
tools plus 26 delegated automatic reads, for 67 total. Generic upstream writes,
mixed tools, arbitrary forwarding, direct Home Assistant fallback, and provider
fallback remain absent. Public schemas, output envelopes, governance, approval,
audit, redaction, dashboard argument constraints, and stable-v1 historical
files are unchanged.

No live upstream, Home Assistant, connector, image publication, tag, release,
or deployment is changed by this candidate.

Rollback is a source rollback to 2.0.1-rc1-dev1, an operator-controlled
upstream rollback to the exact reviewed 7.14.1 image, or an in-place reinstall
of the accepted 2.0.0 Engineering image
`sha256:d91246deab5b50749430f5194b5a9fe1473171526fe4f8551c89b1b3259ff130`.
Preserve `/data`, verify version and selected compatibility entry, wait for
fresh reconciliation, and confirm 26 admitted reads, dashboard status,
governance persistence, audit persistence, and zero fallback. No storage
migration is introduced. Stable v1.1.2 remains operationally retired historical
source and is not a supported rollback target.
