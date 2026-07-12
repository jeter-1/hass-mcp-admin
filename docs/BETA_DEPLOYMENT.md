# Beta deployment and validation

The beta add-on is isolated from production. Production remains **HA MCP
Engineering Server** v1.1.2 (`hass_mcp_admin`, port 8099). Beta v2.0.0-beta.10
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
  -DeployedVersion 2.0.0-beta.9 `
  -ExpectedVersion 2.0.0-beta.10 `
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
  -DeployedVersion 2.0.0-beta.9 `
  -ExpectedVersion 2.0.0-beta.10 `
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

Beta 10 must return 33 serializable tools from the server's real `tools/list`, including
`entity_dependency_analysis`. If ChatGPT retains a 32-tool manifest while the server
returns 33, the remaining mismatch is connector caching rather than server registration;
recreate only the beta connector or append the non-secret cache marker
`?manifest=beta10` to its authenticated connector URL. Never share that complete URL.

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

Beta 10 intentionally reports `standard_ha_mcp_delegation: unavailable`. The real
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
404 on HA OS. A successful Beta 10 call returns bounded structured entries. An empty
System Log is a successful empty list; permission, unavailable, timeout, malformed, or
other upstream failures remain explicit. The command requires an administrative HA
WebSocket identity; do not add broad Supervisor permissions to work around denial.

When future analytical work uses providers, inspect only bounded provider identity,
completeness, failure category, coverage, fallback, and timing fields. A partial result
must identify missing sources. A failed ordinary service operation must not show a
direct-HA fallback. Redact authenticated paths before sharing logs.

## Beta 10 read-only acceptance test

1. Call `server_info`; confirm `2.0.0-beta.10` and 33 tools.
2. Call `get_server_health(check_ha=false)` and capture baseline recent-error and
   provider-routing counters.
3. Call `get_error_log(tail_lines=50)`.
4. Confirm a successful bounded `home_assistant_system_log` result (including a valid
   empty list), or an explicit documented unavailable/permission failure; never an
   unexplained empty success.
5. Inspect the returned content and confirm no access secret, Supervisor token,
   authorization value, credential URL, webhook secret, session identifier, or complete
   authenticated path appears.
6. Call `get_entity(entity_id="../config")`.
7. Confirm `invalid_request`, `home_assistant_ms=0`, source failure
   `request_validation`, `upstream_attempted=false`, and no unsafe endpoint construction.
8. Call `get_entity(entity_id="sensor.beta10_smoke_test_nonexistent")`.
9. Confirm a normalized `entity_not_found` result attributed to `direct_ha_api`.
10. Call `get_server_health(check_ha=false)` again.
11. Confirm each terminal error increased its final public error counter exactly once.
12. Confirm direct-provider success/failure counters changed by the calls' actual
    outcomes, with no Standard HA MCP success or fallback claim.
13. Re-run one approved Phase 3C read, such as
    `get_entity(entity_id="input_boolean.away_mode")`, and confirm its specific direct
    policy, source coverage, timing, and no fallback.
14. Inspect correlated audit/application records and confirm request IDs match and all
    sensitive material remains redacted.

## Rollback and removal

If beta validation fails, stop the beta add-on and return clients to production
on port 8099. For a code rollback, create a new beta release that reverts the
faulty change and uses a version newer than the failed release; this ensures Home
Assistant recognizes it as an update. Re-run the validation workflow, merge it,
refresh the repository, and update beta only.

The beta can be uninstalled after saving any non-secret audit evidence needed for
diagnosis. Removing beta must not delete, reinstall, reconfigure, or change the
stable `hass_mcp_admin` add-on.
