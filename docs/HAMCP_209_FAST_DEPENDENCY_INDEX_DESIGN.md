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
  partial. What can surface from that configuration, and the fix to the
  effect projection found while preparing the review, are in §4.
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
   | `loaded_source_unsupported` | the WebSocket client has no batch read at all (every candidate; not the production client) |

   **Correction (independent review M1).** For `loaded_config_unreadable`,
   `loaded_identity_mismatch` and `loaded_source_unavailable` the running
   configuration could not be read, so a successful stored read does not
   prove what is loaded (an unreloaded or out-of-band edit can differ). Each
   such automation adds the coverage failure `loaded_config_unavailable`, and
   automation coverage is **partial**. Health (`last_build_profile`) reports
   `automation_partial_reasons` and the `loaded_config_unavailable` count.
   `blueprint_instance`, `loaded_entity_unavailable` and the id-less case
   (`automation_id_missing`, a read failure as before) are parity-equivalent
   with the old stored path and stay complete.

   `loaded_source_unsupported` is also complete. It applies only when the
   client has no `command_batch` callable, so there is no loaded source to
   fail, and the build is the pre-HAMCP-209 stored-only path unchanged (no
   WebSocket connection; a coverage warning names the reason). The production
   `HomeAssistantWebSocketClient` always has `command_batch`. A batch read
   that exists but raises, of any exception type, is
   `loaded_source_unavailable` and partial
   (`test_batch_failure_is_not_treated_as_unsupported`, mutation M1c). The
   in-module parity reference is this path, without a patch seam.

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
   - For `blueprint_instance` and `loaded_entity_unavailable`, the stored
     read is used whatever the reload timing, so a successful stored read
     clears the ID. (`loaded_identity_mismatch` no longer clears it; see M1.)
   - If the ID is no longer in the inventory, a REST read with 404 expected
     runs. A 404 clears it. A stored configuration that is not loaded becomes
     the coverage failure `pending_write_unreconciled` (partial), instead of
     being silently absent as in the old path.
   - An ID is cleared only if it was not rewritten after the build's snapshot
     (per-ID sequence number).
   - **Correction (independent review M2).** A journaled write this build
     cannot account for is *unreconciled*: not loaded yet, or its stored
     read failed. It stays in the journal, and every build is partial with
     the coverage failure `pending_write_unreconciled` until a build accounts
     for it (loaded == stored, stable-reason stored read, or 404).
   - The journal holds 256 IDs (bounded storage). A write beyond the bound
     sets an explicit overflow flag and count. Its identity is unknown, so no
     build can show that it was reloaded: the flag is never cleared by a
     build, every build compares every candidate with its stored form, and
     every build is partial with `pending_write_unreconciled`
     (`source_id` `dependency_provider_pending_writes`) until the add-on
     restarts. A candidate routed to the stored read only by the overflow
     whose stored read fails (a package automation) keeps its loaded
     configuration. The journal is in memory; a restart loses it, as before.
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
  `!secret` values (the stored REST read returned 404 for them). What the
  index keeps from automation configuration, and where it can surface:
  - findings and dynamic references from the index carry entity IDs,
    configuration paths and bounded identity fields (`_bounded` →
    `redact_data` → `sanitize_untrusted_data`), and no configuration
    excerpt. Excerpts exist only on the reliability path, which reads the
    stored configuration and is unchanged;
  - literal `label_entities()` selectors pass through the same sanitizer;
  - the helper and entity-metadata effect projection (`effect_targets`,
    `effect_data`) showed literal targets (lowercased) and a few allowlisted
    data fields (`brightness`, `transition` and similar) verbatim. A resolved
    secret placed there reached helper-state plan disclosures. A lowercased
    JWT also evaded the case-sensitive JWT rule, so plan persistence screening
    did not catch it.

  **Fix (found while preparing the review), replaced after independent
  review H1.** The first fix was a denylist: a value was digested only if the
  shared sanitizer recognized it. The review showed that a recognized JWT
  prefixed with `sha256:` or followed by `|ordinary` bypassed it (the prefix
  was trusted as a generated digest, and the delimiter moved the screened
  suffix), reaching helper-state plan responses and plan files lowercased.

  The second fix, an allowlist based on identifier shape (grammar, piece
  length, digit runs, letter-digit alternation), was replaced after delta
  review H1 residual: a high-entropy lowercase identifier and a
  dictionary-word literal both passed it.

  `dependency/provider.py` `_EffectDisplayFilter` now admits a value for
  verbatim display by **registry membership** at every verbatim position
  (`action_domains`, `services`, `effect_targets`, `effect_data`). Otherwise
  the position shows `redacted_sha256:<digest>`. A value is shown verbatim
  only if one of the following holds and the shared sanitizer, including the
  provider's known secret, leaves every original spelling unchanged:
  - **target** (`path|selector|value`): the path is an action step path that
    the projection emits for this exact configuration, followed by the
    field spelling for that selector; and the value is a registered ID of
    the selector's kind:
    - entity: the state machine or the entity registry, both already read by
      the scan;
    - device, area, floor or label: `config/<kind>_registry/list`.
    The enum words `all`/`none` are admitted only as entity targets that were
    configured literally;
  - **service** or **domain**: the service is in Home Assistant's service
    registry (`GET /services`, already in the reviewed REST route templates).
    The domain is a service-registry domain, an entity-ID domain or an
    entity-registry platform;
  - **data** (`path.data.key=value`): the step is an emitted step path. The
    key is a field of that step's registered service schema (fields inside
    `sections` count; a section name does not) or a target key. The value is
    a number, `true`/`false`/`null`, or a fixed HVAC/preset enum word that was
    configured literally. A numeric or enum value is screened by the sanitizer
    and the known-secret check like any other value (review M); a match
    digests the value for display only and never refuses a plan.
    `event_data` keys and nested data keys are always digested.

  A digest token (`sha256:`, `dynamic_sha256:`, `oversized_sha256:`,
  `overflow_sha256:`) is shown as generated only when its text is not itself a
  configuration string; provenance never comes from a prefix. A token of an
  unknown shape is digested whole.

  Registry reads are display-only, bounded (20,000 registry entries; 10,000
  services and 100,000 service fields), and use no new permission:
  - one `GET /services` per build that has automation profiles;
  - a device, area, floor or label registry read only when a target of that
    kind is not already an entity-registry member or a generated digest.
  A malformed or oversized response is unavailable. The scan profile reports
  the sources (`effect_display_registry`, with
  `complete`/`unavailable`/`not_needed` per kind) and
  `effect_display_unverified_profiles`.

  When the registry that could admit a value is unavailable, the value is
  digested (fail closed). Its profile is marked `presentation_truncated`, and
  the entity-metadata coverage carries the warning that registry data for
  effect display was unavailable.

  **Binding.** The projection's raw `effect`, profile and index fingerprints
  are computed upstream from the raw values, and they are unchanged. The
  aggregate helper `evidence_fingerprint` (`stable_hash` over the
  `downstream_profiles` material) does change:
  - it covers the displayed values and `presentation_truncated`, so it
    changes whenever a value is digested;
  - that includes a change in registry availability or membership with an
    unchanged configuration (for example, the service registry is briefly
    unavailable, or a device is removed).
  A plan bound to the earlier evidence then reports stale evidence and must
  be re-planned. That fails closed; it never admits a write.

  Residuals (disclosed):
  - an entity target that is in neither the state machine nor the entity
    registry is digested, so a target that does not exist yet is not shown;
  - nested data keys and `event_data` keys are always digested;
  - while registries are unavailable, entity-metadata label matching cannot
    match a digested label or area target;
  - each build adds one `/services` request, and a target-registry read when
    needed. The Pi measurements were not re-run after this change.

  Residual risk: a resolved secret that is itself a registered entity,
  device, area, floor or label ID, a registered service, or a number or enum
  that the sanitizer and the known-secret check do not recognize, is shown as
  written. Home Assistant already exposes those values through its registries.

  `ResolvedSecretDisclosureTests` plants secret-shaped values (JWT, bearer,
  credential keys, URL userinfo and query, webhook ID, template and label
  selector text, literal target and allowlisted data field) in a loaded-only
  package automation and a stored automation. It then runs dependency, impact,
  integrity, reliability, incident, handoff (structured and markdown),
  helper-state and entity-metadata outputs, and asserts that no planted value
  appears. Positive controls show that the package automation is analyzed and
  that the projection carries the digests. Without the fix, the test fails on
  the helper output (`safefield`, `target`, `targetjwt`).


## 5. Tests, parity and mutations

`tests/test_hamcp209_fast_dependency_index.py` (57 tests, synthetic model in
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
  The recheck against `538a7c7` (beta.20) after the first `main` merge is also
  identical, with the same fingerprints (`recheck_after_main_merge`). So is the
  recheck against `c618884` (beta.22) after the second merge
  (`recheck_after_beta22_merge`). The rechecks after the first and second
  review corrections (`recheck_after_review_corrections`,
  `recheck_after_registry_display`) are identical against all three bases.
  The generated fixture now lists the lights its automations target in the
  state machine, which changes that set's view hash for every tree alike.
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
- **Pending-write journal** (`PendingWriteJournalTests`), 13 tests:
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
- **Resolved-secret disclosure** (`ResolvedSecretDisclosureTests`): see §4.
- **Effect display by registry membership** (`EffectDisplayAllowlistTests`,
  `EffectDisplayScanTests`, `EffectDisplayRegistryScanTests`,
  `EffectDisplayPlanPersistenceTests`):
  - runtime-built planted values are digested in every position of the
    filter, of a real scan's profile, and of a real helper-state plan's
    creation response and persisted plan files. They include the review's
    JWT, a `sha256:`-prefixed, `|`-delimited and lowercased JWT, random hex
    and base62, random letters, URL credentials, a base64 blob, a
    digest-shaped configuration string, the delta review's high-entropy
    lowercase generator and an unregistered dictionary-word literal;
  - registered entity, device, area, floor and label IDs, registered
    services and domains, schema data keys (including keys inside a service
    `sections` entry), numbers, booleans, literally configured enum words
    and generated digests stay verbatim;
  - each position needs its own registry: a value registered for another
    kind, service or step is digested;
  - a registered value, number or enum word that the sanitizer or the known
    secret matches is digested, and the plan still succeeds;
  - an unavailable or malformed service or target registry digests, marks the
    profile `presentation_truncated` and adds the coverage warning;
  - a target registry is read only when needed, and the service-registry
    schema is validated and bounded.
- **Correction-batch coverage** (M1, M2): the review's WebSocket-failure
  reproduction is partial and names the automation whose loaded-only
  dependency is unverified; the review's overflow reproduction is partial on
  both builds; a sticky overflow survives a clean rewrite.
- **Existing suites touched by the corrections.**
  `test_rc2dev4_release_hardening` pins the no-batch breakdown, which is now
  the pre-HAMCP-209 one on `main` (3 `automation_config`, reason
  `loaded_source_unsupported`, complete), plus the display-only
  `effect_display_service_registry` read: 7 requests. The loaded breakdown
  is 5 requests. In `test_core_semantic_continuity`
  one test adds an automation, and its `_Network` socket cannot pipeline a
  batch, so the loaded source fails and M1 makes the build partial; the test
  now serves the loaded form through `command_batch` so it still proves a
  low-risk, executable plan. That file was not in the PR before this
  correction batch.

Two existing tests change. `test_rc2dev4_release_hardening`
`test_cold_build_profile_has_bounded_request_breakdown` pinned the old
per-automation breakdown (3 `automation_config` reads, 6 requests). It now pins
the loaded breakdown: one `loaded_automation_config_batch`, now with one
`effect_display_service_registry` read (5 requests; 4 before correction
batch 2). A new sibling test pins the stored-only case: a client without
`command_batch` gives the pre-HAMCP-209 breakdown (3 `automation_config`,
`loaded_source_unsupported: 3`) plus the service-registry read, 7 requests.
CI shard 5 found this pin in run 39286251867.
The focused module set used locally had missed it.

`test_hamcp191_shared_sanitizer` keeps a committed table of every sanitizer
call site. It gains one row for the new `redact_data` call in
`_EffectDisplayFilter._clean` (family `analysis`; first added for the
replaced `_display_safe_effect_tokens`). The focused rerun found the
missing row (`test_every_sanitizer_call_site_is_in_the_committed_table`).

Mutations (`docs/evidence/hamcp209-mutations/run_mutations.py`,
`results.json`): 47 of 47 are killed and none survive.

| ID | Mutation (design item) | First failing test (recorded) |
| --- | --- | --- |
| M1-per-automation-rest-loop-restored | brief: the per-automation REST loop restored (request-count test must fail) | `test_113_automations_use_one_connection_and_no_per_automation_read` |
| M2a-missing-automation-reported-complete | brief: a missing automation (loaded config not found) read as an empty, complete config | `test_partial_coverage_matches_stored_path` |
| M2b-pending-written-automation-never-checked | design 4: a written automation absent from the loaded inventory reported as complete | `test_deleted_pending_automation_clears_on_404` |
| M2c-pending-not-loaded-reported-unavailable | design 4: with no other automation, a written-not-loaded automation is partial, not unavailable | `test_written_automation_not_loaded_is_partial_with_empty_inventory` |
| M3-silent-fallback | brief: the stored fallback is reported, never silent | `test_unreadable_loaded_and_stored_automation_is_partial` |
| M4-journal-ignored | design 4: a pending write is read from stale loaded config | `test_stored_read_failure_for_pending_write_is_not_cleared` |
| M5-blueprint-search-ignored | design 2: a blueprint instance is read from substituted loaded config | `test_blueprint_loaded_form_is_never_used` |
| M6-identity-unchecked | design 2: a loaded config for another id is accepted | `test_each_per_automation_reason_is_reported` |
| M7-allowlist-open | security: the batch admits any command | `test_only_fixed_read_shapes_are_admitted` |
| M8-governed-write-not-journaled | design 4: the governed automation write skips the journal | `test_automation_gateway_journals_before_dispatch` |
| M9-resource-write-not-journaled | design 4: the resource automation write skips the journal | `test_resource_gateway_journals_automation_only` |
| R1-sanitizer-decision-bypassed | review: the shared-sanitizer decision in the effect display filter always passes | `test_known_numeric_and_enum_values_are_digested` |
| H1a-denylist-instead-of-allowlist | H1: values the sanitizer does not recognize are shown verbatim (denylist) | `test_each_position_requires_registry_membership` |
| H1b-hash-prefix-trusted | H1: a digest-shaped configuration string is trusted as a generated digest | `test_enum_words_require_a_literal_configuration` |
| H1c-target-position-bypasses-filter | H1: the effect-target position is copied verbatim | `test_jwt_variants_are_digested_in_plan_response_and_files` |
| H1d-data-position-bypasses-filter | H1: the effect-data position is copied verbatim | `test_generated_lowercase_value_is_digested_in_every_position` |
| H1e-service-position-bypasses-filter | H1: the service position is copied verbatim | `test_generated_lowercase_value_is_digested_in_every_position` |
| H1f-domain-position-bypasses-filter | H1: the action-domain position is copied verbatim | `test_generated_lowercase_value_is_digested_in_every_position` |
| H1g-data-enum-provenance-ignored | H1: a data enum word is shown without being configured literally | `test_enum_words_require_a_literal_configuration` |
| H1h-target-enum-provenance-ignored | H1: a target enum word (all/none) is shown without being configured literally | `test_enum_words_require_a_literal_configuration` |
| H2a-shape-heuristic-restored | H1 residual: identifier shape admits a target, service, domain and data key (no registry) | `test_each_position_requires_registry_membership` |
| H2b-entity-registry-skipped | H1 residual: a entity target is shown without registry membership | `test_each_position_requires_registry_membership` |
| H2b-device-registry-skipped | H1 residual: a device target is shown without registry membership | `test_each_position_requires_registry_membership` |
| H2b-area-registry-skipped | H1 residual: a area target is shown without registry membership | `test_each_position_requires_registry_membership` |
| H2b-floor-registry-skipped | H1 residual: a floor target is shown without registry membership | `test_each_position_requires_registry_membership` |
| H2b-label-registry-skipped | H1 residual: a label target is shown without registry membership | `test_each_position_requires_registry_membership` |
| H2c-service-registry-skipped | H1 residual: a service is shown without service-registry membership | `test_each_position_requires_registry_membership` |
| H2d-domain-registry-skipped | H1 residual: a domain is shown without registry membership | `test_each_position_requires_registry_membership` |
| H2e-data-key-schema-skipped | H1 residual: a data key is shown without being in the service schema | `test_each_position_requires_registry_membership` |
| H2f-data-key-any-service-schema | H1 residual: a data key of any registered service admits this step's key | `test_each_position_requires_registry_membership` |
| H2g-target-step-path-unchecked | H1 residual: a target path that is not a projected step path is accepted | `test_unregistered_values_are_digested_in_every_position` |
| H2h-registry-member-sanitizer-skipped | H1 residual: a registry member is shown without the sanitizer check | `test_registered_value_the_sanitizer_recognizes_is_digested` |
| H2i-unavailable-registry-treated-complete | H1 residual: an unread target registry is treated as complete (not reported unverified) | `test_unavailable_registry_fails_closed_and_is_partial` |
| H2j-unverified-not-partial | H1 residual: a profile with unverified digests is not marked presentation-truncated | `test_unavailable_registry_fails_closed_and_is_partial` |
| H2k-unverified-warning-dropped | H1 residual: the coverage warning for unverified display values is dropped | `test_unavailable_registry_fails_closed_and_is_partial` |
| H2l-service-section-is-a-key | H1 residual: a service-schema section name is admitted and its keys are not | `test_service_registry_schema_is_validated_and_bounded` |
| H2m-malformed-service-registry-accepted | H1 residual: a malformed service row is skipped instead of failing closed | `test_service_registry_schema_is_validated_and_bounded` |
| M3a-number-literal-sanitizer-skipped | M: a number or JSON literal is shown without the sanitizer and known-secret check | `test_known_numeric_and_enum_values_are_digested` |
| M3b-data-enum-sanitizer-skipped | M: a data enum word is shown without the sanitizer and known-secret check | `test_known_numeric_and_enum_values_are_digested` |
| M3c-target-enum-sanitizer-skipped | M: a target enum word is shown without the sanitizer and known-secret check | `test_known_numeric_and_enum_values_are_digested` |
| M1a-unverified-loaded-reported-complete | M1: a stored read standing in for unreadable loaded configuration is complete | `test_batch_failure_is_not_treated_as_unsupported` |
| M1c-batch-failure-reported-unsupported | M1: a batch read that raises is reported as an unsupported client (complete) | `test_batch_failure_is_not_treated_as_unsupported` |
| M1b-identity-mismatch-treated-stable | M1: a loaded identity mismatch verifies the stored read | `test_each_per_automation_reason_is_reported` |
| M2d-overflow-cleared-after-build | M2: the journal overflow is cleared after a build (the pending write is forgotten) | `test_journal_overflow_is_sticky_and_always_partial` |
| M2e-pending-cleared-after-partial-build | M2: pending writes are cleared after a partial build | `test_stored_read_failure_for_pending_write_is_not_cleared` |
| M2f-overflow-not-reported | M2: an overflowed journal does not make the build partial | `test_journal_overflow_is_sticky_and_always_partial` |
| M2g-unreconciled-read-failure-not-reported | M2: a journaled write whose read failed is not reported unreconciled | `test_stored_read_failure_for_pending_write_is_not_cleared` |

The first M2a draft (dropping the stored failure code) was equivalent: the
consumer's `failure or config is None` guard still caught it. It was replaced
with the realistic regression above.

## 6. Measurements

Synthetic model (`docs/evidence/hamcp209/measure.py`, `measurement.json`;
same host, back to back, re-run after correction batch 2; the head's fifth
request is the display-only `effect_display_service_registry` read). Core's
stored route is modelled as one lock plus a whole-file parse at 2.65 ms per automation, about 0.30 s per read at N=113.
The base run reproduces the live figures (HAMCP-171):

| Metric (113 automations) | Base `a1236c4` | Head | Head, 10x slower WS |
| --- | --- | --- | --- |
| Index build wall time | 34.6 s | 0.45 s | 1.46 s |
| Provider requests | 116 | 5 | 5 |
| `automation_config` REST reads | 113 | 0 | 0 |
| WebSocket batch connections / commands | 0 / 0 | 1 / 226 | 1 / 226 |
| Average / max request queue wait | 15.1 s / 31.9 s | 0 / 0 | 0 / 0 |
| Automation POST issued 50 ms into the build | 2.7 s | 0.32 s | 0.30 s |
| Same POST on an idle model | 0.30 s | 0.30 s | 0.30 s |
| Second index reader during the build (a write's preflight) | 34.5 s | 0.35 s | 1.36 s |

Live reference: builds of 33.7-41.5 s, average queue wait about 15.6 s,
maximum about 33 s, and writes after a restart of 35-44 s.

**Projected live effect.**
- The startup prewarm becomes the inventory reads, one service-registry
  read and one pipelined batch over in-memory configuration, so a write right after a restart runs at
  normal speed.
- Each blueprint instance, unavailable automation, identity mismatch or
  pending write still costs one reported stored read.
- Live WebSocket per-command latency is not measured here (no live access).
  The acceptance live check records it from `last_build_profile`.
