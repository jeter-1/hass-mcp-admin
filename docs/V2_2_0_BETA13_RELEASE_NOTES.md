# HA MCP Engineering Server 2.2.0-beta.13 release notes

## Release boundary

Beta 13 is a dependency-security release. It updates only the Engineering
runtime dependency pins:

- `aiohttp` 3.14.2 to 3.14.3;
- `cryptography` 48.0.1 to 50.0.0.

These are stable, non-yanked releases and the lowest tested combination that
resolves CVE-2026-69244, CVE-2026-69247, CVE-2026-69248, and CVE-2026-69249.
Fresh Python 3.12 resolution, `pip check`, and strict `pip-audit` report no
known vulnerability. No advisory suppression, package ignore, prerelease,
development build, Git dependency, or fallback was introduced.

## Compatibility evidence

Engineering uses `aiohttp` for bounded REST, WebSocket, Supervisor-identity,
and signed-registry HTTP requests with context-managed sessions. It uses
`cryptography` for Ed25519 public-key parsing and fail-closed signature
verification. Current product code does not use PKCS#7 decryption, X.509 path
verification or chain construction, Fernet, or certificate parsing.

Python 3.12-compatible glibc wheels are published for amd64, aarch64, and
armv7l for both selected dependencies. Exact local transport, provider, and
signature compatibility tests remain required, followed by protected CI
packaging and multiarchitecture builds.

## Unchanged runtime and security behavior

Beta 13 changes no Engineering tool, public schema, upstream compatibility
entry, protocol, provider route, dashboard argument, backup or lifecycle
operation, governance policy, approval sequence, task schema, dispatch
boundary, held-tool decision, or fallback behavior. It retains:

- 25 canonical and 23 Engineering-native tools;
- task schema 1 and approval authority 3;
- exact reviewed `ha-mcp` 7.14.2 and 8.0.0 release selection;
- the existing `2025-03-26` protocol policy;
- `ha_search` and `ha_get_operation_status` held under 8.0.0;
- zero fallback.

Stable v1.1.2 is untouched.

## Deferred special-provider correction

Beta 13 does not correct the Beta 12 `ha-mcp` 8.0.0 dashboard, backup, or
lifecycle special-provider failures. That independently developed work moves
to Beta 14 after this security release is reviewed and merged. Production
`ha-mcp` 8.0.0 acceptance remains blocked pending Beta 14.

No production Home Assistant, HAOS, Supervisor, add-on, or credential was
accessed while preparing this source release. Deployment and live acceptance
require separate authorization and the
[`V2_2_0_BETA13_ACCEPTANCE.md`](V2_2_0_BETA13_ACCEPTANCE.md) contract.
