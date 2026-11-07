# HAMCP-134: Security and Effective-Permission Metadata — Feasibility and Privacy Check

Status: feasibility and privacy check (2.6.0 design wave, docs only). Not an
implementation plan, not an acceptance record, and not a security-policy change.

All upstream evidence is pinned source read from verified archives plus the official
developer documentation. Nothing in this document came from a live Home Assistant, a
deployed MCP endpoint, or any credential.

## 1. Register outcome and binding decisions

Register row `HAMCP-134` (read with `register get HAMCP-134`, 2026-11-07):

- **Binding decision (row Next Action, recorded as Josh's, 2026-11-07,
  design-complete scoping):** "IN 3.0. First step: read-only feasibility and privacy
  check of what the Home Assistant API exposes (user roles, MFA, session policy,
  voice/tablet authority, ingress/proxy/TLS boundary); parts the API cannot provide go
  back to Josh. Read-only; may need a new signed read profile."
- **Row notes that bind this check:**
  - Do not reveal credentials, tokens, session identifiers, recovery material, or
    unnecessarily broad identity data.
  - This is metadata visibility, not a security-policy change.
  - HAMCP-048 (Host/Origin enforcement) is separate and is not touched here.

This document is that first step. It records, for each metadata area:

- what the API exposes;
- under which permission;
- which fields must be dropped or reduced;
- which areas are **not provided by the API** and so go back to Josh (section 10).

The row places the build in 3.0. This document does not schedule a build. Section 9
gives slicing only.

## 2. Identity and method

### 2.1 Repository base

| Item | Value |
|---|---|
| Base | `origin/release/2.6.0` = `7246f2d21693f039504542574569f03b71476489` |
| Engineering version at base | `2.4.2-beta.7` (`hass_mcp_engineering_beta/config.yaml:2`) |

All repository `file:line` citations below are at this base. Engineering paths are
relative to `hass_mcp_engineering_beta/ha_mcp_engineering/`.

### 2.2 Upstream pins

The pins are taken from repository source (`ci/support-window.yaml` and the signed
`upstream-trust/ha-core-release-registry.json`), not from memory.

| Upstream | Version / commit | Role in support window | Archive sha256 (verified locally) |
|---|---|---|---|
| HA Core | `2026.10.1` / `abe1fdd7cd6b4de1715c87b6fd87d92699514ef9` | current | `a4305046e34fb6bad4669915c64dfaec0f2b22ff56b6baefca9e8890cabd81c4` (matches signed `source_archive_sha256`) |
| HA Core | `2026.10.3` / `1f214b8461dd1fbbd66e10765c6905f49d3c1d86` | candidate | `57a15d2dcabb8703c9b9d08e8c243e8a7130db8cb8618e1c799ae46e1c75e4e9` (matches signed) |
| ha-mcp | `8.5.0` / `311d6dc273fb4e9a5b8cde0de15f69472a64fe44` | current (8.6.0 on hold) | `02a0f9d8534d7265a21cf52daac193c405c703aac013be0c808ebd8fb9aa056e` (codeload `legacy.tar.gz`, the CI convention) |
| Supervisor | `2026.09.1` / `40e3ee7640a3c44abe67f1a1397f39c3cd949806` | pin in `incident_logs/contracts.py:33` | `b413bb930a2b21378ef22e7a552309b538a8f6c8f0dd65ccdffc17d95366379a` (no committed digest to compare against: **unknown** provenance beyond the commit pin) |

### 2.3 Core 2026.10.1 against 2026.10.3

Every Core file cited in this document is byte-identical between 2026.10.1 and
2026.10.3. This was checked with `cmp` over 22 files:

- auth: `auth/const.py`, `auth/models.py`, `components/auth/__init__.py`,
  `components/config/auth.py`;
- config entries: `components/config/config_entries.py`, `config_entries.py`;
- HTTP and WebSocket: `components/http/websocket_api.py`, `components/http/config.py`,
  `components/websocket_api/commands.py`, `core_config.py`;
- Assist and conversation: `components/homeassistant/exposed_entities.py`,
  `components/assist_pipeline/pipeline.py`, `components/conversation/http.py`,
  `components/conversation/const.py`, `components/anthropic/conversation.py`,
  `components/assist_satellite/websocket_api.py`, `helpers/intent.py`;
- other components: `components/person/__init__.py`,
  `components/frontend/__init__.py`, `components/hassio/addon_panel.py`,
  `components/cloud/system_health.py`;
- helpers: `helpers/collection.py`.

Core line numbers therefore apply to both versions. They are written as
`homeassistant/<path>:<line>`.

### 2.4 Official documentation

`https://developers.home-assistant.io/docs/api/websocket` returned a 301 redirect to
`https://contributors.home-assistant.io/core/internals/websocket-api/`, fetched
2026-11-07. That page is not version-pinned.

- **Documented there:** `get_config`, with no fields listed, and the
  `homeassistant/expose_entity*` family ("All of them need an admin").
- **Not documented there:** `config/auth/list`, `auth/current_user`,
  `auth/refresh_tokens`, `http/config`, `assist_pipeline/pipeline/list` and
  `person/list` are frontend-internal commands.

Under ADR-021, pinned source is the authority for these commands. The documentation is
corroboration only.

### 2.5 Connection identity

Engineering connects to Core as `http://supervisor/core` with `SUPERVISOR_TOKEN`
(`configuration.py:187-188`).

Through the Supervisor proxy, Core authenticates that connection as the **Supervisor
system user**. That user is admin-group and `system_generated`.

Consequences:

- Every "connection user" command (`auth/current_user`, `auth/refresh_tokens`)
  describes that service user, not Josh or any household member.
- `@require_admin` commands are reachable by permission, but reachable is not
  admitted (section 2.6).

### 2.6 Admission rule

Admission rule (HAMCP-222, `docs/HAMCP_222_SIGNED_COVERAGE_AUDIT.md`): a Core command
is admitted only if a signed profile's contract covers that exact use.

A new command, a new field use of an admitted command, or a new adapter is **NOT
ADMITTED** until a new signed profile covers it.

## 3. Current state at the base

| What | Evidence | Status |
|---|---|---|
| `config/auth/list` used internally for the Ingress approver check; only the id match and a verdict are kept, the list is never stored or returned | `approval_identity.py:42`, `:82-117` (`MAX_USERS` fail-closed bound) | internal per-request use; named by no signed profile (HAMCP-222 §7.1, OD-8) |
| `auth/current_user` | `ha_core_readmission/profiles.py:479-488` (`core.automation_baseline_metadata_read`) | admitted for the baseline-capture contract only, not for MFA reporting |
| `get_config` | `profiles.py:168-181` (`core.basic_websocket_read`, `response_contract get_config: {"version"}`) | admitted for `version` only; URL fields are not admitted |
| Nabu Casa / cloud state | `providers/upstream_platform_reads.py:403-418` (`_PLATFORM_KEYS["cloud"]`), typed values reduced to ok/pending/failed at `:451-459`, projection at `:462-493` | **existing reviewed path** via delegated `ha_get_system_health` (`routes.py:213-215`, basic profiles, unattested commands disclosed) |
| Per-entity assistant exposure | `routes.py:207` `ha_get_entity_exposure -> core.delegated_device_effective_area`; ha-mcp 8.5.0 `tools_voice_assistant.py:200`; policy `upstream_tool_policy_8_5_0.json` classification `automatic_read` | **existing reviewed path** |
| Config entries (including `mobile_app`) | `providers/upstream_platform_reads.py:662-692` (entry_id, domain, title capped and email-masked, state, source) | **existing reviewed path** via delegated `ha_get_integration` (`routes.py:217`) |
| Assist pipeline management | ha-mcp 8.5.0 `tools_assist_pipeline.py:527` `ha_manage_pipeline`; policy `mixed_or_requires_wrapper`, no argument restriction, `readOnlyHint: false` | **not admitted**; no reviewed read wrapper exists |
| Bulk exposure listing in the 069B gateway | `governance/entity_availability_exposure.py:113-119` (`NEVER_SENT_COMMANDS` includes `homeassistant/expose_entity/list`, `cloud/status`) | never sent by that gateway |
| Supervisor self info | `providers/supervisor_self.py:71`, `:198` (`/addons/self/info` only, projected to slug/name/version/repository) | existing Engineering-native path; does not project ingress fields |
| Add-on Supervisor role | `config.yaml:11` `homeassistant_api: true`, `:12` `hassio_api: true`, `:13` `hassio_role: homeassistant`, `:15-17` `ingress: true`, `ingress_port: 8110`, `panel_admin: true`, `:21` `homeassistant_config:ro` | read only, not edited |
| Planned profile | HAMCP-222 §3.8 (`docs/HAMCP_222_SIGNED_COVERAGE_AUDIT.md:329-351`), plan row 11 (`:450`) `core.security_posture_metadata_read`, optional (OD-4) | not implemented |

**Missing:** no tool reports any of the following as a bounded, projected read:

- users and roles;
- MFA module configuration;
- the HTTP proxy/TLS boundary;
- URL posture;
- Assist pipeline or agent authority;
- ingress panels.

### 3.1 Notes on HAMCP-222 §3.8

Some line references in HAMCP-222 §3.8 are off by one or two lines against the verified
archives. For example, it gives `http/config` as `:52-82`; the verified range is
`:51-80`.

This document uses the verified lines. HAMCP-222 itself is not edited.

## 4. Findings by area

Classification key:

- **E** — feasible via an existing reviewed path;
- **N** — feasible via a new Core read contract (new signed read profile);
- **S** — feasible only via a new Supervisor read (outside the Core registry; needs its
  own provider review);
- **X** — not provided by the API, so it goes back to Josh.

### 4.1 Users, roles and groups, admin, owner, system-generated, active

**Command:** `config/auth/list`

- Source: `homeassistant/components/config/auth.py:22-33`.
- Permission: `@require_admin`.
- Projector: `_user_info`, `:165-187`.

**Fields returned:**

- `id`;
- `username` (the `homeassistant` provider username, or null);
- `name`;
- `is_owner`, `is_active`, `local_only`, `system_generated`;
- `group_ids`;
- `credentials: [{type}]` (auth provider types only).

There is no secret material in this result.

**Admin flag:** there is no `is_admin` field. Core's rule is:

```text
is_owner or (is_active and "system-admin" in group ids)
```

Source: `homeassistant/auth/models.py:92-96`; group ids at
`homeassistant/auth/const.py:9-11`. Engineering already applies that rule, stricter for
inactive users, in `approval_identity.py:82-117`.

**Writes in the same module (never sent):** `config/auth/create`, `config/auth/update`,
`config/auth/delete` (`components/config/auth.py:36-162`).

**ha-mcp 8.5.0:** has no user-listing tool.

**Supervisor:** not involved.

**Classification: N.** The command is used today only internally. A tool read of
projected fields is a new use and needs a new profile.

### 4.2 MFA modules enabled per user

**Command:** `auth/current_user`

- Source: `homeassistant/components/auth/__init__.py:559-595`.
- Permission: `ws_require_user`.
- Scope: the connection user only.

**Fields returned:**

- `id`, `name`;
- `is_owner`, `is_admin`;
- `credentials: [{auth_provider_type, auth_provider_id}]`;
- `mfa_modules: [{id, name, enabled}]`.

**No other-user read exists.** No admin command lists another user's MFA enrolment:

- `auth/setup_mfa` (`components/auth/mfa_setup_flow.py:65`) is a write flow.
- `auth/depose_mfa` (`:114`) is a write flow.
- `config/auth/list` carries no MFA field.

**What is observable:**

- the catalog of configured MFA module ids and names (system-wide configuration);
- the `enabled` flag for the Supervisor service user only. That flag says nothing
  about people.

**Classification:**

- **N (reduced)** for the configured MFA module catalog;
- **X** for per-user MFA enrolment.

### 4.3 Session policy, refresh tokens, long-lived token existence

**Source constants, with no runtime API:**

- `ACCESS_TOKEN_EXPIRATION = 30 min`, `homeassistant/auth/const.py:5`;
- `MFA_SESSION_EXPIRATION = 5 min`, `:6`;
- `REFRESH_TOKEN_EXPIRATION = 90 days`, `:7`.

These are defaults. Individual refresh tokens can have expiry changed through
`auth/refresh_token_set_expiry` (`components/auth/__init__.py:791-812`, a write), so
the constants are not proof of effective per-token policy.

**`auth/refresh_tokens`** (`components/auth/__init__.py:628-665`):

- Scope: connection user only.
- Fields: `auth_provider_type`, `client_icon`, `client_id`, `client_name`,
  `created_at`, `expire_at`, `id`, `is_current`, `last_used_at`, `last_used_ip`, `type`.
- It would describe only the service user, and it carries session identifiers and IP
  addresses. It is **never sent**.

**Credential-minting commands:**

- `auth/long_lived_access_token` (`:597-625`) mints a credential;
- `auth/sign_path` (`:763`) mints a credential;
- `auth/delete_refresh_token` (`:668`) and `auth/delete_all_refresh_tokens` (`:691`)
  are writes.

**No admin read lists other users' refresh or long-lived tokens.** Even their existence
or count is not exposed.

**Classification:**

- **X** for per-user session and long-lived-token existence.
- Session defaults may be reported only as **documentary source constants**, labelled
  "pinned source, not observed". Whether to report them at all is open question OQ-4.

### 4.4 Voice assistant, Assist exposure and conversation-agent authority

#### Per-entity exposure (`conversation`, `cloud.alexa`, `cloud.google_assistant`)

Known assistants: `homeassistant/components/homeassistant/exposed_entities.py:25`.

**Existing path:**

- `ha_get_entity_exposure` is an existing reviewed path. In ha-mcp it uses
  `homeassistant/expose_entity/list` or the `ha_mcp_tools/exposure` bridge.
- **Classification: E.**

**Native command `homeassistant/expose_entity/list`** (`:418-443`):

- Permission: `@require_admin`.
- Behaviour: read-only; it uses `async_get_entity_settings`.
- Result: only entities whose stored `should_expose` is true.

**Coverage caveat:** entities whose exposure was never evaluated have no stored setting
and are absent from the list. Absence is "not stored", not "not exposed".

**Do not call `async_should_expose` paths.** `async_should_expose` (`:247-271`)
**writes** a computed default into the entity registry. Any read must use only the
listing command and must never trigger evaluation.

**`homeassistant/expose_new_entities/get`** (`:446-460`):

- Permission: admin.
- Read-only.
- Already read by the 069B gateway for its own contract.
- **N** for this tool's use.

#### Pipelines

**Command:** `assist_pipeline/pipeline/list`

- Source: `components/assist_pipeline/pipeline.py:555-568` (registration) and
  `:496-508` (`ws_list_item`).
- Permission: **not admin**. The list is not `admin_only`
  (`helpers/collection.py:564`, `:596`). Create, update, delete and `set_preferred` are
  admin writes (`helpers/collection.py:611-655`; `pipeline.py:446-461`).

**Fields per pipeline:** `PIPELINE_FIELDS` (`pipeline.py:70-84`) plus `id`:

- `conversation_engine`, `conversation_language`, `language`, `name`;
- `stt_engine`, `stt_language`;
- `tts_engine`, `tts_language`, `tts_voice`;
- `wake_word_entity`, `wake_word_id`;
- `prefer_local_intents`, `acknowledge_media_id`.

The result also carries `preferred_pipeline`.

**Classification: N.**

#### Conversation agents

**Command:** `conversation/agent/list`

- Source: `components/conversation/http.py:97-154`.
- Permission: not admin.
- Fields: `{id, name, supported_languages}` per agent.

**Classification: N.**

#### Agent authority to control Home Assistant

LLM agents set `ConversationEntityFeature.CONTROL` (`components/conversation/const.py:26-29`)
only when their subentry has an `llm_hass_api` configured. Example:
`components/anthropic/conversation.py:44-50`; other integrations follow the same
pattern.

**What is observable:** that bit is visible in the `supported_features` state attribute
of `conversation.*` entities, through existing reviewed entity-state reads.

- **Classification: E**, for the coarse fact "this agent may control Home Assistant".

**What is not observable:**

- which LLM API the agent uses (`assist` or another);
- its prompt;
- its tool set.

These live in config-entry options or subentry data. The WebSocket API does not return
them:

- `config_entries/get` returns metadata only (`homeassistant/config_entries.py:677-703`,
  with no `data` or `options`);
- `config_entries/subentries/list` (`components/config/config_entries.py:776-797`)
  returns `subentry_id`, `subentry_type`, `title`, `unique_id` only.

Starting an options flow to read them is a write-adjacent flow and is refused.

- **Classification: X** for the effective LLM API and tool scope.

#### Effective authority model

Assist intent matching filters targets by assistant exposure:

- constraint `assistant`, `helpers/intent.py:335-336`;
- failure reason `ASSISTANT`, `:367`;
- default-agent checks, `components/conversation/default_agent.py:224-230`, `:1059`.

So the observable Assist authority is "which entities are exposed to which assistant".
It is not a per-user permission set.

#### Satellites

**Command:** `assist_satellite/get_configuration`

- Source: `components/assist_satellite/websocket_api.py:76-106`.
- Permission: not admin; one `entity_id` per call.
- Fields: wake words, `pipeline_entity_id`, `vad_entity_id`.

This could map satellites to pipelines. It is optional (OQ-6).

There is no API that states which user, if any, a satellite acts as.

- **Classification: X** for satellite user authority.

**ha-mcp 8.5.0:** besides `ha_get_entity_exposure`, only `ha_manage_pipeline` is
relevant, and it is mixed and unadmitted (section 3).

### 4.5 Tablet, kiosk and companion-app authority

#### Companion-app installations

`mobile_app` config entries (title and count) are visible via the existing
`ha_get_integration` projection, with titles capped and email-masked
(`providers/upstream_platform_reads.py:662-692`). **Classification: E.**

Titles are usually device names. That is identifying, see section 5.

#### Which user a device or app acts as

The `mobile_app` entry `data` (user id, webhook id, secret) is not returned by any read
command (`config_entries.py:677-703`). Another user's refresh tokens cannot be listed
(section 4.3).

**Classification: X.**

#### Kiosk or tablet user identity

No API distinguishes a wall tablet's login from any other session of the same user.

**Classification: X.**

#### Partial signals available through section 4.1

- `local_only` per user;
- credential types per user (for example `trusted_networks`);
- `system-read-only` group membership.

#### Panel visibility

**Command:** `get_panels`

- Status: documented; not admin.
- Source: `components/frontend/__init__.py:966-986`.
- Behaviour: an admin connection sees every panel, with `require_admin` reported per
  panel.
- Fields: `component_name`, `icon`, `title`, `default_visible`, `config`, `url_path`,
  `require_admin`, `config_panel_domain` (`:338-350`).

**Classification: N.**

### 4.6 Ingress, proxy, TLS, and external and internal URL

#### HTTP boundary

**Command:** `http/config`

- Source: `components/http/websocket_api.py:51-80`.
- Permission: `@require_admin`.

**Result:** `{stable, pending, revert_at, active_config_type, default}`.

**Each config** (`ConfData`, `components/http/config.py:96-113`) carries:

- `server_host`, `server_port`;
- `ssl_certificate`, `ssl_peer_certificate`, `ssl_key` — **file paths**;
- `cors_allowed_origins`;
- `use_x_forwarded_for`, `trusted_proxies`;
- `login_attempts_threshold`, `ip_ban_enabled`;
- `ssl_profile`, `use_x_frame_options`;
- `created_at`;
- `error`, `error_message` (free text).

`ActiveConfigType` values (`:116-122`): `stable`, `pending`, `default`,
`default_legacy_port`.

**Storage versus YAML:** HTTP configuration is storage-backed after a one-time YAML
migration (`:200-280`). If YAML is still present after migration, it is ignored and a
repair is raised. The stored config is therefore the effective one.

**Writes (never sent):**

- `http/config/configure` (`websocket_api.py:83-140`) stores a pending config and
  **restarts Home Assistant**.
- `http/config/promote` (`:142-145`) is a write.

**Classification: N.**

#### External and internal URL

**Command:** `get_config`

- Source: `components/websocket_api/commands.py:658-668`.
- Permission: not admin. `external_url` is popped for `local_only` users.
- Data: `homeassistant/core_config.py:685-721`.

The data includes `external_url`, `internal_url`, `latitude`, `longitude`, `elevation`,
`config_dir`, `allowlist_external_dirs`, `allowlist_external_urls`, `location_name`,
and other fields.

Today only `version` is admitted (`profiles.py:178-181`).

**Classification: N** for URL posture.

#### Ingress panels of other add-ons

Visible via `get_panels`. The hassio integration registers each panel as
`component_name "app"` with `config {"addon": <slug>}` and `require_admin = <panel.admin>`
(`components/hassio/addon_panel.py:106-123`).

**Classification: N.**

#### Core's own API TLS and port, as Supervisor sees them

Supervisor `GET /core/info` (`supervisor/api/homeassistant.py:93-111`) returns `ssl`
(`api_ssl`), `port`, `ip_address`, and others.

**Role check:** the add-on role `homeassistant` permits `/core/.+` and `/.+/info`
(`supervisor/api/middleware/security.py:121-126`).

**Classification: S**, optional, and redundant with `http/config` for TLS presence.

The same `/core/.+` grant also permits Core lifecycle writes. That grant exists today;
any new Supervisor read must be a fixed `GET` with a positive projection.

#### Engineering's own ingress

`/addons/self/info` (`supervisor/api/apps.py:221-307`) includes:

- `ingress`, `ingress_entry`, `ingress_port`, `ingress_panel`;
- `auth_api`, `hassio_role`;
- the add-on's **own options**, including `access_secret`. Options are exposed to the
  app itself, `:223-232`.

The existing `supervisor_self` path already projects positively.

**Classification: S**, as an optional projection extension.

#### Full ingress configuration of other add-ons

Our role cannot list add-ons: `/addons` does not match `/.+/info`, `/core/.+` or
`/homeassistant/.+`.

`/addons/<slug>/info` is readable per slug, with options redacted for us because our
role is not manager or admin (`apps.py:223-232`).

Core's `supervisor/api` WebSocket forwarder (`components/hassio/websocket_api.py:111-161`)
would reach `/addons` with **Core's** Supervisor trust. It returns other add-ons'
options to an admin connection (options are popped only for non-admins, `:157-160`).
That is privilege escalation beyond our own role, and arbitrary forwarding.

**Classification: X** beyond the `get_panels` signal. Not proposed.

#### Engineering's own proxy settings

`trusted_proxy_cidrs`, `trust_cf_connecting_ip`, `mcp_allowed_hosts` and
`mcp_allowed_origins` (`config.yaml:26-50`) belong to Engineering's own Host/Origin
boundary. They are HAMCP-048 scope and out of scope here.

### 4.7 Nabu Casa cloud remote state

**Existing reviewed path:** the `ha_get_system_health` cloud block.

**Kept keys:** `logged_in`, `relayer_connected`, `remote_enabled`, `remote_connected`,
`alexa_enabled`, `google_enabled`, `certificate_status`, `iot_state`, and the
`can_reach_*` checks (`providers/upstream_platform_reads.py:413-417`).

**Already dropped by that allowlist** (source `homeassistant/components/cloud/system_health.py:25-60`):

- `subscription_expiration`, `relayer_region`, `remote_server`;
- `instance_id`;
- `certificate_fingerprint`, `certificate_alternative_names`,
  `certificate_expire_date`;
- `iot_last_disconnect_reason`, `cloud_ice_servers_enabled`.

**`cloud/status` is never sent.** It carries `google_secure_devices_pin`
(`governance/entity_availability_exposure.py:113-119`; HAMCP-222 §3.8).

**Classification: E.**

### 4.8 Summary

| Area | Classification | Path |
|---|---|---|
| Users, roles, groups, admin, owner, system-generated, active, local_only, credential types | N | `config/auth/list` (projected) |
| MFA module catalog | N (reduced) | `auth/current_user` → `mfa_modules[].{id,name}` only |
| MFA enrolment per user | **X** | none |
| Session/token policy per user; long-lived token existence | **X** | none; defaults are source constants only |
| Per-entity assistant exposure | E | delegated `ha_get_entity_exposure` |
| Expose-new-entities defaults, per-assistant exposure counts | N | `homeassistant/expose_new_entities/get`, `homeassistant/expose_entity/list` (counts) |
| Pipelines and preferred pipeline | N | `assist_pipeline/pipeline/list` |
| Conversation agents | N | `conversation/agent/list` |
| Agent may control HA (CONTROL bit) | E | entity state `supported_features` of `conversation.*` |
| Agent's LLM API, tool scope; satellite user authority | **X** | none |
| Companion-app installations | E | delegated `ha_get_integration` |
| Device/tablet/kiosk acting-user authority | **X** | none |
| Panel visibility and ingress panels (`require_admin`) | N | `get_panels` |
| HTTP proxy/TLS/ban boundary | N | `http/config` (projected) |
| External/internal URL posture | N | `get_config` URL fields (reduced) |
| Core API TLS/port as Supervisor sees it; own ingress | S (optional) | Supervisor `GET /core/info`; `/addons/self/info` projection |
| Full ingress config of other add-ons | **X** | not reachable without escalation |
| Nabu Casa remote state | E | delegated `ha_get_system_health` |

## 5. Privacy table

Rule: positive projection. Any field not listed is dropped.

"Reduce" means the raw value never leaves the Engineering process and is never logged
or audited raw.

| Source command | Field | Decision | How / reason |
|---|---|---|---|
| `config/auth/list` | `id` | reduce | per-response ordinal pseudonym (`user-1`...) by default; the raw Core id is a stable cross-system identifier. Keeping raw ids is OQ-3 |
| `config/auth/list` | `username` | drop | login identifier; a credential half |
| `config/auth/list` | `name` | drop (default) | personal display name; "unnecessarily broad identity data" per row note. Keeping it is OQ-3 |
| `config/auth/list` | `is_owner`, `is_active`, `local_only`, `system_generated` | keep | role/posture booleans, the purpose of the read |
| `config/auth/list` | `group_ids` | reduce | map to `admin` / `user` / `read_only` for the three `system-*` ids; any other group is counted as `other_group_count`, never named |
| `config/auth/list` | derived `is_admin` | keep | computed with Core's rule (`auth/models.py:92-96`) |
| `config/auth/list` | `credentials[].type` | reduce | sorted set of provider types per user (for example `homeassistant`, `trusted_networks`, `command_line`); no provider ids |
| `auth/current_user` | `mfa_modules[].id`, `.name` | keep | system-wide module catalog |
| `auth/current_user` | `mfa_modules[].enabled` | reduce | reported only as `service_user_enrolled`, explicitly labelled as the Supervisor service user, never as a person |
| `auth/current_user` | `id`, `name`, `credentials`, `is_owner`, `is_admin` | drop | service-user identity; not useful |
| `http/config` | `ssl_certificate`, `ssl_key`, `ssl_peer_certificate` | reduce | booleans `tls_configured`, `client_cert_required`; paths dropped (filesystem layout) |
| `http/config` | `ssl_profile`, `use_x_forwarded_for`, `ip_ban_enabled`, `login_attempts_threshold`, `use_x_frame_options`, `server_port` | keep | boundary posture |
| `http/config` | `trusted_proxies` | reduce | count, plus `includes_loopback`, `includes_private`, `includes_public` booleans; exact CIDRs are network topology. Exact CIDRs are OQ-5 |
| `http/config` | `cors_allowed_origins` | reduce | count only |
| `http/config` | `server_host` | reduce | `binds_all_interfaces` boolean, and a count of explicit binds |
| `http/config` | `active_config_type`, `revert_at`, `pending` presence | keep / reduce | `pending` reduced to `pending_present` and `pending_failed` booleans |
| `http/config` | `error_message`, `created_at` | drop | free text can echo paths or values; timestamp not needed |
| `http/config` | `default` | drop | built-in constant; not posture |
| `get_config` | `external_url`, `internal_url` | reduce | per URL: `present`, `scheme`, `https`, `is_nabu_casa_domain` (`*.ui.nabu.casa`), `uses_default_port`; hostnames and paths dropped |
| `get_config` | `latitude`, `longitude`, `elevation`, `location_name`, `config_dir`, `allowlist_*`, `components`, and the rest | drop | location and filesystem data; not in scope |
| `homeassistant/expose_entity/list` | per-entity map | reduce | per-assistant counts only (detail is already available per entity via `ha_get_entity_exposure`); coverage caveat attached. See OQ-7 on the 069B never-sent precedent |
| `homeassistant/expose_new_entities/get` | `expose_new` per assistant | keep | posture boolean |
| `assist_pipeline/pipeline/list` | `id`, `name`, `conversation_engine`, `stt_engine`, `tts_engine`, `wake_word_entity`, `language`, `prefer_local_intents`, `preferred_pipeline` | keep | engine and agent ids are entity or integration ids, not secrets; `name` is capped and email-masked |
| `assist_pipeline/pipeline/list` | `tts_voice`, `wake_word_id`, `acknowledge_media_id`, `*_language` | drop | not authority-relevant |
| `conversation/agent/list` | `id`, `name` | keep | `name` capped and email-masked |
| `conversation/agent/list` | `supported_languages` | drop | not authority-relevant |
| `get_panels` | `url_path`, `component_name`, `require_admin`, `config.addon` (app panels only) | keep | visibility boundary |
| `get_panels` | `title`, `icon`, `config` (other than `addon`), `default_visible`, `config_panel_domain` | drop / cap | `title` capped; other `config` may carry integration data, so drop |
| `person/list` | everything | not read | `user_id`, `device_trackers` and `picture` are personal and location-linked; a user-to-person mapping is not needed for posture (OQ-8) |
| `auth/refresh_tokens` | everything | never sent | session ids, IPs, service user only |

## 6. Never sent

Each of these must be refused before I/O in any contract built from this check:

- `auth/long_lived_access_token`
- `auth/sign_path`
- `auth/refresh_tokens`
- `auth/delete_refresh_token`
- `auth/delete_all_refresh_tokens`
- `auth/refresh_token_set_expiry`
- `auth/setup_mfa`
- `auth/depose_mfa`
- `config/auth/create`
- `config/auth/update`
- `config/auth/delete`
- `http/config/configure` (restarts Home Assistant)
- `http/config/promote`
- `cloud/status`
- `homeassistant/expose_entity` (write)
- `homeassistant/expose_new_entities/set`
- `assist_pipeline/pipeline/create`, `/update`, `/delete`, `/set_preferred`
- `config_entries/options/flow` and any other flow start
- `person/list`
- `supervisor/api` (Core forwarder), with any endpoint
- every Supervisor path other than a fixed `GET`

Two further rules:

- Delegated `ha_manage_pipeline` and `ha_manage_security_policy` (the latter is ha-mcp's
  own feature-flagged policy editor, unrelated to HA security) are not admitted.
- No code path may call `async_should_expose`. It writes registry defaults.

## 7. Proposed bounded read surface (feasible parts only)

**Tool:** one Engineering-native read tool, working name `get_security_metadata`.

- Provider: `direct_ha_api` under one new runtime-only profile (section 8).
- No delegation, no fallback to ha-mcp or Supervisor, no retries beyond one
  at-most-once read per command.

**Input:**

```text
{ "sections": [ "users" | "mfa" | "http_boundary" | "urls" | "assist" | "panels" ] }
```

- `sections` is optional; the default is all sections.
- There are no free-form arguments, no entity or user selectors, and no pass-through.

**Output, bounded by a fixed byte cap:**

- `schema_version`, `observed_at`, `core_version` (from `auth_ok`);
- `connection_identity: "supervisor_service_user"`;
- `sections.<name>`: the projected values from section 5;
- `coverage.<name>`:
  - `status`: one of `observed`, `partial`, `not_provided`, `failed`;
  - `commands`: exact Core command names used;
  - `reason_code`;
- `omissions`: a fixed list naming every **X** area from section 4.8 as
  `not_provided_by_api`, so absence is never silent;
- `permission_limits`: for example `mfa.service_user_only` and
  `exposure.stored_settings_only`;
- `related_tools`: pointers to `ha_get_system_health` (cloud), `ha_get_entity_exposure`
  (per-entity), and `ha_get_integration` (companion apps). They are not called
  internally.

**Failure behaviour:**

- Sections are independent. A command error, a non-admin connection, an unexpected
  type or an unknown required field marks that section `failed` with a reason. Values
  are never guessed and never partially projected from a drifted shape.
- Unknown extra fields are dropped by the positive projection.
- `config/auth/list` above `MAX_USERS` (the same bound as `approval_identity.py`) fails
  the section closed.
- An unsupported Core version, or a profile not selected by the signed registry, fails
  the whole tool closed, the same as other runtime-only profiles.
- Audit records the section statuses and counts, never projected identity values.

**Public schema impact:**

- Plus 1 Engineering-native read tool.
- The exact tool count is derived from source at build time, not asserted here.
- No change to existing tool schemas.
- Optional Supervisor `S` items (Core API TLS/port; own ingress projection) are
  separate, optional, and need their own provider review. They are not part of the
  Core profile.

## 8. Signing impact

| Profile id | New or changed (from version X) | Core commands / contract added | Why | Fingerprint impact | Can it batch with other 2.6 profiles |
|---|---|---|---|---|---|
| `core.security_metadata_read` (HAMCP-222 plan name `core.security_posture_metadata_read`; final id at build) | New (from none) | `config/auth/list` (projected per section 5); `auth/current_user` (`mfa_modules` id/name/enabled only); `http/config` (projected); `get_config` (URL posture fields only); `homeassistant/expose_new_entities/get`; `homeassistant/expose_entity/list` (counts only, subject to OQ-7); `assist_pipeline/pipeline/list`; `conversation/agent/list`; `get_panels` (app and `require_admin` fields). The section 6 list goes in the contract's `never_sent`. New `CoreCapabilityClass` value (`ha_core_readmission/models.py:46-77`) | No signed profile admits these commands for a tool read. `get_config` is admitted only for `version` (`profiles.py:178-181`); `auth/current_user` only for the baseline contract (`profiles.py:479-488`); `config/auth/list` only as an unnamed internal approver check | New profile fingerprint. Runtime-only, like `CORE_NOVEMBER_BATCH_PROFILES` (`profiles.py:633-638`, not in `CORE_CAPABILITY_PROFILES`), so the signed probe fingerprint and all existing profile fingerprints are unchanged. Needs a new owner-signed registry envelope that lists the capability for each admitted Core version (2026.10.1 and 2026.10.3, plus any later 2.6 target); dormant until then | Technically yes: read-only, independent commands, no shared adapter with other 2.6 profiles. Placement is pending Josh (OQ-1): the row says the build is IN 3.0 |

**Deliberately unchanged:**

- `core.basic_websocket_read` and `core.automation_baseline_metadata_read` are not
  widened. A widened `get_config` or `auth/current_user` contract would silently extend
  existing tools.
- The existing reviewed paths keep their current profiles:
  - `ha_get_system_health`: basic profiles;
  - `ha_get_entity_exposure`: `core.delegated_device_effective_area`;
  - `ha_get_integration`: basic profiles.
- The Supervisor reads (`S`) are outside the signed Core registry.

## 9. Slicing only

Per the row, the build is IN 3.0, and this is not a schedule.

1. **Slice A — contract and gateway, docs-first:**
   - the profile contract and `never_sent` list;
   - fixtures generated from pinned source shapes;
   - negative tests for every section 6 command;
   - the drift tests below.

   Drift tests:

   - no tool path calls `async_should_expose`;
   - path fields never appear;
   - `name` and `username` never appear unless OQ-3 says so.
2. **Slice B — users, MFA catalog, HTTP boundary, URL posture:** sections
   `users`, `mfa`, `http_boundary`, `urls`.
3. **Slice C — Assist and panels:** sections `assist`, `panels`.
4. **Signing:** a separate owner-signed registry envelope. It may batch with other
   profiles per OQ-1.
5. **Optional Slice D:** Supervisor `S` reads, only if Josh wants them, with their own
   provider review.

**Dependencies:**

- No dependency on unlanded 2.5.0 work. None of #384–#387 or HAMCP-290 touches the
  cited files, except shared registry and profile files at build time, where 2.5.0 wins.
- HAMCP-048 is independent.

## 10. Open questions — pending Josh

1. **OQ-1 placement.** The row says IN 3.0. Should the new profile still be included
   in a 2.6 signing envelope (dormant) for later use, or wait for 3.0 entirely?
2. **OQ-2 not provided by the API.** These areas cannot be read from Home Assistant:
   - per-user MFA enrolment;
   - per-user refresh/long-lived token existence and session policy;
   - which user a tablet, kiosk, companion app or satellite acts as;
   - an LLM agent's API and tool scope;
   - full ingress configuration of other add-ons.

   Accept them as `not_provided` omissions, or pursue another source (for example
   owner-maintained documentation)? Any non-API source is a scope change.
3. **OQ-3 identity detail.** Report users by per-response pseudonym only (the proposed
   default), or include the raw Core user id and/or display name?
4. **OQ-4 session defaults.** Report pinned-source session constants (30 min, 90 days,
   5 min) as documentary, or omit them as not observed?
5. **OQ-5 trusted proxies.** Counts and class booleans (proposed), or exact CIDRs?
6. **OQ-6 satellites.** Include `assist_satellite/get_configuration` (one call per
   satellite entity, bounded) for the satellite-to-pipeline mapping?
7. **OQ-7 exposure counts.** The 069B gateway deliberately never sends
   `homeassistant/expose_entity/list`. Should this read send it, projected to counts
   only, or rely solely on the existing per-entity `ha_get_entity_exposure` path?
8. **OQ-8 persons.** Should a user-to-person mapping ever be read? Proposed: no.
9. **OQ-9 Supervisor reads.** Add Core API TLS/port (`/core/info`) and Engineering's own
   ingress projection (`/addons/self/info`) as optional Supervisor reads?
10. **OQ-10 service-user data.** Is reporting the MFA catalog with the service user's
    enrolment useful, or should the `mfa` section report the catalog only?

## 11. Security section

- **Read-only.** No new write, service call, reload, restart, deletion, flow start or
  forwarding is reachable. Every adjacent write is named in `never_sent`.
- **`async_should_expose` is excluded.** That read-looking helper writes, and is
  explicitly excluded.
- **No credentials or session identifiers.** No credential, token, session identifier,
  IP address, recovery material or TLS path is read into output. The commands that
  carry them are never sent (section 6).
- **No escalation.** The Core `supervisor/api` forwarder is excluded because it would
  exceed the add-on's own Supervisor role (`config.yaml:13`).
- **No fallback.** There is no fallback to ha-mcp for this tool. Delegated reads stay on
  their existing reviewed paths and profiles.
- **Accurate labelling.** All service-user-scoped data is labelled as such, so that it
  cannot be misread as a person's posture.
- **Metadata visibility only.** This is not a security-policy change. Nothing here
  changes Home Assistant, Engineering, or HAMCP-048 Host/Origin enforcement.
- **Non-actions.** No live Home Assistant access, no deployed endpoint access, no
  credentials read, and no protected path edited.
