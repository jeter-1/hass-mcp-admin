# HAMCP-196: Engineering add-on permissions review (read-only)

Status: read-only report. This document changes no runtime code, add-on
manifest, test behavior, workflow, release declaration, or stable-v1 file.
It records evidence and recommendations; the removal decisions belong to Josh.

Origin: independent Antigravity review finding S-2 (register row HAMCP-196).

## Revisions examined

| Item | Value |
| --- | --- |
| Base (`origin/main`) | `22370ef4687ed500af2f76bb15d66797cf219812` |
| Engineering version (`hass_mcp_engineering_beta/config.yaml:2`) | `2.4.1-beta.15` |
| HAMCP-162 YAML reader (PR #267 head, read-only ref) | `bfcbcf1342f63fb5321bb500a7c8d11f0f7d4bc3` (descends from `2b6e8cb`) |
| Supervisor role source (vendored, hash-pinned) | Supervisor 2026.09.1, commit `40e3ee7640a3c44abe67f1a1397f39c3cd949806`, `supervisor/api/middleware/security.py`, in `tests/fixtures/supervisor_core_logs_2026_09_1/` |

No live Home Assistant, Supervisor, deployed MCP endpoint, or production
credential was accessed. Statements about Supervisor or frontend behavior that
are not backed by the vendored exact-version source are marked
**unverified (memory)** and must be checked against exact-version upstream
source under ADR-021 before they are relied on.

## Summary

| Grant | Current use | Recommendation |
| --- | --- | --- |
| `map: [homeassistant_config:ro]` | **In use.** Blueprint source YAML is read from `/homeassistant/blueprints/...` by three production paths | **Keep for now.** Removal is possible only after a separately scoped runtime change moves blueprint source reads off the mount (owner decision D1) |
| `hassio_api: true` | `GET http://supervisor/core/logs` for `get_core_log_history` only | **Keep** (owner-approved 2026-09-24), or remove together with the role and drop `get_core_log_history` (D2) |
| `hassio_role: homeassistant` | Same single endpoint | **Keep.** It is the narrowest role that admits `/core/logs`; no narrower role exists (D2) |
| `homeassistant_api: true` | All Core REST/WebSocket access through `http://supervisor/core` | **Keep.** Essential |
| `ingress: true`, `ingress_port: 8110`, `panel_admin: true` | Admin-only approval panel | **Keep.** Already minimal |
| `ports: 8100/tcp: 8100` | Client-facing MCP listener | **Keep.** Whether a host mapping is required is not evidenced in-repo (optional D3) |
| `init: false` | Process model (no s6) | **Keep.** Not a privilege |
| `auth_api`, `privileged`, `devices`, `full_access`, `host_network`, `docker_api`, `apparmor`, other `map` entries | Not granted | No change. `auth_api` absence is enforced by `scripts/validate_addon_metadata.py:220` |

The S-2 premise that YAML reads make the mount unnecessary is correct for the
HAMCP-162 YAML reader, but incomplete for the add-on as a whole. Blueprint
source is still read from the filesystem.

## 1. Filesystem use of the Home Assistant configuration mount

### 1.1 Hits under the mount

All hits are blueprint source reads. Only `<base>/{automation|script}/<path>.yaml`
under `/homeassistant/blueprints` (or the `/config/blueprints` fallback) is
opened.

| Location | What it reads | Reachable in production | API alternative |
| --- | --- | --- | --- |
| `hass_mcp_engineering_beta/ha_mcp_engineering/tools/compatibility.py:579-625` (`BLUEPRINT_BASES`, `_blueprint_base`, native `get_blueprint`) | Raw blueprint YAML for `automation` or `script`. It uses realpath containment and accepts only `.yaml`/`.yml`. If neither base exists it returns "Blueprint directory not mounted ... map: [homeassistant_config:ro]" (`:610-614`) | Yes. Registered `evidence`/`native` (`capabilities.py:29`), routed `BLUEPRINT_SOURCE` (`providers/routing.py:134`), direct-HA exception (`routing.py:274`) with policy `bounded_blueprint_source_read`: "requires bounded read-only access to the mounted blueprint source" (`routing.py:311-316`) | Partial: delegated `ha_get_blueprint` (see 1.3) |
| `hass_mcp_engineering_beta/ha_mcp_engineering/dependency/provider.py:987-1021` (`_read_blueprint_with_status`) | Blueprint YAML for each automation's `use_blueprint.path` (automation domain), size-capped, custom `!input` SafeLoader | Yes. Called for every `use_blueprint` automation during the dependency index scan (`dependency/provider.py:452-460`). The index feeds `entity_dependency_analysis`, `change_impact_analysis`, helper-dependency governance and startup prewarm | Partial: delegated `ha_get_blueprint` |
| `hass_mcp_engineering_beta/ha_mcp_engineering/reliability/provider.py:14,228` (imports `_read_blueprint`) | Same as the previous row | Yes (`automation_reliability_analysis`) | Partial: delegated `ha_get_blueprint` |

Core-independence classification that depends on the mount:
`ha_core_readmission/routes.py:214-218` classifies `get_blueprint` as
`CORE_INDEPENDENT_MOUNTED_SOURCE` ("Reads bounded blueprint YAML from the
read-only mounted homeassistant_config directory; it makes no Core API call").
This is also recorded in `docs/HAMCP_164_CORE_GATE_FAIL_CLOSED_DESIGN.md:101`.

### 1.2 Searched and not found (production code, excluding tests)

- No reads of `configuration.yaml`, `secrets.yaml`, `.storage`,
  `automations.yaml`, `scripts.yaml`, `scenes.yaml`, `ui-lovelace`/`lovelace`
  files, `custom_components`, `/share`, `/ssl`, `/media`, `/backup`, or
  `/addon_configs`. The only `.storage` matches are Python relative imports.
- The only literal filesystem `/homeassistant` and `/config` paths are the two
  blueprint base lists above. Every other `/config...` string is a Core REST
  path or WebSocket command type.
- The only `yaml.load` on file content is `dependency/provider.py:1011` (the
  blueprint read).
- No configuration-directory environment variable, such as `CONFIG_DIR`,
  `HA_CONFIG` or `HASS_CONFIG`, is read.
- The Dockerfile has no entrypoint, run script, s6 tree, `VOLUME`, or
  config-path reference. `CMD` is `python -u -m ha_mcp_engineering`.
- Other filesystem reads are all outside the mount:
  - add-on `/data` options, audit, governance, registry caches, and topology probe;
  - bundled package JSON under `/app/ha_mcp_engineering`;
  - installed `jinja2` hashing;
  - build-time inputs;
  - repository `docs/evidence` reads reachable only from review/test utilities
    (`docs/` is not copied into the image).

Where each data class comes from today:

| Data | Source |
| --- | --- |
| Automations | Core REST `GET /api/states` and `GET /api/config/automation/config/{id}` |
| Scripts | Upstream `ha_config_get_script` for the dependency index; Core REST `/api/config/script/config` for governance |
| Dashboards | Upstream `ha_config_get_dashboard`, WebSocket `config/entity_registry/list`, `lovelace/*` WebSocket |
| Blueprint list | WebSocket `blueprint/list` (`tools/compatibility.py:596`) |
| Blueprint source | **Filesystem (mount)**, as in 1.1 |
| Automation baseline | WebSocket `auth/current_user`, `config_entries/get`, device and entity registry lists (`audit_baseline/capture_contracts.py:19-24`) |
| Backup and lifecycle | Delegated ha-mcp tools and Core REST; no filesystem |

### 1.3 Delegated blueprint path (`ha_get_blueprint`)

A separate public tool, `ha_get_blueprint`, maps to upstream ha-mcp
`ha_manage_blueprints` (`action=list|get`) through a compiled adapter
(`providers/upstream_blueprint.py:1-110`; contract in
`docs/HA_MCP_8_5_0_COMPATIBILITY.md:55-69`). It does not touch the Engineering
filesystem. It is not a drop-in replacement for the mount:

- It requires a configured and admitted upstream ha-mcp release (adapter
  bindings exist for 8.5.0 and 8.6.0 only, `upstream_blueprint.py:13-19`).
- Complete installed content is proven only when `config` is present and
  `yaml_source` is `component`, `file` or `tools_entry`. Otherwise the result is
  metadata-only or of unproven provenance (`upstream_blueprint.py:96-110`).
- It consumes Core authority (`core.basic_websocket_read`,
  `ha_core_readmission/routes.py:88`). The native read is Core-independent.
- The dependency index and reliability analysis do not call it today.

Home Assistant Core's `blueprint/list` returns path, metadata and inputs, not
full source. In-repo evidence shows no other Core API that returns installed
blueprint YAML. Confirming that against exact-version Core source is part of D1.

### 1.4 HAMCP-162 YAML reader (PR #267)

- The YAML reader does **not** use the mount.
  - `providers/upstream_yaml_reader.py` (new) imports no filesystem APIs (`:25-54`).
  - It reads `configuration.yaml` only through upstream `ha_config_get_yaml`
    (`:56-59`, `upstream_arguments()` `:288-295`), dispatched via the upstream
    read gateway.
  - `secrets.yaml`, `packages/` and every other file are refused before any
    upstream call (`:100-101`, `:221`).
- The only new path code on the branch is a path-equality comparison in
  `upstream_tool_policy.py:1544`. It reads nothing.
- PR #267 leaves the blueprint mount reads unchanged.
- PR #267's only `config.yaml` change is the version, which moves to
  `2.4.1-beta.16`.

### 1.5 Exposure the mount creates

The process can read the entire Home Assistant configuration directory
read-only. That includes `secrets.yaml` and `.storage/`, which hold
authentication and integration state. Code reads only the blueprint subtree,
behind realpath/`resolve()` containment checks:
- `tools/compatibility.py:615-617`
- `dependency/provider.py:997-999`

The exposure is therefore a blast-radius concern: a compromised process or
dependency could read these files. It is not a reachable read path in current
code.

The `/config/blueprints` fallback does not match the current manifest:
`homeassistant_config` mounts at `/homeassistant` **(unverified, memory)**. It
would only become meaningful if a different map type were added. This is
low-severity dead code, noted for D1.

## 2. Other grants

### 2.1 Supervisor role map (exact vendored source)

From `tests/fixtures/supervisor_core_logs_2026_09_1/_V1_PATTERNS.txt` and
`token_validation.txt`:

| Rule | Paths |
| --- | --- |
| `api_bypass` (any add-on token, with no `hassio_api` needed) | `/addons/self/*` (except `security` and `update`), `/addons/self/options/config`, `/info`, `/services*`, `/discovery*`, `/auth` |
| `default` | `/.+/info` |
| `homeassistant` | `/.+/info`, `/core/.+`, `/homeassistant/.+` |
| `backup` | `/.+/info`, `/backups.*` |
| `manager` | broad: add-ons, host, supervisor, os, store, network, backups, core, and more |
| `admin` | everything |
| `no_security_check` | `/core/api/.*`, `/core/websocket`, `/homeassistant/api/.*`, `/homeassistant/websocket`, ingress, and more (permission enforced by the Core proxy) |
| `BLACKLIST` | includes `/core/api/hassio(/\|_).*`. So `/core/logs` cannot be reached through the Core proxy |

### 2.2 Per-grant evidence

| Grant | Used by | Endpoints | Narrower option | Evidence |
| --- | --- | --- | --- | --- |
| `homeassistant_api: true` | `configuration.py:69-81,187-188` (`http://supervisor/core`, `ws://supervisor/core/websocket`); `clients/rest.py`, `clients/websocket.py` and every Core read and governed write | `/core/api/*`, `/core/websocket` | None. All Core functionality depends on it | Code. The proxy-side permission check (`supervisor/api/proxy.py`) is not vendored: **unverified (memory)** |
| `hassio_api: true` | `providers/core_logs.py:26` (literal `http://supervisor/core/logs`, one GET, with `Range`, no redirects or retries) for `get_core_log_history` | GET `/core/logs` | Remove it only by dropping `get_core_log_history` (`get_error_log` remains). `/addons/self/info` (`providers/supervisor_self.py`) uses `api_bypass` and needs no `hassio_api` | Vendored role map; `docs/V2_1_1_BETA2_RELEASE_NOTES.md:20` |
| `hassio_role: homeassistant` | Same | `/core/logs` matches `/core/.+` | None. `default`/`backup` do not match; `manager`/`admin` are broader | Vendored role map; `tests/test_core_log_supervisor_contract.py:120-127,155-176` |
| `ingress`, `ingress_port: 8110`, `panel_admin` | `application.py:491-495`; `approval_web.py:54-55,365-399` (peer `172.30.32.2` and `X-Ingress-Path` required, otherwise 403) | Approval panel only; 8110 is not host-mapped | Already minimal | `docs/EXTERNAL_APPROVAL.md:60-73`; `scripts/validate_addon_metadata.py:212-221` |
| `ports: 8100/tcp` | `inbound_topology_observer.py:550-553` (`MCP_PORT`, `proxy_headers=False`) | MCP streamable HTTP (secret path plus Host/Origin policy) | Unknown: the repo does not document whether the Nabu Casa relay needs the host mapping | `README.md`; `docs/BETA_DEPLOYMENT.md:537`; validator `:134-144,205-211` |
| `init: false` | Dockerfile `CMD`; uvicorn owns signals | None | Not a privilege | Dockerfile |

Latent authority of `hassio_role: homeassistant`: the role also admits
`/core/restart`, `/core/stop`, `/core/update`, `/core/options`,
`/homeassistant/.+`, and every `/.+/info` path. No Engineering code calls any of
these. Restart, reload, add-on restart and backup are delegated MCP calls to
the separate ha-mcp add-on:
- `providers/operational_lifecycle.py`
- `providers/operational_backup.py`

Josh approved this trade-off on 2026-09-24 with that latent authority
disclosed (`docs/CORE_LOG_HISTORY.md:14-37`). The test
`test_permission_expansion_is_visible_and_not_misrepresented_as_read_only`
keeps it visible.

Granted add-on history:
- `homeassistant_api`, the `homeassistant_config:ro` map and port 8100 came
  with the v2 scaffold (`fe44c50`, 2026-07-11), copied from stable v1. Stable
  v1 reads blueprints the same way (`hass_mcp_admin/server.py:360,404`; v1 is
  frozen and out of scope).
- `ingress` and `panel_admin` were added in `4aae730` (2026-07-14).
- `hassio_api` and `hassio_role` were added in `b1b7a3d` (2026-09-24).

## 3. Tests and CI that assume these grants

| Location | Assumes | Breaks if map removed | Breaks if role narrowed or removed |
| --- | --- | --- | --- |
| `tests/test_core_log_supervisor_contract.py:155-159` | `hassio_api is True`, `hassio_role == "homeassistant"` | No | **Yes** |
| `scripts/core_log_supervisor_probe.py:60-67` (manual disposable probe, not in CI) | Same | No | **Yes** (manual runs) |
| `scripts/validate_addon_metadata.py:184-282` (CI `ci.yml:150-151`) | Version, image, single port 8100, `ingress`, `panel_admin`, `ingress_port`, no `auth_api`, exact options/schema. Does not check map, `hassio_*` or `homeassistant_api` | No | No |
| `scripts/validate_addon_metadata.py:465-494` | Any change under `hass_mcp_engineering_beta/` must raise the version, or stage `.release/next-version` | **Yes, unless the version is bumped** | **Yes, unless the version is bumped** |
| `tests/test_hamcp164_core_gate_fail_closed.py:36,70` | Pins `get_blueprint` as `CORE_INDEPENDENT_MOUNTED_SOURCE` (static; it would not fail, but would become untrue) | Needs update | No |
| `tests/test_ha_core_2026_9_integration.py:3528`, `tests/test_beta39_obligation_pipeline.py:1521`, `tests/test_automation_reliability_analysis.py:374-379` | Mock the blueprint readers | No (they stay green, which hides the runtime loss) | No |
| CI container lanes (`ci.yml:288-331` smoke; `ci.yml:741-750` read-gateway acceptance) | Mount only `/data`, with a fake Supervisor; no `/homeassistant` | No | No |
| `ci.yml:436-500`, `scripts/ha_mcp_850_container_acceptance.py:516-591` | `/config` mounts for the HA Core and upstream ha-mcp containers, not Engineering | No | No |

The absence of a Real HA lane that exercises native `get_blueprint` against a
mounted directory means CI cannot detect the loss of the mount. A removal PR
would need a negative test proving the explicit, disclosed degradation in
section 5. It would also need a positive test of the replacement source.

## 4. Recommendations and change mechanics

### 4.1 `map: [homeassistant_config:ro]`: keep now; remove only after D1

Removing it today silently degrades three production features (section 5), so
the recommendation is **keep**. The least-privilege goal can still be met
through a separately scoped runtime increment (D1):

1. Move dependency-index and reliability blueprint resolution, and native
   `get_blueprint`, to the reviewed delegated blueprint provider.
   - Accept content only when the provenance is complete (`config` present and
     `yaml_source` in `component|file|tools_entry`).
   - Fail closed to the existing `blueprint_source_unavailable` coverage
     failure otherwise.
2. Reclassify `get_blueprint` from `CORE_INDEPENDENT_MOUNTED_SOURCE` to a
   Core/upstream-dependent route. Update `routing.py:311-316`, `routes.py:214-218`,
   the HAMCP-164 design table and `test_hamcp164_core_gate_fail_closed.py`.
3. Then make the manifest change in the same or a following release PR:

   ```diff
    panel_icon: mdi:shield-check
   -map:
   -  - homeassistant_config:ro
    ports:
   ```

   Also remove the dead `BLUEPRINT_BASES` constants and the "not mounted"
   message.

Narrowing instead of removing is not available. Supervisor has no
blueprints-only map type, and `addon_config` maps the add-on's own folder, not
Home Assistant's **(unverified, memory)**.

### 4.2 `hassio_api` and `hassio_role: homeassistant`: keep

This is already an explicit owner decision with disclosed latent authority. The
only reduction is to remove both lines and retire `get_core_log_history` (D2).
Narrowing the role alone breaks the tool, because `/core/logs` requires
`/core/.+`.

### 4.3 Everything else: keep

`homeassistant_api`, `ingress`/`ingress_port`/`panel_admin` and `init: false`
need no change. Port 8100 stays unless D3 finds the host mapping unnecessary.

### 4.4 Effect on existing installs (any manifest permission change)

- On update, Supervisor recreates the container from the new manifest. A
  removed map is gone after the update, and a removed `hassio_*` grant takes
  effect immediately **(unverified, memory)**.
- I am not aware of any consent prompt in the Home Assistant add-on update flow
  for permission changes; the add-on security rating changes on its info page
  **(unverified, memory)**. Removing grants lowers the add-on's privilege.
- Before release notes rely on either claim, confirm the exact-version
  Supervisor `addons` update path and the frontend update dialog under ADR-021.

### 4.5 Release implications

- Any `hass_mcp_engineering_beta/config.yaml` change requires a version bump,
  enforced by `scripts/validate_addon_metadata.py:465-494` and CI
  `ci.yml:154-173`.
- PR #267 already takes `2.4.1-beta.16`. A permission PR would therefore take
  the next free version (`beta.17` or later), or be folded into an existing
  release PR by owner choice.
- The release flow is:
  1. Stage `.release/next-version`.
  2. Add exact `docs/V2_4_1_BETA<N>_ACCEPTANCE.md` and `_RELEASE_NOTES.md`.
  3. Run `python scripts/promote_next_release.py --apply` in the same PR.
  4. Add the CHANGELOG and README entries.
  5. Run Full/Evidence and exact-head CI.
  6. Josh marks the PR Ready, which triggers merge and publication.
- Deployment remains a separate decision.

### 4.6 Rollback

- The fix is forward: ship a new version that restores the removed grant.
- Supervisor offers no direct add-on downgrade. To return to the prior image,
  restore the add-on from a pre-update partial backup **(unverified, memory)**.
- The fail-closed degradation in section 5 is not destructive: no data is
  written or lost, and coverage returns once the source is restored.

## 5. Production impact of removing the mount today

Nothing crashes, and no write path changes. All three readers fail closed or
disclose partial results. These features lose data:

| Feature | Effect without the mount |
| --- | --- |
| Native `get_blueprint` | Always returns "Blueprint directory not mounted ...". Users must use `ha_get_blueprint`, which needs ha-mcp and may be metadata-only. It also loses Core-independence (no blueprint reads while Core is down) |
| Dependency index → `entity_dependency_analysis`, `change_impact_analysis` | Each `use_blueprint` automation records a `blueprint_source_unavailable` coverage-failure obligation, and its actions become an unresolved placeholder (`dependency/provider.py:474-498`). Results for blueprint-backed automations become incomplete |
| Helper-dependency governance (`create_helper_state_plan`) | `blueprint_inventory_incomplete` is raised (`governance/helper_dependency.py:900-914`). It is a conservative all-automation lock reason (`:73-86`), not an execution-blocking reason (`:87-95`). Where any blueprint automation exists, helper plans fall back to the conservative all-automation scope instead of exact dependency scope |
| `automation_reliability_analysis` | `blueprint_source` coverage is `partial` with "Blueprint source could not be read; analysis is partial." (`reliability/provider.py:228-236`) |

Removing the map before D1 is implemented would therefore break user-visible
capability. That conflicts with the product north star, which values useful
authorized success as well as refusal.

## 6. Owner decisions

- **D1: migrate blueprint source off the mount, then remove the map?**
  Options:
  - (a) Approve a scoped runtime increment (section 4.1) followed by the map
    removal in a versioned release.
  - (b) Keep the mount and accept the documented read-only exposure of the whole
    configuration directory.
  - (c) Remove the map now and accept the degradation in section 5.
    This is not recommended.

  Trade-off for (a): blueprint reads then depend on ha-mcp availability, an
  admitted release and complete provenance. They lose Core-down availability.
  Blast radius moves to ha-mcp, which already has its own configuration access.
- **D2: keep `hassio_api` + `hassio_role: homeassistant`?** Recommended: keep,
  per the 2026-09-24 approval. The alternative is to remove both and retire
  `get_core_log_history`.
- **D3 (optional): investigate whether port 8100 needs a host mapping** for the
  Nabu Casa path. This is not evidenced in-repo, and the validator currently
  requires the mapping.
- **D4: release vehicle** for any manifest change: next free version after
  PR #267's `beta.16`, standalone or folded in.

## 7. Validation

- **Evidence method:** static source inspection, `git log -S` history of each
  grant, and `git grep` on the read-only PR #267 ref. Grep command lists are
  retained in the review brief in the session handoff.
- **Tests:** none run. This is a docs-only change with no behavior change.
- **CI:** results come from the draft PR's run.
- **Not run:** live Home Assistant, Real HA lanes and container acceptance. They
  are out of scope for a read-only review.
