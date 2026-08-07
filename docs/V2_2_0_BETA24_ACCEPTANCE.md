# HA MCP Engineering Server 2.2.0-beta.24 held-read canary acceptance

## Release boundary

- repository: `jeter-1/hass-mcp-admin`;
- implementation base: resolve the exact merge-base and head from GitHub;
- advertised version before promotion: `2.2.0-beta.23`;
- staged version: `2.2.0-beta.24`;
- stable version: `1.1.2`;
- protocol: `2025-03-26`; and
- merge, promotion, publication, deployment, live canary, and Home Assistant
  mutation are excluded from the feature pull request.

## Source acceptance

Require all of the following before merge:

- `run_held_read_canary` is a static read-only Engineering-native tool with no
  fallback and a caller-supplied expected compatibility-entry ID;
- it is documented as an unapproved read-only operator diagnostic whose
  invocation conveys no approval and creates no approval state;
- only the active exact entry and a tool classified exactly
  `held_for_canary` can reach the upstream transport;
- argument and all known descriptor/security/runtime mismatches reject before
  dispatch;
- same-session identity, release, protocol, and target-contract revalidation
  precedes `tools/call`;
- successful, partial, upstream-error, output-mismatch, transport-error, and
  bounded-result outcomes remain truthful;
- audit excludes caller values and upstream content while retaining bounded
  identity, binding, dispatch, provider, result, and failure evidence;
- `promotion_performed` is always false and neither held tool becomes a dynamic
  delegated tool or changes source classification;
- automatic, mixed, persistent-write, physical/high-risk, prohibited,
  unsupported, unreviewed, missing, duplicate, and mismatched targets are
  unreachable through this path;
- no plan, approval, task, configuration write, service call, direct Home
  Assistant fallback, or Home Assistant mutation is created;
- the exact 8.1.1 catalog remains 78 advertised/accounted, 24 automatic, two
  held, with zero fallback; and
- Dashboard provider, stable v1.1.2, F2/F3, packaging, and deployment contracts
  remain unchanged except for the one additive static tool and resulting
  49-local/73-total count.

## Post-deployment live acceptance

After separate deployment approval, obtain the active compatibility-entry ID
from Engineering health. Execute `ha_search` and `ha_get_operation_status`
independently through `run_held_read_canary` with schema-valid read-only
arguments. For each tool, preserve the complete bounded result and audit
evidence, verify exact 8.1.1 identity/protocol and no fallback, and classify it
independently as PASS/promotable or HOLD. A pass is evidence only.

Do not promote either tool during canary acceptance. Promotion requires a later
human architecture decision and a separate source-policy pull request. Runtime
artifact digest/revision verification remains an independent deployment check.
