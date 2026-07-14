# Beta deployment and validation

The beta add-on is isolated from production. Production remains **HA MCP
Engineering Server** v1.1.2 (`hass_mcp_admin`, port 8099). Beta v2.0.0-beta.24
is **HA MCP Engineering Server Beta** (`hass_mcp_engineering_beta`, port 8100).
The workflow in this document deploys or updates only the beta.

Beta 23 must expose 38 registered/25 canonical tools and no planned feature
capabilities. It adds no tool or schema, so connector recreation is not normally
required. Follow the read-only acceptance procedure in
[`HANDOFF_GENERATION.md`](HANDOFF_GENERATION.md). Rollback affects only beta;
production v1.1.2 remains on port 8099.

## Before opening or merging a beta release

Every Home Assistant add-on change must increment both the beta add-on version
in `hass_mcp_engineering_beta/config.yaml` and `SERVER_VERSION` in
`hass_mcp_engineering_beta/ha_mcp_engineering/version.py`. Home Assistant uses
the add-on version to decide whether an update is available, so merging code
under an unchanged version may leave a cached image installed.

From a clean branch in Windows PowerShell, run:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\deploy-beta.ps1 `
  -DeployedVersion 2.0.0-beta.22 `
  -ExpectedVersion 2.0.0-beta.24 `
  -PythonExecutable .\.venv\Scripts\python.exe `
  -FullTests
```

The deployed version must be supplied explicitly, or through the
`HA_MCP_BETA_DEPLOYED_VERSION` environment variable. This makes the version
comparison fail closed instead of guessing what Home Assistant currently runs.
Python 3.12 must be on `PATH`, or its virtual-environment executable must be
provided with `-PythonExecutable` as shown above.
The script validates the clean working tree and metadata, compiles beta Python,
runs beta-focused tests by default (or the full suite with `-FullTests`), and
builds the beta image. `-SkipTests` and `-SkipDockerBuild` are intended only for
targeted diagnostics. `-DryRun` performs the read-only repository and metadata
checks while reporting the remaining actions without running them.

After Home Assistant has updated and started the beta, health can be checked
without supplying authentication material:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\deploy-beta.ps1 `
  -DeployedVersion 2.0.0-beta.22 `
  -ExpectedVersion 2.0.0-beta.24 `
  -PythonExecutable .\.venv\Scripts\python.exe `
  -SkipTests -SkipDockerBuild `
  -HealthHost homeassistant.local `
  -HealthTimeoutSeconds 20
```

`-ExecutionPolicy Bypass` applies only to this PowerShell process; it does not
change the machine or user execution policy.

`-HealthHost` accepts a hostname or IP address only. Do not put a URL, MCP
authenticated path, credential, or secret in the argument.

## Normal GitHub add-on repository deployment

1. Push the release branch and open a pull request to `main`.
2. Confirm CI tests and both add-on image builds pass, then merge the pull
   request. Do not deploy production.
3. In Home Assistant, open **Settings > Add-ons > Add-on Store**, open the menu,
   and choose **Check for updates**. Refresh the custom repository if needed.
4. Open **HA MCP Engineering Server Beta** and choose **Update**. Verify the
   beta slug and port before proceeding; do not update or reinstall the stable
   add-on as part of this workflow.
5. Start beta and confirm `/health` on port 8100.
6. With MCP Inspector connected through the secret-prefixed MCP URL, run
   `tools/list`, then call `server_info`, `list_capabilities`, and
   `get_server_health`. `server_info` must report the expected beta version.
7. Make one intentional request for a nonexistent HA entity. Confirm the client
   request ID matches the structured log and audit record, whose status must be
   `error` with a stable error code.

The unauthenticated health endpoint proves that a process is responding. It
does not replace `server_info` for verifying the running server version.

Beta 18 must return 36 serializable tools from the server's real `tools/list`, including
`entity_dependency_analysis`, `automation_reliability_analysis`, and
`change_impact_analysis`, and `configuration_integrity_analysis`. If ChatGPT or
Claude retains an older manifest while the server returns 36, refresh or
recreate only the beta connector. This is a
manifest cache, not server registration. Never share the complete authenticated URL.

## Optional local add-on development

For a Home Assistant development host, copy or check out the repository into
the host's local add-ons directory (commonly `/addons`) so that the directory is
`/addons/hass_mcp_engineering_beta`. Refresh the Add-on Store, install the local
beta entry, configure a new beta-only access secret, and build/start it. Keep
the production and beta directories, configuration, data, slugs, and ports
separate. Never copy `/data/options.json`, audit files, Supervisor tokens, or an
existing authenticated MCP URL into the repository.

Local add-on discovery and filesystem access vary by Home Assistant installation
type. This workflow is for a development or test host and must not alter the live
Home Assistant production system.

## Secret handling

- Store the beta access secret only in the beta add-on options or an approved
  secret manager. Use at least 24 characters and a different value from
  production.
- Never pass a secret to `deploy-beta.ps1`; it deliberately has no secret
  parameter.
- Never paste authenticated MCP paths into commits, issues, CI variables,
  screenshots, command output, or artifacts. Redact the entire secret-prefixed
  path when sharing diagnostics.
- Review structured logs and audit output for redaction before sharing them.

## Supervisor cache delays

If Home Assistant does not offer the new beta version:

1. Confirm the merged `main` branch contains the new beta `config.yaml` version.
2. Confirm GitHub Actions successfully built both images for that commit.
3. Use **Check for updates** in the Add-on Store and reload the store page.
4. Confirm the repository URL is correct and inspect Supervisor logs for fetch,
   manifest, architecture, or registry errors. Do not publish logs until secret
   paths and tokens have been redacted.
5. Allow time for repository and image-manifest caches, then check again. Restart
   the Supervisor only during an approved maintenance window.
6. As a last resort, remove and re-add the custom repository entry. This is a
   repository-index action; do not uninstall the production add-on.

An installed add-on card can show repository metadata before a new container is
actually running. Verify the live image by calling beta `server_info` and matching
its version to the intended release. Also verify the container exposes port 8100
and that `get_server_health` identifies the beta server.

## Provider-routing troubleshooting

Beta 12 intentionally reports `standard_ha_mcp_delegation: unavailable`. The real
Home Assistant endpoint is `/api/mcp` (internally
`http://supervisor/core/api/mcp` with Supervisor bearer authentication), but the Assist
tool surface lacks exact entity-ID, complete area-registry, and service-catalog
semantics. The beta does not configure or call that endpoint.

`get_entity`, `list_areas`, `search_services`, and `list_services` must use
`direct_ha_api` through facilitator routing. Four successful calls must add four direct
provider requests and successes, add no Standard MCP success, and show no fallback.
Their responses must include lifecycle `transitional`, route `transitional_direct`, the
specific direct-read policy, source coverage, and current request timing.

For dependency analysis, a request with `limit=20` must report requested and effective
limits of 20. A repeat cache-hit request must show near-current lookup/request timing;
original build and source provenance durations remain separately labeled.

`get_error_log` uses `system_log/list`, not the historical `/api/error_log` REST path.
On Home Assistant 2026.7.2 that REST view is conditional on file logging and can return
404 on HA OS. A successful Beta 12 call returns bounded structured entries. An empty
System Log is a successful empty list; permission, unavailable, timeout, malformed, or
other upstream failures remain explicit. The command requires an administrative HA
WebSocket identity; do not add broad Supervisor permissions to work around denial.

If a System Log response appears insufficiently sanitized, stop testing that response:
do not copy it into an issue, terminal transcript, screenshot, or PR. Record only the
request ID and safe redaction telemetry, stop the beta connector if necessary, and roll
forward to a newer beta version. The sanitizer is expected to process unknown nested
fields before bounding; `sanitization_failed_closed=true` means affected fields were
replaced, not passed through.

When future analytical work uses providers, inspect only bounded provider identity,
completeness, failure category, coverage, fallback, and timing fields. A partial result
must identify missing sources. A failed ordinary service operation must not show a
direct-HA fallback. Redact authenticated paths before sharing logs.

## Beta 11 read-only acceptance test

1. Call `server_info`; confirm `2.0.0-beta.11` and 33 tools.
2. Call `get_server_health(check_ha=false)` and capture baseline counters.
3. Call `get_error_log(tail_lines=50)`.
4. Inspect every returned entry and nested field for Matter commissioning material,
   authentication-flow identifiers, webhook secrets, tokens, credential-bearing URLs,
   and authentication session credentials. Do not copy any detected value into a
   report.
5. Confirm every detected value uses a stable `[REDACTED:<category>]` marker.
6. Confirm no original prefix, suffix, hash, partial value, length, or reversible
   encoding remains.
7. Confirm useful non-secret diagnostics remain: logger/integration names, entity IDs,
   source filenames and lines, timestamps, counts, ordinary error codes, device names,
   and non-credential private IPs.
8. Confirm `content_is_untrusted_data=true`, bounded results, explicit truncation,
   redaction telemetry, provider `direct_ha_api`, policy
   `structured_system_log_read`, and no fallback.
9. Inspect the audit record correlated to the request ID.
10. Confirm the audit contains bounded arguments and the request ID but no returned log
    payload or sensitive value.
11. Repeat the Beta 10 invalid-ID and nonexistent-entity checks; confirm local
    `request_validation`, zero HA time, and one terminal `entity_not_found` increment.
12. Re-run one approved Phase 3C administrative read and confirm its specific direct
    policy remains intact.
13. Confirm neither the System Log call nor the Phase 3C read claims fallback or a
    Standard HA MCP success.

## Beta 14 read-only acceptance test

Do not run this procedure from CI or against production. After the user deploys Beta 14:

1. Call `server_info(check_ha=false)`.
2. Confirm `2.0.0-beta.14`, 34 tools, and 25 canonical tools.
3. Call `list_capabilities`; confirm no new tool was added.
4. Capture health, provider, reliability, and error counters.
5. Select an automation with recent retained traces.
6. Call `list_automation_traces` and record the bounded returned run count and IDs.
7. Immediately call `automation_reliability_analysis` for the same internal ID with a covering lookback and sufficient `trace_limit`.
8. Confirm `analysis_timestamp` is a non-null RFC 3339 UTC timestamp.
9. Confirm the analyzer examines the same eligible runs, subject only to documented bounds.
10. Confirm it does not emit `no_recent_execution_evidence` when eligible traces exist.
11. Confirm earliest/latest observations are chronological.
12. Confirm overlapping failures retain correct root-cause grouping.
13. Confirm trace coverage counts, state, cutoff, and completeness are truthful.
14. Confirm timing separates wall-clock duration and cumulative HA effort.
15. Repeat with an automation having legitimately zero recent traces; confirm the evidence-gap finding is accurate.
16. Analyze `beta14_smoke_test_nonexistent`; confirm `automation_not_found`.
17. Analyze `../config`; confirm `invalid_request`, zero HA time, zero HA requests, and no upstream attempt.
18. Capture health again and verify exact counter deltas.
19. Inspect audit records; confirm configuration, trace bodies, logs, findings, evidence, state values, and error content are absent.
20. Call `get_error_log(tail_lines=50)` and confirm Beta 11 redaction remains intact.
21. Confirm no writes, service calls, triggers, plans, reloads, restarts, fallback, or Standard MCP successes occurred.

Do not require a cache hit. New analyses always recollect evidence; only an active
cursor page may reuse its bounded sanitized five-minute pagination snapshot. Beta 14
does not change the tool manifest, so connector recreation is not expected. Production
remains on port 8099.

## Beta 15 read-only acceptance test

Beta 15 adds a tool and therefore requires a beta-connector manifest refresh. Follow
the exact 29-step procedure in
[`CHANGE_IMPACT_ANALYSIS.md`](CHANGE_IMPACT_ANALYSIS.md). It verifies the 35/25 tool
counts, capability routing, all three operation semantics, destination conflict,
nonexistent and locally invalid entities, traversal depth, signed pagination,
no provider/counter repetition, conservative coverage, fixed timing provenance,
audit/health exclusions, System Log sanitation, no fallback, and absence of any write
or governance action. Do not execute the procedure from CI or against production.

## Beta 16 read-only acceptance test

Beta 16 adds no tool and changes no input schema, so a connector recreation is not
expected when upgrading from Beta 15. Follow the exact 34-step procedure in
[`CHANGE_IMPACT_ANALYSIS.md`](CHANGE_IMPACT_ANALYSIS.md). In particular, create a
paginated first page with `refresh_index=true`, continue immediately with
`refresh_index=false`, and verify the same timestamp/snapshot with no new HA request
or index build. Reconcile findings versus unique objects, verify cursor pages do not
repeat whole-analysis health aggregates, check requested-scope dynamic uncertainty,
and exercise field-level validation details. The procedure is read-only and must not
be run automatically against a deployed Home Assistant instance.

## Beta 18 read-only acceptance test

Beta 18 adds no tool and changes no public schema, so connector recreation is not
normally required. Follow the procedure in
[`CONFIGURATION_INTEGRITY_ANALYSIS.md`](CONFIGURATION_INTEGRITY_ANALYSIS.md).
Verify the 36/25 tool counts, then run a low-limit automation-only first page with
orphan candidates disabled and `refresh_index=true`; follow at least two cursor
pages. Confirm stable timestamp, totals, coverage, and provenance with no HA or
index activity on continuation. Verify the deployed false-positive tokens never
appear as targets, inspect remaining exact missing references, confirm dynamic
evidence remains target-free, exercise field-level validation and cursor tamper
rejection, inspect bounded redacted audit/health output, and confirm no mutation.

## Rollback and removal

If beta validation fails, stop the beta add-on and return clients to production
on port 8099. For a code rollback, create a new beta release that reverts the
faulty change and uses a version newer than the failed release; this ensures Home
Assistant recognizes it as an update. Re-run the validation workflow, merge it,
refresh the repository, and update beta only.

The beta can be uninstalled after saving any non-secret audit evidence needed for
diagnosis. Removing beta must not delete, reinstall, reconfigure, or change the
stable `hass_mcp_admin` add-on.

## Beta 19 deployment and connector refresh

Beta 19 adds one public tool. After merging and publishing the add-on update,
refresh the Home Assistant repository, update only
`hass_mcp_engineering_beta`, verify port 8100 health, and reconnect or recreate the
beta connector if `tools/list` remains cached at 36. Never replace the production
connector, reveal its secret path, or log the authenticated beta URL.

Verify `server_info` reports `2.0.0-beta.19`, `list_capabilities` reports 37
registered/25 canonical tools with only `handoff_generation` planned, and
`incident_correlation` is callable. Follow the full read-only procedure in
[`INCIDENT_CORRELATION.md`](INCIDENT_CORRELATION.md). If any source is mislabeled,
pagination performs upstream work, counters double, audit contains raw evidence,
or a write occurs, stop testing and roll forward to a newer beta version. Do not
downgrade metadata in place. Supervisor cache delays can be distinguished from a
bad image by comparing live `server_info`, add-on logs without secrets, repository
metadata, and real `tools/list` after a connector refresh.

## Beta 20 coverage-correction deployment

Beta 20 adds no public tool or schema, so a connector refresh is not normally
required. After merge and publication, refresh the Home Assistant repository,
update only `hass_mcp_engineering_beta`, and verify `server_info` reports
`2.0.0-beta.20` while `tools/list` remains 37 registered/25 canonical tools.
Production remains on port 8099 and must not be updated or restarted.

Use the read-only target and procedure in
[`INCIDENT_CORRELATION.md`](INCIDENT_CORRELATION.md). A successful partial
dependency-index row must have zero failed items and `failure_category=null`, with
unsupported source types retained as warnings and
`dependency_index_unsupported_source_types`. Follow two cursor pages and confirm
the same frozen coverage with no HA/provider/index work. Then run the no-index,
validation, cursor-tamper, health, audit, and non-mutation checks.

If the live add-on still reports Beta 19 after repository refresh, compare
`server_info`, the add-on store version, repository URL/branch, and image build
timestamp without printing authenticated URLs or secrets. Restarting Home
Assistant is not required for repository cache diagnosis. If Beta 20 reports an
upstream error for successful zero-failure partial coverage, stop acceptance and
roll forward with a newer beta version rather than editing metadata in place.

## Beta 22 handoff-stabilization deployment

Beta 22 adds no tool or schema, so a connector refresh is not normally required.
Refresh the repository, update only `hass_mcp_engineering_beta`, and verify
`server_info` reports `2.0.0-beta.22` with 38 registered/25 canonical tools and
an empty planned list. Production remains untouched on port 8099.

Run the read-only procedure in [`HANDOFF_GENERATION.md`](HANDOFF_GENERATION.md).
Focused and incident handoffs must contain one effective `dependency_index` row;
successful partial coverage has zero failed items and a null failure category.
Expired, superseded, rolled-back, and terminal validation-only plans must not
inflate open, risk, or authorization counts. Verify resolved automation entity IDs
in both structured and Markdown scope, then follow two pages and confirm frozen
scope/lifecycle/coverage with zero upstream work. If live metadata remains Beta 21,
compare repository metadata and `server_info` without printing the secret path;
roll forward rather than editing a deployed version in place.

## Beta 23 provider-accounting deployment

Beta 23 adds no tool or schema, so connector recreation is not normally required.
Refresh the repository, update only `hass_mcp_engineering_beta`, and verify
`server_info` reports `2.0.0-beta.23` with 38 registered/25 canonical tools and
an empty planned list. Capture `provider_routing` and governance baselines before
the read-only checks in `BETA_23_RELEASE_NOTES.md`.

Successful system/focused handoffs may increment provider request/success counts
only for operations actually dispatched. Two signed-snapshot pages must leave all
provider counters unchanged. A focusless handoff, tampered cursor,
cursor-plus-refresh request, another invalid Engineering analysis request, and a
malformed transitional direct-read request must fail before upstream work without
changing provider or source-failure counters. Do not manufacture an outage to
test failure attribution; use automated timeout/failure coverage when no natural
read-only failure exists. Confirm audit remains bounded/redacted and governance
plan count remains unchanged.

## Beta 24 final pre-RC deployment

Beta 24 adds no tool or public schema, so connector recreation is not normally
required. Refresh the repository, update only `hass_mcp_engineering_beta`, and
verify `server_info` reports `2.0.0-beta.24`, 38 registered/25 canonical tools,
and zero planned capabilities. Production remains v1.1.2 on port 8099 and must
not be restarted or changed.

Before any governed write, inspect existing plans. Re-create all pending or
approved plans made before Beta 24 because automation normalization and bound
hashes changed; do not migrate or reapprove them. Terminal history remains
readable. The full read-only checks and separately approved, description-only
apply/rollback procedure are in
[`BETA_24_RELEASE_NOTES.md`](BETA_24_RELEASE_NOTES.md).

`cf-connecting-ip` is ignored by default. Configure
`trust_cf_connecting_ip=true` and the bounded `trusted_proxy_cidrs` list only
after confirming the actual proxy source networks. Do not enable forwarding
trust merely because the deployment uses Nabu Casa. See
[`RATE_LIMITING.md`](RATE_LIMITING.md).

Signed cursor snapshots are process-local and invalidated by add-on restart.
They are a five-minute pagination mechanism, not durable workflow state or a
general result cache. After a restart, begin a new analysis instead of retaining
an old cursor.

For rollback, publish a newer beta that reverts the defect; do not decrease the
add-on version in place. If live results show an ungoverned upsert, a false
`other:id` mismatch, forwarded-header trust without configuration, whole-store
rate reset, pre-dispatch provider failure accounting, or an unbounded audit read,
stop acceptance and roll forward.
