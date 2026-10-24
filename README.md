# HA MCP Engineering Server

Engineering **2.4.1-beta.33** adds the read-only `get_configuration_definitions`
tool (HAMCP-132 slice 2). For YAML-defined template, SQL and helper entities
(template, sql, filter, derivative, statistics, group, integration, min_max,
history_stats, threshold, trend, utility_meter) it reports:
- how each is defined in `configuration.yaml`, as an allowlisted projection;
- how the definition was matched to the entity: exact unique ID, a heuristic
  with a stated confidence, ambiguous, or not found;
- why an entity is unavailable, from Core's read-only signals.

It never returns the SQL `db_url`, a credential or a `!secret` name. It
discloses `!include` and packages without following them, and lists UI-created
entries as not readable without a flow. The `ha_mcp_tools` component is not
used. Static tools 66, advertised catalog 94 (95 with the optional YAML
reader). Read the
[2.4.1-beta.33 release notes](docs/V2_4_1_BETA33_RELEASE_NOTES.md) and
[2.4.1-beta.33 acceptance contract](docs/V2_4_1_BETA33_ACCEPTANCE.md).

It builds on beta.32 below.

Engineering **2.4.1-beta.32** corrects `get_incident_logs` entry times
(HAMCP-220). Supervisor 2026.10.3 writes log times in Home Assistant's local
time without an offset, and earlier betas read them as UTC. Each call now
reads Home Assistant's configured zone from Core's `/api/config`, under the
tool's own read lease, before and again after its journal reads; a zone that
changed or cannot be read again withholds what was read. Each page's first
entry takes its exact UTC journal time, which confirms the zone. An unreadable zone reads no journal, so while
Core is down the tool returns no entries; it never assumes the add-on's `TZ`
or UTC. Any zone disagreement makes the read partial with `zone_mismatch`.
DST-ambiguous or nonexistent times are resolved exactly within the request
budget, or reported as uncertain with every candidate. Their surroundings
are marked `ordering_uncertain`, and the read is partial. No tool, schema,
permission or configuration changes; catalog 93 (94). Read the
[2.4.1-beta.32 release notes](docs/V2_4_1_BETA32_RELEASE_NOTES.md) and
[2.4.1-beta.32 acceptance contract](docs/V2_4_1_BETA32_ACCEPTANCE.md).

It builds on beta.31 below.

Engineering **2.4.1-beta.31** ships governed entity enable/disable and voice
assistant exposure **inactive** (HAMCP-069B). The new tool
`create_entity_availability_exposure_plan` proposes one operation (`disable`,
`enable`, `expose` or `unexpose`) over 1-8 exact registered entities, for
Assist or Google Assistant (Alexa refused). Its two resource types need two
new Core capability profiles that the signed registry (sequence 12) does not
reference. Every call is therefore refused with `CoreCapabilityUnavailable` /
`pending_signed_registry_admission`, with no plan stored and nothing
dispatched. `list_capabilities` and health show the same status. The
release activates nothing new; activation and the live canaries wait for a
separate owner-signed registry sequence 13. Static tools 65, advertised
catalog 93 (94 with the optional YAML reader). Read the
[2.4.1-beta.31 release notes](docs/V2_4_1_BETA31_RELEASE_NOTES.md) and
[2.4.1-beta.31 acceptance contract](docs/V2_4_1_BETA31_ACCEPTANCE.md).

It builds on beta.30 below.

Engineering **2.4.1-beta.30** adds two read-only tools (HAMCP-131).
`get_incident_logs` reads one incident window of at most 6 hours from the Core
journal and Engineering's own add-on journal. It merges them in time order,
with per-source coverage, gaps, retention edges, filter skips and a signed
cursor. Supervisor, host and other add-on logs need Supervisor role `manager`.
They are listed as unavailable with that exact reason and zero requests; no
role is added. `get_history_slices` reads entity history continuously, through
the sanitized delegated `ha_get_history` route. It uses fixed 6-hour slices
over at most 7 days and a cursor that fails closed as stale when earlier
history changes. Static tools 64, advertised catalog 92 (93 with the optional
YAML reader). Read the
[2.4.1-beta.30 release notes](docs/V2_4_1_BETA30_RELEASE_NOTES.md) and
[2.4.1-beta.30 acceptance contract](docs/V2_4_1_BETA30_ACCEPTANCE.md).

It builds on beta.29 below.

Engineering **2.4.1-beta.29** makes exact automation and script changes that
touch a protected physical object (lock, alarm panel, valve, water control or
garage cover) actionable instead of prohibited (HAMCP-213 PR 1, policy
`f2-v3`). Engineering compares the before and after physical effects. An
equivalent change needs plan approval only. A changed or undeterminable
effect, and any unlock or disarm, also needs a per-object safety-critical
acknowledgement on the Ingress panel, which dispatch rechecks. Incomplete
execution contracts, and changes past the review bounds, stay prohibited.
Every called script is re-read before approval is consumed and before each
write. No tool, schema or descriptor changes (90 tools, 91 with the optional
YAML reader). Read the
[2.4.1-beta.29 release notes](docs/V2_4_1_BETA29_RELEASE_NOTES.md) and
[2.4.1-beta.29 acceptance contract](docs/V2_4_1_BETA29_ACCEPTANCE.md).

It builds on beta.28 below.

Engineering **2.4.1-beta.28** is a break-fix for Home Assistant Core
authority (HAMCP-215). After a transient verification failure, Core authority
now recovers on its own:
- a bounded, jittered backoff (20 s doubling to a 240 s cap) replaces the
  fixed 300 s retry;
- authority returns only after 2 consecutive complete background
  re-verifications about 15 s apart, or 3 when it flaps; a changed Core
  version, identity, signed-registry authority or reviewed contract, seen by
  any check, restarts the count;
- only a process's very first boot may admit without confirmation; any later
  re-initialization passes the same gate;
- the catalog, the YAML reader and the dependency index then resume.

Every verification failure, retirement, catalog withdrawal, recovery attempt
and restoration is now a bounded WARNING log event. Health's
`home_assistant_core_authority.recovery` reports the recovery state, the last
failure reason and time, and the next attempt. Fail-closed behavior during
recovery is unchanged. After a Core restart or update, Core-backed tools refuse
for about 15–18 s until the confirmation completes. The same wait (about
15 s) follows a changed Core version or signed-registry authority, an
incompatible Core becoming compatible, and a Core not initialized at boot.
Josh accepted these delays.
No tool, schema, permission or descriptor changes (90
tools, 91 with the optional YAML reader). Read the
[2.4.1-beta.28 release notes](docs/V2_4_1_BETA28_RELEASE_NOTES.md) and
[2.4.1-beta.28 acceptance contract](docs/V2_4_1_BETA28_ACCEPTANCE.md).

It builds on beta.27 below.

Engineering **2.4.1-beta.27** adds two read-only tools (HAMCP-132 slice 1).
`get_recorder_settings` reports the recorder's effective settings, each with
its origin: written in `configuration.yaml`, Core's default, or `unknown` with
the reason, such as packages that could supply the key. It also reports
filters, live recorder state, and Core's own "is this entity recorded" answer
beside the computed filter, with `filter_drift` when they disagree. It never
returns `db_url` or secret names. `get_unregistered_entities` lists entities
that have a state but no registry entry, with their platform and config entry.
The definition file is never guessed. Static tools 62, advertised catalog 90
(91 with the optional YAML reader). Read the
[2.4.1-beta.27 release notes](docs/V2_4_1_BETA27_RELEASE_NOTES.md) and
[2.4.1-beta.27 acceptance contract](docs/V2_4_1_BETA27_ACCEPTANCE.md).

It builds on beta.26 below.

Engineering **2.4.1-beta.26** is a dependency security release (HAMCP-217).
It upgrades `certifi` to 2026.10.19 for GHSA-6q48-6c7w-9gw2, removing the
`emSign ECC Root CA - G3` root from the bundle that Engineering's httpx MCP
clients trust. No runtime code, tool, schema or descriptor changes. Read the
[2.4.1-beta.26 release notes](docs/V2_4_1_BETA26_RELEASE_NOTES.md) and
[2.4.1-beta.26 acceptance contract](docs/V2_4_1_BETA26_ACCEPTANCE.md).

It builds on beta.25 below.

Engineering **2.4.1-beta.25** reads script configuration from what Home
Assistant has loaded (HAMCP-212, fast dependency index part 2). The index
build's single WebSocket batch adds two fixed, read-only commands per available
script (`script/config` and `search/related`) and replaces one delegated ha-mcp
read per script. Blueprint, unavailable and pending-write scripts still use the
stored read, each counted with its reason. A failed loaded read is always
reported as partial, and health's `last_build_profile` reports the script
source counts. Script evidence matches beta.24 on the parity fixtures. No tool,
schema, permission or descriptor changes (88 tools, 89 with the optional YAML
reader). Read the
[2.4.1-beta.25 release notes](docs/V2_4_1_BETA25_RELEASE_NOTES.md) and
[2.4.1-beta.25 acceptance contract](docs/V2_4_1_BETA25_ACCEPTANCE.md).

It builds on beta.24 below.

Engineering **2.4.1-beta.24** adds one read-only tool, `get_backup_inventory`
(HAMCP-130). It reports Home Assistant's backups from Core's own backup
manager: per backup its date, size, content scope, protection flag, add-ons,
folders and storage locations; the newest full backup and confirmed off-site
copies; the automatic-backup schedule, retention and last attempt; and health
signals that report `unknown` whenever an input could not be read. It makes two
fixed admin-only Core WebSocket reads and needs no new permission. It cannot
create, restore, delete, download or change backups. The encryption key is
withheld in any letter case; a deliberately transformed copy in a backup name
is an owner-accepted residual (see the release notes). Static tools 60,
advertised catalog 88 (89 with the optional YAML reader). Read the
[2.4.1-beta.24 release notes](docs/V2_4_1_BETA24_RELEASE_NOTES.md) and
[2.4.1-beta.24 acceptance contract](docs/V2_4_1_BETA24_ACCEPTANCE.md).

It builds on beta.23 below.

Engineering **2.4.1-beta.23** builds the dependency index from the automation
configuration Home Assistant has loaded (HAMCP-209). One WebSocket connection
with two fixed, read-only commands per automation replaces one serialized REST
fetch per automation. On a synthetic 113-automation model the build drops from
34.6 s and 116 requests to 0.45 s and 5 requests, and a write during the build
from 2.7 s to 0.32 s. Blueprint instances, unavailable or unreadable entries
and writes Home Assistant has not reloaded keep the stored read, and each
such fallback is counted with its reason. No tool, schema or route changes.
Read the [2.4.1-beta.23 release notes](docs/V2_4_1_BETA23_RELEASE_NOTES.md) and
[2.4.1-beta.23 acceptance contract](docs/V2_4_1_BETA23_ACCEPTANCE.md).

It builds on beta.22 below.

Engineering **2.4.1-beta.22** is a dependency security release (HAMCP-211).
It upgrades `aiohttp` to 3.14.4 for GHSA-2g87-pp6c-29x5, CVE-2026-111063 and
CVE-2026-111064, with the `yarl` 1.25.1 upgrade that 3.14.4 requires. beta.21
was superseded before publication, so beta.22 is the first published beta with
HAMCP-205's administrator verification below.
Read the [2.4.1-beta.22 release notes](docs/V2_4_1_BETA22_RELEASE_NOTES.md) and
[2.4.1-beta.22 acceptance contract](docs/V2_4_1_BETA22_ACCEPTANCE.md).

It builds on beta.21 below.

Engineering **2.4.1-beta.21** (superseded before publication; shipped in
beta.22) requires a Home Assistant administrator on every
Ingress approval page (HAMCP-205). On every review and decision it looks up the
requesting user's role through Home Assistant's read-only `config/auth/list`
and fails closed when the identity is missing or the lookup fails. Inactive
users never decide. The anonymous default approver is removed: an approval
stored under the anonymous principal must be re-approved by planning the change
again. `get_server_health` counts refusals by reason.
Read the [2.4.1-beta.21 release notes](docs/V2_4_1_BETA21_RELEASE_NOTES.md) and
[2.4.1-beta.21 acceptance contract](docs/V2_4_1_BETA21_ACCEPTANCE.md).

It builds on beta.20 below.

Engineering **2.4.1-beta.20** gives every tool one shared sanitizer with
identity-only redaction markers (HAMCP-191). Every rule runs first, then any
untrusted string that still looks like a redaction marker is replaced as a
whole by a collision marker, which closes two leaks where forged marker text
let a secret through. Markers that Engineering produced in the same request
are kept by object identity; stored plans keep beta.19's tolerance on re-read
until HAMCP-207. `get_server_health` counts collisions and refusals, and a
redacted delegated read now reports `partial`. Ordinary data is unchanged.
Read the [2.4.1-beta.20 release notes](docs/V2_4_1_BETA20_RELEASE_NOTES.md) and
[2.4.1-beta.20 acceptance contract](docs/V2_4_1_BETA20_ACCEPTANCE.md).

It builds on beta.19 below.

Engineering **2.4.1-beta.19** verifies slow typed fan and power devices
promptly (HAMCP-192). After dispatch it polls the entity inline for about 10
seconds and verifies on the first match, so a device that reports a few
seconds late is `succeeded_verified` instead of `observing`. The typed entity
lease is 30 s instead of 120 s, so a task that still yields is re-read within
about a minute, and one final read at the evidence deadline decides verified
or manual review. A `lock_conflict` receipt names the blocking task using the
lock check's own predicate, and receipts gain additive `observation_status`
hints. A manual-review hold can be released only on the Ingress page `/typed`,
which shows the requested and freshly read state side by side; the release
records a fresh readback and exactly one audit entry, never dispatches and
keeps the outcome `manual_review_required`.

It builds on beta.18 below.

Engineering **2.4.1-beta.18** makes `get_server_health` much cheaper on
large plan stores (HAMCP-171). Health reuses, in process memory only, the
safety verdict of each unchanged persisted plan record. The verdict is keyed
by filename, inode, size, mtime and a SHA-256 of the parsed bytes, so any
change validates fresh. `get_server_health(deep_storage_audit=true)`
re-validates everything. The 30 s recovery sweep now reads a parent task only
when the child's own status requires it. No persisted format changes.

It builds on beta.17 below.

Engineering **2.4.1-beta.17** makes every Home Assistant REST path safe by
construction (HAMCP-200). The REST transport accepts only a typed `Route`
issued by `rest_path` for a reviewed template in a closed registry; a plain
string is refused before any session or credential exists. Each bound value
passes one guard that refuses dot segments, separators, control characters
and their encoded forms. The transport sends a request only when the
normalized URL equals the template, and it never follows redirects.
`get_automation_config`, `get_entity`, `get_history` and
`automation_reliability_analysis` refuse an invalid identifier as
`invalid_request` without contacting Home Assistant. Automation
configuration IDs no longer accept dots. The compatibility module's
standalone entry point is removed. Before this change a crafted identifier
could make Engineering send token-bearing GET requests to other
HA/Supervisor routes, including any GET-enabled webhook (which can trigger
automations); no POST/PUT/DELETE/PATCH was reachable.

It builds on beta.16, which adds one optional reviewed delegated read,
`ha_config_get_yaml` (HAMCP-162). It reads one mapping-key path of
`configuration.yaml` only:
- every other file, including `secrets.yaml`, and every credential-shaped
  path is refused before any upstream call;
- `!secret`/`!env_var` become reference-name markers;
- credential-shaped keys, connection strings and credential text are redacted
  whole;
- every result discloses that values are ha-mcp's YAML 1.2 parse.

It is admitted only on the exact reviewed ha-mcp 8.4.3 and 8.5.0
descriptors. `ha_list_files` and `ha_read_file` stay unexposed.

Beta.16 builds on beta.15, which closes the remaining HAMCP-189 residuals:
fresh rollback planning acquires its own Core lease before any planning read,
so under degraded Core it is refused before any Home Assistant read; an
observed read counts an upstream attempt only after the Core authorizer
passes; a contract-v3 task whose approval expired before its dispatch intent
ends truthfully as `failed_pre_dispatch` (`plan_expired`) after bounded
retries, with no dispatch; and the gateway audit keeps the exact refusal
reason for empty and non-string tool names. It builds on beta.14
(HAMCP-189 PR A), where a governed
step acquires Core authority before any read that could conclude "already
satisfied", so under degraded Core it is refused before any Home Assistant
read; an unmapped F3 operation is refused with the exact reason
`core_requirement_unmapped`; and cancel is recorded durably before any
provider contact, succeeds during an ha-mcp outage, and is never dispatched by
a later recovery pass. It builds on beta.13's fail-closed Core gateway
(HAMCP-164): a tool with no Core requirement mapping is refused as
`core_requirement_unmapped` before any provider, upstream or Home Assistant
call, and Core independence comes only from a closed, justified allowlist of
exact tool names. It also builds on beta.12's
audit hardening (HAMCP-184): provider
dispositions fail closed and every compiled ha-mcp release declares them
explicitly (ha-mcp 8.2.0 backup and lifecycle become held; all other
admission unchanged, including production 8.5.0 and rollback 8.4.3), F3
re-apply validates the approved plan hash, approvals decided by the anonymous
Ingress fallback principal no longer report `principal_separation_enforced:
true` (reporting only; eligibility unchanged), and the CI runner guard checks
generated matrix runners. It also builds on beta.11's MCP Python SDK 1.30.0 upgrade
for CVE-2026-59951 (HAMCP-188), beta.10's truthful dispatch facts on every
public surface (HAMCP-181), beta.9's truthful entity-registry error results
(HAMCP-183), beta.8's dependency-scoped dashboard write admission (HAMCP-177),
beta.7's delegated platform reads (`ha_get_system_health`, `ha_get_app` and
`ha_get_integration`), beta.6's typed power and fan admission repair, beta.5's
deep-health correction, beta.4's dormant governed
entity-registry metadata administration (`create_entity_metadata_plan`) and
beta.3's exact **ha-mcp 8.6.0** and **Core 2026.10.0** support.
At beta.18 the healthy catalog was **87 tools: 59 native and 28 admitted
delegated reads** (the current count is under *Capabilities and compatibility*). The Core profiles, probe and signed fingerprints, storage
formats and RC1's multidict 6.9.1 security correction are preserved.
See the [release notes](docs/V2_4_1_BETA18_RELEASE_NOTES.md) and
[acceptance contract](docs/V2_4_1_BETA18_ACCEPTANCE.md).
Installing Engineering does not upgrade upstream or complete installed acceptance.

Josh's Engineering installation continues on the development track. The earlier
RC1-to-GA proposal is superseded; this beta uses the same existing technical Beta
installation and public connector, with no GA prerequisite or second installation.

Beta.16's [bounded garage configuration eligibility](docs/HAMCP135_CONFIGURATION_ELIGIBILITY.md)
and beta.15's [dashboard analyzer](docs/DASHBOARD_INTEGRITY_ANALYSIS.md) remain
available, together with beta.1's
[governed configuration inverse](docs/HAMCP135_GOVERNED_ROLLBACK.md).
Completed household D2 evidence remains separate; this release requires no
repeat drill. Restoring the original script also restores its retry weakness,
and saved bytes alone do not prove activation or settled runs. Govee
administration and add-on restart work are not included.
A release milestone does not authorize household operations.

Historically, beta.14 added [native automation-baseline capture](docs/V2_4_0_BETA14_RELEASE_NOTES.md)
for the existing offline comparator. It captures loaded automations, including
those switched off, with canonical IDs, exact configuration hashes, explicit
coverage and a verified registry lineage. Raw configuration bodies are not
retained; frozen continuation pages perform no additional Home Assistant reads.
Its release catalog was **57 static tools plus 25 delegated reads (82 total)**.
The new read requires a separately reviewed signed Core capability; this release
does not activate it automatically. See [capture scope](docs/AUTOMATION_BASELINE_CAPTURE.md)
and the [offline command and format](docs/AUTOMATION_AUDIT_BASELINE.md).

Historically, beta.13 delivered the offline comparator and superseded unpublished
beta.12, with the merged Alarmo responsiveness-test isolation correction. That
correction preserves its fixture and timing limits; it makes no production
latency claim. Legacy baseline identity/fingerprint gaps remain explicit.

Beta.11's [bounded Alarmo configuration inspector](docs/V2_4_0_BETA11_RELEASE_NOTES.md)
reports selected configured membership, modes and delays with explicit partial
coverage. Its separately signed Core extension is retained unchanged. Installing
beta.14 neither grants authority nor proves Alarmo protection.

PyJWT remains at 2.15.0 with hash-locked inputs and security regressions.
Beta.10's [bounded logbook and response processing](docs/LOGBOOK_READS.md)
remain unchanged; no prior acceptance campaign is repeated. Publication,
deployment and installed acceptance remain separate gates.

Beta.9's cold-health correction remains unchanged, including its disclosed
synchronous costs and separately required approval-interval continuity.
See [performance scope](docs/HEALTH_PERFORMANCE.md).

Beta.7's [configuration verification and supplementary re-verification](docs/V2_4_0_BETA7_RELEASE_NOTES.md)
remain available. Standalone trigger-condition IDs verify correctly, and preflight
rejects unverifiable candidates before that operation dispatches. The existing
[`reverify_configuration_task`](docs/CONFIGURATION_REVERIFICATION.md) checks all
approved saved objects of an eligible historical mismatch and writes a dated
supplementary receipt without resending configuration, operating a device,
consuming approval or replacing the original failed outcome. Beta.14 acceptance
does not repeat a completed supplementary observation.
The inherited [beta.6 lock recovery](docs/V2_4_0_BETA6_RELEASE_NOTES.md) preserves
complete unions up to 256 tokens and can settle qualifying retained locks.
**Beta.5 cannot read expanded token records above sixteen; preserve compatible
recovery state and the optional new receipt namespace through any downgrade.**
Beta.5 [bounded automation lifecycle analysis](docs/AUTOMATION_LIFECYCLE_ANALYSIS.md)
remains available, with exact paths and explicit partial coverage. Static
findings do not prove observed failure or restart recovery.
Beta.4 [bounded script-call dependency paths](docs/SCRIPT_CALL_DEPENDENCIES.md)
remain available; neither analysis executes scripts or automations.
The beta.3 [256-leaf dashboard capacity and safe diagnostics](docs/F3_DASHBOARD_WRITE_CONTRACT.md#semantic-capacity-and-safe-diagnostics)
remain, with unchanged approval binding, one-dispatch execution and exact readback.
[Bounded Core log history](docs/CORE_LOG_HISTORY.md) remains available through the
existing native Supervisor reader. Its owner-approved `homeassistant` role grants
broader Core administration to the process; the MCP tool exposes only the fixed log read.
The beta.10 baseline has 55 static tools plus 25 admitted delegated reads (80 total);
published beta.1 retains its original 53/78 inventory. Existing bounded script
dependency evidence is preserved. **2.3.0** remains the last accepted stable release.
Historical plan readability, exact fan/light/switch operations, signed Core
applicability and protected automatic publication retain their existing behavior.
Received Host/Origin enforcement and secret-path authentication remain.
It provides one public MCP endpoint through the existing Nabu Casa
connector, with a unified catalog.
Engineering selects reviewed, admitted ha-mcp or native providers internally.

The installation retains its technical **Beta** identity. Choose **HA MCP
Engineering Server Beta**, directory and slug `hass_mcp_engineering_beta`.
No second installation or migration is required. Historical v1.1.2 in
`hass_mcp_admin/` is frozen and operationally retired; it is neither the
current installation target nor a supported Engineering rollback. Since
HAMCP-203 it is no longer offered by this add-on repository: its add-on
configuration is renamed so the Supervisor does not discover it, and only the
code is kept. See [`hass_mcp_admin/README.md`](hass_mcp_admin/README.md).

Read the [2.4.1-beta.18 release notes](docs/V2_4_1_BETA18_RELEASE_NOTES.md) and
[2.4.1-beta.18 acceptance contract](docs/V2_4_1_BETA18_ACCEPTANCE.md). A source version does
not prove publication, deployment or installed acceptance.

## Install or update Engineering

Read the [inbound policy and migration requirements](docs/INBOUND_SECURITY.md)
before an authorized 2.4.1-beta.29 deployment. MCP aliases beyond the strict loopback
authority defaults and browser Origins need exact configuration. Explicit host
lists replace the defaults. Retain independent management access because invalid
options refuse startup; historical acceptance does not establish this boundary.

1. Use this repository in the Home Assistant add-on store:
   `https://github.com/jeter-1/hass-mcp-admin`.
2. Select **HA MCP Engineering Server Beta**. Update an existing Engineering
   installation in place after publication and authorized deployment. Do not
   install the historical v1 package or move its data into Engineering.
3. Reconcile `mcp_allowed_hosts` and `mcp_allowed_origins` for intended paths
   through the supported add-on interface. Preserve other connection and trust
   settings, keeping access secrets in the operator's private interface.
4. Use the existing public Engineering Nabu Casa connector. MCP remains on port
   `8100`; authenticated, admin-only **HA MCP Approval** ingress uses internal
   port `8110`. The approval panel is not a second MCP endpoint.
5. Verify the final installed artifact, reconnect the client and complete the
   bounded [acceptance checks](docs/V2_4_0_BETA13_ACCEPTANCE.md).

The image repository remains
`ghcr.io/jeter-1/hass-mcp-engineering-beta`, supporting `linux/amd64`,
`linux/arm64` for 2.4.0-beta.13. Published 2.2.0 retains its historical
`linux/arm/v7` image. Home Assistant has retired 32-bit armv7 support; Engineering
requires a supported 64-bit installation. This source change does not migrate or
uninstall an existing system. Existing options, persistent paths, ports, ingress
and technical Beta identities are preserved. Persisted field names and schema
versions remain, but the expanded lock-token range is not backward-reader
compatible; follow the beta.6 recovery contract. The existing header-policy
options retain strict admission defaults; preserve the intended path configuration.

The controlled build introduced in 2.2.1 pins the base image and complete runtime
wheel closure.
See [controlled build inputs](docs/BUILD_INPUTS.md) for architecture checks,
dependency updates, publication verification and their evidence limits.

The [private observer contract](docs/INBOUND_TOPOLOGY_CAPTURE.md) requires
separate source-bound arming, bounded collection and private evidence handling.
Installation does not authorize or start a capture, and observation does not
implement Host/Origin enforcement.

## Capabilities and compatibility

This development source paired with an exactly admitted **ha-mcp 8.4.3, 8.5.0 or 8.6.0** defines
**66 static tools plus 28 delegated reads, 94 total** (95 with the optional
reviewed YAML reader admitted; the six newest static tools are HAMCP-132's
read-only `get_recorder_settings`, `get_unregistered_entities` and
`get_configuration_definitions`, HAMCP-131's read-only `get_incident_logs`
and `get_history_slices`, and HAMCP-069B's
`create_entity_availability_exposure_plan`, refused until a signed registry
admits its profiles). The Core inventory represents
**24 capability profiles** (three dormant, unselected by any signed entry: the
earlier one plus HAMCP-069B's two). Each signed entry selects its exact reviewed reference
set; historical journal sequences do not prove the current installation.
Actual authority and provider admission determine availability. Fresh protocol
enumeration and descriptor comparison establish the catalog; a health count or
cached client inventory alone does not.
`ha_get_operation_status` remains held and absent from ordinary registration.

Engineering supports bounded inspection, dependency/reliability/integrity
analysis, reviewed reads and governed operations. Native capabilities supplement
upstream functionality where deeper Engineering semantics are required.
Provider unavailability grants no fallback authority or arbitrary forwarding.
Dependency analysis adds bounded script configuration evidence through the
admitted internal reader, with canonical renamed/disabled identities and explicit
partial inventory. Script diagnostics do not expand helper execution evidence.
Scene, group, template and dashboard configuration coverage remains unavailable;
dynamic references and script blueprint bodies remain opaque. An analysis with
no detected references does not prove none exist. See
[dependency coverage](docs/ENTITY_DEPENDENCY_ANALYSIS.md).

Installed inspector acceptance requires the actual installed Core version
to be admitted by a valid signed registry, with all 21 applicable profiles
compatible after the separately authorized extension, and an exactly
admitted provider. The 8.6 upgrade has its separate acceptance contract. Record REST/WebSocket identity agreement, the observed Core
version, registry sequence and authority state in each acceptance receipt;
beta.13 adds no installed-Core version pin. Reproducible CI lanes keep
their explicit version pins and prove only their declared scenarios.
Core 2026.9.2 retains its immutable 17-reference entry and historical typed
fan/power route; its two new signed typed references remain unavailable as
intended. Installing 2.4.0-beta.13 neither configures trust nor updates Core. Admission still requires exact current
Core/provider identity and valid authority.

The [8.5.0 compatibility contract](docs/HA_MCP_8_5_0_COMPATIBILITY.md)
preserves the public blueprint getter through a closed list/get adapter.
Metadata-only results remain partial; installed configuration and source-download
provenance stay distinct. Generic blueprint writes remain unavailable.
Historical acceptance does not establish the new installed pairing.

The [Core registry contract](docs/CORE_RELEASE_REGISTRY.md) governs exact-version
review, separate Core trust, signatures, expiry, retained denials and authority
retirement. Signed data can select existing compiled contracts; incompatible
semantics require review and possibly code changes. Future Core and ha-mcp
versions receive no blanket compatibility from this milestone.

## Ordinary fan requests

`control_fan` supports exact `turn_on`, `turn_off` and `set_percentage` requests
for one fan under ordinary authenticated connector authorization. The assistant
generates and reuses the operation ID; no configuration plan or panel approval
is added for this typed action. Generic service forwarding stays closed.

Fan operations require admitted ha-mcp 8.4.3, 8.5.0 or 8.6.0 and exact Core authority.
The signed registry selects reviewed typed applicability for each exact Core
version; Core 2026.9.2 retains its historical route. A version without reviewed
applicability remains unavailable.
Inspect authoritative task state and exact state/percentage readback. An uncertain
action is reconciled read-only, never blindly retried; unresolved outcomes retain
protection for the affected fan. Verification reflects HA state, not independent
physical feedback, and consumer coverage remains incomplete. Restoration requires
a separate exact owner request. See [typed fan control](docs/TYPED_FAN_CONTROL.md).

## Ordinary light and switch requests

`control_power` supports one exact light or switch ON/OFF request under ordinary
authenticated connector authority. The assistant manages its operation ID; no
configuration plan or panel approval is created. The contracts require exact
Core authority and admitted ha-mcp 8.4.3, 8.5.0 or 8.6.0, with the same registry-selected
and historical routes described above. Generic services, toggle, brightness,
color, bulk targets and other domains remain outside this tool.

Power may affect critical loads, integration-defined groups and consumers.
Coverage is incomplete, and light ON may restore integration defaults. Verify
the authoritative task and independent HA state; physical effects are not
independently established. Uncertainty protects the exact target and never
permits redispatch. Restoration needs a separate exact owner request.
See [typed power control](docs/TYPED_POWER_CONTROL.md).

## Governed changes and evidence

Inspect, explain evidence and effects, prepare an exact plan, obtain its
authenticated approval, apply once, inspect execution, read back and verify.
`approve_change_plan` requests approval; it does not grant it. Approval
remains bound to current evidence and execution conditions.

After a timeout or incomplete response, reconcile the persisted task and
authoritative target state. Attempt count alone does not prove dispatch.
Do not blindly repeat a mutation, reuse a failed approval or change providers.
Dashboard saves remain non-atomic against external editors.

Engineering 2.3.0-beta.4 introduced Core-bound fan/power records that
2.3.0-beta.3 cannot read. Preserve
current execution IDs, receipts and holds; binary downgrade alone is not storage
recovery. Use a verified compatible artifact and an exact recovery plan.

- [Change governance](docs/CHANGE_GOVERNANCE.md)
- [External approval](docs/EXTERNAL_APPROVAL.md)
- [Execution recovery](docs/GOVERNED_EXECUTION_RECOVERY.md)
- [Bounded response guidance](docs/TOKEN_EFFICIENCY.md)
- [Integrity analysis](docs/CONFIGURATION_INTEGRITY_ANALYSIS.md)
- [Automation reliability](docs/AUTOMATION_RELIABILITY_ANALYSIS.md)
- [Audit records](docs/AUDIT_LOG.md)

## Development and release

Follow the [Engineering README](hass_mcp_engineering_beta/README.md),
[docs/CODEX_WORKFLOW.md](docs/CODEX_WORKFLOW.md) and applicable instructions.
Resolve the exact release context before work:

```sh
python scripts/codex-context.py --format json
```

Materialize a release in its original PR through
`scripts/promote_next_release.py`; the temporary declaration is consumed.
Final clean-head Evidence and candidate CI remain separate from publication
and installed acceptance. A version-changing main merge can automatically
publish; the owner decision must cover that consequence. Deployment is separate.
The [current roadmap](docs/2_1_ROADMAP.md#current-direction) tracks script dependency
acceptance and subsequent feature priorities.

## Historical references

Published artifacts and historical evidence remain immutable. RC9's accepted
runtime is preserved by 2.2.0; its observations retain their RC9 pairing/time.

- [2.3.0 stable notes](docs/V2_3_0_RELEASE_NOTES.md) and
  [2.3.0 acceptance](docs/V2_3_0_ACCEPTANCE.md)
- [2.2.0 notes](docs/V2_2_0_RELEASE_NOTES.md) and
  [2.2.0 acceptance](docs/V2_2_0_ACCEPTANCE.md)
- [RC9 notes](docs/V2_2_0_RC9_RELEASE_NOTES.md) and
  [RC9 acceptance](docs/V2_2_0_RC9_ACCEPTANCE.md)
- [Architecture history](V2_BETA_ARCHITECTURE.md)
- [Historical beta deployment guidance](docs/BETA_DEPLOYMENT.md)
- [Published releases](https://github.com/jeter-1/hass-mcp-admin/releases)

Historical counts, pairings, installation and rollback guidance describe their
original release and do not replace the current 2.4.0-beta.13 contract.
