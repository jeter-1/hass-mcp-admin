# HA MCP Engineering Server 2.2.0-beta.13 acceptance

## Source boundary

- required base: `c9ecb953f7373a45015e6e0fb11b172a87175539`
- Engineering version: `2.2.0-beta.13`
- stable source version: `1.1.2` (unchanged)
- release purpose: dependency security only
- Engineering-local tools: 48
- task schema: 1
- approval authority: 3
- protocol: `2025-03-26`
- fallback: 0

Record the exact reviewed PR head and later merge/build identity separately.
Source validation must not access production Home Assistant, Engineering,
HAOS, Supervisor, credentials, or logs.

## Dependency requirements

Require exact direct pins:

- `aiohttp==3.14.3`;
- `cryptography==50.0.0`.

In a newly created Python 3.12 environment, require successful deterministic
installation and `pip check`, then run:

```text
python -m pip_audit --strict --progress-spinner off \
  --requirement hass_mcp_engineering_beta/requirements.txt
```

The command must exit zero with no known vulnerability. Reject any advisory
suppression, package ignore, prerelease, development build, Git or local-source
dependency, yanked artifact, or weakened audit command.

## Required source validation

1. Run dependency and release-metadata tests, the exact-pin/prerelease guard,
   aiohttp transport and MCP client tests, dashboard, backup, lifecycle,
   registry, and Ed25519 positive and fail-closed tests.
2. Run Fast, Full, and Evidence with the exact base and authorized Engineering
   runtime/config paths. Require the complete suite to pass with only
   documented intentional skips.
3. Require protected push and pull-request CI at the exact final head:
   dependency audit, metadata, secret and protected-path checks, stable and
   Engineering packaging, exact immutable image lanes, disposable real-HA
   contract, and amd64, arm64, and arm/v7 validation.
4. Require a clean worktree and prove no stable-v1, provider, dashboard,
   backup, lifecycle, registry, protocol, governance, held-tool, workflow, or
   fallback change entered the release.

## Preserved runtime contract

With `ha-mcp` 7.14.2, retain 48 Engineering-local tools, 26 delegated reads,
74 total tools, exact reviewed admission, zero mismatch, quarantine, missing,
unreviewed, or fallback result, and healthy governance and task storage.

Beta 13 retains the Beta 12 automatic-read admission model for exact 8.0.0 but
does not claim that its dashboard, backup, or lifecycle special providers are
production-ready. Do not update production `ha-mcp` to 8.0.0 during Beta 13
acceptance. The special-provider correction and controlled 8.0.0 canary move
to Beta 14.

## Later deployment — separately authorized

After protected merge and publication, deploy only the Engineering Beta add-on
while upstream remains `ha-mcp` 7.14.2. Verify:

- version `2.2.0-beta.13`, exact merge/build SHA, and `dirty=false`;
- Home Assistant connectivity and valid configuration;
- 25 canonical, 23 Engineering-native, 48 local, 26 delegated, and 74 total
  tools;
- exact `ha-mcp-v7.14.2-7917b2d3` admission and zero fallback;
- healthy governance, task storage, dashboard reads, and bounded inactive
  restart reconciliation.

Stop and roll back to the previously accepted Engineering artifact on an
identity, dependency-startup, transport, signature-verification, storage,
catalog, fallback, or material CPU regression. A rollback to Beta 12 restores
the previous vulnerable dependency set, so investigate and return to the fixed
release promptly rather than treating rollback as a security resolution.

This source pull request remains draft and unmerged until independent review.
This document authorizes no deployment, Home Assistant action, `ha-mcp`
update, backup, restart, or live canary.
