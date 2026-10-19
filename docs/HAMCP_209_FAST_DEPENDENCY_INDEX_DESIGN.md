# HAMCP-209 Fast Dependency Index: Design Note

Register: HAMCP-209 (break-fix, next beta). Parent investigation: HAMCP-171,
[`HAMCP_171_HEALTH_PERFORMANCE_INVESTIGATION.md`](HAMCP_171_HEALTH_PERFORMANCE_INVESTIGATION.md)
section 5 and owner decisions D3 (prefer Home Assistant's API for the loaded
configuration, one request or a small bounded number) and D4 (no direct read of
`automations.yaml` or any configuration file).

Base: `origin/main` `a1236c407aff5203e37328aea43b2b2fa48180f2` (2.4.1-beta.19).
After HAMCP-191 (PR #280, 2.4.1-beta.20) merged, `main` `538a7c7` was merged
into this branch (merge commit `78a2595`, no conflicts) and parity was rechecked.

## 1. Problem

Live beta.16-19 index builds took 33.7-41.5 s. The provider issued one
`GET /api/config/automation/config/{id}` per automation (113 live), with an
average queue wait of about 15.6 s and a maximum of about 33 s. Writes right
after a restart took 35-44 s because helper-dependency preflight requires a
current index and joins the single-flight startup prewarm build.

## 2. Part 1: verification against pinned Core source (ADR-021)

Authority: `ci/support-window.yaml` (current `2026.10.1`, rollback `2026.10.0`,
candidate none). Source was read from the exact tags:

| Lane | Tag | Commit |
| --- | --- | --- |
| current | `2026.10.1` | `abe1fdd7cd6b4de1715c87b6fd87d92699514ef9` |
| rollback | `2026.10.0` | `6a811d3359c7b2076dc9e1cf900843a129c044af` (matches `CORE_RELEASE_REGISTRY.md`) |

Every file cited below is byte-identical between the two tags
(`automation/__init__.py`, `automation/config.py`, `script/__init__.py`,
`config/view.py`, `config/automation.py`, `blueprint/models.py`,
`blueprint/websocket_api.py`, `search/__init__.py`,
`websocket_api/commands.py`). Paths are relative to
`homeassistant/components/`.

### 2.1 Why the current route is slow

- `config/view.py:86-97` `EditIdBasedConfigView.get` takes
  `async with self.mutation_lock` (one `asyncio.Lock` per view, `:51`) and calls
  `read_config`, which runs `_read` -> `load_yaml(path)` on the whole
  `automations.yaml` in the executor (`:160-165`, `:244-249`). Every GET is
  serialized on the lock and re-parses the complete file, so N fetches cost
  O(N^2) parse work. HAMCP-171's model is about 0.277 s per live request.
- `load_yaml` is called with `secrets=None` (`util/yaml/loader.py:40-52`), so a
  `!secret` tag in `automations.yaml` makes every REST read fail.
- Writes use the same lock (`:128`) and return before the reload. The post-write
  hook is scheduled with `hass.async_create_task` (`:134-137`).
  `config/automation.py:22-28` turns that hook into
  `automation.reload {id: <config_key>}`.

### 2.2 Loaded-configuration API

- **WebSocket `automation/config`** (`automation/__init__.py:1252-1273`) takes
  `{"type": "automation/config", "entity_id": str}` and is `@require_admin`. It
  returns `{"config": automation.raw_config}` from the in-memory entity: no lock
  and no file read. A missing entity returns `ERR_NOT_FOUND`.
- **WebSocket `script/config`** (`script/__init__.py:784`) has the same shape
  for scripts.
- **No list-all command exists.** Core 2026.10.x registers no other
  automation/script configuration commands. "One request" is therefore
  impossible. The bounded alternative is one authenticated WebSocket
  connection per build carrying N in-memory command messages, pipelined with
  a small in-flight window. HA's per-connection outgoing limits are
  `PENDING_MSG_PEAK = 1024` and `MAX_PENDING_MSG = 4096`
  (`websocket_api/const.py:21-29`), far above the window used here.
- **Authority.** Engineering already dispatches `automation/config` as a Core
  readmission probe (`ha_core_readmission/source.py:~521`) under the same
  administrative token. Both commands are read-only. No write, service call,
  reload or subscription becomes reachable.

### 2.3 Is the content identical to the REST read?

`raw_config` is set in `automation/config.py:91-215`
(`_async_validate_config_item`):

- **Non-blueprint automation:** `raw_config = dict(config)` of the loaded
  configuration item before validation (`:97-101`). For an item from
  `automations.yaml` this has the same content as the REST read of that file
  entry, provided the file and the loaded state agree. Both responses use
  HA's JSON encoder.
- **Blueprint-based automation:** not identical. After
  `blueprint_inputs.async_substitute()` the code sets
  `raw_config = dict(config)` (`:178-181`) to the *substituted* configuration.
  The `use_blueprint` form survives only in `raw_blueprint_inputs`, which the
  command does not return (`blueprint/models.py:149-186`). Engineering's
  extraction is built on `use_blueprint.path` and `input`, including
  blueprint-role obligations. The WebSocket form would change findings, so it
  cannot be used for these automations.
- **Failed automations** (`UnavailableAutomationEntity`,
  `automation/__init__.py:384-430`) have `_attr_available = False` (state
  `unavailable`) and `referenced_blueprint = None`. Their `raw_config` may be
  either the original `use_blueprint` form (input or substitution failure) or
  the substituted form (schema failure).
- **Detecting blueprint instances without a file read:** WebSocket
  `search/related` `{item_type: "automation", item_id: <entity_id>}`
  (`search/__init__.py:61-85`) calls `_async_search_automation`
  (`:216-227`). That adds `automation_blueprint` from
  `automation.blueprint_in_automation` (`automation/__init__.py:230-240`),
  which reads `referenced_blueprint` (`:586-591`). The lookup is complete for
  every loaded `AutomationEntity`, is in-memory, and is not admin-only.
  Per-blueprint `blueprint/list` plus `search/related` was rejected: it misses
  an automation whose blueprint file has disappeared from the listing.
- **Automations without an `id`:** REST cannot address them. The current
  provider records `automation_id_missing`. WebSocket could read them by
  entity ID, but the source identity would change. For parity the candidate set
  and identity rule are unchanged (follow-up candidate).
- **Automations defined outside `automations.yaml`** (packages,
  `configuration.yaml`): REST returns 404 for them, recorded as
  `automation_config_unreadable`. WebSocket returns their loaded configuration,
  including values resolved from `!secret`. This is the one intentional
  difference: coverage improves, and nothing reported complete before is now
  partial. Excerpts stay bounded and redacted by the existing
  `extraction._bounded` / `redact_data` path and are only emitted at
  `detail_level=evidence`.
- **Loaded versus stored after a write:** a REST POST returns before the
  reload task runs (`config/view.py:134-137`). Directly after a governed write,
  the loaded configuration can lag the stored configuration that governance just
  read back and verified.

### 2.4 Invalidation signals

- `automation_reloaded` is fired after every reload: full, per-ID, and the
  post-write hook (`automation/__init__.py:292-300`). Scripts have no
  equivalent event; `script/__init__.py:230-270` reloads without firing one.
- Engineering's WebSocket client has no subscription support. A reload
  subscription would add a long-lived connection lifecycle (reconnect,
  authority revalidation, event bounds). That is new transport infrastructure,
  out of scope for this break-fix.
- Incremental per-automation hashing is not adopted. Knowing whether an
  automation changed still needs its loaded configuration, which is now the
  cheap part. Extraction (`parsing_indexing_time_ms`) is small next to the old
  fetch cost (section 6). Soft and hard TTLs (600 s / 3600 s), governance
  invalidation and Core-authority invalidation stay unchanged.

**Stop-condition check:** an API that avoids the lock and the re-parse exists
(`automation/config`), so Part 2 proceeds.

## 3. Design

### 3.1 Source selection, per build

1. Inventory is unchanged: REST `/config`, REST `/states`, WebSocket
   `config/entity_registry/list`. Candidates are still `automation.*` states,
   sorted, capped at `MAX_AUTOMATION_SOURCES`. Only candidates with an `id`
   attribute are read; the rest stay `automation_id_missing`.
2. One WebSocket connection,
   `HomeAssistantWebSocketClient.command_batch`. It authenticates once and
   pipelines commands with an in-flight window of 16. The command set is fixed:
   `automation/config {entity_id}` and
   `search/related {item_type: "automation", item_id}`. Any other type or shape
   is refused before dispatch. Core dispatch authority is re-checked before
   every send. Per-command errors are returned per command; transport, auth or
   timeout failures raise.
3. A candidate uses its **loaded** configuration unless one of the following
   applies. In each case it is read from **stored** configuration through the
   unchanged REST route (`clients/paths.py` Route), and the reason is recorded:

   | Reason | Condition |
   | --- | --- |
   | `blueprint_instance` | `search/related` reports an `automation_blueprint`, or the loaded configuration contains `use_blueprint` |
   | `loaded_entity_unavailable` | state is `unavailable` (failed validation; `raw_config` form is ambiguous) |
   | `loaded_config_unreadable` | per-command error, missing or non-object `config`, or a malformed lookup |
   | `loaded_identity_mismatch` | loaded `id` differs from the state's `id` attribute |
   | `pending_write_reload` | a governed write to this ID is pending and loaded != stored |
   | `loaded_source_unavailable` | the WebSocket batch failed as a whole (every candidate) |

4. **Pending-write journal.** The two governed automation write boundaries
   (`AutomationGateway.write`, `ConfigurationResourceGateway._write_rest_configuration`
   for `automation`) record the ID before dispatch. Home Assistant reloads the
   automation in a task created after the write response
   (`config/view.py:128-137`, `config/automation.py:22-28`), so the loaded form
   can briefly predate a write. Recording before dispatch also covers
   ambiguous writes. For a pending ID:
   - The provider reads both forms. If loaded == stored (canonical JSON), the
     ID is cleared and loaded is used.
   - If they differ, stored is used (the old behavior), the reason is
     `pending_write_reload`, and the ID is retained.
   - For `blueprint_instance`, `loaded_entity_unavailable` and
     `loaded_identity_mismatch`, the stored read is used whatever the reload
     timing, so a successful stored read clears the ID.
   - If the ID is no longer in the inventory, a REST read with 404 expected
     runs. A 404 clears it. A stored configuration that is not loaded becomes
     the coverage failure `automation_pending_reload_not_loaded` (partial),
     instead of being silently absent as in the old path.
   - An ID is cleared only if it was not rewritten after the build's snapshot
     (per-ID sequence number).
   - The journal holds 256 IDs. On overflow every candidate is compared
     (loaded vs stored), and the automation coverage carries the coverage
     failure `pending_write_journal_overflow` (partial), because an overflowed
     ID that is not loaded cannot be ruled out. Overflow clears after a build
     in which every comparison agreed, no ID was pending-not-loaded, the loaded
     source was available, and no write happened during the build.
   - Out-of-band edits (UI or file edits outside Engineering) are not
     journaled. The index reports what Home Assistant has loaded, which is the
     configuration that runs (D3). The old path reported the stored file even
     when it had not been reloaded.
5. **Truthful metadata.**
   - Automation coverage `policy` names the source mix and fallback reasons.
   - A whole-source failure also adds a coverage warning.
   - The build profile (and therefore health `last_build_profile`) adds
     `automation_config_source`, `automation_config_source_counts`,
     `stored_config_fallback_reasons`, `loaded_config_connection_count`,
     `loaded_config_command_count` and `pending_write_journal`.
   - `coverage.fallback_occurred` is deliberately not used: elsewhere it means
     provider fallback, and both reads are the same `direct_ha_api` provider.
6. Extraction, blueprint resolution, obligations, risk profiles, coverage
   counts, the read-failure ledger and script diagnostics consume
   `(state, config, failure)` exactly as before.

### 3.2 Protected-file list (final)

Authorized by the task (dependency modules and their call sites):

- `hass_mcp_engineering_beta/ha_mcp_engineering/dependency/provider.py`
- `hass_mcp_engineering_beta/ha_mcp_engineering/dependency/runtime.py`
  (journal hook)
- `hass_mcp_engineering_beta/ha_mcp_engineering/clients/websocket.py`
  (`command_batch`, fixed command set; addition reported)
- `hass_mcp_engineering_beta/ha_mcp_engineering/governance/service.py` and
  `governance/resources.py` (one pre-dispatch journal call each; addition
  reported, **overlaps PR #280**)

Release files are changed only through `promote_next_release.py --apply`.

Not changed: `dependency/index.py`, `health.py` and `application.py` (which
overlap PRs #280 and #281); the script provider; signed registry; workflows;
support window; stable v1.

### 3.3 Not changed and follow-ups

- Scripts still use the delegated `upstream_read_gateway` reader. Moving them
  to `script/config` is a provider-routing change.
- Automations without an `id` keep `automation_id_missing`.
- No `automation_reloaded` subscription.

## 4. Security

- New reachability is limited to two read-only WebSocket commands, already
  dispatched by Engineering (`automation/config`) or non-admin
  (`search/related`). There are no writes, service calls, reloads or
  subscriptions, and no new HTTP route.
- The batch method's command allowlist is enforced inside the client. Core
  dispatch authority is re-checked per message. There is no fallback to any
  other provider.
- Stored reads use the unchanged typed Route. No configuration file is read
  for automations. The existing blueprint-source read path is unchanged.
- Disclosure: package-defined automations become readable, including resolved
  `!secret` values (the stored REST read returned 404 for them). Only bounded
  evidence leaves the provider, through `redact_data` →
  `sanitize_untrusted_data` (credential-named keys, bearer/JWT/URL-userinfo,
  webhook paths, the configured token). A resolved secret in a key or format
  the sanitizer does not recognise is not redacted. This is the same residual
  risk as an inline literal in `automations.yaml`, which the old path already
  read, and only `detail_level=evidence` excerpts carry configuration text.

## 5. Tests, parity and mutations

`tests/test_hamcp209_fast_dependency_index.py` (35 tests, synthetic model in
`tests/hamcp209_fixtures.py`; the client tests use a disposable loopback
WebSocket peer):

- **Parity** (`ExtractionParityTests`): the loaded source and the stored-only
  path give an identical index view and an identical `DependencyIndex`
  snapshot fingerprint. The representative set covers plain, templated
  (`states()`), dynamic (`input_text` target), `label_entities()`,
  blueprint-based, unavailable missing-blueprint and id-less automations. The
  generated set has 113 automations. The blueprint instance is read stored even
  when the loaded form hides `use_blueprint`.
- **Cross-tree parity** (`docs/evidence/hamcp209/parity.py`,
  `parity.json`): the base commit's REST-only provider and this tree give
  identical index views and fingerprints on both fixture sets: 21 findings,
  26 obligations and 7 dynamic references; 339 findings and 452 obligations.
  REST configuration reads drop from 6 to 2 and from 113 to 0.
  The recheck against `538a7c7` (beta.20) after the `main` merge is also
  identical, with the same fingerprints (`recheck_after_main_merge`).
- **Bounded requests** (`BoundedRequestTests`): 113 automations use one
  WebSocket connection with 226 commands, no `automation_config` REST read, and
  at most 5 provider requests in total. Unavailable and id-less candidates are
  not sent. An empty inventory sends no batch.
- **Fallback reporting** (`FallbackReportingTests`): each per-automation
  reason is counted in the profile and named in the coverage `policy`, and
  malformed loaded results are reported. A whole-source failure is reported
  with a warning and stored-only policy for unavailable, authorization and
  malformed errors, and an incomplete batch is treated as a failure.
  `fallback_occurred` stays false.
- **Coverage** (`CoverageTests`): an automation unreadable from both routes is
  partial with `failed_item_count` 1. Partial coverage matches the stored path.
  A package automation is complete from loaded configuration (the intended
  difference).
- **Pending-write journal** (`PendingWriteJournalTests`), 11 tests:
  - retention until reload, then clearing;
  - written-but-not-loaded is partial, including with an empty inventory;
  - a 404 clears an entry;
  - unreadable entries are retained;
  - a rewrite during the build is retained;
  - stable reasons clear;
  - overflow compares every candidate, then clears;
  - overflow with stale loaded configuration is retained;
  - the runtime hook works.
- **Governed write hooks** (`GovernedWriteHookTests`): both automation write
  boundaries journal before dispatch. An invalid identifier is not journaled,
  and script writes are not journaled.
- **Batch client** (`BatchAllowlistTests`, `CommandBatchClientTests`):
  - only the two fixed shapes are admitted, and every other type, extra key,
    wrong domain or non-dict payload is refused;
  - a refused or oversized batch never connects;
  - results are pipelined out of order with interleaved events, matched by
    id, with at most 16 in flight;
  - a per-command `not_found` is returned per command;
  - `unauthorized` and `auth_invalid` raise;
  - duplicate or unknown ids and an early close are malformed;
  - withdrawn dispatch authority stops sending.

Mutations (`docs/evidence/hamcp209-mutations/run_mutations.py`,
`results.json`): 11 of 11 are killed and none survive.

| ID | Mutation | First failing test (recorded) |
| --- | --- | --- |
| M1 | per-automation REST loop restored | `test_113_automations_use_one_connection_and_no_per_automation_read` |
| M2a | missing loaded config read as an empty, complete config | `test_partial_coverage_matches_stored_path` |
| M2b | written automation absent from the inventory never checked | `test_deleted_pending_automation_clears_on_404` |
| M2c | written-not-loaded reported unavailable instead of partial | `test_written_automation_not_loaded_is_partial_with_empty_inventory` |
| M3 | fallback reason not recorded (silent fallback) | `test_unreadable_loaded_and_stored_automation_is_partial` |
| M4 | pending journal ignored | `test_journal_overflow_compares_every_candidate_until_clean` |
| M5 | `search/related` blueprint report ignored | `test_blueprint_loaded_form_is_never_used` |
| M6 | loaded identity check removed | `test_each_per_automation_reason_is_reported` |
| M7 | batch allowlist open | `test_only_fixed_read_shapes_are_admitted` |
| M8 | `AutomationGateway` write not journaled | `test_automation_gateway_journals_before_dispatch` |
| M9 | resource automation write not journaled | `test_resource_gateway_journals_automation_only` |

The first M2a draft (dropping the stored failure code) was equivalent: the
consumer's `failure or config is None` guard still caught it. It was replaced
with the realistic regression above.

## 6. Measurements

Synthetic model (`docs/evidence/hamcp209/measure.py`, `measurement.json`;
same host, back to back). Core's stored route is modelled as one lock plus a
whole-file parse at 2.65 ms per automation, about 0.30 s per read at N=113.
The base run reproduces the live figures (HAMCP-171):

| Metric (113 automations) | Base `a1236c4` | Head | Head, 10x slower WS |
| --- | --- | --- | --- |
| Index build wall time | 34.8 s | 0.42 s | 1.44 s |
| Provider requests | 116 | 4 | 4 |
| `automation_config` REST reads | 113 | 0 | 0 |
| WebSocket batch connections / commands | 0 / 0 | 1 / 226 | 1 / 226 |
| Average / max request queue wait | 15.1 s / 31.9 s | 0 / 0 | 0 / 0 |
| Automation POST issued 50 ms into the build | 2.7 s | 0.32 s | 0.30 s |
| Same POST on an idle model | 0.30 s | 0.30 s | 0.30 s |
| Second index reader during the build (a write's preflight) | 34.5 s | 0.32 s | 1.35 s |

Live reference: builds of 33.7-41.5 s, average queue wait about 15.6 s,
maximum about 33 s, and writes after a restart of 35-44 s.

**Projected live effect.**
- The startup prewarm becomes the inventory reads plus one pipelined batch
  over in-memory configuration, so a write right after a restart runs at
  normal speed.
- Each blueprint instance, unavailable automation, identity mismatch or
  pending write still costs one reported stored read.
- Live WebSocket per-command latency is not measured here (no live access).
  The acceptance live check records it from `last_build_profile`.
