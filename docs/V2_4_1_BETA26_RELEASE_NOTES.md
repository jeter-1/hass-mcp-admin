# Engineering 2.4.1-beta.26 release notes

Engineering 2.4.1-beta.26 is a dependency security release (HAMCP-217). It
upgrades `certifi` from 2026.7.22 to **2026.10.19** for GHSA-6q48-6c7w-9gw2
(PYSEC-2026-4320, critical).

`pip-audit --strict` reports this advisory against 2026.7.22, so CI's
`validate_prerequisites` failed on `main` and on every pull request. The fix
was first implemented and independently reviewed inside HAMCP-215 (PR #289).
**Josh decided on 2026-10-20 to ship it separately**, ahead of HAMCP-215, so
that other pull requests can merge. HAMCP-215 will receive it through `main`.

beta.26 follows 2.4.1-beta.25 (HAMCP-212) and includes everything in it. No
Engineering runtime code, tool, input schema, description, provider route,
signed registry, HOLD, support window, workflow or stable-v1 change is
included. The tool catalog is unchanged: 88 tools, or 89 with the optional YAML
reader admitted. Acceptance:
[`V2_4_1_BETA26_ACCEPTANCE.md`](V2_4_1_BETA26_ACCEPTANCE.md).

## Advisory

Earlier certifi versions bundle the `emSign ECC Root CA - G3` root, and eight
eMudhra intermediates under it have compromised keys. An on-path attacker
holding one could present a trusted certificate for any hostname to a client
that trusts the certifi bundle.

## Dependency change

- `certifi` 2026.7.22 → 2026.10.19 in
  `hass_mcp_engineering_beta/requirements.lock`, `tests/requirements.lock` and
  `build-inputs.json`. The wheel is `py3-none-any` and is the same on amd64
  and arm64:
  `sha256:42d35340cc5b9ba78e818ddbff0c5b1c2b779de448a340fd1b989f5fa8670e1b`.
- certifi has no `Requires-Dist`, so no other package changes.
  `requirements.txt` is unchanged, because certifi is transitive.
- Wheel-only pip 25.0.1 reports for linux/amd64 and linux/arm64 on CPython
  3.12.14 both select that single wheel. Every reported wheel is admitted by
  the new runtime lock, and the test lock re-rendered by
  `scripts/prepare_build_inputs.py` equals the committed test lock. Only the
  certifi entry changes in either lock.
- `build-inputs.json` records the version and the new runtime lock digest.

## Upstream delta (2026.07.22...2026.10.19)

- `cacert.pem` loses exactly one root, `emSign ECC Root CA - G3`
  (SHA-256 `86a1ecba…a36bbe6b`), going from 121 roots to 120.
- No root is added.
- The other three emSign roots, removed in 2026.10.18, are restored.
- `certifi/core.py` and the `where()`/`contents()` API are unchanged.

## Where Engineering is affected

- **Affected.** Engineering's httpx-based MCP clients build their verifying
  SSL context from `certifi.where()`. These are the SDK default client used by
  `clients/mcp.py` and `clients/upstream_read.py`, and the dashboard-analysis
  delegate transport. When a configured upstream URL is `https`, a chain to
  the removed root is now refused.
- **Not affected.** aiohttp REST and WebSocket clients, including the
  signed-registry fetch, use the interpreter default (base image) store and do
  not read certifi. The registries are additionally Ed25519
  signature-verified.
- **Not changed.** The base image's own CA store. No base image update is part
  of this release.

## Pinned by

`tests/test_hamcp217_certifi_20261019_contract.py` (11 tests):
- the installed version, and agreement between both locks and the inventory;
- the G3 root is absent and the unaffected emSign roots are present;
- each Engineering httpx path trusts exactly the bundle, with
  `CERT_REQUIRED` and hostname checking;
- a loopback peer whose synthetic root is outside the bundle is refused with
  zero requests, with a positive control;
- aiohttp does not import certifi.

The module fails on 2026.7.22 and on 2026.10.18.

## Preserved

All of the following are unchanged from beta.25:
- the public tool catalog and every descriptor;
- the Core profiles, probe and signed fingerprints;
- the signed ha-mcp registry, the HOLD and the support window;
- storage formats, workflows and pinned runners;
- every dependency except `certifi`;
- stable v1.1.2, which does not pin certifi.

The test-only lock's `urllib3` 2.7.0 findings (PYSEC-2026-4175, -4176 and
-4177) are pre-existing, not in the image, and outside this release under the
2026-10-19 owner decision.

## Compatibility and rollback

- **Stored formats** are unchanged from beta.25.
- **Rollback** is reinstalling the beta.25 artifact. That restores certifi
  2026.7.22 and its advisory.
