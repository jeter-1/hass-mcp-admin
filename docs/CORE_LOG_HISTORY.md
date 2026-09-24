# Bounded Core log history (unreleased)

`get_core_log_history(limit=100, offset=0)` reads one retained Core journal
window from Supervisor. It complements the existing `get_error_log` System Log
snapshot. Neither source establishes complete historical coverage or proves
that an absent event never occurred.

This source increment adds one static tool: **54 static + 25 admitted delegated
reads = 79** with the healthy reviewed upstream pairing. Published
`2.4.0-beta.1` retains its original **53 + 25 = 78** contract. This increment
does not choose a release version or establish publication/installed acceptance.

## Owner decision and boundary

On September 24, 2026, Josh approved the Engineering-native Core-only reader
after the reviewed ha-mcp 8.5.0 implementation could not guarantee backend
attribution or bounded downloading. Josh subsequently approved the source
manifest additions `hassio_api: true` and `hassio_role: homeassistant` after
being told that Supervisor's role also permits Core restart, stop, and update.
Deployment is a separate decision. The abandoned Core-proxy transport is not
retained as a fallback.

Responsibilities: the bounded reader, schema, attribution, sanitization and
failure semantics belong to Engineering; granting the role during installation
belongs to deployment; choosing useful windows and stopping on repeated content
belongs to the client. Script inventory expansion and artifact provenance are
outside this increment.

The role is **not read-only at the process boundary**. A compromised Engineering
process could use the broader Core administration permission. The tool itself
has no caller-controlled host, path, method, source, headers, app slug, credential,
follow mode, or write operation. It makes one GET to the literal
`http://supervisor/core/logs`, using only the add-on's existing `SUPERVISOR_TOKEN`.
It refuses missing add-on context rather than substituting `HA_TOKEN` or another
backend. No new credential setting or client-facing connector is introduced.

## Contract

| Property | Behavior |
| --- | --- |
| Inputs | Strict integers: `limit` 2–200, `offset` 0–10000; all other fields rejected before SDK coercion |
| Provider / transport / source | `supervisor_core_logs` / `supervisor_api` / `core_journal` |
| Request | Fixed GET; `Range: entries=:-{offset + limit - 1}:{limit}`; no redirects, retries, fallback or automatic paging |
| Bounds | One concurrent read; network deadline of 10 seconds (or lower configured HA timeout), 256 KiB response body consumed, 32 KiB sanitized log text in the serialized response (lower when response budget requires it) |
| Representation | UTF-8 `text/plain`, identity encoding; other representations refused |
| Evidence | Original source order, timestamps and multiline text; no invented event times; arrival time is labeled separately |
| Sanitization | Existing fail-closed redactor plus exact Supervisor/Core/access secrets; sanitization precedes shortening; incomplete final lines are omitted |
| Completeness | Always partial; retention and journal-window completeness unknown, `has_more=null` |
| Navigation | Suggested older offset only for nonempty, untruncated output within the bound; never a verified cursor |
| Audit / metrics | Request correlation, selected provider, source, timing and actual dispatched outcome; no log body added to audit context |

`downloaded_bytes` counts response-body bytes consumed by this reader, not raw
socket traffic; HTTP/OS buffering can read ahead. The byte cap bounds application
evidence processing. `download_complete=true` means HTTP EOF was observed before the byte cap; it
does **not** certify that Supervisor completed the requested journal window.
Supervisor can close a started stream after a journal error without returning
an HTTP error. Lines are not journal-entry counts. Rotation or growth can cause
overlap, gaps or repetition between offset windows. Compare the sanitized
content fingerprints and stop on repeated output. Do not automatically continue
until an empty page and then claim history is exhausted.

Authorization failures, missing routes, other HTTP failures, unavailable
transport, deadlines, malformed representations, invalid encoding and
sanitization failure return structured failures with no response-body echo.
Cancellation closes the request and releases the single-read slot; it is not
retried. A truncated successful read remains explicitly partial. Known retained
text is useful evidence even though retention coverage remains unknown.

The new Supervisor read requires no Core API capability or healthy Core process;
Core logs remain useful during a Core outage. Supervisor authentication and the
dedicated native routing contract enforce this fixed read. No signed Core
profile or registry data is added or rewritten, and existing tools retain their
Core requirements. Signed Core authority does not certify Supervisor semantics.

## Exact upstream evidence

Reviewed Supervisor **2026.09.1**, commit
[`40e3ee7640a3c44abe67f1a1397f39c3cd949806`](https://github.com/home-assistant/supervisor/tree/40e3ee7640a3c44abe67f1a1397f39c3cd949806):

- `supervisor/api/middleware/security.py`: app permission and role enforcement;
  the `homeassistant` role includes broader Core operations.
- `supervisor/api/__init__.py`: fixed `/core/logs` registration selects the
  `homeassistant` journal identifier.
- `supervisor/api/host.py`: Range forwarding, plain-text streaming and late
  stream-error behavior.
- `supervisor/api/utils.py`: supported token-header extraction.
- `supervisor/api/proxy.py`: the Core proxy rejects the nested hassio route;
  its forwarding header allowlist also omits Range.

Tests preserve licensed source fragments with exact revision, original line
positions and hashes in `tests/fixtures/supervisor_core_logs_2026_09_1/`.
They execute the upstream security and HTTP-handler code with synthetic
installed-app and journal boundaries. The separate
`scripts/core_log_supervisor_probe.py` runs the actual Engineering reader in its
Python 3.12 process against Supervisor's full API routing, app model and security
middleware in Python 3.14. It checks useful retained reads, missing permission,
wrong role, and a late journal stream error. Docker, D-Bus and journal services
remain synthetic; this is assembled API integration, not booted HAOS or
installed-image acceptance. The installed Supervisor version has not been
observed during implementation. Follow ADR-021 for assembled integration
and deployment evidence; source fragments alone do not close those gates.

## Release and installed acceptance

Before release, select the next version and update its release/acceptance
contract in a separately authorized release transition. Preserve existing
signed authority, upstream admission and no-fallback controls. Retain exact
Supervisor assembled integration evidence, independent review and candidate CI.

After separately authorized deployment:

1. Bind the expected image, source and new version to publication evidence;
   record the actual Supervisor version and installed app permission/role.
2. Check normal health, Core authority, upstream admission and settlement.
   Fresh public catalog evidence must show the additive tool and its strict
   read-only schema; existing tools remain unchanged.
3. Make one bounded Core log read and, if useful, one older window. Verify
   attributable retained evidence, redaction, bounds, partial completeness and
   no fallback. Absence of a chosen event is not failure or proof of absence.
4. Recheck existing native/delegated reads, System Log snapshot and final
   settlement. No device cycle, approval or write is required for this reader.

If the read is denied, preserve the failure and inspect the installed permission
and exact Supervisor contract; do not retry through the Core proxy. Recovery is
to deploy the previously accepted image **and restore its manifest permissions**
through the normal reviewed deployment process. An image downgrade alone must
not be described as removing the Supervisor role. This change introduces no
persisted record format or data migration.

## Reproducing the disposable integration

Use the exact Supervisor commit above in a disposable checkout whose path has
no hidden components (Supervisor excludes app fixtures under dot-directories).
Use its declared Python 3.14 runtime and pinned `requirements.txt` and
`requirements_tests.txt`. The host must provide the native library dependencies;
task-local extracted libraries are sufficient. Do not mount a Docker socket or
use production data. Its fixtures provide an isolated D-Bus session and mocks.

Copy `scripts/core_log_supervisor_probe.py` to
`tests/api/test_engineering_core_logs.py` in that disposable checkout. Set
`ENGINEERING_PROBE_REPO` to the Engineering candidate checkout and
`ENGINEERING_PROBE_PYTHON` to its Python 3.12 test interpreter, then run:

```text
python -m pytest -q tests/api/test_engineering_core_logs.py
python -m pytest -q tests/api/test_homeassistant.py::test_api_core_logs tests/api/test_host.py -k "core_logs or advanced_logs"
```

The probe refuses a different Supervisor commit and replaces only the reader's
internal endpoint constant with the fixture's random loopback address. It does
not add a configurable runtime endpoint. Keep the probe hash, both repository
revisions, environment dependency inventory, commands and results in evidence.
