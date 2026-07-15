# HA MCP Engineering Server 2.0.0-rc.1 release notes

## Release identity

- Version: `2.0.0-rc.1`
- Beta 26 baseline commit: `b64db57ddffc5108b9078717ce720440f5361412`
- Add-on slug: `hass_mcp_engineering_beta`
- MCP port: `8100`
- administrator-only Ingress approval port: `8110` (not host mapped)
- public schema version: `1`
- catalog: 38 registered tools, 25 canonical tools, zero planned capabilities
- external approval authority version: `2`
- stable production coexistence target: `hass_mcp_admin` v1.1.2 on port `8099`

RC1 is a release freeze and validation milestone. It is not a feature release.
The accepted Beta 26 lifecycle, schemas, enums, Home Assistant behavior, and
governance trust boundary are preserved. The only provider-routing and
direct-access-policy correction is the explicit `search_entities` direct-read
path described below.

## Changes from Beta 26

RC1 changes only release metadata, deterministic RC image provenance, the
`search_entities` release-blocker correction, release compatibility tests, and
release/acceptance documentation.

Deployed acceptance found that `search_entities` returned
`provider_unavailable` immediately while direct Home Assistant reads remained
healthy. The capability was incorrectly classified as
`standard_mcp_preferred` even though the Standard HA MCP gateway is unavailable
and its compatibility handler already implements a read-only state search. RC1
now routes only this capability as `transitional_direct` to `direct_ha_api`
under policy `bounded_entity_state_search`, with no fallback. The handler makes
exactly one `GET /states` request, matches `entity_id` and `friendly_name`
case-insensitively, supports an optional exact domain filter, sorts by
`entity_id`, returns only `entity_id`, `state`, and `friendly_name`, and enforces
the existing runtime limit of 1 through 100. `truncated=true` produces honest
partial completeness when additional matches exist. The public input schema is
unchanged, Standard HA MCP is not claimed as available, no write or service call
is authorized, and the 38/25/0 catalog remains unchanged.

RC1 is installed from a prebuilt multi-architecture GHCR image rather than a
container built locally by Home Assistant. Its app metadata uses the generic
image name `ghcr.io/jeter-1/hass-mcp-engineering-beta`, and the app version
`2.0.0-rc.1` selects the exact version image
`ghcr.io/jeter-1/hass-mcp-engineering-beta:2.0.0-rc.1`. The published manifest
must contain `linux/amd64`, `linux/arm64`, and `linux/arm/v7`, corresponding to
the RC app's declared `amd64`, `aarch64`, and `armv7` architectures. The same
image is also tagged `sha-<commit>` for the exact accepted source commit.

The existing `server_info.server.build_sha` and `build_time` fields are
populated by the controlled release image build:

- `build_sha` is the complete lowercase Git commit ID checked out for the image;
- `build_time` is one bounded UTC RFC3339 timestamp generated once for the
  publishing run and shared by all architecture builds;
- both values are passed as Docker build arguments and immutable container
  environment values, rather than read from runtime repository state;
- `BUILD_VERSION` is `2.0.0-rc.1` for every architecture build;
- OCI source, revision, creation-time, and version labels carry the same
  public, non-secret release metadata;
- an absent, malformed, non-UTC, or unbounded value fails closed to the existing
  `unknown` fallback used for local development.

No token, credential, authenticated URL, branch credential, or secret is part
of build provenance. The public response structure is unchanged. Provenance is
not added to health, capabilities, audit secret fields, or redaction categories.
The production v1.1.2 Dockerfile and runtime are unchanged.

## Controlled publication and installation gate

Merging the RC1 pull request does not publish the immutable RC image and does
not authorize a Home Assistant repository refresh or update. After the accepted
commit is on `main`, create the exact Git tag `v2.0.0-rc.1` on that commit. Only
that controlled release tag may push the version and `sha-<commit>` tags. The
publication workflow must first prove that the Git tag, `config.yaml` version,
and server version are all `2.0.0-rc.1`; any disagreement must fail before
registry login or publication.

The GHCR package must be public because Home Assistant must be able to pull it
without registry credentials. A successful authenticated push or pull is not
evidence of public accessibility. Publication is not complete until a separate,
unauthenticated pull or manifest inspection of
`ghcr.io/jeter-1/hass-mcp-engineering-beta:2.0.0-rc.1` succeeds. If the first
publish creates the package as private and `GITHUB_TOKEN` cannot safely change
its visibility, a package administrator must perform the one-time GitHub UI
change: open the `jeter-1` profile's **Packages** tab, select
`hass-mcp-engineering-beta`, open **Package settings**, and under **Danger
Zone** choose **Change visibility** > **Public**, type the package name, and
confirm. Then rerun or repeat the unauthenticated verification. Do not refresh
the Home Assistant repository until that check passes.

The repository and release candidate must not claim that GHCR publication,
multi-architecture manifest verification, digest verification, or anonymous
pull has passed before the post-merge `v2.0.0-rc.1` workflow actually runs and
the corresponding checks succeed. Follow the complete ordered operator sequence
in [`BETA_DEPLOYMENT.md`](BETA_DEPLOYMENT.md).

If an RC1 correction is reviewed after the original RC pull request merged but
before the exact tag or version image exists, CI may retain `2.0.0-rc.1` only
through the explicit unreleased same-version gate. The gate checks the exact
remote Git tag and inspects the exact GHCR version image using the CI token's
read-only package permission. This authenticated probe prevents a private
existing image from being mistaken for an absent public image. An existing tag
or image, an authentication ambiguity, or a network/registry failure rejects
the correction. The exception is inactive when no beta files changed and
cannot make an older version valid.

## Compatibility freeze

RC1 pins the Beta 26 public contract by exact SHA-256 snapshots of:

- all 38 tool names;
- every complete public input schema;
- every public input enum;
- all canonical and beta-native lifecycle classifications;
- every tool-to-capability provider-routing decision except the single reviewed
  `search_entities` correction; and
- the complete direct-HA exception and read-policy mapping, with only
  `bounded_entity_state_search` added.

The governance compatibility fixture contains an expired plan, active external
challenge, expired challenge, approved plan, consumed approval, applied plan,
rollback-pending plan, rolled-back plan, rejected plan, legacy authority-v1
terminal history, and an active legacy authority-v1 record used to prove
fail-closed execution. RC1 reads the persisted records without migration,
preserves historical hashes and authority versions, leaves terminal records
unchanged, excludes expired challenges from actionable views, and keeps
repeated reads idempotent. Authority-v2 external approval behavior remains the
only executable approval path.

## External approval trust boundary

An MCP caller may create a plan and request review but cannot grant approval.
Only a Home Assistant administrator authenticated through the separate Ingress
listener can approve or reject an exact plan hash. The MCP access secret is not
approval authority. Apply and rollback require separate single-use grants,
principal separation, exact-hash binding, and stale-state checks. Rejection is
terminal. Expired or replaced challenges are unusable. Legacy authority-v1
active records fail closed and must be recreated.

## Upgrade from Beta 26

Only after the exact release tag workflow, three-platform manifest check,
unauthenticated pull, and expected-digest check all pass, refresh the Home
Assistant repository and perform an in-place update of **HA MCP Engineering
Server Beta** only. Keep the existing Beta 26 connector initially: because the
tool catalog and public schemas are identical, connector recreation is not
normally required. Reconnect it and call `server_info` before any other
acceptance test. It must report version `2.0.0-rc.1`, a non-`unknown`
`build_sha` equal to the tagged source commit, and a non-`unknown` valid UTC
RFC3339 `build_time`. Then create a separate fresh RC1 connector for an
independent exact-schema discovery comparison. Remove the temporary fresh
connector after acceptance if it is not needed.

RC1 starts directly against the Beta 26 governance repository. Startup does
not grant, consume, replace, revive, migrate, rehash, or silently upgrade any
approval. Persisted pending and terminal classifications remain authoritative.
An already expired lifecycle state remains idempotent; a newly observed expiry
is persisted once under the existing Beta 26 contract.

## Clean install

An empty governance directory initializes with healthy storage, zero counters,
no plans, no challenges, an empty Ingress review collection, and no audit write
caused solely by initialization or inspection. Configure a new beta/RC-only
`access_secret`; do not reuse the production secret.

## Known limitations

- `server_info` reports `unknown` provenance for local images built without the
  supplied build arguments.
- A newly created GHCR package may initially be private and require the one-time
  manual visibility change documented above before anonymous validation and
  Home Assistant installation can proceed.
- Signed analysis cursors and in-memory snapshots are process-local and become
  stale after an add-on restart.
- The standard Home Assistant MCP transport has no approved exact mapping in
  this release and is not used as a hidden fallback.
- Existing connector metadata can be cached; reconnect it if RC1 identity is
  stale, but do not recreate it unless reconnection fails.
- Apply verification proves exact stored configuration and identity readback,
  not a behavioral observation window.
- There is no notification support, assistant-native elicitation, mobile
  approval action, background monitoring, or result cache.

## Production coexistence and rollback

Production v1.1.2 remains installed independently as `hass_mcp_admin` on port
`8099` and remains running throughout RC publication and testing. Do not update,
stop, restart, replace, disable, or modify it while deploying or accepting RC1.
RC1 continues to use slug
`hass_mcp_engineering_beta`, MCP port `8100`, and internal Ingress port `8110`.

Before updating, retain the accepted Beta 26 add-on image/version reference and
back up the beta-only `/data` governance and audit files according to the normal
Home Assistant backup procedure. If RC1 fails an acceptance criterion, stop
testing, make no new approval decisions, roll back only the beta/RC add-on to
the accepted Beta 26 build, reconnect the existing connector, and verify Beta
26 identity, health, catalog, governance readability, and zero active test
plans. Never use production v1.1.2 as the RC rollback mechanism.

## Acceptance, soak, and stable-release exit

Run the exact post-deployment sequence in
[`RC1_ACCEPTANCE.md`](RC1_ACCEPTANCE.md). The procedure includes the existing
connector, a fresh connector, representative reads, all six Engineering-native
tools, governed apply/rollback/rejection/persistence, security checks, and final
reconciliation.

After acceptance, soak RC1 for at least 48 hours under normal read-only
Engineering use. Inspect `get_server_health` and bounded `get_audit_log` at the
start, after 24 hours, and at the end. Do not leave a test plan or challenge
pending. Stable `2.0.0` may proceed only when:

- every blocking CI job, Docker build, and pinned Home Assistant 2026.7.2
  contract job passes for the final commit;
- the post-merge `v2.0.0-rc.1` publication workflow succeeds, the manifest
  contains amd64, arm64, and arm/v7, and unauthenticated access and the expected
  version-image digest are verified before Home Assistant is refreshed;
- the existing and fresh connectors agree on the exact frozen schema;
- no RC-blocking catalog, routing, governance, security, persistence,
  provenance, or coexistence defect is found;
- final governance health has zero pending challenges, active applies,
  rollback-pending plans, failed applies, storage corruption, audit write
  failures, and prohibited fallbacks;
- the test automation is exactly restored and no active test plan remains;
- production v1.1.2 is confirmed untouched; and
- any defect discovered during the soak is either resolved by the smallest
  reviewed RC-blocking fix with the full gate rerun or explicitly defers the
  stable release.
