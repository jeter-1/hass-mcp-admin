# HAMCP-134: Security and Effective-Permission Metadata — Feasibility and Privacy Check

Status: feasibility and privacy check (2.6.0 design wave, docs only), corrected after the
Codex design review of 2026-11-08 (section 13). Not an implementation plan, not an
acceptance record, and not a security-policy change.

All upstream evidence is pinned source read from verified archives plus the official
developer documentation. Nothing in this document came from a live Home Assistant, a
deployed MCP endpoint, or any credential.

## 1. Register outcome and binding decisions

### 1.1 Current direction

**Josh direction (register 2026-11-07; confirmed in-session 2026-11-08):**

- HAMCP-134 is delivered in **2.6.0**.
- Profiles are **additive only**. No existing signed profile is version-bumped or
  replaced. Existing fingerprints stay byte-identical.
- New 2.6 profiles are signed for **Core 2026.11.0 only**. There are no 10.x targets
  for new profiles.
- All new 2.6 profiles go into **one combined 2.6 signing**: one `extend-capabilities`
  extension of the existing 2026.11.0 registry entry. There are no separate or phased
  2.6 signings.

These points are settled. They are not open questions (section 10).

### 1.2 Row notes that still bind this check

Register row `HAMCP-134` (read with `register get HAMCP-134`, 2026-11-07):

- Do not reveal credentials, tokens, session identifiers, recovery material, or
  unnecessarily broad identity data.
- This is metadata visibility, not a security-policy change.
- HAMCP-048 (Host/Origin enforcement) is separate and is not touched here.
- Parts the API cannot provide go back to Josh (section 10).

### 1.3 Superseded (historical)

The 2026-11-07 row Next Action read: "IN 3.0. First step: read-only feasibility and
privacy check of what the Home Assistant API exposes (user roles, MFA, session policy,
voice/tablet authority, ingress/proxy/TLS boundary); parts the API cannot provide go
back to Josh. Read-only; may need a new signed read profile." The "IN 3.0" placement is
superseded by section 1.1. The first draft of this document (head `638510ff`) also cited
Core 2026.10.1 and 2026.10.3 as the authority, proposed a 10.x signing, and asked about
batching (old OQ-1). All three are superseded. The Core authority is now 2026.11.0
(section 2.3).

This document records, for each metadata area:

- what the API exposes, at Core 2026.11.0;
- under which permission;
- which fields must be dropped or reduced;
- which areas are **not provided by the API** and so go back to Josh (section 10).

## 2. Identity and method

### 2.1 Repository base

| Item | Value |
|---|---|
| Design base | `7246f2d21693f039504542574569f03b71476489` (`origin/release/2.6.0` when this design was cut) |
| Engineering version at base | `2.4.2-beta.7` (`hass_mcp_engineering_beta/config.yaml:2`) |

All repository `file:line` citations below are at this base. Engineering paths are
relative to `hass_mcp_engineering_beta/ha_mcp_engineering/`.

### 2.2 Upstream pins

| Upstream | Version / commit | Role here | Archive sha256 (verified locally) |
|---|---|---|---|
| HA Core | `2026.11.0` / `74cf5a49c63ed8d4d87717844bf14f79c28c9cea` | **authority** for every Core claim and for the new profile | `85f3c42c9f77aa45e01faff4471007beadf5cb50c4324f496a239139eec70ff1` (verified 2026-11-08; `homeassistant/const.py` reads 2026 / 11 / "0") |
| HA Core | `2026.10.3` / `1f214b8461dd1fbbd66e10765c6905f49d3c1d86` | historical comparison only (section 2.3) | `57a15d2dcabb8703c9b9d08e8c243e8a7130db8cb8618e1c799ae46e1c75e4e9` |
| ha-mcp | `8.5.0` / `311d6dc273fb4e9a5b8cde0de15f69472a64fe44` | current (8.6.0 on hold); existing delegated paths only | `02a0f9d8534d7265a21cf52daac193c405c703aac013be0c808ebd8fb9aa056e` (codeload `legacy.tar.gz`, the CI convention) |
| Supervisor | `2026.09.1` / `40e3ee7640a3c44abe67f1a1397f39c3cd949806` | pin in `incident_logs/contracts.py:33` | `b413bb930a2b21378ef22e7a552309b538a8f6c8f0dd65ccdffc17d95366379a` (no committed digest to compare against: **unknown** provenance beyond the commit pin) |

The 2026.11.0 Core registry entry that the combined 2.6 signing extends is not present
in this branch's base or in the `main`, `release/2.5.0` or `release/2.6.0` tips at
2026-11-08. It lives on the registry branches. This document does not assert its
contents; the build binds to it.

### 2.3 Rebinding to Core 2026.11.0

Every Core citation below was re-read in the 2026.11.0 archive. Of the 22 Core files the
first draft cited, **14 differ** between 2026.10.3 and 2026.11.0 (`cmp`). Of 9 further
Core files read for this correction, 8 differ. (Codex counted 17 of 27 for its own file
set; the counts differ only because the file sets differ.)

| Core file | 10.3 vs 11.0 | Effect on this document |
|---|---|---|
| `auth/const.py`, `auth/models.py`, `components/config/auth.py`, `components/auth/mfa_setup_flow.py` | identical | none |
| `components/assist_pipeline/pipeline.py`, `components/assist_satellite/websocket_api.py`, `helpers/collection.py` | identical | none |
| `components/hassio/addon_panel.py`, `components/cloud/system_health.py` | identical | none |
| `components/auth/__init__.py` | differs | lines moved; claims unchanged. `auth/delete_all_refresh_tokens` gained a `delete_current_token` argument (write; never sent) |
| `config_entries.py`, `components/config/config_entries.py` | differs | lines moved; `as_json_fragment` still has no `data`/`options`; `config_entries/subentries/list` is `@require_admin` |
| `components/http/websocket_api.py`, `components/http/config.py` | differs | lines moved. The active-slot model is now projected correctly (section 4.6, finding M2) |
| `components/websocket_api/commands.py`, `core_config.py` | differs | lines moved; `get_config` still pops `external_url` for `local_only` users |
| `components/homeassistant/exposed_entities.py` | differs | new non-persisting `async_get_should_expose` (`:301-319`). No WebSocket read command exposes it; it is used inside the `homeassistant/expose_entity` write. The listing command is byte-identical in behaviour (section 4.4) |
| `components/conversation/http.py`, `components/conversation/const.py`, `components/anthropic/conversation.py`, `components/conversation/default_agent.py`, `helpers/intent.py` | differs | lines moved; claims unchanged |
| `components/person/__init__.py` | differs | not read by this design (section 5) |
| `components/frontend/__init__.py` | differs | `get_panels` reports the effective `require_admin` after an admin-set override, and returns `show_in_sidebar`. The override is set by the admin write `frontend/update_panel` (never sent). The first draft missed both (section 4.5) |
| `components/hassio/websocket_api.py` | differs | `supervisor/api` forwarder moved to `:120-172`; new `supervisor/ingress/info`, `supervisor/ingress/session`, `supervisor/ingress/validate_session` commands (section 4.6) |
| `components/hassio/__init__.py`, `components/http/auth.py`, `components/websocket_api/auth.py`, `components/websocket_api/http.py` | differs | Supervisor user creation/reuse and the dedicated Unix-socket authentication (section 2.5) |
| `components/http/server.py`, `components/http/const.py` | differs | `DEFAULT_BIND` and the Unix-socket request check; cited only for context |

Core 2026.11.0 also replaces `voluptuous` with `probatio` for command schemas. Command
names and result shapes cited here are unchanged by that, but schema-error messages may
differ. The tool never echoes error text (section 7.3), so this does not change the
contract. Exact-11.0 fixtures must still be generated from 11.0 (section 9).

Core line numbers are written as `homeassistant/<path>:<line>` and apply to
**2026.11.0 only**.

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

Engineering's default connection is `http://supervisor/core` with `SUPERVISOR_TOKEN`
(`configuration.py:187-188`). That code also accepts alternatives: `HA_URL` overrides
the URL, and `HA_TOKEN` is used when `SUPERVISOR_TOKEN` is empty. An admin connection
alone therefore does **not** identify the Supervisor.

**On the supported, established Supervisor connection** (default URL, token taken from
`SUPERVISOR_TOKEN`, add-on runtime):

- The Supervisor WebSocket proxy authenticates the add-on by its token, then connects to
  Core with Supervisor's own Core API client (Supervisor
  `supervisor/api/proxy.py:283-352`; `supervisor/homeassistant/api.py:220-245`).
- Core creates or reuses one admin-group, `system_generated` Supervisor user
  (`homeassistant/components/hassio/__init__.py:201-246`). It is created without
  credentials (`homeassistant/auth/__init__.py:266-280`).
- Core 2026.11.0 authenticates the dedicated Supervisor Unix socket directly as that user
  (`homeassistant/components/http/auth.py:189-213`;
  `homeassistant/components/websocket_api/http.py:401-404`,
  `homeassistant/components/websocket_api/auth.py:71-83`). Over TCP, Supervisor uses
  its own refresh token for the same user.

So, on that path only, "connection user" commands (`auth/current_user`,
`auth/refresh_tokens`) describe the Supervisor system user, not Josh or any household
member.

**Identity rule for the new tool** (corrects finding M3):

| Condition | `connection_identity.kind` |
|---|---|
| Established Supervisor connection **and** Core `auth/current_user` corroborates it (`is_admin: true`, `credentials: []`; and, if the `users` section is also read, the current id matches a `system_generated` user, compared in-process only) | `supervisor_system_user` |
| `HA_URL` and/or `HA_TOKEN` alternative in use, and `auth/current_user` succeeds | `other_connection_user` (with `is_admin` true/false; no name or id) |
| Topology established but corroboration fails, `auth/current_user` fails, or the token source cannot be established | `unknown` |

Implementation note: `configuration.py` does not currently record which variable
supplied the token. The build must record the token source, without the token value,
so that this rule can be evaluated. That is build scope, not a change made here.

Consequences:

- MFA enrolment is never labelled "Supervisor" or "service user" unless the kind is
  `supervisor_system_user` (section 4.2). A personal token's MFA is never reported as
  Supervisor MFA.
- `coverage` and `permission_limits` state the kind, so every connection-user-scoped
  value is read against the right identity.
- `@require_admin` commands are reachable by permission only when the connection user is
  admin. Reachable is not admitted (section 2.6).

### 2.6 Admission rule

Admission rule (HAMCP-222, `docs/HAMCP_222_SIGNED_COVERAGE_AUDIT.md`): a Core command
is admitted only if a signed profile's contract covers that exact use.

A new command, a new field use of an admitted command, or a new adapter is **NOT
ADMITTED** until a new signed profile covers it. Per section 1.1, the new profile is
selected for Core 2026.11.0 only and is denied on every 10.x version.

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
| Bulk exposure listing in the 069B gateway | `governance/entity_availability_exposure.py:113-119` (`NEVER_SENT_COMMANDS` includes `homeassistant/expose_entity/list`, `cloud/status`) | never sent by that gateway; unchanged by this design (section 4.4) |
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

HAMCP-222 §3.8 cites 2026.10.x lines, and some are off by one or two even there (for
example `http/config` as `:52-82`; the 10.x range was `:51-80`). At 2026.11.0 the range
is `:52-81`. This document uses verified 2026.11.0 lines. HAMCP-222 itself is not edited.

## 4. Findings by area

Classification key:

- **E** — feasible via an existing reviewed path;
- **N** — feasible via a new Core read contract (new signed read profile, Core
  2026.11.0 only);
- **S** — technically feasible only via a new Supervisor read (outside the Core
  registry). Feasible is not authorized: each S read needs its own provider review;
- **X** — not provided by the API (or provided only through a path this design
  excludes), so it goes back to Josh.

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
projected fields is a new use and needs the new profile.

### 4.2 MFA modules enabled per user

**Command:** `auth/current_user`

- Source: `homeassistant/components/auth/__init__.py:599-634`.
- Permission: `ws_require_user`.
- Scope: the connection user only.

**Fields returned:**

- `id`, `name`;
- `is_owner`, `is_admin`;
- `credentials: [{auth_provider_type, auth_provider_id}]`;
- `mfa_modules: [{id, name, enabled}]` — every configured module, with `enabled` for
  the connection user.

**No other-user read exists.** No admin command lists another user's MFA enrolment:

- `auth/setup_mfa` (`components/auth/mfa_setup_flow.py:65`) is a write flow.
- `auth/depose_mfa` (`:114`) is a write flow.
- `config/auth/list` carries no MFA field.

**What is observable:**

- the catalog of configured MFA module ids and names (system-wide configuration);
- the `enabled` flag for the **connection user only**. Its meaning depends on the
  connection identity (section 2.5):
  - `supervisor_system_user`: reported as `service_user_enrolled`, labelled as the
    Supervisor system user. It says nothing about people.
  - `other_connection_user` or `unknown`: **withheld** by default
    (`connection_user_enrolment: not_reported`, reason
    `connection_identity_not_supervisor`). Whether to report it is part of remaining
    choice 1 (section 10). It is never labelled Supervisor MFA.

**Classification:**

- **N (reduced)** for the configured MFA module catalog;
- **X** for per-user MFA enrolment.

### 4.3 Session policy, refresh tokens, long-lived token existence

**Source constants, with no runtime API:**

- `ACCESS_TOKEN_EXPIRATION = 30 min`, `homeassistant/auth/const.py:5`;
- `MFA_SESSION_EXPIRATION = 5 min`, `:6`;
- `REFRESH_TOKEN_EXPIRATION = 90 days`, `:7`.

These are defaults. Individual refresh tokens can have expiry changed through
`auth/refresh_token_set_expiry` (`components/auth/__init__.py:798-820`, a write), so
the constants are not proof of effective per-token policy.

**`auth/refresh_tokens`** (`components/auth/__init__.py:668-704`):

- Scope: connection user only.
- Fields: `auth_provider_type`, `client_icon`, `client_id`, `client_name`,
  `created_at`, `expire_at`, `id`, `is_current`, `last_used_at`, `last_used_ip`, `type`.
- It would describe only the connection user, and it carries session identifiers and IP
  addresses. It is **never sent**.

**Credential-minting commands:**

- `auth/long_lived_access_token` (`:637-665`) mints a credential;
- `auth/sign_path` (`:771-795`) mints a credential;
- `auth/delete_refresh_token` (`:707-727`) and `auth/delete_all_refresh_tokens`
  (`:730-768`) are writes.

**No admin read lists other users' refresh or long-lived tokens.** Even their existence
or count is not exposed.

**Classification:**

- **X** for per-user session and long-lived-token existence.
- Session defaults could be reported only as **documentary source constants**, labelled
  "pinned source, not observed". Whether to report them is part of remaining choice 2.

### 4.4 Voice assistant, Assist exposure and conversation-agent authority

#### Per-entity exposure (`conversation`, `cloud.alexa`, `cloud.google_assistant`)

Known assistants: `homeassistant/components/homeassistant/exposed_entities.py:25`.

**Existing path:**

- `ha_get_entity_exposure` is an existing reviewed path. In ha-mcp it uses
  `homeassistant/expose_entity/list` or the `ha_mcp_tools/exposure` bridge.
- **Classification: E.**

**Native command `homeassistant/expose_entity/list`** (`:453-477`):

- Permission: `@require_admin`.
- Behaviour: read-only. It walks both stores (legacy exposed entities and the entity
  registry) and reads each through `async_get_entity_settings` (`:227-244`), which does
  not persist anything.
- Result: `{"exposed_entities": {entity_id: {assistant: true}}}`. An entity/assistant
  pair appears **only when its stored `should_expose` is true** (`:470-472`).

**Coverage (corrects finding M5):** the listing contains stored-true entries only. An
absent entity or assistant means **stored false, or unset (never evaluated)**. The
listing cannot tell those apart. So:

- the projection is `stored_true_count` per assistant, never "exposed" or "not exposed"
  counts;
- coverage is always `partial` with reason `stored_true_only`;
- no output claims complete effective exposure. Effective exposure for unset entities
  depends on `expose_new_entities` and Core's default rules (`:321-360`), which this
  read does not evaluate.

**Do not call `async_should_expose` paths.** `async_should_expose` (`:247-270`)
**writes** a computed default into the entity registry (`:264-267`); the legacy-entity
variant schedules a store save (`:272-299`). Core's own Assist matching calls it
(`helpers/intent.py:602`), which is Core behaviour, not ours. Core 2026.11.0 adds a
non-persisting `async_get_should_expose` (`:301-319`), but no WebSocket read command
exposes it; it is used inside the `homeassistant/expose_entity` write (`:430-450`). So
there is still no read of effective exposure. The tool uses only the listing command and
never triggers evaluation.

**Relation to 069B (finding M5):** 069B design §3.4
(`docs/HAMCP_069B_ENABLE_EXPOSE_DESIGN.md:383-401`) already classifies this listing as a
safe read, and excludes it from 069B because false and unset look the same. The 069B
gateway's `NEVER_SENT_COMMANDS` entry is a per-contract choice for that gateway. A
separate counts-only read in the new profile, with the stored-true caveat, therefore
does not contradict 069B. The 069B signed contract and its never-sent list stay
unchanged.

**`homeassistant/expose_new_entities/get`** (`:480-495`):

- Permission: admin.
- Read-only. One call per assistant: the `assistant` argument is required and is taken
  only from the fixed `KNOWN_ASSISTANTS` tuple.
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
- Arguments: optional `language` and `country`; the tool sends neither.
- Fields: `{id, name, supported_languages}` per agent.

**Classification: N.**

#### Agent authority to control Home Assistant

LLM agents set `ConversationEntityFeature.CONTROL` (`components/conversation/const.py:26-29`)
only when their subentry has an `llm_hass_api` configured. Example:
`components/anthropic/conversation.py:46-50`; other integrations follow the same
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

- config entries are serialized without `data` or `options`
  (`homeassistant/config_entries.py:707-734`);
- `config_entries/subentries/list` (`components/config/config_entries.py:781-809`,
  `@require_admin`) returns `subentry_id`, `subentry_type`, `title`, `unique_id` only.

Starting an options flow to read them is a write-adjacent flow and is refused.

- **Classification: X** for the effective LLM API and tool scope.

#### Effective authority model

Assist intent matching filters targets by assistant exposure:

- constraint `assistant`, `helpers/intent.py:335-336`;
- failure reason `ASSISTANT`, `:257`, `:367`;
- default-agent checks, `components/conversation/default_agent.py:224-230`, `:1075`.

So the observable Assist authority is "which entities are exposed to which assistant".
It is not a per-user permission set.

#### Satellites

**Command:** `assist_satellite/get_configuration`

- Source: `components/assist_satellite/websocket_api.py:76-106`.
- Permission: not admin; one `entity_id` per call.
- Fields: wake words, `pipeline_entity_id`, `vad_entity_id`.

This could map satellites to pipelines. It is optional (remaining choice 3).

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
command (`config_entries.py:707-734`). Another user's refresh tokens cannot be listed
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
- Source: `components/frontend/__init__.py:756-775`; response built by `to_response`
  (`:218-242`), shape `PanelResponse` (`:917-928`).
- Behaviour: an admin connection sees every panel. Each panel reports the **effective**
  `require_admin` and `show_in_sidebar`, after any admin-set override
  (`:765-773`, `:233-241`). Overrides are set by the admin write `frontend/update_panel`
  (`:873-912`), which is never sent.
- Fields: `component_name`, `icon`, `title`, `default_visible`, `config`, `url_path`,
  `require_admin`, `config_panel_domain`, `show_in_sidebar`.

**Meaning:** `require_admin` is the frontend's panel-listing value. It is not proof of
access enforcement. For app ingress panels, Core 2026.11.0 enforces non-admin access
when creating an ingress session from Supervisor's own `panel.admin`
(`components/hassio/websocket_api.py:175-183`), which a frontend override does not
change. The output labels the field `panel_listing_require_admin`.

**Classification: N.**

### 4.6 Ingress, proxy, TLS, and external and internal URL

#### HTTP boundary

**Command:** `http/config`

- Source: `components/http/websocket_api.py:52-81`.
- Permission: `@require_admin`.

**Result:** `{stable, pending, revert_at, active_config_type, default}` (`:74-80`).

**Each config** (`ConfData`, `components/http/config.py:97-114`) carries:

- `server_host` (absent means Core's default bind), `server_port`;
- `ssl_certificate`, `ssl_peer_certificate`, `ssl_key` — **file paths**;
- `cors_allowed_origins`;
- `use_x_forwarded_for`, `trusted_proxies`;
- `login_attempts_threshold`, `ip_ban_enabled`;
- `ssl_profile`, `use_x_frame_options`;
- `created_at`;
- `error` (a code: `apply_failed` or `not_promoted`, `:91-92`) and `error_message`
  (free text, typically an exception string).

**Which slot is running (corrects finding M2).** `active_config_type`
(`ActiveConfigType`, `:117-123`) names the slot the running server was started with:

- `pending`: normal start with a pending config that has no recorded error
  (`async_activate_config`, `:561-580`). A five-minute trial then runs; if it is not
  promoted, the pending config gets `error: not_promoted` and Core restarts onto stable
  (`:436-488`).
- `stable`: no usable pending config, recovery mode, or a pending config that failed to
  apply in this same start (`:597-616`, which records `error: apply_failed` and an
  `error_message` on the pending slot).
- `default`: in recovery mode, after stable could not be applied (`:618-653`). In this
  case the in-memory stable slot can carry an `error` and `error_message` that is not
  persisted (`:623-627`, `:545-559`).
- `default_legacy_port`: in recovery mode under Supervisor, after default could not be
  applied. It is the default config with the previous default port (`:209-214`,
  `:643-653`). **The API does not return this slot**: `default` in the result is the
  ordinary default (`:351-355`).

So the stored config is not automatically the effective one. Stable TLS or proxy values
must never be reported while pending or default is active.

**Projection rule:**

1. `active` is projected from the slot that `active_config_type` selects, and only from
   it:
   - `pending` → `pending`, which must be a mapping with `error` null;
   - `stable` → `stable`;
   - `default` → `default`;
   - `default_legacy_port` → **not returned**: `active` fields are `unknown`, coverage
     `partial`, reason `active_slot_not_returned`. Values are not borrowed from
     `default` and not derived from source constants.
2. If `active_config_type` is missing or not one of the four values, or the selected slot
   is missing, not a mapping, or inconsistent (for example `pending` active but `pending`
   null or carrying an error), `active` is `unknown` with a fixed reason code
   (`active_config_type_unrecognized`, `active_slot_missing`,
   `active_slot_inconsistent`). Another slot is never substituted.
3. `stable` and `pending` may also be projected as **configured observations**, each
   labelled `configured_not_necessarily_active`. They never fill gaps in `active`.
4. `default` is not projected separately; it appears only as `active` when it is the
   active slot.
5. Per-slot projection is identical for `active` and configured observations
   (section 5). `error` is mapped to `none`, `apply_failed`, `not_promoted` or `other`.
   `error_message` is never projected, logged or audited.
6. `revert_at` is projected as `pending_trial_revert_at` (a timestamp) and
   `pending_trial_active` (boolean).

**Storage versus YAML:** HTTP configuration is storage-backed after a one-time YAML
migration (`:217-296`). If YAML is still present after migration, it is ignored and a
repair is raised (`:237-249`). YAML therefore does not override the slots above.

**Writes (never sent):**

- `http/config/configure` (`websocket_api.py:84-147`) stores a pending config and
  **restarts Home Assistant**.
- `http/config/promote` (`:150-175`) is a write.

**Classification: N.**

#### External and internal URL

**Command:** `get_config`

- Source: `components/websocket_api/commands.py:706-717`.
- Permission: not admin. `external_url` is popped for `local_only` users (`:714-715`).
- Data: `homeassistant/core_config.py:687-723`.

The data includes `external_url`, `internal_url`, `latitude`, `longitude`, `elevation`,
`config_dir`, `allowlist_external_dirs`, `allowlist_external_urls`, `location_name`,
and other fields.

Today only `version` is admitted (`profiles.py:178-181`).

If the `external_url` key is absent (a `local_only` connection user), the URL posture is
`unknown` with reason `withheld_for_local_only_user`, not "no external URL".

**Classification: N** for URL posture.

#### Ingress panels of other add-ons

Visible via `get_panels`. The hassio integration registers each panel as
`component_name "app"` with `config {"addon": <slug>}` and `require_admin = <panel.admin>`
(`components/hassio/addon_panel.py:106-123`). After registration an admin override can
change the reported `require_admin` (section 4.5).

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
  app itself (`:227-232`, `:248`).

The existing `supervisor_self` path already projects positively and excludes options.

**Classification: S**, as an optional projection extension.

#### Other add-ons: per-slug ingress metadata versus inventory (corrects finding M4)

Three things are separate:

1. **Per-slug metadata for known panel slugs — technically feasible (S).**
   `GET /addons/<slug>/info` matches the `homeassistant` role's `/.+/info` grant
   (`security.py:121-126`). The response (`apps.py:221-307`) carries ingress fields
   (`ingress`, `ingress_panel`, `ingress_port`, `ingress_entry`, `ingress_url`) and
   capability fields (`hassio_api`, `hassio_role`, `homeassistant_api`, `auth_api`,
   `protected`, `full_access`, `privileged`, `host_network`, and others) independently
   of options. For a caller that is another app without manager/admin role, Supervisor
   returns `options` as `{}` (`:227-232`, `:248`). The slugs come from `get_panels`
   (`config.addon`), so no inventory call and no role escalation is needed. A bounded
   per-slug read with a positive projection that **always excludes `options`** is
   therefore feasible. Feasible does not authorize it: it needs its own Supervisor
   provider review and is optional (remaining choice 3). It is not part of the Core
   profile.
2. **Complete add-on inventory — not available (X).** `/addons` does not match `/.+/info`,
   `/core/.+` or `/homeassistant/.+`, so the role cannot list add-ons. Add-ons without an
   ingress panel are not discoverable on this path. Coverage for any per-slug slice is
   `partial`, reason `panel_slugs_only`.
3. **Other add-ons' options — excluded.** They can carry secrets. Supervisor redacts
   them for our role, and the projection drops them anyway (defense in depth).

**Core's `supervisor/api` forwarder is not reachable on the stated path.** The first
draft said it "would reach `/addons` with Core's Supervisor trust". On Engineering's
supported path that is false. The forwarder exists in Core
(`components/hassio/websocket_api.py:120-172`; options popped only for non-admins,
`:170-171`), but Engineering's WebSocket runs through the Supervisor add-on proxy, and
that proxy rejects every client command whose type starts with `supervisor/` or `hassio/`
before it is forwarded, answering `unauthorized` (Supervisor `api/proxy.py:34-61`,
`:218-255`, `:349-352`). The same filter blocks Core 2026.11.0's new
`supervisor/ingress/info`, `supervisor/ingress/session` and
`supervisor/ingress/validate_session` commands. Engineering still keeps its own local
never-sent prohibition for these prefixes (section 6) as defense in depth, and never
bypasses the proxy (for example by a direct Core connection) to reach them.

#### Engineering's own proxy settings

`trusted_proxy_cidrs`, `trust_cf_connecting_ip`, `mcp_allowed_hosts` and
`mcp_allowed_origins` (`config.yaml:26-50`) belong to Engineering's own Host/Origin
boundary. They are HAMCP-048 scope and out of scope here.

### 4.7 Nabu Casa cloud remote state

**Existing reviewed path:** the `ha_get_system_health` cloud block.

**Kept keys:** `logged_in`, `relayer_connected`, `remote_enabled`, `remote_connected`,
`alexa_enabled`, `google_enabled`, `certificate_status`, `iot_state`, and the
`can_reach_*` checks (`providers/upstream_platform_reads.py:413-417`).

**Already dropped by that allowlist** (source `homeassistant/components/cloud/system_health.py:25-60`,
identical in 2026.11.0):

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
| MFA module catalog | N (reduced) | `auth/current_user` → `mfa_modules[].{id,name}`; connection-user `enabled` only as `service_user_enrolled` when identity is `supervisor_system_user` |
| MFA enrolment per user | **X** | none |
| Session/token policy per user; long-lived token existence | **X** | none; defaults are source constants only |
| Per-entity assistant exposure | E | delegated `ha_get_entity_exposure` |
| Expose-new-entities defaults | N | `homeassistant/expose_new_entities/get` (one call per known assistant) |
| Per-assistant stored-true exposure counts (optional) | N, partial by construction | `homeassistant/expose_entity/list` (counts; stored true only) |
| Complete effective exposure | **X** | no read evaluates defaults without writing |
| Pipelines and preferred pipeline | N | `assist_pipeline/pipeline/list` |
| Conversation agents | N | `conversation/agent/list` |
| Agent may control HA (CONTROL bit) | E | entity state `supported_features` of `conversation.*` |
| Agent's LLM API, tool scope; satellite user authority | **X** | none |
| Companion-app installations | E | delegated `ha_get_integration` |
| Device/tablet/kiosk acting-user authority | **X** | none |
| Panel listing and ingress panels (effective `require_admin`, `show_in_sidebar`) | N | `get_panels` |
| HTTP proxy/TLS/ban boundary of the **active** slot, plus labelled configured slots | N (partial for `default_legacy_port`) | `http/config` (projected) |
| External/internal URL posture | N | `get_config` URL fields (reduced) |
| Core API TLS/port as Supervisor sees it; own ingress | S (optional; separate provider review) | Supervisor `GET /core/info`; `/addons/self/info` projection |
| Per-slug ingress/capability metadata for known panel slugs, options excluded | S (optional; separate provider review) | Supervisor `GET /addons/<slug>/info` |
| Complete add-on inventory; other add-ons' options | **X** / excluded | `/addons` not permitted for the role; options never projected |
| Nabu Casa remote state | E | delegated `ha_get_system_health` |

## 5. Privacy table

Rule: positive projection. Any field not listed is dropped.

**Where raw values exist.** The projected commands necessarily return raw values (user
ids, names, usernames, TLS file paths, CIDRs, origins, hosts, URLs, entity ids, error
text) into Engineering process memory. That is unavoidable for a read. "Reduce" and
"drop" mean: the raw value is used only inside the projection function, then
discarded. It never reaches tool output, audit records, logs, exception messages, error
results, persisted state, or any cache. It is not claimed that raw values are never
read.

| Source command | Field | Decision | How / reason |
|---|---|---|---|
| `config/auth/list` | `id` | reduce | per-response ordinal pseudonym (`user-1`...) by default; the raw Core id is a stable cross-system identifier. Keeping raw ids is remaining choice 1 |
| `config/auth/list` | `username` | drop | login identifier; a credential half |
| `config/auth/list` | `name` | drop (default) | personal display name; "unnecessarily broad identity data" per row note. Keeping it is remaining choice 1 |
| `config/auth/list` | `is_owner`, `is_active`, `local_only`, `system_generated` | keep | role/posture booleans, the purpose of the read |
| `config/auth/list` | `group_ids` | reduce | map to `admin` / `user` / `read_only` for the three `system-*` ids; any other group is counted as `other_group_count`, never named |
| `config/auth/list` | derived `is_admin` | keep | computed with Core's rule (`auth/models.py:92-96`) |
| `config/auth/list` | `credentials[].type` | reduce | sorted set of provider types per user (for example `homeassistant`, `trusted_networks`, `command_line`); no provider ids |
| `auth/current_user` | `mfa_modules[].id`, `.name` | keep | system-wide module catalog |
| `auth/current_user` | `mfa_modules[].enabled` | reduce / withhold | reported only as `service_user_enrolled` when `connection_identity.kind` is `supervisor_system_user`; otherwise withheld (`not_reported`). Never labelled as a person, and never as Supervisor MFA for another identity |
| `auth/current_user` | `is_admin`, `credentials` (emptiness only) | reduce | used in-process to corroborate connection identity (section 2.5); output carries only the identity kind and `is_admin` |
| `auth/current_user` | `id`, `name`, `credentials[]` contents, `is_owner` | drop | identity data; `id` used only for the in-process `system_generated` match when `users` is also read |
| `http/config` (each projected slot) | `ssl_certificate`, `ssl_key`, `ssl_peer_certificate` | reduce | booleans `tls_configured`, `client_cert_required`; paths dropped (filesystem layout) |
| `http/config` (each projected slot) | `ssl_profile`, `use_x_forwarded_for`, `ip_ban_enabled`, `login_attempts_threshold`, `use_x_frame_options`, `server_port` | keep | boundary posture |
| `http/config` (each projected slot) | `trusted_proxies` | reduce | count, plus `includes_loopback`, `includes_private`, `includes_public` booleans; exact CIDRs are network topology. Exact CIDRs are remaining choice 1 |
| `http/config` (each projected slot) | `cors_allowed_origins` | reduce | count only |
| `http/config` (each projected slot) | `server_host` | reduce | `bind: default` when absent; otherwise `bind: explicit`, `explicit_bind_count`, `explicit_bind_includes_wildcard` (`0.0.0.0` or `::`) |
| `http/config` (each projected slot) | `error` | reduce | `none` / `apply_failed` / `not_promoted` / `other` |
| `http/config` (each projected slot) | `error_message`, `created_at` | drop | free text can echo paths or values; timestamp not needed |
| `http/config` | `active_config_type` | keep | selects the `active` slot (section 4.6); unrecognized values map to `unknown` |
| `http/config` | `stable`, `pending` | project as configured observations | labelled `configured_not_necessarily_active`; never substituted for `active` |
| `http/config` | `default` | project only when active | appears only as `active` when `active_config_type` is `default` |
| `http/config` | `revert_at` | keep | `pending_trial_revert_at`, `pending_trial_active` |
| `get_config` | `external_url`, `internal_url` | reduce | per URL: `present`, `scheme`, `https`, `is_nabu_casa_domain` (`*.ui.nabu.casa`), `uses_default_port`; hostnames and paths dropped. Absent `external_url` key → `unknown` (local-only user) |
| `get_config` | `latitude`, `longitude`, `elevation`, `location_name`, `config_dir`, `allowlist_*`, `components`, and the rest | drop | location and filesystem data; not in scope |
| `homeassistant/expose_entity/list` | per-entity map | reduce | per-assistant `stored_true_count` only; coverage always `partial`, reason `stored_true_only`; entity ids never output (detail is available per entity via `ha_get_entity_exposure`). Optional: remaining choice 3 |
| `homeassistant/expose_new_entities/get` | `expose_new` per assistant | keep | posture boolean |
| `assist_pipeline/pipeline/list` | `id`, `name`, `conversation_engine`, `stt_engine`, `tts_engine`, `wake_word_entity`, `language`, `prefer_local_intents`, `preferred_pipeline` | keep | engine and agent ids are entity or integration ids, not secrets; `name` is capped and email-masked |
| `assist_pipeline/pipeline/list` | `tts_voice`, `wake_word_id`, `acknowledge_media_id`, `*_language` | drop | not authority-relevant |
| `conversation/agent/list` | `id`, `name` | keep | `name` capped and email-masked |
| `conversation/agent/list` | `supported_languages` | drop | not authority-relevant |
| `get_panels` | `url_path`, `component_name`, `require_admin` (as `panel_listing_require_admin`), `show_in_sidebar`, `config.addon` (app panels only) | keep | visibility boundary; not an enforcement claim (section 4.5) |
| `get_panels` | `title`, `icon`, `config` (other than `addon`), `default_visible`, `config_panel_domain` | drop / cap | `title` capped; other `config` may carry integration data, so drop |
| `person/list` | everything | not read | `user_id`, `device_trackers` and `picture` are personal and location-linked; a user-to-person mapping is not needed for posture (remaining choice 1) |
| `auth/refresh_tokens` | everything | never sent | session ids, IPs, connection user only |
| Supervisor `GET /addons/<slug>/info` (S, optional, separate review) | `slug`, `state`, `ingress`, `ingress_panel`, `hassio_api`, `hassio_role`, `homeassistant_api`, `auth_api`, `protected`, `full_access`, `host_network`, `privileged` (count) | candidate keep | ingress and capability posture; final field list belongs to that provider review |
| Supervisor `GET /addons/<slug>/info` (S) | `options` | **always drop** | can carry secrets; redacted by Supervisor for our role, dropped anyway |
| Supervisor `GET /addons/<slug>/info` (S) | `ingress_entry`, `ingress_url`, `hostname`, `ip_address`, `devices`, `network`, descriptions, and the rest | drop | ingress URLs embed the ingress token path; network and device data are topology |

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
- `frontend/update_panel`
- `config_entries/options/flow` and any other flow start
- `person/list`
- every command type starting with `supervisor/` (including `supervisor/api`,
  `supervisor/subscribe`, `supervisor/event`, `supervisor/ingress/info`,
  `supervisor/ingress/session`, `supervisor/ingress/validate_session`) or `hassio/`.
  The Supervisor proxy also blocks these (section 4.6); the local refusal is defense in
  depth
- Supervisor `/addons` (inventory), `/ingress/*`, and every Supervisor path other than a
  fixed, separately reviewed `GET`

Further rules:

- Arguments are fixed. `conversation/agent/list` is sent without `language` or
  `country`; `homeassistant/expose_new_entities/get` only with an `assistant` value from
  `KNOWN_ASSISTANTS`.
- Delegated `ha_manage_pipeline` and `ha_manage_security_policy` (the latter is ha-mcp's
  own feature-flagged policy editor, unrelated to HA security) are not admitted.
- No code path may call `async_should_expose`. It writes registry defaults.

## 7. Proposed bounded read surface (feasible parts only)

### 7.1 Tool and input

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
- `auth/current_user` is sent once per call, whatever the sections, to establish
  `connection_identity` (section 2.5). `config/auth/list` is sent only when `users` is
  requested.

### 7.2 Output, bounded by a fixed byte cap

- `schema_version`, `observed_at`, `core_version` (from `auth_ok`);
- `connection_identity`: `{kind, is_admin, basis}` where `kind` is
  `supervisor_system_user`, `other_connection_user` or `unknown`, and `basis` is a fixed
  code (for example `supervisor_topology_and_core_corroboration`);
- `sections.<name>`: the projected values from section 5. `http_boundary` has `active`
  (with `active_config_type`) and optional `configured.stable` / `configured.pending`;
- `coverage.<name>`:
  - `status`: one of `observed`, `partial`, `not_provided`, `failed`;
  - `commands`: exact Core command names used;
  - `reason_code`: from a fixed enumeration;
- `omissions`: a fixed list naming every **X** area from section 4.8 as
  `not_provided_by_api`, so absence is never silent;
- `permission_limits`: for example `mfa.connection_user_only`,
  `exposure.stored_true_only`, `panels.listing_not_enforcement`,
  `http.default_legacy_port_not_returned`;
- `related_tools`: pointers to `ha_get_system_health` (cloud), `ha_get_entity_exposure`
  (per-entity), and `ha_get_integration` (companion apps). They are not called
  internally.

### 7.3 Failure, error, audit and truncation behaviour

- Sections are independent. A command error, a non-admin connection, an unexpected
  type or an unknown required field marks that section `failed` with a reason code.
  Values are never guessed and never partially projected from a drifted shape.
- Unknown extra fields are dropped by the positive projection.
- `config/auth/list` above `MAX_USERS` (the same bound as `approval_identity.py`) fails
  the section closed.
- An unsupported Core version, or a profile not selected by the signed registry, fails
  the whole tool closed before Core I/O, the same as other runtime-only profiles. That
  includes every 2026.10.x version (section 8).
- **No echo in errors.** Core error results carry free-text `message` fields, and
  exceptions carry text that can include paths, hosts, addresses, user names or values.
  Only the Core error `code` is mapped to a fixed `reason_code`. Error text, exception
  text and `repr` of upstream data never reach tool output, MCP error results, logs or
  audit.
- **No echo in audit.** Audit records the requested sections, section statuses, reason
  codes, command names and counts. It never records projected identity values, the
  pseudonym map, raw ids, names, paths, CIDRs, origins, hosts, URLs, entity ids or
  error text.
- **No echo in truncation.** If the projected output would exceed the byte cap, whole
  list items are removed from the lowest-priority section first, and that section's
  coverage becomes `partial` with reason `output_cap` and a removed-item count. No
  string is cut mid-value, and the truncation marker carries no upstream value.

### 7.4 Public schema impact

- Plus 1 Engineering-native read tool.
- The exact tool count is derived from source at build time, not asserted here.
- No change to existing tool schemas.
- Optional Supervisor `S` items (Core API TLS/port; own ingress projection; per-slug
  ingress metadata) are separate, optional, and need their own provider review. They
  are not part of the Core profile.

## 8. Signing impact

| Profile id | New or changed (from version X) | Core commands / contract added | Why | Fingerprint impact | Signing placement |
|---|---|---|---|---|---|
| `core.security_metadata_read` (HAMCP-222 plan name `core.security_posture_metadata_read`; final id at build) | New (from none) | `auth/current_user` (identity corroboration and `mfa_modules` only); `config/auth/list` (projected per section 5); `http/config` (active-slot projection per section 4.6); `get_config` (URL posture fields only); `homeassistant/expose_new_entities/get`; `homeassistant/expose_entity/list` (stored-true counts only, if remaining choice 3 includes it); `assist_pipeline/pipeline/list`; `conversation/agent/list`; `get_panels` (app, effective `require_admin`, `show_in_sidebar`). The section 6 list goes in the contract's `never_sent`. New `CoreCapabilityClass` value (`ha_core_readmission/models.py:46-77`) | No signed profile admits these commands for a tool read. `get_config` is admitted only for `version` (`profiles.py:178-181`); `auth/current_user` only for the baseline contract (`profiles.py:479-488`); `config/auth/list` only as an unnamed internal approver check | New profile fingerprint. Runtime-only, like `CORE_NOVEMBER_BATCH_PROFILES` (`profiles.py:633-638`, not in `CORE_CAPABILITY_PROFILES`), so the signed probe fingerprint and all existing profile fingerprints stay byte-identical. Existing registry entries are unchanged | **Core 2026.11.0 only**, in the **one combined 2.6 signing** (one `extend-capabilities` extension of the existing 2026.11.0 entry) — Josh direction (register 2026-11-07; confirmed in-session 2026-11-08). No 10.x target. Dormant until that signing selects it |

**Deliberately unchanged:**

- `core.basic_websocket_read` and `core.automation_baseline_metadata_read` are not
  widened. A widened `get_config` or `auth/current_user` contract would silently extend
  existing tools.
- The 069B contract (`core.entity_availability_exposure_operation` gateway) and its
  `never_sent` list are unchanged (section 4.4).
- The existing reviewed paths keep their current profiles:
  - `ha_get_system_health`: basic profiles;
  - `ha_get_entity_exposure`: `core.delegated_device_effective_area`;
  - `ha_get_integration`: basic profiles.
- The Supervisor reads (`S`) are outside the signed Core registry.

No signing was performed.

## 9. Slicing and required implementation evidence

HAMCP-134 is delivered in 2.6.0 (section 1.1). This section gives slicing, not dates.

1. **Slice A — contract and gateway, docs-first:** the profile contract and `never_sent`
   list; exact-11.0 fixtures; negative tests for every section 6 command; drift tests.
2. **Slice B — users, MFA catalog, HTTP boundary, URL posture:** sections `users`,
   `mfa`, `http_boundary`, `urls`.
3. **Slice C — Assist and panels:** sections `assist`, `panels`.
4. **Signing:** the profile enters the single combined 2.6 signing after Slices A–C and
   their evidence are complete.
5. **Optional Slice D:** Supervisor `S` reads, only if Josh selects them (remaining
   choice 3), each with its own provider review.

**Required implementation evidence** (none of this has been run; it is the proof the
build must supply):

- **Exact version (finding M1).** For each admitted command, a fixture generated from
  Core 2026.11.0 source (`74cf5a49`) and disposable-lane evidence against an exact
  2026.11.0 Core: `auth/current_user`, `config/auth/list`, `http/config`, `get_config`,
  `homeassistant/expose_new_entities/get`, `homeassistant/expose_entity/list` (if
  included), `assist_pipeline/pipeline/list`, `conversation/agent/list`, `get_panels`.
  The tool is refused before Core I/O on 2026.10.0, 2026.10.1 and 2026.10.3. The signed
  probe fingerprint, every existing profile fingerprint and every existing registry
  entry are byte-identical before and after.
- **HTTP active slot (finding M2).** Fixtures and lane cases for: active `stable` with
  no pending; active `pending` during a trial (with `revert_at`) where stable and
  pending differ in TLS and trusted proxies, so `active` shows pending values and
  stable is a labelled configured observation; active `stable` with a pending
  `apply_failed` whose `error_message` contains a synthetic path; pending
  `not_promoted`; active `stable` with an error-free pending (recovery mode); active
  `default` with an in-memory stable error; active `default_legacy_port` → `active`
  unknown, coverage partial; active `pending` with `pending` null; active `pending` with
  a pending error; missing or unrecognized `active_config_type`; selected slot of the
  wrong type or with drifted field types. No raw path, CIDR, host, origin or error text
  appears in output, error results, logs or audit.
- **Connection identity (finding M3).** Synthetic or disposable cases (no production
  credential): established Supervisor connection with corroboration →
  `supervisor_system_user`; `HA_URL`/`HA_TOKEN` with an ordinary admin user →
  `other_connection_user`, MFA enrolment withheld, never labelled Supervisor; non-admin
  user → `other_connection_user`, admin-only sections `failed` with `not_admin`;
  unknown identity (corroboration fails, `auth/current_user` fails, token source
  unknown) → `unknown`; `sections=["mfa"]` without `users` → identity from topology plus
  `auth/current_user` only, and `config/auth/list` is not sent.
- **Ingress and forwarder (finding M4), only if Slice D is selected.** Per-slug
  `GET /addons/<slug>/info` projection with `options` excluded even when the fixture
  carries synthetic secret options; `/addons` inventory refused before I/O;
  partial-inventory coverage (`panel_slugs_only`). Independently of Slice D:
  `supervisor/api` and every `supervisor/*` / `hassio/*` type refused before Engineering
  I/O, and shown blocked by the exact Supervisor `40e3ee76` proxy (proxy-level test or
  disposable lane).
- **Exposure (finding M5), if the count read is included.** Entities with stored true,
  stored false and unset settings, plus non-registry (legacy) entities; correct
  per-assistant `stored_true_count` and `partial` coverage; registries byte-unchanged
  after the call; zero `async_should_expose` calls (spy).
- **No echo (error, audit, truncation).** A Core error result with synthetic sensitive
  text in `message`; a transport exception whose text carries a synthetic host and path;
  an over-cap response forcing truncation. In each, output, MCP error results, logs and
  audit contain only fixed reason codes, statuses and counts.
- **Drift tests.** No tool path calls `async_should_expose`; path fields never appear;
  `name` and `username` never appear unless remaining choice 1 selects them.

**Dependencies:**

- No dependency on unlanded 2.5.0 work. None of #384–#387 or HAMCP-290 touches the
  cited files, except shared registry and profile files at build time, where 2.5.0 wins.
- HAMCP-048 is independent.

## 10. Remaining choices — pending Josh

Release placement, Core target and signing batching are settled (section 1.1). The old
OQ-1 (sign dormant in 2.6 or wait for 3.0) is closed by that direction. The other old
questions are grouped into three decisions. The proposal in each is the conservative
default; it is not a decision.

1. **Privacy and detail** (old OQ-3, OQ-5, OQ-8, OQ-10). Keep per-response user
   pseudonyms, trusted-proxy counts and classes, no user-to-person mapping, and MFA as
   catalog plus Supervisor-service-user enrolment only? Or select more: raw Core user
   ids and/or display names, exact trusted-proxy CIDRs, a person mapping, MFA catalog
   only (drop service-user enrolment), or connection-user enrolment for a non-Supervisor
   connection? Proposed: the conservative set.
2. **Coverage** (old OQ-2, OQ-4). Accept the source-bounded omissions in section 4.8
   (per-user MFA enrolment, per-user session/token existence, device/kiosk/satellite
   acting user, agent LLM API and tool scope, complete add-on inventory, complete
   effective exposure) as explicit `not_provided` omissions? Omit session defaults as
   not observed, or report them as labelled documentary constants (30 min, 90 days,
   5 min)? Any non-API source, such as owner-maintained documentation, is a separate
   scope decision. Proposed: accept the omissions; omit the session defaults.
3. **Optional reads** (old OQ-6, OQ-7, OQ-9). Include any of: stored-true exposure
   counts (with the section 4.4 caveat); the satellite-to-pipeline mapping
   (`assist_satellite/get_configuration`, one bounded call per satellite entity);
   separately reviewed Supervisor reads (`/core/info`, `/addons/self/info` ingress
   projection, per-slug `/addons/<slug>/info` for panel slugs)? Proposed: leave them
   out of the first build.

## 11. Security section

- **Read-only.** No new write, service call, reload, restart, deletion, flow start or
  forwarding is reachable. Every adjacent write is named in `never_sent`.
- **`async_should_expose` is excluded.** That read-looking helper writes, and is
  explicitly excluded.
- **Raw values are discarded, not avoided.** The projected commands return raw ids,
  names, usernames, TLS paths, CIDRs, origins, hosts, URLs, entity ids and error text
  into Engineering memory. They are used only by the projection and then discarded.
  They never reach output, audit, logs, errors, persisted state or caches (section 5,
  section 7.3).
- **No credentials or session identifiers.** The commands that return credentials,
  tokens, session identifiers or IP addresses are never sent (section 6). No such value
  is read at all.
- **No escalation and no forwarder.** On Engineering's supported path the Supervisor
  proxy already blocks `supervisor/*` and `hassio/*` commands. Engineering also refuses
  them locally before I/O, and never bypasses the proxy. Other add-ons' options are
  never projected.
- **Accurate identity labelling.** Connection-user-scoped data is labelled with the
  established connection identity. The Supervisor label is used only on the supported,
  corroborated Supervisor connection.
- **Accurate slot labelling.** HTTP posture is reported for the active slot only;
  configured slots are labelled as such; an unestablished active slot is `unknown`.
- **No fallback.** There is no fallback to ha-mcp for this tool. Delegated reads stay on
  their existing reviewed paths and profiles.
- **Metadata visibility only.** This is not a security-policy change. Nothing here
  changes Home Assistant, Engineering, or HAMCP-048 Host/Origin enforcement.
- **Non-actions.** No live Home Assistant access, no deployed endpoint access, no
  credentials read, no protected path edited, no signing.

## 12. Codex design review 2026-11-08 — disposition

Review of `7246f2d2..638510ff` (PR #392). Five findings, all Medium; all corrected in
this revision.

| ID | Severity | Finding | Correction |
|---|---|---|---|
| M1 | Medium | Release placement and exact-version authority: IN 3.0, 10.1/10.3 signing and pending batching | Section 1.1 (Josh direction), 1.3 (superseded history), 2.2 (Core 2026.11.0 pin), 2.3 (per-file rebind table; all Core lines at 11.0), 8 (11.0 only, one combined signing, no 10.x), 9 (exact-11.0 fixture and lane evidence per command; refusal on 10.x; fingerprints preserved), 10 (OQ-1 closed) |
| M2 | Medium | HTTP projection did not represent the effective configuration | Section 4.6 "Which slot is running" and "Projection rule" (active slot from `active_config_type`; configured observations labelled; `default_legacy_port` and malformed shapes → unknown/partial; no borrowing; no path or error echo), section 5 rows, section 9 fixture list |
| M3 | Medium | Fixed Supervisor identity assertion | Section 2.5 (identity rule: Supervisor label only on the established, corroborated connection; `other_connection_user` / `unknown` otherwise; token source recorded at build), 4.2 (enrolment withheld unless Supervisor; never "Supervisor MFA" for a personal token), 5, 7.2, 9 (five identity cases incl. `sections=["mfa"]` without users) |
| M4 | Medium | Per-add-on ingress metadata misclassified; forwarder reachability overstated | Section 4.6 "Other add-ons" (per-slug `/addons/<slug>/info` = S, technically feasible, options excluded, separate provider review; inventory X; options excluded; forwarder blocked by Supervisor proxy, local never-sent kept as defense in depth), 4.8, 5, 6, 9, 11 |
| M5 | Medium | Exposure absence treated as "not stored" | Section 4.4 (stored true only; absent = false or unset; no complete-effective-exposure claim; 069B §3.4 reconciliation; 069B contract unchanged), 4.8, 5, 8, 9 |
| — | — | Add error/audit/truncation no-echo cases; reconcile prose with raw values inside Engineering | Section 5 "Where raw values exist", 7.3, 9 (no-echo cases), 11 |
| — | — | Remaining Josh questions reduced to three groups | Section 10 |

Also corrected while rebinding to 2026.11.0 (not separately raised): `get_panels`
reports the effective, override-adjusted `require_admin` and `show_in_sidebar`, and
`frontend/update_panel` joins `never_sent` (section 4.5); `get_config` absence of
`external_url` for local-only users is `unknown`, not absent (section 4.6).
