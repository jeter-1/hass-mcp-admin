# HAMCP-218: What the Supervisor `manager` role unlocks (read-only)

Status: read-only investigation and decision input for Josh. This document
changes no runtime code, add-on manifest, add-on option, role, permission,
workflow, registry, release declaration, or stable-v1 file.

Owner direction (Josh): keep `manager` **ungranted** for now and get the facts
first. Nothing here grants it or recommends granting it now.

Related: HAMCP-196 (`docs/HAMCP_196_ADDON_PERMISSIONS_REVIEW.md`), HAMCP-131
(PR #295, `docs/HAMCP_131_INCIDENT_LOGS.md` on `agent/HAMCP-131-incident-logs`),
and HAMCP-151 (add-on restart, owner-deferred).

## Answer first

- **The role check is one regex over the request path.** It ignores the HTTP
  method. No role or setting allows only reads.
- **`manager` adds 163 Supervisor API routes** beyond `homeassistant`:
  73 reads (`GET`) and 90 writes or control actions. Of the writes, 46 are
  flagged high impact. Among them:
  - add a store repository and install any add-on from it (arbitrary code);
  - uninstall add-ons, or change their options;
  - restore, upload, download or delete backups;
  - change OS, network, Docker and security settings, update the OS, or move
    the data disk;
  - reboot or shut down the host;
  - update, restart or change the options of the Supervisor itself.
- **`manager` also exposes other add-ons' secrets.** A manager-role add-on
  reading `/addons/<slug>/info` gets the other add-on's options without
  redaction.
- **There is no narrower Supervisor role** that grants only the read-only
  subset. `backup` is narrower but is backups only, including restore. There
  are no per-endpoint grants.
- **The narrower read-only mechanism that does exist is the add-on option
  `journald: true`.** It bind-mounts the host systemd journal read-only. The
  Supervisor's own host, Supervisor and add-on log endpoints read that same
  journal. It grants no API authority.
- **Using it needs authorized protected-path changes**: `config.yaml`, and
  possibly the Dockerfile for a journal reader. It also needs exact-HAOS
  verification of what the journal contains.
- **Much of the control `manager` would add is already reachable today, without
  `manager`, through Core.**
  - Every add-on request to Core through the Supervisor proxy runs as Core's
    `Supervisor` system user, which is in the admin group.
  - The proxy does not block `call_service` for `hassio.*` admin services or
    `update.install`.
  - Engineering does not expose this. Its generic service dispatch is closed,
    and plans that contain `hassio.*` actions are classified elevated.
  - It is still latent authority held by the add-on token.
- **Engineering's own logs and stats need no new grant.** The add-on can read
  `/addons/self/logs` and `/addons/self/stats` today through the `self` bypass.
  The same bypass would also let it restart, stop or uninstall itself.
- **Recommendation:** stay on **(A) `homeassistant`** now. If Engineering needs
  host, Supervisor or other add-on logs, pursue **(C) `journald: true`** as a
  separately scoped protected-path change. Do **not** choose (B): typed
  read-only tools would not reduce what the token itself can do.

## Revisions examined

| Item | Value |
| --- | --- |
| Base (`origin/main`) | `3513ba3ccc75bf3eb2c3cb30ac975a3216b9c643` |
| Engineering version (`hass_mcp_engineering_beta/config.yaml`) | `2.4.1-beta.29`; `hassio_api: true`, `hassio_role: homeassistant`, `homeassistant_api: true` |
| Supervisor source | `home-assistant/supervisor` 2026.09.1, commit `40e3ee7640a3c44abe67f1a1397f39c3cd949806`. The same pin is used by HAMCP-131 and HAMCP-196. `supervisor/api/middleware/security.py` matches the hash in `tests/fixtures/supervisor_core_logs_2026_09_1/provenance.json` |
| Core source | `home-assistant/core` 2026.10.1, commit `abe1fdd7cd6b4de1715c87b6fd87d92699514ef9` (the HAMCP-131 Core pin). Sparse checkout of `hassio`, `http`, `websocket_api`, `repairs`, `system_health`, `system_log`, `logger`, `helpers/service.py` |

All citations below are `file:line` in those exact revisions. Supervisor paths
are relative to the Supervisor repository root, and Core paths to
`homeassistant/components/` unless a path says otherwise. No live Home
Assistant, Supervisor, deployed MCP endpoint, or credential was accessed.

### Method for the delta

The route inventory was taken mechanically, not by hand:

1. A local script parsed every `web.get/post/put/delete` registration in
   `supervisor/api/__init__.py` with the Python AST.
2. It replaced each path placeholder with a concrete sample value.
3. It evaluated each path against the exact `security.py` V1 regexes for each
   role. The regexes were executed from the pinned source with stubbed imports
   and `RE_SLUG = [-_.A-Za-z0-9]+`.
4. Routes registered only for the V2 API are excluded, because V2 is off by
   default (`supervisor/api/__init__.py:109-112`).
5. Duplicate (method, path) pairs were removed. Resolution routes are
   registered under both V1 and V2 branches.

The result is 163 unique routes that `manager` allows and `homeassistant`
denies. The extractor itself is not committed. It is investigation tooling,
and the table below is its complete output.

## 1. How roles are enforced

| Fact | Source |
| --- | --- |
| Five roles: `default`, `homeassistant`, `backup`, `manager`, `admin` | `supervisor/const.py:447-453` |
| An add-on's role is one value from that set (default `default`). There is no per-endpoint or custom grant | `supervisor/apps/validate.py:518` (`vol.In(ROLE_ALL)`) |
| A role is one compiled path regex | `supervisor/api/middleware/security.py:115-165` |
| `homeassistant` = `/.+/info`, `/core/.+`, `/homeassistant/.+` | `security.py:121-127` |
| `backup` = `/.+/info`, `/backups.*` | `security.py:128-133` |
| `manager` = `/.+/info` plus 25 area prefixes. Exclusions: `/addons/<slug>/security`, `/os/datadisk/wipe`, `/os/ssh/authorized_keys` | `security.py:134-163`; add-ons `:137`, OS `:154` |
| `admin` = `.*` | `security.py:164` |
| Check order: (1) add-on token; (2) `api_bypass` (`/addons/self/...`, `/info`, `/services*`, `/discovery*`, `/auth`) passes even without `hassio_api`; (3) otherwise `hassio_api` is required and `role_access[role].match(request.path)` decides | `security.py:99-108`, `:365-376` |
| **The method is not part of the check.** A role admitting a path admits every method registered on it | `security.py:370` (`match(request.path)` only) |
| `core_only` paths (`/addons/<slug>/sys_options`, `/os/ssh/authorized_keys`) are never add-on reachable. `BLACKLIST` (including `/core/api/hassio...`) applies to all callers | `security.py:109-114`, `:45-50` |
| Protection mode affects Docker privileges, not API role | `supervisor/docker/app.py:292`, `:362`, `:620`, `:678` |
| Security rating: `manager` −1, `admin` −2 | `supervisor/apps/utils.py:74-78` |

Handler-level effects that the path regex does not show:

| Effect | Source |
| --- | --- |
| `/addons/<other>/info` returns the other add-on's **options unredacted** when the caller's role is `manager` or `admin`. Options may contain secrets | `supervisor/api/apps.py:221-232` |
| `/addons/<slug>/options/config` refuses every caller except the add-on itself, even `manager` | `supervisor/api/apps.py:421-425` |
| An add-on cannot update itself through the store | `supervisor/api/store.py:259-260` |
| The cloud backup location is Core-only | `supervisor/api/backups.py:320-334` |

## 2. What `homeassistant` already reads (no change)

`/.+/info` is in every role (`security.py:123`). Today's token can therefore
already read:

| Endpoint | Content (handler) |
| --- | --- |
| `GET /supervisor/info` (`api/__init__.py:570`) | Installed add-ons with name, slug, version, latest version, update available, state and repository, plus repositories (`supervisor/api/supervisor.py:121-143`) |
| `GET /host/info` (`api/__init__.py:231`) | Disk total/used/free, disk life time, boot time, kernel, OS, hostname, time sync, virtualization, agent and AppArmor versions (`supervisor/api/host.py:161-190`) |
| `GET /resolution/info` (`api/__init__.py:483`) | Unhealthy and unsupported reasons, issues, suggestions, checks (`supervisor/api/resolution.py:101-120`) |
| `GET /backups/info`, `GET /backups/<slug>/info` (`api/__init__.py:838-840`) | Backup list and metadata (`supervisor/api/backups.py:266-271`) |
| `GET /addons/<slug>/info` (`api/__init__.py:737`) | Per add-on detail, **options redacted** for `homeassistant` (`apps.py:221-232`) |
| `GET /jobs/info`, `/os/info`, `/network/info`, `/docker/info`, `/hardware/info`, `/dns/info`, `/audio/info`, `/cli/info`, `/observer/info`, `/multicast/info` | Component info |
| `/core/.+` and `/homeassistant/.+` | Core logs and stats, plus Core lifecycle. HAMCP-196 records the latent `/core/restart`, `/core/stop`, `/core/update`, `/core/options` authority |
| `/addons/self/<one segment>` through `api_bypass` (`security.py:101`) | Its own `info`, **`logs`** and **`stats`** (`api/__init__.py:751-752`). Also its own `start`, `stop`, `restart`, `rebuild`, `uninstall`, `options` and `stdin`, since the handlers resolve `self` to the caller (`apps.py:159-168`, `:466-510`). Multi-segment paths such as `/addons/self/logs/follow` do not match the bypass and are denied |

Engineering today calls only `GET /core/logs` (`providers/core_logs.py:31`) and
`GET /addons/self/info` (`providers/supervisor_self.py:196-200`). It calls no
Supervisor write endpoint.

## 3. Delta: everything `manager` allows that `homeassistant` does not

Summary by area. READ is `GET`; everything else is WRITE/CONTROL. Line numbers
are `supervisor/api/__init__.py`.

| Area | READ | WRITE/CONTROL | Notable writes |
| --- | ---: | ---: | --- |
| host | 12 | 4 | reboot L267, shutdown L268, options L270 |
| network | 1 | 3 | interface update L293 (lockout risk), VLAN L301 |
| time | 0 | 1 | options L316 |
| os | 6 | 8 | update L328, data disk move L332, boot slot L335, RPi firmware update L351 |
| security | 0 | 2 | options L369, integrity L370 |
| jobs | 1 | 3 | options/reset L382-383 |
| cli / observer / multicast | 8 | 4 | plugin updates and restart |
| hardware | 1 | 0 | — |
| root | 1 | 1 | `refresh_updates` L467 |
| supervisor | 7 | 5 | update/reload/restart/options/repair L572-576 |
| resolution | 1 | 6 | apply suggestion L515, run check L488 |
| auth | 0 | 1 | `DELETE /auth/cache` L541 |
| addons | 10 | 11 | install L1039, uninstall L738, options L742, stdin L749, start/stop/restart L739-741 |
| backups | 2 | 10 | restore full/partial L872/L841, upload L870, delete L871; **download L873 is a read that returns every secret in the backup** |
| dns | 6 | 4 | options/reset L941-943 |
| audio | 6 | 9 | — |
| mounts | 1 | 5 | create/change/delete L990-992 |
| store | 9 | 8 | **add repository L1094** and install L1021-1022 (arbitrary code) |
| docker | 1 | 5 | registry credentials L1123, options L1121, reset storage L1125 |
| **Total** | **73** | **90** | 46 flagged HIGH |

Still denied even to `manager`:

- `/addons/<slug>/security` (`security.py:137`, `api/__init__.py:750`);
- `/os/datadisk/wipe` (`security.py:154`, `api/__init__.py:334`);
- `core_only` paths;
- `/auth`, `/auth/list`, `/auth/reset`, `/ingress/session`, `/ingress/panels`;
- `/discovery*` and `/services*`, which are Core-only in their handlers;
- `/reload_updates`.

### Full route table

The last column marks writes that the add-on token can already cause today
without `manager`, through the Core proxy (see section 4.3). The source for
that column is Core 2026.10.1 `hassio/services.py`, `hassio/update.py` and
`hassio/switch.py`.

| Method | Path | `api/__init__.py` | Class | Flag | Already reachable via Core as admin `Supervisor` user |
| --- | --- | --- | --- | --- | --- |
| GET | `/host/logs` | L236 | READ | sensitive read |  |
| GET | `/host/logs/follow` | L240 | READ | sensitive read |  |
| GET | `/host/logs/identifiers` | L244 | READ | sensitive read |  |
| GET | `/host/logs/identifiers/{identifier}` | L245 | READ | sensitive read |  |
| GET | `/host/logs/identifiers/{identifier}/follow` | L246 | READ | sensitive read |  |
| GET | `/host/logs/boots` | L250 | READ | sensitive read |  |
| GET | `/host/logs/boots/{bootid}` | L251 | READ | sensitive read |  |
| GET | `/host/logs/boots/{bootid}/follow` | L255 | READ | sensitive read |  |
| GET | `/host/logs/boots/{bootid}/identifiers/{identifier}` | L259 | READ | sensitive read |  |
| GET | `/host/logs/boots/{bootid}/identifiers/{identifier}/follow` | L263 | READ | sensitive read |  |
| POST | `/host/reboot` | L267 | WRITE/CONTROL | **HIGH** | hassio.host_reboot/host_shutdown (`services.py:333-358`) |
| POST | `/host/shutdown` | L268 | WRITE/CONTROL | **HIGH** | hassio.host_reboot/host_shutdown (`services.py:333-358`) |
| POST | `/host/reload` | L269 | WRITE/CONTROL |  |  |
| POST | `/host/options` | L270 | WRITE/CONTROL | **HIGH** |  |
| GET | `/host/services` | L271 | READ |  |  |
| GET | `/host/disks/{disk}/usage` | L272 | READ |  |  |
| POST | `/network/reload` | L289 | WRITE/CONTROL |  |  |
| POST | `/network/interface/{interface}/update` | L293 | WRITE/CONTROL | **HIGH** |  |
| GET | `/network/interface/{interface}/accesspoints` | L297 | READ |  |  |
| POST | `/network/interface/{interface}/vlan/{vlan}` | L301 | WRITE/CONTROL | **HIGH** |  |
| POST | `/time/options` | L316 | WRITE/CONTROL |  |  |
| POST | `/os/update` | L328 | WRITE/CONTROL | **HIGH** | update.install on OS update entity (`update.py:282-286`) |
| GET | `/os/config/swap` | L329 | READ |  |  |
| POST | `/os/config/swap` | L330 | WRITE/CONTROL |  |  |
| POST | `/os/config/sync` | L331 | WRITE/CONTROL | **HIGH** |  |
| POST | `/os/datadisk/move` | L332 | WRITE/CONTROL | **HIGH** |  |
| GET | `/os/datadisk/list` | L333 | READ |  |  |
| POST | `/os/boot-slot` | L335 | WRITE/CONTROL | **HIGH** |  |
| GET | `/os/boards/green` | L345 | READ |  |  |
| POST | `/os/boards/green` | L346 | WRITE/CONTROL |  |  |
| GET | `/os/boards/raspberrypi/firmware` | L347 | READ |  |  |
| POST | `/os/boards/raspberrypi/firmware/update` | L351 | WRITE/CONTROL | **HIGH** |  |
| GET | `/os/boards/yellow` | L355 | READ |  |  |
| POST | `/os/boards/yellow` | L356 | WRITE/CONTROL |  |  |
| GET | `/os/boards/{board}` | L357 | READ |  |  |
| POST | `/security/options` | L369 | WRITE/CONTROL | **HIGH** |  |
| POST | `/security/integrity` | L370 | WRITE/CONTROL | **HIGH** |  |
| POST | `/jobs/options` | L382 | WRITE/CONTROL |  |  |
| POST | `/jobs/reset` | L383 | WRITE/CONTROL |  |  |
| GET | `/jobs/{uuid}` | L384 | READ |  |  |
| DELETE | `/jobs/{uuid}` | L385 | WRITE/CONTROL |  |  |
| GET | `/cli/stats` | L400 | READ |  |  |
| POST | `/cli/update` | L401 | WRITE/CONTROL |  |  |
| GET | `/observer/stats` | L418 | READ |  |  |
| POST | `/observer/update` | L419 | WRITE/CONTROL |  |  |
| GET | `/multicast/stats` | L436 | READ |  |  |
| POST | `/multicast/update` | L437 | WRITE/CONTROL |  |  |
| POST | `/multicast/restart` | L438 | WRITE/CONTROL |  |  |
| GET | `/multicast/logs` | L441 | READ | sensitive read |  |
| GET | `/multicast/logs/boots/{bootid}` | L441 | READ | sensitive read |  |
| GET | `/multicast/logs/boots/{bootid}/follow` | L441 | READ | sensitive read |  |
| GET | `/multicast/logs/follow` | L441 | READ | sensitive read |  |
| GET | `/multicast/logs/latest` | L441 | READ | sensitive read |  |
| GET | `/hardware/audio` | L453 | READ |  |  |
| POST | `/refresh_updates` | L467 | WRITE/CONTROL |  |  |
| GET | `/available_updates` | L468 | READ |  |  |
| GET | `/supervisor/available_updates` | L472 | READ |  |  |
| POST | `/resolution/check/{check}/options` | L484 | WRITE/CONTROL |  |  |
| POST | `/resolution/check/{check}/run` | L488 | WRITE/CONTROL |  |  |
| GET | `/resolution/issue/{issue}/suggestions` | L491 | READ |  |  |
| POST | `/resolution/suggestion/{suggestion}` | L515 | WRITE/CONTROL | **HIGH** |  |
| DELETE | `/resolution/suggestion/{suggestion}` | L519 | WRITE/CONTROL |  |  |
| DELETE | `/resolution/issue/{issue}` | L523 | WRITE/CONTROL |  |  |
| POST | `/resolution/healthcheck` | L527 | WRITE/CONTROL |  |  |
| DELETE | `/auth/cache` | L541 | WRITE/CONTROL |  |  |
| GET | `/supervisor/stats` | L571 | READ |  |  |
| POST | `/supervisor/update` | L572 | WRITE/CONTROL | **HIGH** | update.install on Supervisor update entity (`update.py:355`) |
| POST | `/supervisor/reload` | L573 | WRITE/CONTROL |  |  |
| POST | `/supervisor/restart` | L574 | WRITE/CONTROL | **HIGH** |  |
| POST | `/supervisor/options` | L575 | WRITE/CONTROL | **HIGH** |  |
| POST | `/supervisor/repair` | L576 | WRITE/CONTROL | **HIGH** |  |
| GET | `/supervisor/logs` | L609 | READ | sensitive read |  |
| GET | `/supervisor/logs/follow` | L610 | READ | sensitive read |  |
| GET | `/supervisor/logs/latest` | L614 | READ | sensitive read |  |
| GET | `/supervisor/logs/boots/{bootid}` | L618 | READ | sensitive read |  |
| GET | `/supervisor/logs/boots/{bootid}/follow` | L619 | READ | sensitive read |  |
| GET | `/addons` | L736 | READ |  |  |
| POST | `/addons/{app}/uninstall` | L738 | WRITE/CONTROL | **HIGH** |  |
| POST | `/addons/{app}/start` | L739 | WRITE/CONTROL | **HIGH** | hassio.app_*/addon_* (`services.py:246-330`); add-on switch (`switch.py:48-86`, disabled by default) |
| POST | `/addons/{app}/stop` | L740 | WRITE/CONTROL | **HIGH** | hassio.app_*/addon_* (`services.py:246-330`); add-on switch (`switch.py:48-86`, disabled by default) |
| POST | `/addons/{app}/restart` | L741 | WRITE/CONTROL | **HIGH** | hassio.app_*/addon_* (`services.py:246-330`); add-on switch (`switch.py:48-86`, disabled by default) |
| POST | `/addons/{app}/options` | L742 | WRITE/CONTROL | **HIGH** |  |
| POST | `/addons/{app}/options/validate` | L744 | WRITE/CONTROL |  |  |
| GET | `/addons/{app}/options/config` | L747 | READ |  |  |
| POST | `/addons/{app}/rebuild` | L748 | WRITE/CONTROL | **HIGH** |  |
| POST | `/addons/{app}/stdin` | L749 | WRITE/CONTROL | **HIGH** | hassio.app_*/addon_* (`services.py:246-330`); add-on switch (`switch.py:48-86`, disabled by default) |
| GET | `/addons/{app}/stats` | L751 | READ |  |  |
| GET | `/addons/{app}/logs` | L752 | READ | sensitive read |  |
| GET | `/addons/{app}/logs/follow` | L753 | READ | sensitive read |  |
| GET | `/addons/{app}/logs/latest` | L757 | READ | sensitive read |  |
| GET | `/addons/{app}/logs/boots/{bootid}` | L761 | READ | sensitive read |  |
| GET | `/addons/{app}/logs/boots/{bootid}/follow` | L762 | READ | sensitive read |  |
| GET | `/backups` | L837 | READ |  |  |
| POST | `/backups/new/partial` | L839 | WRITE/CONTROL |  | hassio.backup_full/partial (`services.py:380-427`) |
| POST | `/backups/{slug}/restore/partial` | L841 | WRITE/CONTROL | **HIGH** | hassio.restore_full/partial (`services.py:428-457`) |
| POST | `/backups/options` | L865 | WRITE/CONTROL |  |  |
| POST | `/backups/reload` | L866 | WRITE/CONTROL |  |  |
| POST | `/backups/freeze` | L867 | WRITE/CONTROL |  |  |
| POST | `/backups/thaw` | L868 | WRITE/CONTROL |  |  |
| POST | `/backups/new/full` | L869 | WRITE/CONTROL |  | hassio.backup_full/partial (`services.py:380-427`) |
| POST | `/backups/new/upload` | L870 | WRITE/CONTROL | **HIGH** |  |
| DELETE | `/backups/{slug}` | L871 | WRITE/CONTROL | **HIGH** |  |
| POST | `/backups/{slug}/restore/full` | L872 | WRITE/CONTROL | **HIGH** | hassio.restore_full/partial (`services.py:428-457`) |
| GET | `/backups/{slug}/download` | L873 | READ | **HIGH (all secrets)** |  |
| GET | `/dns/stats` | L939 | READ |  |  |
| POST | `/dns/update` | L940 | WRITE/CONTROL |  |  |
| POST | `/dns/options` | L941 | WRITE/CONTROL |  |  |
| POST | `/dns/restart` | L942 | WRITE/CONTROL |  |  |
| POST | `/dns/reset` | L943 | WRITE/CONTROL |  |  |
| GET | `/dns/logs` | L947 | READ | sensitive read |  |
| GET | `/dns/logs/boots/{bootid}` | L947 | READ | sensitive read |  |
| GET | `/dns/logs/boots/{bootid}/follow` | L947 | READ | sensitive read |  |
| GET | `/dns/logs/follow` | L947 | READ | sensitive read |  |
| GET | `/dns/logs/latest` | L947 | READ | sensitive read |  |
| GET | `/audio/stats` | L964 | READ |  |  |
| POST | `/audio/update` | L965 | WRITE/CONTROL |  |  |
| POST | `/audio/restart` | L966 | WRITE/CONTROL |  |  |
| POST | `/audio/reload` | L967 | WRITE/CONTROL |  |  |
| POST | `/audio/profile` | L968 | WRITE/CONTROL |  |  |
| POST | `/audio/volume/{source}/application` | L969 | WRITE/CONTROL |  |  |
| POST | `/audio/volume/{source}` | L970 | WRITE/CONTROL |  |  |
| POST | `/audio/mute/{source}/application` | L971 | WRITE/CONTROL |  |  |
| POST | `/audio/mute/{source}` | L972 | WRITE/CONTROL |  |  |
| POST | `/audio/default/{source}` | L973 | WRITE/CONTROL |  |  |
| GET | `/audio/logs` | L977 | READ | sensitive read |  |
| GET | `/audio/logs/boots/{bootid}` | L977 | READ | sensitive read |  |
| GET | `/audio/logs/boots/{bootid}/follow` | L977 | READ | sensitive read |  |
| GET | `/audio/logs/follow` | L977 | READ | sensitive read |  |
| GET | `/audio/logs/latest` | L977 | READ | sensitive read |  |
| GET | `/mounts` | L988 | READ |  |  |
| POST | `/mounts/options` | L989 | WRITE/CONTROL | **HIGH** |  |
| POST | `/mounts` | L990 | WRITE/CONTROL | **HIGH** |  |
| PUT | `/mounts/{mount}` | L991 | WRITE/CONTROL | **HIGH** |  |
| DELETE | `/mounts/{mount}` | L992 | WRITE/CONTROL | **HIGH** |  |
| POST | `/mounts/{mount}/reload` | L993 | WRITE/CONTROL | **HIGH** | hassio.mount_reload (`services.py:487-489`) |
| GET | `/store` | L1005 | READ |  |  |
| GET | `/store/addons` | L1006 | READ |  |  |
| GET | `/store/addons/{app}` | L1007 | READ |  |  |
| GET | `/store/addons/{app}/changelog` | L1010 | READ |  |  |
| GET | `/store/addons/{app}/documentation` | L1013 | READ |  |  |
| GET | `/store/addons/{app}/availability` | L1017 | READ |  |  |
| POST | `/store/addons/{app}/install` | L1021 | WRITE/CONTROL | **HIGH** |  |
| POST | `/store/addons/{app}/install/{version}` | L1022 | WRITE/CONTROL | **HIGH** |  |
| POST | `/store/addons/{app}/update` | L1026 | WRITE/CONTROL | **HIGH** | update.install on add-on update entity (`update.py:175-188`) |
| POST | `/store/addons/{app}/update/{version}` | L1027 | WRITE/CONTROL | **HIGH** |  |
| GET | `/store/addons/{app}/{version}` | L1032 | READ |  |  |
| POST | `/addons/reload` | L1038 | WRITE/CONTROL |  |  |
| POST | `/addons/{app}/install` | L1039 | WRITE/CONTROL | **HIGH** |  |
| POST | `/addons/{app}/update` | L1040 | WRITE/CONTROL | **HIGH** | update.install on add-on update entity (`update.py:175-188`) |
| GET | `/addons/{app}/changelog` | L1043 | READ |  |  |
| GET | `/addons/{app}/documentation` | L1044 | READ |  |  |
| POST | `/store/reload` | L1088 | WRITE/CONTROL |  |  |
| GET | `/store/repositories` | L1089 | READ |  |  |
| GET | `/store/repositories/{repository}` | L1090 | READ |  |  |
| POST | `/store/repositories` | L1094 | WRITE/CONTROL | **HIGH** |  |
| DELETE | `/store/repositories/{repository}` | L1095 | WRITE/CONTROL | **HIGH** |  |
| POST | `/store/repositories/{repository}/repair` | L1098 | WRITE/CONTROL |  |  |
| POST | `/docker/migrate-storage-driver` | L1117 | WRITE/CONTROL | **HIGH** |  |
| POST | `/docker/options` | L1121 | WRITE/CONTROL | **HIGH** |  |
| GET | `/docker/registries` | L1122 | READ |  |  |
| POST | `/docker/registries` | L1123 | WRITE/CONTROL | **HIGH** |  |
| DELETE | `/docker/registries/{hostname}` | L1124 | WRITE/CONTROL | **HIGH** |  |
| POST | `/docker/reset-storage` | L1125 | WRITE/CONTROL | **HIGH** |  |

## 4. Narrower role or mechanism

### 4.1 No narrower role

None exists. The role set is closed (`supervisor/const.py:447-453`) and
validated with `vol.In(ROLE_ALL)` (`apps/validate.py:518`). Each role is a
path regex with no method term (`security.py:370`). Every role at or above
`backup` admits writes in its areas. Specifically:

- `backup` includes `/backups.*`, which covers restore.
- `manager` is the lowest role that reaches host, Supervisor or other add-on
  logs.

No Supervisor option, add-on option, or per-endpoint grant gives the read-only
subset of `manager`.

### 4.2 `journald: true`: a narrower read-only path to the same logs

| Fact | Source |
| --- | --- |
| Add-on option `journald` (default false) | `supervisor/apps/validate.py:553`; `supervisor/apps/model.py:664-666` |
| Bind-mounts `/var/log/journal` and `/run/log/journal` **read-only** into the add-on | `supervisor/docker/app.py:650-665`; paths at `supervisor/const.py:51-52` |
| Supervisor's host, Supervisor and add-on log endpoints read the same host journal, through systemd-journal-gatewayd | `supervisor/host/logs.py:41`; `api/__init__.py:167-222` (`_register_advanced_logs`) |
| Add-on logs are selected by `SYSLOG_IDENTIFIER` `addon_<slug>` / `app_<slug>` | `supervisor/api/__init__.py:706-713` |
| No security-rating penalty for `journald` | `supervisor/apps/utils.py:25-78` has no `journald` term |

What it does: gives read access to the host journal. It adds no Supervisor API
role, no write endpoint, and no secret un-redaction.

What it costs or leaves open:

- **Protected-path changes.** It needs `hass_mcp_engineering_beta/config.yaml`
  and probably a journal reader in the `Dockerfile` (`journalctl` or a Python
  journal library). Both paths need explicit authorization.
- **Exposure is the whole host journal.** It is not limited to selected units.
  Engineering would need a typed, allow-listed, bounded and redacted reader,
  scoped to the identifiers it needs.
- **Content is unverified.** That the journal holds Supervisor and add-on
  container output depends on HAOS (Docker's journald log driver and journal
  persistence). This was **not verified** at an exact HAOS version. The
  Supervisor reads it that way, which is strong but indirect evidence.
- **Some data stays manager-only via Supervisor.** Per-add-on CPU and memory
  stats are still `manager`-only through the Supervisor (`api/__init__.py:751`).
  The Core alternative is in section 5.

### 4.3 Verified: the Core proxy runs as Core's admin `Supervisor` user

HAMCP-196 marked this "unverified (memory)". It is now verified from source.
The chain:

1. An add-on with `homeassistant_api` reaches Core through the Supervisor proxy
   (`supervisor/api/proxy.py:99-116`).
2. The proxy sends REST traffic with `sys_homeassistant.api.make_request`
   (`proxy.py:119-149`) and opens WebSocket traffic with `connect_websocket`
   (`proxy.py:207-215`).
3. With Core ≥ 2026.4 the Supervisor talks to Core over the Unix socket
   `/run/os/core.sock` (`supervisor/homeassistant/api.py:27-29`, `:70-122`,
   `:128-139`, `:233-234`; `supervisor/const.py:48`). On older Core it uses a
   bearer token from the Supervisor user's refresh token (`api.py:159-207`,
   `:305-307`).
4. Core 2026.10.1 authenticates every Unix-socket request as the
   `DATA_SUPERVISOR_USER`:
   - REST: `http/auth.py:190-224`
   - WebSocket: `websocket_api/http.py:396-399`, `websocket_api/auth.py:71-82`
5. That user is the system user `Supervisor`, created in and migrated to
   `GROUP_ID_ADMIN` (`hassio/__init__.py:196-218`; stored at `:343`).
6. Core admin services check only `user.is_admin`
   (`homeassistant/helpers/service.py:966-990`).
7. The proxy denies only:
   - REST paths `^hassio(/|_)` (`proxy.py:38`, also in the `BLACKLIST`);
   - WebSocket types prefixed `supervisor/` or `hassio/` (`proxy.py:39-62`).

   It does **not** deny `call_service` (`websocket_api/commands.py:266-277`)
   or REST `/api/services/...`.

So the add-on token can already invoke all of these through Core:

- `hassio.addon_*` and `hassio.app_*` start, stop, restart and stdin
  (`hassio/services.py:246-330`);
- `hassio.host_reboot` and `hassio.host_shutdown` (`:333-358`);
- `hassio.backup_full` and `hassio.backup_partial` (`:380-427`);
- `hassio.restore_full` and `hassio.restore_partial` (`:428-457`);
- `hassio.mount_reload` (`:487-489`);
- `update.install` on add-on, OS, Supervisor and Core update entities
  (`hassio/update.py:175`, `:282`, `:355`, `:454`).

Those entities are enabled by default.

Engineering does not expose this path:

- Legacy `call_service` and `reload_domain` always refuse
  (`tools/compatibility.py:666-681`; `providers/routing.py:200-207`).
- The REST route allowlist has no `/services/hassio/...` route
  (`clients/paths.py:51-64`).
- The only direct WebSocket `call_service` is fixed to `input_boolean`
  (`governance/helper_state.py:154-159`).
- Automation, script or dashboard plans that *contain* a `hassio.*` action are
  classified `high_risk_service` / `ELEVATED_ADMIN`, not prohibited
  (`governance/risk.py:14-20`, `:735-741`; `governance/policy.py:72-78`,
  `:803-816`). Home Assistant would run that action later as the automation,
  not through Engineering.

Paths in this list are relative to `hass_mcp_engineering_beta/ha_mcp_engineering/`.

The consequence for this decision: `manager` does not newly enable add-on
restart, host reboot, backup or restore. Those are already latent through Core.
What `manager` newly adds is mainly:

- installing arbitrary code through a store repository;
- uninstalling add-ons;
- reading and writing other add-ons' options and secrets;
- downloading, uploading and deleting backups;
- OS, network, Docker, security, time and mount configuration;
- Supervisor options and restart;
- the log and stats reads.

## 5. Core-side alternatives (no role change)

| Need | Core-side source | Coverage gap |
| --- | --- | --- |
| Core logs | `GET /core/logs` (already used, `homeassistant` role); WebSocket `system_log/list` (`system_log/__init__.py:343-345`, `require_admin`, passes as the admin proxy user) | None for Core. `system_log` holds only recent warning and error records |
| Engineering's own logs and stats | `GET /addons/self/logs`, `/addons/self/stats` through `api_bypass` (`security.py:101`) | None. No streaming (`/follow`) or boot selection; those are multi-segment paths |
| Supervisor logs | Core `/api/hassio/supervisor/logs` exists for admin users (`hassio/http.py:53-67`) but is blocked for add-ons by `BLACKLIST` and the proxy `CORE_API_DENY` (`proxy.py:38`) | **Full gap.** Only `manager` or `journald` |
| Host and system logs | Same Core route; same block | **Full gap.** Only `manager` or `journald` |
| Other add-ons' logs | Same Core route; same block | **Full gap.** Only `manager` or `journald` |
| Add-on inventory, version, update availability | `GET /supervisor/info` (already allowed); Core `update.*` entities (enabled by default) | None |
| Add-on running state | `GET /addons/<slug>/info` (`state`; already allowed); Core `binary_sensor` running entity (`hassio/binary_sensor.py:40`, disabled by default) | None through info. The entity needs enabling |
| Add-on CPU and memory | Core `hassio` sensors (`hassio/sensor.py:81-98`), refreshed every 60 s (`hassio/const.py:128-132`), fetched only for enabled entities (`hassio/coordinator.py:1226+`) | **Partial.** Disabled by default (`sensor.py:83`, `:91`). Enabling them is an entity-registry change in live HA (owner action). Not on demand |
| Host disk, OS, health | `GET /host/info`, `/os/info` (already allowed); WebSocket `system_health/info` (`system_health/__init__.py:177`; `hassio/system_health.py:42-103`) | None material. Disk sensors exist too, disabled by default (`sensor.py:115-155`) |
| Unhealthy, unsupported, issues, suggestions | `GET /resolution/info` (already allowed); Core repairs issues raised by the hassio coordinator (`hassio/coordinator.py:143-157`, `:328-445`), WebSocket `repairs/list_issues` (`repairs/websocket_api.py:86-90`) | Suggestions per issue (`/resolution/issue/<id>/suggestions`, L491) are `manager`. The suggestion list is already inside `/resolution/info` |
| Backup inventory | `GET /backups/info` (already allowed; HAMCP-130 design) | None for metadata. Download is `manager` and is deliberately not wanted |
| Job progress | `GET /jobs/info` (already allowed) | `GET /jobs/<uuid>` is `manager`, but the same jobs appear in `/jobs/info` |

## 6. HAMCP-151 relevance (add-on restart)

What the source shows:

- **`manager` would grant direct restart of any add-on.**
  `POST /addons/<slug>/restart` (`api/__init__.py:741`) and start/stop
  (`:739-740`) are allowed by `security.py:137`. The handler calls
  `app.restart()` with no further caller check (`apps.py:488-493`).
- **No role change is needed to reach add-on restart technically.** Core's admin
  service `hassio.addon_restart` / `hassio.app_restart`
  (`hassio/services.py:246-330`) is reachable through the proxy as the admin
  `Supervisor` user (section 4.3). Whether Engineering ever uses that path is a
  product and security decision. Today Engineering does not.
- **Engineering's own restart is already allowed** through `api_bypass`
  (`POST /addons/self/restart`), which does not apply to other add-ons.
- **HAMCP-151 does not use any of these today.** `create_addon_restart_plan` is
  a governed proposal (`ELEVATED_ADMIN`). Its reviewed apply path delegates to
  ha-mcp `ha_manage_addon` with `{"slug", "action": "restart"}`
  (`providers/operational_lifecycle.py:53`, `:334-348`). It is held:
  - lifecycle is `admitted` only for ha-mcp 7.14.1–8.1.1 in
    `upstream_release_registry.json`;
  - newer catalogs name the tool `ha_manage_app`, so they fail
    `required_tool_missing` (`operational_lifecycle.py:791-794`, `:842-846`).

  How ha-mcp performs the restart was not examined; it is outside this
  repository.
- **Implication:** granting `manager` is **not required** for HAMCP-151. If
  HAMCP-151 resumes, the narrower choices are:
  - re-admit the ha-mcp lifecycle tool under its new name; or
  - add a single-purpose typed restart through the Core `hassio.app_restart`
    admin service, with fixed domain, service and slug.

  Either is a write-path security change that needs its own scope, negative
  tests and review. This document does not recommend either.

## 7. Options

| | (A) Stay on `homeassistant` | (B) `manager` + typed read-only tools only | (C) `journald: true` + typed journal reader |
| --- | --- | --- | --- |
| What changes | Nothing. Optionally add typed reads of already-allowed endpoints: `/supervisor/info`, `/host/info`, `/resolution/info`, `/addons/self/logs` | `hassio_role: manager`; new tools for host, Supervisor and add-on logs and stats; no control tools wired | `journald: true` in `config.yaml`; a journal reader in the image; a typed tool allow-listed to identifiers (`hassio_supervisor`, `addon_<slug>`, host units), bounded and redacted |
| Value | Core logs, own logs and stats, add-on inventory and state, updates, host disk and health, resolution issues, backup metadata. **No** Supervisor, host or other add-on logs | Everything in A, plus Supervisor, host and other add-on logs (with streaming and boot selection), per-add-on stats, `/jobs/<uuid>`, resolution suggestions per issue | Everything in A, plus Supervisor, host and other add-on logs read locally. No stats. No streaming unless built |
| Risk | Unchanged. Latent authority (`/core/restart` etc.; `hassio.*` services through the admin proxy user; own lifecycle through `self`) stays as it is today | **Large residual risk.** Typed tools constrain Engineering's code, not the token. A compromised process, dependency, or a future code path holding `SUPERVISOR_TOKEN` gets the 90 writes (46 HIGH) in section 3: arbitrary add-on install, uninstall, backup download (all secrets), other add-ons' secrets un-redacted in `/addons/<slug>/info`, OS, network and Supervisor changes. Security rating −1 (`apps/utils.py:74-78`) | Small. Read-only bind mount, no new API authority. Exposure is the whole host journal, which may include other add-ons' secrets if they log them. Needs strict allow-listing, bounding and redaction |
| Effort | None to small (typed reads are ordinary feature work) | Medium: role change (protected `config.yaml`), new provider and tools, negative tests proving no control route is reachable, review, release | Medium: `config.yaml` and `Dockerfile` (protected), journal reader dependency, typed provider, redaction tests, exact-HAOS verification |
| Reversibility | — | Role revert plus redeploy | Option revert plus redeploy |

A variant of B, **B′ (separate log-relay add-on holding `manager`)**, would keep
Engineering itself on `homeassistant`. But the relay's token would carry the
same 90-write authority, so it moves the risk rather than removing it. It
also adds a second add-on to supply-chain review. It is not better than C.

### Recommendation

**(A) now.** It matches the owner direction and has no residual-risk change. It
already covers inventory, health, resolution, backup metadata, Core logs and
Engineering's own logs and stats.

**(C) if and when host, Supervisor or other add-on logs are a real need** (for
example HAMCP-131 incident correlation). Before implementing it:

- verify journal contents at the exact deployed HAOS version;
- explicitly authorize the `config.yaml` and `Dockerfile` paths;
- review the identifier allow-list and redaction.

**Not (B).** "Read-only tools" would be an Engineering code property, not a
permission boundary. The Supervisor would honour every `manager` write for
that token. The main value B adds over C is per-add-on stats, and Core's
hassio stats sensors cover that partially without a role change.

Separately, and outside this decision: section 4.3 confirms that the
`homeassistant_api` grant already carries admin-level Core authority. That
includes `hassio.*` admin services and `update.install`. Engineering keeps this
closed in code. That fact belongs in HAMCP-196's latent-authority record and
in any review of new service-dispatch paths.

## 8. Unverified and limits

- **Deployed Supervisor version** was not re-observed. The 2026.09.1 pin comes
  from HAMCP-131 and HAMCP-196 provenance. Supervisor role regexes change
  between releases; re-check at the exact deployed version before acting.
- **Deployed Core version** was not re-observed. Unix-socket authentication
  (section 4.3 step 3) applies to Core ≥ 2026.4. On older Core the bearer-token
  path authenticates the same admin user.
- **HAOS journal contents** (container logs in journald, persistence) were not
  verified at an exact HAOS version.
- **ha-mcp's internal restart mechanism** was not examined.
- **Supervisor V2 API** (feature flag, default off) was not tabulated. Its
  pattern set is `security.py:179-253`.
- The route extractor evaluates the regexes on sample path values. A route
  whose admission depends on the specific slug value would need a direct
  check. The only slug-dependent term found is `(?!security)` at
  `security.py:137`, which is accounted for.

## 9. Non-actions

- No change to `config.yaml`, add-on options, role, permissions, runtime code,
  tests, workflows, registries, release declarations, or stable v1.
- No live Home Assistant, Supervisor, deployed MCP, or credential access.
- `manager` not granted; `journald` not enabled; no tool added.
