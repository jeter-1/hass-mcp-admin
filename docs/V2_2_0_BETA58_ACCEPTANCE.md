# Engineering 2.2.0-beta.58 acceptance

Beta 58 is the materialized source candidate for exact ha-mcp 8.4.3 continuity
and the protected ADR-023 release-registry preparation path. It is based on
protected main `8b4eae426d42f49e9bfc39e2d31c09cfcee86c17`. The advertised
Engineering version is 2.2.0-beta.58, stable remains 1.1.2, and
`.release/next-version` was consumed. Materialization has occurred. Beta 57 is
the prior and rollback release.

Materialization did not merge, publish, deploy, restart anything or access a
live system. This document authorizes source review only. The remaining
separately owner-authorized actions are merge, publication, deployment,
production-key creation, environment/secret configuration, registry
publication, trust-anchor activation, and live updates. No production key,
environment, secret, registry, trust anchor, upstream App, or live Home
Assistant system was changed by materialization.

## Beta 57 falsification

Unmodified Beta 57 was exercised through its production readmission and
dashboard selection paths with an ephemeral Ed25519 key and exact 8.4.3
evidence. The compiled 8.4.1 control admitted all 25 delegated reads. After a
simulated in-process 8.4.1 to 8.4.3 update, a valid signed 8.4.3 entry bound to
the existing 8.4.1 profile admitted 24 reads and held only `ha_search`, because
that tool's exact input contract changed. The result retained 51 Engineering
tools, 75 total tools, zero fallback, and zero provider calls during
reconciliation.

The separate exact 8.4.3 dashboard attestation was rejected because Beta 57's
dashboard family selection named only previously compiled release versions.
Therefore registry publication alone could not preserve the full 76-tool and
dashboard contract. A bounded Beta 58 runtime profile and dashboard-family
binding are required. This result proves the behavior of Beta 57 under that
synthetic signed input; it does not claim that a production registry or key
exists.

## Exact 8.4.3 authority

The candidate independently binds the official `homeassistant-ai/ha-mcp`
8.4.3 release to:

- annotated tag object `a4c06d0756f9feca01eda9406f9714bd75cd06a9`;
- source commit `eac7a3aa7063432e9af17e7d7726040e909c7b8f` and tree
  `ffc545fa7e3ad683737454de0217e2b9f672589e`;
- standalone OCI index
  `sha256:d5cea47a0115e5d161c2b319ee637b1b0a5bcfafe1597cb490299bbbc6329456`;
- standalone amd64 and arm64 manifests
  `sha256:56b631cbebc795d6c4f381be72c3ea98055033162acea5e9abe414b2dd213253`
  and
  `sha256:f7bb181a8b244e2ae94669d7b6a42a187f6446d949defbdcec60bc807340dbda`;
- Home Assistant App amd64 index/manifest
  `sha256:366361a088089f3cb55b51318d86eeb968f3deb913a41397d741075da879c2fb`
  and
  `sha256:13ed061af93d539b0872564ea340e4e683b5d437e5a520a3ac5d4e9732442b32`;
- Home Assistant App arm64 index/manifest
  `sha256:90e850213882d6c83dd4b2076546d34873b90e83800eadaccd94558936dd74b3`
  and
  `sha256:a51107e1a1f1f9dec9c52b9a9e714362a3f2374e80613d8c4810e8f54e43c41e`;
- image revision `47582a700b0889a355033ade36de7c3077780b48`;
- MCP protocol `2025-03-26`, 78 tools, and exact catalog fingerprint
  `4b75e198df50a633ab94d51f006961c5fc31a1edfcf524b4dc925a48799e98f7`.

PR #2366 is contained in the exact source commit. The exact image capture was
performed twice and was byte-identical. Seventy-seven tool descriptors match
8.4.1 exactly. `ha_search` remains an automatic read and changes only by
narrowing `config_time_budget` to an inclusive minimum of 0.001. The four
bounded error probes retain the exact 8.4.1 aggregate fingerprint
`03000635a7b0a506c12a6f99ce86433a09683693a0e61d4265b1f11ec52b2d46`.

The candidate therefore admits the exact 25 reviewed reads. With 51
Engineering tools, the client-visible catalog is 76. `ha_get_operation_status`
remains held. App, mixed, action, write, destructive, unknown, and generic
forwarding tools remain unreachable, and fallback remains zero.

## Capability-scoped update continuity

Compiled exact releases retain their complete reviewed catalog order. A signed
future release selects one existing profile only through exact policy resource
and digest bindings; its compatible reads are evaluated independently and do
not inherit release-wide catalog-order authority. Missing, changed, duplicate,
malformed, or unverified reviewed tools are withheld individually. Unknown
additions remain `unsupported` and unreachable.

A synthetic future transition removes the reviewed blueprint getter and adds a
mixed read/write blueprint manager. The getter is withheld, the new manager is
not registered, and the other 24 exact reads remain admitted. Identity,
protocol, incomplete pagination, registry, generation, session, lease, and
same-session pre-call checks remain fail-closed. No semantic retry, alternate
provider, direct-HA route, or fallback is added.

## Signed registry preparation and dashboard separation

The manual `Prepare ha-mcp release-registry update` workflow runs only from
protected main and provides closed `add` and `revoke` operations. For `add`,
one exact stable version tag is observed once to resolve its OCI index digest;
raw-index inspection, architecture extraction, pull, label inspection, runtime
capture, and evidence generation then use only the immutable digest reference.
Fixed official source/image linkage is verified, the exact runtime catalog and
four error probes are captured twice, and the disposable fixture must report
zero mutations. The protected environment holds the Ed25519 seed only for the
signing step. The workflow opens only a draft data PR containing the signed
journal, bounded compatibility evidence, and generated index.

For `revoke`, the workflow requires one exact existing positive signed entry,
does not observe source or OCI state, preserves compatibility evidence, and
produces only the updated signed journal and generated index. The resulting
tombstone is denial-only and prevents re-addition of the revoked version. Both
operations retain the existing permissions and cannot merge, publish an
Engineering image, deploy, restart, mutate Home Assistant, or alter runtime
routes.

The journal supports initial bootstrap, monotonic sequence and digest linkage,
90-day expiry, bounded signed checkpoints, retained denial-only revocations,
strict parsing, and atomic replacement with file and directory fsync. Signed
data can repeat observed contracts and select only one compiled read profile;
it cannot define code, adapters, classifications, argument builders, tools,
routes, writes, actions, or fallback.

Generic release-registry data explicitly quarantines dashboard authority. Exact
8.4.3 dashboard getter/setter authority is separately binary-owned and bound to
the Beta 57 governed provider contract. A future compatible dashboard release
still requires the separate signed dashboard-attestation path. The exact 8.4.3
getter and setter match the reviewed 8.4.1 descriptors and retain planning,
approval, complete F3 preflight, one dispatch, authoritative reread, duplicate
suppression, response-loss recovery, and zero fallback.

## Required validation

Acceptance requires the Beta 57 falsification record; focused 8.4.3,
signed-registry, ADR-023, gateway, catalog, routing, cache, restart, revocation,
session, lease, dashboard, planner, and F3 suites; complete unittest discovery;
Fast Instructions and Validation; protected Full and clean-head Evidence with
both workflow and runtime paths declared; promotion-candidate validation;
compilation and JSON/YAML/PowerShell checks; dependency consistency and strict
audit; secret and whitespace checks; stable-v1 comparison; exact 8.4.1 and
8.4.3 image/App lanes; packaging, architecture, and disposable Home Assistant
lanes; and exact-head CI.

No production private key, GitHub secret or environment, signed production
registry, live option change, Engineering restart, live ha-mcp update, Home
Assistant mutation, publication, or deployment may occur during source
acceptance.
