# Beta deployment and validation

The beta add-on is isolated from production. Production remains **HA MCP
Engineering Server** v1.1.2 (`hass_mcp_admin`, port 8099). Beta v2.0.0-beta.13
is **HA MCP Engineering Server Beta** (`hass_mcp_engineering_beta`, port 8100).
The workflow in this document deploys or updates only the beta.

## Before opening or merging a beta release

Every Home Assistant add-on change must increment both the beta add-on version
in `hass_mcp_engineering_beta/config.yaml` and `SERVER_VERSION` in
`hass_mcp_engineering_beta/ha_mcp_engineering/version.py`. Home Assistant uses
the add-on version to decide whether an update is available, so merging code
under an unchanged version may leave a cached image installed.

From a clean branch in Windows PowerShell, run:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\deploy-beta.ps1 `
  -DeployedVersion 2.0.0-beta.12 `
  -ExpectedVersion 2.0.0-beta.13 `
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
  -DeployedVersion 2.0.0-beta.12 `
  -ExpectedVersion 2.0.0-beta.13 `
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

Beta 13 must return 34 serializable tools from the server's real `tools/list`, including
`entity_dependency_analysis` and `automation_reliability_analysis`. If ChatGPT or
Claude retains a 33-tool manifest while the server returns 34, the remaining mismatch
is connector caching rather than server registration;
refresh only the beta connector. Beta 13 does not change the manifest, so connector
recreation should not be required. Never share the complete authenticated URL.

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

## Beta 13 read-only acceptance test

Do not run this procedure from CI or against production. After the user deploys Beta 13:

1. Call `server_info(check_ha=false)`; confirm `2.0.0-beta.13` and 34 tools.
2. Call `list_capabilities`; confirm no tool was added and the analyzer remains additive beta-native.
3. Capture health, provider, reliability, and error counters.
4. Run standard-detail analysis for internal ID `office_tv_lights_on` with `lookback_hours=168`, `trace_limit=10`, and `limit=20`, if it still exists.
5. Confirm the real Govee failure remains visible only when current evidence supports it.
6. Confirm `first_observed` is not later than `last_observed` for every finding and root-cause group.
7. Confirm every reliability timestamp follows the RFC 3339 UTC `Z` contract.
8. Confirm generic `Task exception was never retrieved` entries are excluded unless a separate explicit basis binds them.
9. Confirm overlapping action/trace findings share one root cause or follow the documented deduplication rule.
10. Confirm System Log coverage does not claim complete 168-hour retention.
11. Confirm request wall clock, HA cumulative attempt effort, and HA wall-clock span are separately labeled and plausible.
12. Repeat the analysis; confirm cache status remains truthfully `not_configured` rather than requiring a hit.
13. Analyze `beta13_smoke_test_nonexistent`; confirm `automation_not_found` and one terminal error increment.
14. Analyze a malformed identifier; confirm local validation, no upstream request, zero HA time, and one terminal error increment.
15. Confirm reliability/provider counters match outcomes and pagination did not repeat finding/root-cause aggregates.
16. Inspect audit records by request ID; confirm configuration, traces, logs, findings, evidence, and error text are absent.
17. Call `get_error_log(tail_lines=50)` and confirm Beta 11 redaction remains intact.
18. Confirm no write, service call, trigger, plan, reload, restart, fallback, or Standard MCP success occurred.

If the named automation no longer exists or has no current failure evidence, select
another automation with known trace failures and retain only sanitized identifiers in
the report. Never manufacture a finding. Beta 13 does not change the tool manifest, so
connector recreation is not expected. Production remains on port 8099.

## Rollback and removal

If beta validation fails, stop the beta add-on and return clients to production
on port 8099. For a code rollback, create a new beta release that reverts the
faulty change and uses a version newer than the failed release; this ensures Home
Assistant recognizes it as an update. Re-run the validation workflow, merge it,
refresh the repository, and update beta only.

The beta can be uninstalled after saving any non-secret audit evidence needed for
diagnosis. Removing beta must not delete, reinstall, reconfigure, or change the
stable `hass_mcp_admin` add-on.
