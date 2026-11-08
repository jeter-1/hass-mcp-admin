# HAMCP-290: Marketplace inventory read (design)

Status: design for the Release 2.5.0 build on branch
`agent/HAMCP-290-marketplace-read`. The base is `origin/release/2.5.0` at
`62008070e9edc04fd7d31098ec5eb5632dddaad6`.

Authority: this build follows Josh's 2026-11-05 **Option B** direction, which
is recorded on HAMCP-319: usable 8.5 parity plus a bounded Marketplace
inventory read. This note does not make a new product decision. Anything below
marked *implementation choice* is open to architect objection.

Constraints:

- The contract stays **unsigned**. The build signs nothing and creates no
  signed Core sequence.
- Production signing is a separate owner action that Josh takes after 2.5
  merges.

## 1. Upstream surface (exact versions, ADR-021)

The pinned sources are:

- Core `2026.11.0`, tag commit `74cf5a49c63ed8d4d87717844bf14f79c28c9cea`;
- HACS `2.0.5`, tag commit `c0dfd8b44297c3673c21973e2539375a53687a9c`.

All paths below are under `homeassistant/` unless they are HACS paths.

| Fact | Citation |
| --- | --- |
| Marketplace is a default integration, so it is set up unless recovery mode is active | `bootstrap.py:219-231`; recovery set at `:275-280` |
| The websocket commands are registered once in `async_setup`, whether or not a config entry is loaded | `components/marketplace/__init__.py:89-95` |
| There are 20 registered commands | `components/marketplace/websocket/__init__.py:37-62` |
| `marketplace/repositories/list` takes an optional `categories: [str]`, requires admin, is not paginated, and returns every repository whose `last_fetched` is set, **installed or not** | `components/marketplace/websocket/repositories.py:62-89` |
| The record shape is `repository_summary`, with 25 keys including `local_path`, `authors`, `topics` and `stars` | `components/marketplace/websocket/repositories.py:28-59` |
| An entry that is not loaded gets error code `not_loaded` | `components/marketplace/websocket/decorators.py:17,118-122` |
| Repository categories | `components/marketplace/enums.py:7-13` (integration, plugin, template, theme) |
| An unregistered type gets `unknown_command` / "Unknown command." | `components/websocket_api/connection.py:308-314`; `components/websocket_api/const.py:42` |
| Other error codes | `const.py:36` invalid_format, `:43` unknown_error, `:44` unauthorized, `:45` timeout |
| HACS 2.0.5 `hacs/repositories/list` has the same shape plus `can_download`, `country` and `state`, and filters by country and `last_fetched` | HACS `custom_components/hacs/websocket/repositories.py:23-76` |

Disposable-lane observations come from the HAMCP-319 Phase 1 evidence. They
were not repeated here, because this task has no live access.

- Core 2026.11.0 returned **6,194** records in **4,264,900** raw bytes. That
  exceeds aiohttp's default 4 MiB receive bound.
- Sending `offset` is an `invalid_format` error.
- An unknown category returns `[]`.
- `hacs/info` returned `unknown_command`.
- A disabled entry returned `not_loaded`.

Sources: `docs/evidence/hamcp319/phase1/README.md` §"Marketplace read
boundary" and `docs/evidence/hamcp319/phase1/receipts/native-marketplace.json`.

## 2. Ownership (*implementation choice*)

`ha_get_hacs_info` becomes an **Engineering-native** read, keeping the same
public name. It is not a delegated `ha-mcp` dispatch, for four reasons:

1. 9.1 is not admitted on this base.
2. 8.x `ha_get_hacs_info` speaks HACS only. On 2026.11 it fails as
   `capability_unavailable` (HAMCP-251 B1).
3. 9.1's backend detector falls back on `unknown_command` **or any text
   containing "unknown command"**. It also accepts malformed envelopes as
   empty Marketplace info. Both are broader than the exact rule below (Phase 1
   README :75, `receipts/fallback-exact-image.json`).
4. Engineering cannot verify a delegated provider's self-reported `store.api`
   attribution.

How this preserves the one-connector topology:

- The upstream `ha_get_hacs_info` descriptor is **suppressed** in the read
  gateway's exposure loop and recorded as withheld. It is not aliased to
  `ha_mcp__ha_get_hacs_info`.
- The client therefore sees exactly one `ha_get_hacs_info`.
- As implemented, the gateway withholds any delegated automatic read whose
  public name an Engineering-native static tool owns
  (`ENGINEERING_NATIVE_OWNED_DELEGATED_TOOLS`, today exactly
  `{"ha_get_hacs_info"}`). It records the read in `core_withheld` with
  disposition `engineering_native_owned` and reason
  `engineering_native_route_owns_name`. It never aliases it.
- When 8.x is admitted, the connector total is unchanged at 109: 82 static
  tools plus 27 exposed delegated reads. `dynamically_exposed_count` drops by
  one, and `exact_matched_automatic_read_count` is unchanged because the read
  still matches the reviewed policy.
- A bare test server that does not register the static tool still exposes
  the delegated read, so the withholding depends on the static registry, not
  on a name list alone.
- The static Engineering tool count goes from 81 to 82, and the runtime
  profile count from 32 to 33.
- The container lane scripts (`exact_image_read_gateway_acceptance.py`,
  `exact_image_readmission_acceptance.py`) and `codex-context.py` subtract
  native-owned reads from their expected exposure, and require the
  `engineering_native_owned` withheld disposition.
- The existing delegated requirement
  `DELEGATED_CORE_REQUIREMENTS["ha_get_hacs_info"]` is removed. The tool is no
  longer delegated.

## 3. Admission (fail closed, dormant until signed)

New profile:

- capability `core.marketplace_read`;
- class `MARKETPLACE_INVENTORY_READ` (semantic, not mutation-capable);
- adapter `compiled-core-marketplace-read-v1`;
- provider boundary `direct_ha_api`;
- `fallback: "hacs_repositories_list_on_exact_unknown_command"`.

The fallback value enters the contract fingerprint. `_contract` gains an
optional `fallback` keyword that defaults to `"none"`, so every existing
fingerprint is byte-identical.

Where the profile is registered:

- It is listed in `CORE_RUNTIME_CAPABILITY_PROFILES` as a runtime-only,
  dormant profile, following the HAMCP-232 script-trace pattern.
- It is added to `PENDING_ADMISSION_TOOLS` and `F3_ENTRY_ADMISSION_PROFILES`.
- Its static requirement is `("core.marketplace_read",)`.

**No signed registry entry references it.** The tool therefore fails closed at
two independent points:

1. **Gateway**: `pending_signed_registry_admission`, returned as
   `PROVIDER_UNAVAILABLE` / `CoreCapabilityUnavailable` with
   `provider_dispatch_occurred: false`. This happens before the app runs.
2. **Service**: `authority_unavailable`, with zero Home Assistant commands.
   The service calls `route_status` and `authorize_core_dispatch` and then
   re-checks the generation before each command.

The tool becomes available only after Josh's later signed Core sequence
references this profile's exact fingerprint. No candidate registry data is
committed in signed form.

## 4. Public schema and bounds

Input: a JSON object with `additionalProperties: false`. Validation uses exact
keys, and a refused value is never echoed back.

| Field | Type | Bound | Default |
| --- | --- | --- | --- |
| `query` | string | ≤ 200 characters after trimming, no control characters, case-insensitive substring match | `""` |
| `category` | `integration` / `plugin` / `template` / `theme` / null | enum | null |
| `limit` | integer | 1..50 | 20 |
| `offset` | integer | 0..20000 | 0 |

There is no `action` parameter. No detail, release, install, update, remove,
download, refresh, add or configuration operation exists or can be reached.
9.1's `max_results ≤ 100` is replaced by `limit ≤ 50`.

Upstream payloads are **constants**:

- `{"type": "marketplace/repositories/list"}`, and only on the exact fallback,
  `{"type": "hacs/repositories/list"}`;
- no arguments are forwarded;
- category and query filtering happen locally.

Acquisition bounds are enforced before parsing, and any breach fails closed
with no partial page:

- a dedicated bounded reader, modeled on `read_alarmo_inspection`, with
  `trust_env=False`, no retry, and redirects rejected;
- `auth_ok.ha_version` must equal the admitted Core version;
- at most **8 MiB** per command frame (about 2x the observed 4,264,900 bytes),
  64 KiB per auth frame, and at most 4 frames (auth_required, auth_ok and two
  command results);
- a wall-clock cap of the configured HA timeout, never above 30 seconds;
- at most **20,000** records;
- the JSON parse rejects duplicate keys and non-finite numbers;
- at most 2 commands per call, on one authenticated connection;
- the service-level timeout comes from existing settings.

Per-record projection: every store record must be a mapping with a strict
boolean `installed`. Records that are not installed are skipped without
further projection. Every installed record must satisfy the full projection
below, and installed repository ids must be unique. Any violation fails the
whole call.

| Field | Rule |
| --- | --- |
| `id` | Digit string of 1..20 characters (a non-bool non-negative int is normalized to a string) |
| `full_name` | `owner/repo`, matching `^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$`; a `.` or `..` segment is refused |
| `category` | `^[a-z_]{1,32}$` |
| `domain` | null, or `^[a-z0-9_]{1,64}$` (an empty string becomes null) |
| `installed`, `pending_upgrade`, `custom` | Strict booleans |
| `name`, `installed_version`, `available_version`, `description` | Strings or null, passed through the shared sanitizer, then truncated last to 200 / 64 / 64 / 300 characters |

These upstream fields are **dropped**: `local_path`, `authors`, `topics`,
`stars`, `downloads`, `homeassistant`, `file_name`, `hide`, `new`, `status`,
`last_updated`, `config_flow`, `can_install`, `can_download`, `country`,
`state` and `integration_categories`.

Output: `success`, `backend`, `results` (sorted by `full_name` casefold, then
`id`) and the page fields:

- `total_matches`, `offset`, `limit`, `count`;
- `has_more`, and `next_offset` (null on the last page);
- `inventory_fingerprint`, a SHA-256 of the sorted installed projection, so a
  client can detect that the inventory changed between pages;
- `completeness` (`partial` when the sanitizer withheld or truncation
  shortened any text), `fallback_occurred` and `commands_sent`.

`query` matches the **sanitized** `full_name`, `name`, `domain` and
`description`, case-insensitively. A search therefore cannot probe text the
sanitizer withheld.

Every call is an independent snapshot. The response passes through the
standard `response_size_limit`.

## 5. Attribution

- `backend` is `"core_marketplace"` or `"hacs"`. It is decided by which
  command produced the accepted result, never by upstream self-description.
- Metadata is `{"provider": "engineering", "data_providers": ["direct_ha_api"],
  "fallback_occurred": <bool>}`.
- `fallback_occurred` is true only when the HACS command produced the result.
- `METRICS.record_provider_result("direct_ha_api", ...)` is recorded for each
  command.

## 6. The single fallback condition

Fall back to `hacs/repositories/list`, on the same connection, **only if all
of these hold**:

- the Marketplace reply is a well-formed result envelope with matching `id`,
  `type: "result"` and `success: false`;
- `error` is a mapping;
- `error.code == "unknown_command"` exactly.

The message text is ignored.

Negative cases. Each of these fails closed with **no** second command:

- `not_loaded`, `unauthorized`, `invalid_format`, `unknown_error`, `timeout`,
  `home_assistant_error`, or any other code;
- message text such as "Unknown command" combined with a different code;
- a missing or non-mapping `error`;
- a missing `success`;
- a `success: true` with a non-list result;
- an oversized frame, too many records, or a malformed record;
- a transport error, timeout or cancellation;
- an identity mismatch;
- a refused authority re-check.

If HACS also returns `unknown_command`, or fails in any way, the result is
`marketplace_unavailable` with no third command.

On Core 2026.11 the fallback is practically unreachable:

- Marketplace is default-loaded, so its commands are registered.
- A custom HACS cannot load in recovery mode.

It is implemented exactly so that admission on any Core without the
Marketplace commands stays reviewed rather than implicit.

## 7. Closed refusal reasons

| Reason | Error code |
| --- | --- |
| `authority_unavailable` | provider unavailable (also an anonymous caller, with zero commands) |
| `invalid_arguments` | invalid request |
| `marketplace_not_loaded` | unavailable |
| `marketplace_unauthorized` | authorization |
| `marketplace_unavailable` | unavailable |
| `response_too_large` | upstream response limit |
| `malformed_response` | upstream invalid response |
| `identity_mismatch` | unavailable |
| `timeout` | timeout |
| `transport_unavailable` | unavailable |
| `provider_error` | upstream API error (any other failure code, an unexpected exception, or an unknown reason) |

Every refusal carries `{"reason", "fallback_occurred"}` and never contains raw
upstream text.

## 8. Test matrix (`tests/test_hamcp290_marketplace_inventory.py`)

- **Unavailable while unsigned or unadmitted**:
  - the gateway refuses `pending_signed_registry_admission`;
  - the service refuses `authority_unavailable` with zero commands;
  - an anonymous request makes zero commands;
  - a generation change between authority and dispatch is refused.
- **Useful success when admitted**, using a test-only ephemeral-key signed
  registry fixture (`core_registry_fixtures.CoreSigner`). Covered:
  - installed-only filtering;
  - query and category filtering;
  - deterministic order;
  - pagination including the last page, an offset past the end, and
    `next_offset` / `has_more`;
  - the projection drops `local_path` and the other dropped fields;
  - secret-shaped values are redacted;
  - truncation;
  - `inventory_fingerprint` stability.
- **Negative reachability**:
  - every payload ever sent is one of the two constants;
  - a static AST scan of the new modules finds no `marketplace/*` or `hacs/*`
    string other than the two list commands;
  - all 20 registered Marketplace command types and HACS management commands
    are enumerated as unreachable;
  - the tool schema refuses unknown keys such as `action`, `repository_id` and
    `version`.
- **Fallback**:
  - it happens only on an exact `unknown_command` code;
  - each negative case in §6 is parameterized with exactly one command
    observed;
  - HACS `unknown_command` after fallback ends at 2 commands and
    `marketplace_unavailable`.
- **Fail closed on malformed or oversized input**:
  - frame over 8 MiB, records over 20,000;
  - duplicate keys, NaN, bad `id`, `full_name`, `installed` or `category`;
  - a non-list result, a wrong `id` or `type`;
  - no partial page is ever returned.
- **Input bounds**: `limit` 0 / 51 / bool / string, `offset` −1 / 20001,
  `query` over 200 characters or containing control characters, an unknown
  `category`, and refused values never echoed.
- **Gateway**:
  - upstream `ha_get_hacs_info` is withheld with disposition
    `engineering_native_owned`, not aliased;
  - the connector total is unchanged at 109 with 8.x admitted;
  - the static/delegated name overlap is allowed only as exactly
    `{"ha_get_hacs_info"}`.
- **Catalog**: count and digest pins move from 81 to 82, and `capabilities`
  entries carry `signed_core_registry_admission`.
- **Contract**:
  - existing fingerprints are unchanged;
  - the new profile's material contains its fallback value;
  - no signed registry in the repository references it.
- **Defense in depth**:
  - the 64 KiB auth-frame bound is separate from the 8 MiB command bound;
  - a configured timeout above 30 s is capped;
  - an admission change while the page is built does not publish it;
  - the service asks for exactly `core.marketplace_read`;
  - a reader that dispatches outside the two constants, or a third command,
    is refused with `provider_error`.
- **Mutation evidence**: `docs/evidence/hamcp290-mutations/` uses the
  HAMCP-253 runner machinery, with mutants for the fallback predicate, the
  installed filter, bounds, the projection, the admission checks, the command
  constants and attribution. A mutant is killed only by an assertion failure.
  One mutant is equivalent (the frame-count guard, which is unreachable by
  construction). One is caught fail closed at import (an empty static
  requirement stops the runtime from importing); it is reported separately
  and not counted as killed.

## 9. Not in scope

- Detail, release and info lookups.
- Any management command.
- Critical or warning flows.
- A disposable-Core CI lane. That would need a `.github/workflows/` change,
  which this task does not authorize. Real-Core proof remains the HAMCP-319
  Phase 1 receipts plus a later separately authorized lane run.
- Version metadata (`config.yaml`, `.release/`), which is not authorized here.

Also pending the separate signing step, and not changed here:

- `ci/core-contract-read-files.json` and `ci/core-evidence-groups.json` do not
  name `core.marketplace_read`.
- Adding its Core source paths would require regenerating the hash-pinned
  `docs/evidence/hamcp315/core-sources/2026.11.0.json` record, which holds no
  `components/marketplace/` paths today.
- Adding an evidence group needs a lane that produces its result, which means a
  workflow change.
- Until then, `passing_subset` refuses to inherit the profile ("unmapped
  predecessor profile"), which is fail closed.

## 10. Unsigned-window disclosure

While `core.marketplace_read` is unsigned, `ha_get_hacs_info` is unavailable on
every Core. This is a regression on Core 2026.7 through 2026.10 with HACS
installed: there, the delegated 8.x read answered as an automatic read gated by
`core.basic_rest_read`. Once a signed sequence admits the profile for such a
release, the exact `unknown_command` fallback covers that case. Josh owns
whether this window is acceptable for the 2.5 release (*pending Josh*).

## 11. Rollback

The change is source-only and dormant until signed. To roll back:

- revert the PR;
- before signing, nothing at runtime changes except the static catalog entry,
  which is unavailable, and the withholding of upstream `ha_get_hacs_info`.
  That upstream tool already fails on 2026.11 (HAMCP-251 B1);
- after a future signing, revoking or omitting the reference in a later signed
  sequence makes the tool unavailable again without a code change.
