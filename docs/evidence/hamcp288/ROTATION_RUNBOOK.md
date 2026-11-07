# HAMCP-288: ha-mcp secret path rotation runbook (for Josh)

Josh performs every step. No agent rotates, reads or records the path. This
runbook contains no secret value. Do not paste the old or the new path into a
chat, an issue, a pull request, the register or a handoff.

## Why rotate, and why in this order

Before containment, the path could appear in Engineering outputs: the ha-mcp
app's own log through `get_incident_logs`, and upstream error text. See
`LAN_LISTENER_FINDING.md` and the PR. Any copy that left Engineering before
containment stays valid until the path changes. Rotation retires those
copies. It must come **after** containment is deployed, or the new path
could leak the same way.

1. **Deploy containment first.** Deploy the Engineering release that contains
   HAMCP-288 through the normal, separately authorized deployment. Before
   continuing, confirm that the running Engineering version is that release.
2. **Rotate the path in the ha-mcp app.** In the ha-mcp app's configuration,
   either clear the `secret_path` option so the app generates a new random
   path, or set a new value of your own (at least 8 characters, no spaces).
   Restart the app. Then read the new path where you normally read it. Read
   it yourself, in the app's own log or UI in Home Assistant, not through an
   AI client. Through Engineering, that log source is withheld.
3. **Update Engineering's option.** Set `upstream_dashboard_mcp_url` to the
   new URL, with the same host, port and endpoint suffix as before and only
   the path segment changed. Save, and restart Engineering.
4. **Confirm the new path works.** Confirm that a delegated read tool you
   normally use (one backed by ha-mcp, such as a dashboard read) succeeds
   again.
5. **Confirm the old path fails.** From a device you control, on the same
   network path you would normally use, request the old URL. Expect an
   HTTP 404 or a refusal, not an MCP response. Do not send that request
   through an AI client.

## Behavior during rotation

- **Between steps 2 and 3**, Engineering still holds the old path.
  Delegated (`ha-mcp`-backed) tools fail closed with an upstream-unavailable
  error. Engineering-native tools keep working. Error text names only the
  redacted marker `[REDACTED:token]`, never the old path. Nothing falls back
  to a direct Home Assistant call.
- **Throughout**, the ha-mcp app's own log source (`addon:<…>_ha_mcp`, and
  `ha_mcp_dev`) stays withheld in `get_incident_logs`. Its reason is
  `upstream_secret_path_source_withheld`. This holds while the new path is
  still unknown to Engineering, because the withholding depends on the source,
  not on the configured value.
- If the new path appears in any **other** source before step 3, Engineering
  cannot recognize it. Keep the window between steps 2 and 3 short.

## Behavior after rotation

- Engineering redacts the new path in every output: tool results, errors,
  diagnostics, audit and governance records, log tools, its own stdout and
  stderr, and HTTP responses. That covers the exact text, URL-encoded forms
  and JSON-escaped forms, and a cut fragment at a line or string edge.
- Engineering no longer redacts the old path. Records written before
  containment may still hold it (for example, old audit lines or a client's
  chat history). They no longer grant access once step 5 passes.

## Rollback

- **If step 4 fails** (delegated tools stay unavailable), recheck the URL in
  Engineering's option first: the host, port and suffix must be unchanged.
  If needed, set the ha-mcp app's `secret_path` back to the previous value and
  restore the previous Engineering option. Both apps then behave as before
  rotation. The previous path is still exposed to anyone who held it, so
  plan a new rotation.
- **If containment itself must be rolled back** (by redeploying the previous
  Engineering release), rotation is unaffected. The previous release does not
  withhold the ha-mcp log source and does not redact the path everywhere, so
  the new path can leak again. Rotate again after containment is redeployed.
- Rollback needs no data deletion. No Engineering storage format changed.

## Not covered

- Changing ha-mcp's `host_network` or bind address (see the LAN finding).
- Any automated check of steps 4–5. Both are manual and done by Josh.
