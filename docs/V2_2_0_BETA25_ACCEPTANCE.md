# HA MCP Engineering Server 2.2.0-beta.25 `ha_search` promotion acceptance

## Release boundary

- feature base: record the exact `origin/main` SHA in the pull request;
- advertised version before promotion: `2.2.0-beta.24`;
- staged version: `2.2.0-beta.25`;
- exact upstream entry: `ha-mcp-v8.1.1-e1d76a6e`;
- protocol: `2025-03-26`;
- stable version: `1.1.2`; and
- merge, publication, deployment, and production access are excluded from the
  feature pull request.

## Source and offline acceptance

Require all of the following before merge:

- only exact 8.1.1 `ha_search` changes from `held_for_canary` to
  `automatic_read`;
- `ha_get_operation_status` is the only held tool and remains unreachable
  through ordinary delegation;
- exact 8.0.0 and 8.1.0 remain 24 automatic reads and two held reads;
- exact 8.1.1 remains 78 advertised/accounted, with 25 automatic reads, one
  held read, 49 Engineering-local tools, and 74 total runtime tools;
- `ha_search` is dynamically registered and a positive call reports provider
  `upstream_read_gateway`, complete non-truncated output, and fallback `none`;
- `run_held_read_canary` rejects `ha_search` before dispatch because it is no
  longer held, while continuing to support only `ha_get_operation_status`;
- input schema, description, annotations/security policy, output contract,
  runtime contract, exact server/release, and protocol checks remain exact;
- every other upstream classification is byte-for-byte unchanged;
- governance, Dashboard, backup, lifecycle, held-read canary implementation,
  and fallback behavior are unchanged;
- exact standalone-image and add-on-runtime acceptance pass for every supported
  release profile without fixture mutation; and
- strict dependency audit reports no known vulnerabilities.

## Later deployment acceptance

After separate merge, publication, and deployment authorization, verify the
deployed build identity and exact 8.1.1 entry. Require 78 upstream advertised,
25 delegated, one held, 49 Engineering-local, and 74 total tools. The held set
must be exactly `ha_get_operation_status`; every mismatch, quarantine, missing,
unreviewed, and fallback counter must be zero.

Call `ha_search` through its normal registered tool with bounded read-only
arguments and verify provider `upstream_read_gateway`, complete output,
fallback `none`, and no Home Assistant mutation. Do not use the canary route
for promoted `ha_search`. Do not promote `ha_get_operation_status` until a
separately reviewed legitimate positive-path operation ID is available.

Rollback is required for a build-identity mismatch, accounting drift, any
admission mismatch or quarantine, fallback, loss of the normal `ha_search`
route, accidental exposure of `ha_get_operation_status`, or a Dashboard,
governance, provider, dependency, or transport regression.
