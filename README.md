# HA MCP Engineering Server

HA MCP Engineering Server is a Home Assistant add-on that gives an AI client
one MCP endpoint for inspecting, analyzing and safely changing a Home
Assistant installation. The client connects to that endpoint through the
Home Assistant Cloud (Nabu Casa) path. Reviewed
[ha-mcp](https://github.com/homeassistant-ai/ha-mcp) tools serve as internal
delegated providers behind it, and Engineering-native tools supply deeper
semantics: dependency, reliability and integrity analysis, governed
configuration changes, typed device control, recovery and audit. The client
never needs a second connector or has to choose a provider.

The aim is to make AI use with Home Assistant as capable and safe as
practical. Reads are bounded and redacted. Changes are exact, approved by
the owner, applied once and verified by read-back.

## Current release

The current source version is **2.4.2-beta.6**. It makes never-approvable
prohibited operational plans and stored approval kinds project correctly in
governance inspection and health (HAMCP-304). It also bundles two
development-only fixes (HAMCP-305, HAMCP-312). Read the
[2.4.2-beta.6 release notes](docs/V2_4_2_BETA6_RELEASE_NOTES.md) and the
[2.4.2-beta.6 acceptance contract](docs/V2_4_2_BETA6_ACCEPTANCE.md).

This version's delegated catalog is reviewed against ha-mcp 8.6.0. Its source
baseline is 109 expected connector tools (81 native/static plus 28 automatic
delegated reads), or 110 (81 + 29) with the optional reader, unchanged from
2.4.2-beta.5. Rolling back to 2.4.2-beta.5 is safe.

Every earlier release is listed in the [release index](docs/RELEASES.md).
Published images and notes are on
[GitHub Releases](https://github.com/jeter-1/hass-mcp-admin/releases). A
source version is not proof of what is installed: read health and the
connector catalog on the running system.

## Install or update Engineering

1. Add this repository to the Home Assistant add-on store:
   `https://github.com/jeter-1/hass-mcp-admin`.
2. Install or update **HA MCP Engineering Server Beta**
   (`hass_mcp_engineering_beta`). Update an existing Engineering installation
   in place. Options, persistent data, ports, ingress and the technical Beta
   identity are preserved across updates.
3. Read the [inbound policy](docs/INBOUND_SECURITY.md) and set
   `mcp_allowed_hosts` and `mcp_allowed_origins` for the paths you use. The
   defaults admit only strict loopback authorities, and an explicit list
   replaces them. Invalid options refuse startup, so keep independent
   management access while you change them. Keep access secrets in the
   add-on's private options, never in chat or files.
4. Connect the client to the Engineering Nabu Casa endpoint. MCP listens on
   port `8100`. The authenticated, admin-only **HA MCP Approval** ingress
   panel uses internal port `8110`; it is not a second MCP endpoint.
5. Refresh the client connector and read health before relying on the new
   version.

Engineering requires a supported 64-bit Home Assistant installation
(`linux/amd64` or `linux/arm64`). Images are published at
`ghcr.io/jeter-1/hass-mcp-engineering-beta`. The build pins its base image and
the full runtime wheel closure; see [controlled build inputs](docs/BUILD_INPUTS.md).

Do not install the retired v1 package (`hass_mcp_admin`) or move its data into
Engineering. Its retirement is recorded in
[`hass_mcp_admin/README.md`](hass_mcp_admin/README.md).

## Using it

Ask the client in ordinary language. Typical work:

- **Look things up.** Entities, devices, areas, services, history, logbook,
  traces, logs, recorder settings, dashboards, blueprints and configuration,
  through bounded reads. See the [read coverage matrix](docs/READ_COVERAGE_MATRIX.md)
  and the [feature readiness map](docs/FEATURE_READINESS_MAP.md).
- **Analyze.** What depends on an entity, what a change would affect, why an
  automation is unreliable, and where configuration or dashboards are
  inconsistent. See [dependency analysis](docs/ENTITY_DEPENDENCY_ANALYSIS.md),
  [integrity analysis](docs/CONFIGURATION_INTEGRITY_ANALYSIS.md) and
  [automation reliability](docs/AUTOMATION_RELIABILITY_ANALYSIS.md).
  Coverage is disclosed: a result with no detected references does not prove
  that none exist.
- **Change configuration.** Automations, scripts, scenes, helpers, entity
  metadata, labels and dashboards, through the governed cycle below.
- **Operate one device.** The typed fan and power requests below.

Responses are bounded and paginated. See
[bounded response guidance](docs/TOKEN_EFFICIENCY.md).

## Ordinary fan requests

`control_fan` handles one exact `turn_on`, `turn_off` or `set_percentage`
request for one fan under ordinary authenticated connector authorization. The
assistant generates and reuses the operation ID; no configuration plan or panel
approval is involved. It requires an admitted ha-mcp provider and exact Core
authority, and reports authoritative task state with exact state and
percentage read-back. An uncertain outcome is reconciled read-only, never
blindly retried, and keeps the fan protected until it is resolved.
Verification reflects Home Assistant state, not independent physical feedback.
See [typed fan control](docs/TYPED_FAN_CONTROL.md).

## Ordinary light and switch requests

`control_power` handles one exact ON or OFF request for one light or switch
under ordinary authenticated connector authorization, with the same provider,
Core authority and read-back requirements. Toggle, brightness, color, bulk
targets, other domains and generic service calls are outside this tool. Power
can affect critical loads, groups and other consumers, and a light turned on
may restore integration defaults; these consequences are disclosed, not
hidden. See [typed power control](docs/TYPED_POWER_CONTROL.md).

## Capabilities and compatibility

The source baseline of expected connector tools, native/static plus
automatic delegated reads, is what `python scripts/codex-context.py --format json`
counts; the current figures are under **Current release**. The optional
`ha_config_get_yaml` reader is admitted per exact ha-mcp version and adds one
tool when admitted. Source counts are expectations, not installed proof.

Delegated eligibility is decided per tool and per exact upstream contract;
see `hass_mcp_engineering_beta/ha_mcp_engineering/upstream_tool_policy.py`,
the compiled `hass_mcp_engineering_beta/ha_mcp_engineering/upstream_release_registry.json`
and `hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_yaml_reader.py`.
Admission fails closed, so a running system can expose a smaller compatible
subset; a fresh client catalog establishes what is actually available.

Home Assistant Core compatibility comes from a
[signed Core release registry](docs/CORE_RELEASE_REGISTRY.md). Each signed
entry selects the reviewed capability profiles for one exact Core version. A
new Core version gets no blanket compatibility: until its entry is published,
Core-backed capabilities for it stay unavailable. Do not upgrade Home Assistant
Core before its release is published on the `registry` branch; see the
[owner runbook](docs/CORE_REGISTRY_ONE_TAP_RUNBOOK.md).

## Safety model

- **One endpoint, explicit providers.** Routing to a delegated provider is
  explicit, attributed and admitted per tool. An upstream description or schema
  never admits itself.
- **No fallback.** If a provider is unavailable, the tool is unavailable. There
  is no direct-HA fallback, generic service forwarding or provider switching.
- **Bounded and redacted.** Output is bounded, secrets are redacted, and every
  governed action is attributed in the [audit log](docs/AUDIT_LOG.md).
- **Exact or refused.** An operation is refused when its exact target,
  arguments, authority, provider, current state, concurrency safety or
  verification cannot be established.
- **Consequences are disclosed, not decisive.** Physical and household
  consequences and risk labels are explained to the owner. The owner decides
  whether they are acceptable; see
  [ADR-022](docs/architecture/ADR-022-OWNER-AUTHORITATIVE-PRODUCT-DIRECTION.md).

## Governed changes and evidence

Configuration changes follow one cycle:

1. inspect the current configuration and explain the evidence and effects;
2. prepare an exact, immutable change plan;
3. approve it in the **HA MCP Approval** ingress panel (`approve_change_plan`
   requests approval; it does not grant it);
4. apply it once;
5. inspect the execution task, then read back and verify.

Approval is bound to the exact plan hash and to current evidence. Multi-step
plans stop at the first failure and never roll back automatically.

After a timeout or incomplete response, reconcile the persisted task against
the authoritative target state. Do not repeat a mutation, reuse a failed
approval or change providers. An attempt count alone does not prove dispatch.

`inspect_governance_record` explains why one exact plan or task is stuck. An
administrator can acknowledge a stuck record or revoke its acknowledgement,
through the Ingress **Stuck records** page only. Acknowledgement is a
supplementary warning ledger; it never rewrites, deletes, approves or
re-applies the underlying plan or task, and revoking it only restores the
warning.

- [Change governance](docs/CHANGE_GOVERNANCE.md)
- [External approval](docs/EXTERNAL_APPROVAL.md)
- [Execution recovery](docs/GOVERNED_EXECUTION_RECOVERY.md)
- [Audit records](docs/AUDIT_LOG.md)

## Recovery and rollback

Rollback is reinstalling the previous release; each release's notes name its
predecessor and any storage limits. A binary downgrade alone is not storage
recovery. When a newer release has written records an older one cannot read,
follow that release's recovery contract and the
[downgrade versus backup restore](docs/runbooks/DOWNGRADE-VERSUS-BACKUP-RESTORE.md)
runbook. Other runbooks are in [`docs/runbooks/`](docs/runbooks/), including
[post-update recovery verification](docs/runbooks/POST-UPDATE-RECOVERY-VERIFICATION.md)
and [governance and task persistence recovery](docs/runbooks/RECOVER-GOVERNANCE-AND-TASK-PERSISTENCE.md).

## Development and release

Follow [docs/CODEX_WORKFLOW.md](docs/CODEX_WORKFLOW.md), the repository
[`AGENTS.md`](AGENTS.md) and the nested instruction files it names. Resolve the
exact release context before work:

```sh
python scripts/codex-context.py --format json
```

A release is materialized in its own pull request through
`scripts/promote_next_release.py`; the temporary declaration is consumed in
that PR. Evidence, CI, publication, deployment and installed acceptance are
separate steps with separate authorization. A release PR updates the
**Current release** section above and adds one row to
[`docs/RELEASES.md`](docs/RELEASES.md); it does not add release narration here.

## Historical references

- [Release index](docs/RELEASES.md): every release's notes and acceptance
  contract, newest first.
- [Documentation map](docs/README.md).
- [Published releases](https://github.com/jeter-1/hass-mcp-admin/releases).
- [Architecture history](V2_BETA_ARCHITECTURE.md).
- [This README before consolidation](https://github.com/jeter-1/hass-mcp-admin/blob/34b8538ba704ded6032f13600ec5b185590100eb/README.md),
  with the per-release narration through 2.4.1-beta.63.

Published artifacts and historical evidence are immutable. Historical counts,
pairings and installation guidance describe their own release only.
