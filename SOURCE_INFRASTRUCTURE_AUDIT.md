# HA MCP Engineering Server Source and Infrastructure Audit

## Scope and status

This audit covers the uploaded repository after the architecture and tool-classification documentation updates. It is read-only with respect to runtime behavior: no Python, add-on, Home Assistant, or deployment configuration was changed.

Inspected files:

- `hass_mcp_admin/server.py`
- `hass_mcp_admin/config.yaml`
- `hass_mcp_admin/Dockerfile`
- `hass_mcp_admin/requirements.txt`
- `repository.yaml`
- `README.md`
- `ARCHITECTURE.md`
- `TOOL_AUDIT.md`

Validation performed:

- Python syntax compilation succeeded.
- Both YAML files parsed successfully.
- The server contains 32 top-level functions and two classes in one 747-line module.
- No tests, CI workflows, dependency lockfile, security policy file, or release automation are present.

## Executive assessment

The server is a compact and understandable first-generation implementation with several sound controls: it refuses weak or missing credentials, uses the Supervisor token, limits add-on privileges, bounds output size and history windows, implements request throttling, contains blueprint file access, and records request-level audit events.

The design is not yet suitable for expansion into a broader engineering platform without first separating infrastructure concerns. Authentication, rate limiting, audit logging, Home Assistant transport, response formatting, policy enforcement, and all MCP tools are coupled in one module. The main next step should be a small foundation refactor, not new analytical features.

## Findings

| Severity | Finding | Evidence | Impact | Recommended direction |
|---|---|---|---|---|
| Critical | No new critical source defect was confirmed in the inspected code. | Static inspection and syntax/YAML validation completed. | This does not establish production security; dynamic and adversarial testing still do not exist. | Preserve the current containment controls while addressing the high-severity design gaps below. |
| High | Authentication uses a secret URL path as a bearer credential. | `Gateway` accepts any request whose path begins with `/<access_secret>/`; no authorization header, signed request, client identity, or expiry is used. | Secrets can leak through endpoint configuration, proxy logs, browser/client histories, screenshots, or diagnostic output. Any holder receives the same authority. | Support header-based credentials or an upstream authenticated proxy; retain the path only as a compatibility option and never expose it in diagnostics. |
| High | Unknown physical or administrative services are allowed by default. | `call_service` requires confirmation only when `domain.service` appears in `DESTRUCTIVE_SERVICES`. | New integrations, scripts, automation triggers, or services can cause consequential actions without entering the denylist. | Remove/delegate `call_service`, or replace the denylist with allow-by-policy risk classification where unknown writes require approval. |
| High | Write approval is not bound to an immutable plan. | `confirm: bool` is supplied in the same `delete_automation` or `call_service` invocation. | A model can assert confirmation without proving that the user reviewed the exact target and arguments. | Introduce plan-bound approvals before retaining any write surface. |
| High | Automation writes are full replacement without optimistic locking. | `upsert_automation` posts a complete config and then reads it back; no prior hash/revision is required. | Concurrent UI or agent changes can be overwritten. Read-back detects storage success, not lost updates. | Delegate writes to standard `ha-mcp` or require a config hash taken from a fresh read. |
| High | All operational concerns are concentrated in one 747-line module. | `server.py` contains configuration loading, HA REST/WS clients, all 23 tools, policy gates, audit storage, authentication, throttling, and process startup. | Security changes and feature changes have a large shared blast radius; isolated testing and maintenance are difficult. | Extract a reusable HA client, response/error model, policy layer, gateway, and audit store before adding analysis tools. |
| Medium | A new HTTP session and WebSocket connection are created for every HA operation. | `rest()` and `ws_command()` each create `aiohttp.ClientSession`; WebSocket commands authenticate from scratch. | Increased latency and resource use; multi-source analyses would amplify connection churn. | Create an application-lifecycle HA client with connection pooling, explicit shutdown, and optional managed WebSocket reuse. |
| Medium | Timeout handling is coarse and error classification is absent. | REST and WebSocket calls use one 60-second total timeout and raise generic `RuntimeError` strings. | Clients cannot distinguish authentication failure, HA unavailability, timeout, malformed response, not found, conflict, or retryable failure. | Add typed internal exceptions and a stable external error envelope with `code`, `retryable`, `source`, and `request_id`. |
| Medium | Refusals are returned as successful tool strings. | Validation paths return strings beginning with `Refused:` or `Not found:`. | MCP clients and audit logic may treat denied or invalid calls as successful results. | Return structured status/error objects or raise framework-supported tool errors. |
| Medium | Outputs are serialized JSON strings instead of structured results. | Most tools call `dump()` and return `str`. | Clients must parse text again; truncation can produce invalid JSON; warnings, partial results, and metadata are not reliably machine-readable. | Return native structured content with explicit truncation/pagination metadata. |
| Medium | Character truncation can cut JSON mid-value. | `dump()` slices the serialized string at 60,000 characters. | The result may no longer be valid JSON and may silently omit important status or errors. | Apply result-specific pagination/projection, or return a structured truncation envelope containing a safe preview. |
| Medium | Request auditing records attempts, not authoritative tool outcomes. | The gateway logs `tools/call`, arguments, confirmation, and HTTP status. MCP-level refusals generally remain HTTP 200, and tool result/error content is not captured. | Audit records cannot prove whether a change occurred or distinguish refusal, validation failure, HA failure, and success. | Instrument tool execution or use MCP middleware to record normalized outcome, duration, error code, affected resources, and change receipt. |
| Medium | Audit argument redaction is insufficient. | `_summarize_args` truncates long strings but does not redact keys or embedded credentials. | Templates, service data, URLs, tokens, webhook values, or sensitive messages can be stored in `/data/audit.jsonl`. | Add recursive, key-aware and pattern-aware secret redaction before persistence. |
| Medium | Audit attribution is incorrect and static. | Every tool event records `"user": "claude"`. | ChatGPT and other clients are mislabeled; actions cannot be tied to a real user, client, or session. | Use neutral client attribution until verified metadata exists; capture request/session IDs and authenticated principal where available. |
| Medium | Audit storage is synchronous and rotates only one backup file. | `audit_write()` performs blocking file I/O in the event loop and replaces the log with `.1` after 5 MiB. | Bursts can delay requests; only a small amount of history is retained; concurrent writes are not serialized explicitly. | Move audit writes behind an async queue or dedicated logger and use bounded multi-file/time-based rotation. |
| Medium | Client-IP trust is incomplete. | `_client_ip` trusts `cf-connecting-ip` whenever present and otherwise uses the socket peer. | Without a trusted-proxy boundary, a direct caller may spoof the header and manipulate per-client rate limits or attribution. | Trust forwarding headers only from configured proxy addresses; otherwise use the transport peer. |
| Medium | Token-bucket stores are cleared wholesale at 1,000 keys. | `_bucket()` calls `store.clear()` when the map exceeds 1,000 entries. | An attacker can churn addresses and reset throttling state for all callers. | Use timestamped LRU/TTL eviction and preserve active penalty state. |
| Medium | The POST body audit buffer can forward an incomplete request after exceeding 2 MB. | The read loop breaks when `total > 2_000_000` but replays only chunks read so far. | Oversized requests may fail unpredictably downstream rather than receiving a clear size error. | Reject over-limit bodies with HTTP 413 and do not forward partial input. |
| Medium | Health is liveness-only and unauthenticated. | `/health` always returns `ok` without checking HA connectivity; it is available outside the secret prefix. | Monitoring cannot detect expired tokens, HA outages, or WebSocket failure. | Keep `/health` as minimal liveness and add authenticated readiness with HA REST/WS checks and version information. |
| Medium | Query parameters lack consistent bounds and validation. | Some limits are bounded manually, while `tail_lines`, list limits, logbook hours, and service/domain strings are not consistently constrained. | Very large requests can increase memory, response size, or HA load; malformed identifiers reach HA directly. | Define strict schemas with min/max values, enums, and identifier validation. |
| Medium | URL construction does not encode path/query values. | Entity IDs, timestamps, and filters are interpolated into REST paths directly. | Ordinary HA IDs are usually safe, but malformed input can alter query semantics or generate misleading failures. | Use URL builders and explicit query parameters. |
| Medium | Blueprint reads perform synchronous file I/O inside async tools. | `get_blueprint` and `get_audit_log` use direct `open()` calls. | Large or slow storage can block the event loop. | Use bounded async/threaded file reads after module separation. |
| Medium | Dependencies are broad minimum ranges with no reproducible lock. | `mcp>=1.9.0`, `aiohttp>=3.9.0`, `uvicorn>=0.29.0`. | Rebuilding the same version can install materially different dependencies and break behavior. | Pin tested versions or generate a lock/constraints file; add scheduled dependency update testing. |
| Medium | Container supply-chain hardening is minimal. | `python:3.12-slim` is tag-based; packages are installed without hashes; the process runs as the container default user. | Base image and packages can drift; compromise has unnecessary container-user privileges even though add-on privileges are constrained. | Pin a digest in release builds, use hashed dependencies where practical, create a non-root application user, and document build provenance. |
| Medium | No automated tests or CI exist. | Repository contains no tests or workflow files. | Authentication, path stripping, rate limits, redaction, HA API compatibility, and tool contracts can regress silently. | Add unit tests first for pure gateway/policy functions, then mocked HA contract tests and add-on smoke tests. |
| Medium | No explicit startup validation of HA URL/token behavior occurs. | Startup checks only that a token string exists and the access secret is long enough. | The process can report healthy while unable to authenticate to HA. | Add readiness probing and clear startup/degraded-state diagnostics without blocking liveness unnecessarily. |
| Low | Naming is still partly legacy in source and logs. | Module docstring, MCP name `ha-admin`, startup messages, slug, and class descriptions retain Admin/Claude terminology. | Server identity remains ambiguous during comparison with standard `ha-mcp`. | Preserve slug for compatibility, but add explicit server identity/version tools and update user-visible names incrementally. |
| Low | Version is duplicated manually and build provenance is absent. | `config.yaml` contains `1.0.0`; the runtime does not report commit SHA, build time, schema version, or dependency versions. | Troubleshooting cannot reliably identify the exact running build. | Generate version/build metadata once and expose it through `server_info`. |
| Low | Documentation describes safeguards that are not yet enforceable architecture. | Architecture target includes exact approval scope, optimistic concurrency, structured errors, and untrusted-input handling, while runtime remains transitional. | Readers may mistake target principles for current guarantees. | Continue labeling current versus target behavior clearly and add implementation-status tables. |

## Existing strengths to preserve

- The add-on uses `homeassistant_api: true` and Supervisor-provided credentials instead of requiring a user-managed long-lived token on HAOS.
- The add-on does not request privileged mode, Docker API access, host PID access, or host networking in its own manifest.
- Home Assistant configuration is mounted read-only for blueprint inspection.
- Startup fails when the access secret is absent or shorter than 24 characters.
- Failed authentication attempts have a stricter limiter than authenticated traffic.
- A global limiter supplements per-client limiting.
- Output and history windows have some explicit bounds.
- Blueprint paths are normalized, contained to the selected domain directory, and restricted to YAML extensions.
- Automation writes include immediate read-back.
- The server includes configuration validation and trace/template evidence tools.
- Python and YAML syntax currently validate successfully.

## Recommended target boundaries

The next implementation should establish these internal boundaries without changing the public tool catalog:

1. `config.py`: immutable validated settings and build metadata.
2. `ha/client.py`: shared REST and WebSocket transport, lifecycle, and typed failures.
3. `models/result.py`: stable success/error/partial response envelope.
4. `policy/redaction.py`: recursive secret-safe logging and output handling.
5. `policy/risk.py`: operation classification independent of tool functions.
6. `audit/store.py`: structured outcome records and safe rotation.
7. `transport/gateway.py`: path/authentication, trusted-proxy handling, request size limits, and correlation IDs.
8. `tools/`: existing tools grouped by evidence, automation, registry, and transitional write responsibilities.

This is a target decomposition, not a recommendation for a one-shot rewrite.

## Prioritized remediation sequence

### Phase 1: test harness and identity

- Add `server_info` and `list_capabilities`.
- Add unit tests for access-path stripping, request-size rejection, rate limiting, argument redaction, and result envelopes.
- Add a CI workflow that compiles Python, parses YAML, and runs tests.
- Add reproducible dependency constraints.

### Phase 2: reliable HA client foundation

- Extract a lifecycle-managed HA client.
- Use pooled HTTP connections.
- Add typed timeout, authentication, connectivity, not-found, validation, and conflict errors.
- Add readiness diagnostics.

### Phase 3: structured results and auditing

- Return native structured tool results.
- Preserve valid structure when limiting output.
- Add correlation IDs and tool outcome/duration recording.
- Redact secrets recursively.

### Phase 4: policy and write containment

- Mark `call_service`, `reload_domain`, `delete_automation`, and `upsert_automation` transitional.
- Disable or delegate generic physical execution.
- Require optimistic locking and immutable plan approval for retained writes.

### Phase 5: unique engineering analysis

Only after the read foundation is tested and stable, add dependency, reliability, incident, change-impact, and configuration-debt tools.

## Smallest practical next change

Implement **server identity and capability reporting together with a minimal test/CI foundation**.

Exact scope:

- Add `server_info` as a read-only tool.
- Add `list_capabilities` as a read-only tool.
- Add one version/build metadata module.
- Add unit tests for both tool outputs and configuration loading.
- Add CI checks for Python compilation, YAML parsing, and tests.
- Do not move existing functions or alter existing tool behavior in the same change.

Why this is next:

- It resolves the confirmed ambiguity between the Engineering server and standard `ha-mcp`.
- It introduces the first automated safety net before structural refactoring.
- It is backward-compatible and does not affect Home Assistant behavior.
- It establishes metadata needed for future deprecation and capability migration.

## Verification plan for the proposed next change

- Validate `server_info` identifies this server, semantic version, build metadata availability, runtime mode, and HA connection target without exposing tokens or secret paths.
- Validate `list_capabilities` reports every public tool exactly once and labels it native, delegated, transitional, planned, or unavailable.
- Confirm existing 23 tools remain registered and unchanged.
- Confirm Python compilation and YAML parsing in CI.
- Confirm tests pass under the pinned supported Python version.
- Confirm no credential, endpoint secret, Supervisor token, or private URL appears in either response.

## Audit conclusion

The server has a good compact baseline, but the infrastructure should be stabilized before adding analytical tools. The highest-value work is not a broad module split yet. It is identity reporting, tests, reproducible builds, a shared HA client, structured failures, and secret-safe outcome auditing, delivered in small compatible steps.
