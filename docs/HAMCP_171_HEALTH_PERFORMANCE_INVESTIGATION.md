# HAMCP-171 health and dependency-index performance investigation

Status: **read-only investigation report**. It changes no runtime, test, release,
workflow, registry or stable-v1 file. Nothing here authorizes an implementation;
each proposed change needs a separately scoped, reviewed task, and the owner
decisions in [section 8](#8-owner-decisions) come first.

Base: `origin/main` at `27df75b` (Engineering `2.4.1-beta.15`). All `file:line`
references are against that commit. Runtime paths are relative to
`hass_mcp_engineering_beta/ha_mcp_engineering/`. Where a bare file name is
used, `service.py`, `storage.py`, `task_storage.py`, `models.py` and
`resources.py` mean `governance/`; `runtime.py` means `f3_runtime/runtime.py`
(`governance/runtime.py` and `dependency/runtime.py` are always written in
full); `sanitization.py` and `application.py` are at the package root. In the
"Where" column of the section 2 phase table a bare `:NNN` is
`governance/service.py`; elsewhere it refers to the file most recently named
in the same cell or sentence.

**Base update (`2.4.1-beta.16`).** While this report was in validation,
`main` advanced to `119be1b` (PR #267, HAMCP-162, `2.4.1-beta.16`), and this
branch merged it. Of the files cited here, only `sanitization.py` changed. Its
default pass now drives the same 12 segment rules and 8 serialized patterns
(20 `_replace` passes per segment) from a `_SEGMENT_RULES` table, and
`_sanitize_segment` (now `:411`) is still pure. The new strict pass
(`identity_markers_only`) is used only by `providers/upstream_yaml_reader.py`,
never by the health path, and `governance/resources.py` is unchanged. All
`file:line` references stay pinned to `27df75b`. Re-run offline on the same
435-plan store with `119be1b` code: the validation worker took 18.5 s (21.1 s
at `27df75b`), and the per-job memo took 3.8 s (4.8×) with identical verdicts
and the same 97.9% hit rate. The conclusions and owner decisions are
unchanged.

## Evidence labels

| Label | Meaning |
| --- | --- |
| **Live (reported)** | Production numbers in the HAMCP-171 task (beta.5 to beta.15). This investigation did not observe them; it did not access live Home Assistant or the deployed MCP endpoint. |
| **Source** | Read from `27df75b` source or from Home Assistant Core `2026.10.1` source. |
| **Offline** | Measured in this session on a disposable synthetic store written by the current writers with fake gateways. The host was an i7-10700 (6 vCPU), CPython 3.12. These are not production numbers. |
| **Model** | Arithmetic derived from the live and source evidence above. |

The Antigravity findings **E-1/E-2** named in the task brief were not available
to this session. They are not in the register row, `~/handoffs` or the register
log, so this report neither confirms nor contradicts them. See
[section 10](#10-limitations-and-follow-up-evidence).

## 1. Summary of answers

1. **Where the ~32 s `validation_wait` goes.** It is CPU spent in the
   persistence-safety detector. For each contract-v2 plan, `plan.to_dict()` is
   run through `sanitize_untrusted_data`. That builds a full redacted copy and
   runs about 20 regex passes over *every string value and every dict key*;
   the copy is then thrown away. The work runs in one `asyncio.to_thread`
   worker that holds the GIL, so the event loop slows down for the whole
   window. Offline, on a 435-plan store, health took 23.4 s and validation
   21.4 s (92%, the same share as live), at about 1.5 ms per KB of plan JSON.
   Nothing in the async path reuses an earlier result; this is by design
   (`service.py:12755-12759`, `docs/HEALTH_PERFORMANCE.md`).
2. **Who causes the 77k task reads.** The 30-second F3 periodic recovery
   loop. Its historical orphan sweep calls `task_repository.get(parent)` once
   per F3 parent on every pass, before it knows whether that child needs any
   work (`f3_runtime/runtime.py:5535-5547`). `get()` always deserializes
   (`task_storage.py:550-572`). Model: 73,085 reads ÷ ~360 passes in 3 h ≈ 203
   reads per pass, which is about one per parent. For a parent that dispatched,
   the predicate that consumes the read (`_orphan_cleanup_pending`,
   `runtime.py:4835-4866`) returns `False` without using any parent field
   except `state`, `provider_attempts` and `dispatched_at`. Offline, all 239
   parent reads per pass were avoidable, and each pass blocked the loop for
   0.39 s in one stretch.
3. **Caching safety.** A verdict cache keyed only by
   `(name, st_ino, st_size, st_mtime_ns)` is **not safe**. Anyone who can write
   the file can keep all four values the same (same-size edit plus `utime`),
   and inode reuse lets the key collide. A cache keyed by the **SHA-256 of the
   exact bytes that were deserialized**, plus the detector version and a keyed
   digest of the sensitive-value set, held **in memory only**, closes those
   threats. Offline, the warm path costs well under 1% of the worker. It
   still relaxes the documented "fresh validation every call" contract, so it
   is owner decision **D1**. A smaller step needs no relaxation: memoizing the
   pure per-segment scan *inside one validation job*. Offline it gave the same
   verdicts at 4.7× lower worker cost (21.1 s → 4.5 s).
4. **Dependency index (~36 s).** The time is upstream. HA Core 2026.10.1
   serves `GET /api/config/automation/config/{id}` under a mutation lock and
   re-parses the whole `automations.yaml` on every request. 113 per-id reads
   are therefore serialized, and each one is O(file). HA sends no ETag or
   validator, so Engineering cannot skip refetches with conditional requests.
   The queue-wait numbers fit a serial ~0.28 s service time exactly
   ([section 5.2](#52-model-check)). The options with a real effect are: read
   loaded configs over the websocket `automation/config` command (owner
   decision **D3**, because it changes file semantics to loaded semantics);
   one bounded read of `automations.yaml` (D3/D4); or event-driven
   invalidation with background refresh, which only becomes affordable once
   the fetch is cheap. HAMCP-195's gated helper toggle needs
   `freshness == "current"` (≤ 600 s) and today rebuilds inline. Without one of
   these changes, the "fast path" pays about 36 s.
5. **Event loop and HAMCP-172.** Offline, a GIL-bound validation worker raised
   the median loopback request/response round trip on the owner loop from
   0.039 ms to 10.5 ms (p99 21.0 ms) for the whole 21.5 s worker, and cut
   throughput by about 320×. A process worker removes most of the convoy (p99
   tick gap 6.85 ms → 1.33 ms). About 1.8 s of each health call and the whole
   recovery pass still block the loop synchronously, as do the dependency
   parse loop and blueprint reads.
   HAMCP-172 (synchronous logging) is a separate stall source; neither fix
   replaces the other.
6. **Recommendation.** Start with the changes that preserve current
   semantics: (A) child-first orphan sweep and (B) per-job segment memo. Then,
   by owner decision: (C) content-digest verdict cache (D1), (D) process-isolated
   worker (D2), and (E) a cheaper automation-config source (D3), which is the
   enabler for HAMCP-195. See [section 7](#7-options-recommendation-and-pr-split).

## 2. Health: where the time goes (question 1)

### 2.1 Call chain (Source)

`get_server_health` → `tools/compatibility.py:193` → `health.py:71-79` →
`governance/runtime.py:220-234` → `ChangeGovernanceService.async_health_summary`
(`governance/service.py:12716`).

| Phase | Where | What it does |
| --- | --- | --- |
| reader wait / drain | `service.py:12735-12752` | `_health_read_lock`; drains any abandoned worker first. |
| cache reset | `service.py:12755-12759` | `self._health_cache_key = None`: the async path always rebuilds. `health_cache_hit_count` only moves in sync `health_summary` (`:12692`), which is why live shows 0. |
| snapshot | `:12777`, `:12786` | Plan `collect_health` (`storage.py:612-666`) and task `collect_health` (`task_storage.py:696-753`): every plan and task file is deserialized once. |
| **validation** | `:12797-12809` | `asyncio.to_thread(_validate_health_records, plans, sensitive_values)`, awaited under `asyncio.shield`. |
| fence | `:12811`, `runtime.py:4622` | `fence.verify_files()` stamp checks. |
| projection | `:12813-12838` | Historical-policy match hashing (`governance/historical_policy.py:165-182`, `:780`) and `get_for_plan` globs (`task_storage.py:573-597`). |
| assembly | `:13276` | `_build_health_summary`; repeats projection-match hashing at `:13304-13315`. |
| F3 overlay | `:12849-12857` → `runtime.py:4580-4657` | Reads all child envelopes and manifests (`f3_runtime/repository.py:1236-1284`) and deep-copies them (`service.py:12925`, `:13100`). |

### 2.2 The validation worker (Source)

```text
service.py:14241  _validate_health_records(plans, sensitive_values)
service.py:14224    _require_safe_persisted_plan(plan, ...)   # contract_version >= 2 only (CONFIGURATION_PLAN_CONTRACT_VERSION, :238)
models.py:469         plan.to_dict()                          # asdict -> json.dumps -> json.loads
resources.py:1203     persistence_safety_errors(...)
sanitization.py:388     sanitize_untrusted_data(value, known_secrets=...)
sanitization.py:341       _Sanitizer.sanitize                 # recursive full copy
sanitization.py:352         _sanitize_string(str(raw_key))    # every dict KEY is scanned too
sanitization.py:321         _sanitize_string(value)
sanitization.py:207           _sanitize_segment               # known secrets, 12 patterns, 8 serialized patterns
sanitization.py:186             _replace                      # per pattern: marker split + findall + sub
resources.py:1262     any("/mcp/" ... for _values(...))       # second full walk
```

The result is a boolean. The sanitized copy is discarded. This is the same
fail-closed detector that every authoritative plan load and save already runs
(`_require_v2_persisted_plan_safe`, `service.py:2208`; called at `:1644`,
`:1662`, `:2201`, `:2813`, `:5162`, `:6192`, `:6768`, `:12165`, `:12414`,
`:12417`). The health pass is therefore a defence-in-depth re-scan of records
that were already checked when written and when last loaded. It does not add a
new authority check.

### 2.3 Offline measurements

**Full synthetic store** (435 contract-v2 plans, 240 applied; 13.85 MB of plans,
mean 31.8 KB; 240 task files, 480 child envelopes, 240 manifests):

| Measurement | Call 1 | Call 2 | Call 3 |
| --- | --- | --- | --- |
| Deep health wall | 23.60 s | 23.35 s | 23.39 s |
| `validation_wait_ms` | 21,716 | 21,446 | 21,399 (≈ 92%, the same share as live) |
| snapshot / projection / assembly / overlay (ms, owner loop) | 523 / 356 / 572 / 373 | 538 / 365 / 614 / 343 | 575 / 347 / 668 / 362 |
| Longest single owner-loop stall | 574 ms | 617 ms | 670 ms |
| Plan / task records deserialized | 435 / 480 | 435 / 480 | 435 / 480 |

Every unchanged call repeats the full work; there is no reuse.

| Worker breakdown (435 plans) | Value |
| --- | --- |
| Worker total | 20.44 s (47.0 ms per plan) |
| `to_dict` / sanitize / `/mcp` walk | 0.89 s / 19.20 s / 0.24 s |
| Items scanned | 276,798 string values + 549,967 dict keys; ≈ 24.7 µs each |
| Cost density | 1.51 ms per KB of plan JSON |
| cProfile (instrumented, 66.8 s) | 16.5 M `_replace` calls (42.0 s cumulative), 826,765 `_sanitize_segment` (52.1 s cumulative); `re.sub` 17.9 M, `split`/`findall` 18.2 M each |

Two further facts the trial store hid:

* About 1.8 s of each call (snapshot, projection, assembly, overlay) runs
  **synchronously on the owner loop**. Its longest single stall is
  0.57-0.67 s. This is separate from the worker convoy, and it remains after
  any worker fix.
* In the same harness, the startup recovery pass blocked the loop for 4.2 s in
  one stretch.

**Trial store** (10 plans, 5 applied, mean plan 31.9 KB), used to develop the tooling:

| Measurement | Value |
| --- | --- |
| Deep health wall (3 unchanged calls) | 0.455 / 0.457 / 0.496 s |
| `validation_wait_ms` | 419-461 ms (≈ 92% of the call, the same share as live) |
| snapshot / projection / assembly / overlay | ≈ 9 / 7 / 10.5 / 7 ms |
| Plan / task records deserialized per call | 10 / 10 on every call (no reuse) |
| Worker split | `to_dict` 0.013 s, sanitize 0.441 s, `/mcp` walk 0.005 s |
| Items scanned | 6,468 string values + 12,862 dict keys; ≈ 21.7 µs each |
| Cost density | 1.343 ms per KB of plan JSON |
| cProfile top | 386,600 `_replace` calls (20 per string), then `_sanitize_segment`, `re.sub`, `split`, `findall` |

Dict keys make up two-thirds of the scanned strings. Plan keys come from a
small, fixed schema vocabulary plus Home Assistant config keys, so the same key
text is scanned hundreds of times per call.

### 2.4 Calibration against production (Model)

Live: about 32 s of validation for about 435 plans, or about 74 ms per plan.
Offline, the same count of plans at 31.8 KB took 21.4 s (49 ms per plan), so
production is about 1.5× slower. At the offline density of 1.51 ms/KB that
means one of two things, or both: production plans average about 48 KB, or the
production CPU is about 1.5× slower. The ~92% share of `validation_wait` in the
call matches live exactly, which supports the attribution. The architect probes in [section 9](#9-read-only-probes-requested-from-the-architect)
(plan-directory byte total, contract-v2 count) would settle this. The linear
model is enough to rank the options either way.

## 3. The 77k task reads (question 2)

### 3.1 Callers

| Caller | Where | Frequency | Reads | Needed? |
| --- | --- | --- | --- | --- |
| **Periodic orphan sweep** | `application.py:433-440` → `runtime.py:6296-6310` → `_reconcile_orphaned_children` `:5507-5552` | Every 30 s (`RECOVERY_CADENCE_SECONDS`, `runtime.py:150`), when terminal history exists | One `get()` per distinct parent in a page of up to 1,024 declarations (`RECOVERY_DECLARATION_PAGE_SIZE`, `f3_runtime/repository.py:54`) | Only for parents that are terminal **and** zero-dispatch, or that carry a readiness fault |
| Active recovery candidates | `runtime.py:4868`, `:4913` | Each pass | Non-terminal tasks only | Yes |
| Checkpoint candidates | `runtime.py:5050-5153` | Each pass | ≤ 16 | Yes |
| Expired-lock loop | `runtime.py:6350-6395` | Each pass, when locks expire | Per expired lock | Yes (minor) |
| `reconcile_execution_tasks` | `service.py:9201` | Each pass | 2 per non-terminal task, ≤ 20 | Yes |
| Deep health | `task_storage.py:696-753` | Per health call | All tasks (counted as a full scan) | See section 2 |
| Readiness storage check | `runtime.py:1401` | Startup / faults | Bounded | Yes |
| Sync F3 `health()` / reconciliation items | `runtime.py:4228`, `:4245`, `:4767` | On demand | Bounded | Yes |
| Approval web `/f3` pages | `approval_web.py:507`, `:546` | On demand | Bounded | Yes |
| `list_execution_tasks` | `service.py:2116` | On demand | Page | Yes |
| Legacy conflict check | `runtime.py:2427` (from `configuration_reverification.py:316`, `:388`) | On demand | Bounded | Yes |
| New-task uniqueness | `task_storage.py:526-539` → `_list_unlocked` `:668` | Every new-task save | **Full scan, not counted** in `full_history_scan_count` | Yes, but O(n) |
| Idempotency lookup | `task_storage.py:603-610` | Per idempotent request | Full scan, not counted | Yes, but O(n) |
| Navigation rebuild | `task_storage.py:231-276` | Startup / repair | Full scan, not counted | Yes |

### 3.2 The dominant caller (Source + Model)

```python
# f3_runtime/runtime.py:5535-5547
if public_task_id not in parents:
    parents[public_task_id] = self.service.task_repository.get(public_task_id)
record = self.children.get(declaration["child_id"])
runtime = self.children.runtime(declaration["child_id"])
...
if runtime["next_eligible_at"] is not None and ... > now:
    continue                                   # backoff skip happens AFTER the read
pending = self._orphan_cleanup_pending(parent=..., ...)
```

`_orphan_cleanup_pending` (`runtime.py:4835-4866`) returns `False` straight
away when the parent is not terminal zero-dispatch (`:4788-4793`), or when the
child record is missing or has a `dispatch_intent`. The read parent is used
only for those three fields, plus `parent.state` for the `terminal_fault`
branch (`:5564-5569`), which also needs a readiness fault to be present. Once
a task is terminal, `state`, `provider_attempts` and `dispatched_at` are
immutable.

`get()` increments `rehydration_attempts` (`task_storage.py:555`) and always
deserializes (`:649-651`), so these reads explain both live counters: 73,085
attempts and 77,501 records (the extra ~4.4k come from the 15 counted full
scans and the other callers). Model: 3 h ÷ 30 s ≈ 360 passes, and
73,085 ÷ 360 ≈ 203 reads per pass, which is about the number of applied F3
parents in a 242-task store. The child envelope reads on the next line
(`self.children.get`) run once per declaration as well, but the task counters
do not count them.

Offline, on the full store, every pass behaved the same way:

| Per periodic pass (5 passes, identical) | Value |
| --- | --- |
| `task_repository.get()` calls | **239** (= every applied parent; live ≈ 203) |
| Of which terminal records deserialized | 239 |
| Full history scans | 0 |
| Pass wall time | 0.39-0.42 s, **one uninterrupted owner-loop stall** |
| Declarations examined / distinct parents | 478 / 239 |
| Parent reads a child-first order would avoid | **239 of 239** (no parent was terminal zero-dispatch; every child had a `dispatch_intent`) |
| Time in those parent reads | 113 ms per pass (child envelope reads: 155 ms) |

Scaled by the 1.5× calibration, production pays about 150-170 ms of avoidable
synchronous parent reads every 30 s. That is roughly 18-20 s of loop time per
hour, with no change in outcome. The child envelope reads are still needed for
the predicate. They are the next-largest cost in the pass (`_envelope_at`,
JSON decode, `deepcopy` in the cProfile).

## 4. Caching safety (question 3)

### 4.1 What a verdict cache would replace

A verdict is the boolean "this contract-v2 record passes the persistence-safety
detector for this sensitive-value set and detector version". The authoritative
reads and saves listed in section 2.2 are unchanged by any option here. A cache
would only let the **health re-scan** skip records already proven safe.

### 4.2 Threat analysis

| Threat | Stat key `(name, st_ino, st_size, st_mtime_ns)` | Content key `sha256(bytes deserialized)` |
| --- | --- | --- |
| Tampering by a writer with file access | **Defeated**: rewrite the same size, then restore mtime with `utime`. | Holds: any byte change changes the key. |
| Same-size edit within mtime granularity | **Defeated**: coarse or coalesced timestamps on some filesystems and overlays. | Holds. |
| Clock games (clock rollback, NTP step, `touch -d`) | **Defeated**: mtime is writer-controlled. | Holds: no clock input. |
| Inode reuse after atomic replace | **Possible collision**: freed inode, same size, mtime set back. | Holds. |
| Partial or torn write | Stamp taken before the read can match a later, different read. | Holds **only if** the digest is computed over the exact bytes that `collect_health` parsed, not a second read (no TOCTOU). |
| Poisoned cache | A persisted cache becomes a new tamper target. | Keep it **in memory only**, per process; a restart cold-starts it. |
| Sensitive-value rotation | A verdict is valid only for one secret set. | Include an HMAC-style digest of `sensitive_values` (never the raw values) in the key; flush on change. |
| Detector change | New patterns must re-scan everything. | Include a detector version constant; flush on mismatch. |
| Contract-v1 records | Validation is skipped today (`service.py:14224-14240`). | Unchanged. |

**Conclusion.** Stat stamps are already used correctly as *fences*: they detect
change during a call and refuse publication. They must not become *proof of
unchanged content*. A content-digest key is sound if (a) the repository returns
the digest of the bytes it deserialized, which means a `storage.py` and
`task_storage.py` interface change; (b) the cache lives only in memory; and (c)
the key binds the detector version and the sensitive-set digest. The cache
stores only `True` (safe). Unsafe or failed verdicts are never cached, so every
health call re-reports them.

### 4.3 Compared with a `deep_storage_audit` flag

| | Content-digest verdict cache | `deep_storage_audit` flag, fast default |
| --- | --- | --- |
| Default health scans unchanged records? | No, but proves byte identity on every call | No, and does not prove anything about unchanged records |
| Detects out-of-band edits on next default call | **Yes** (digest changes → full scan of that record) | **No**, only when someone asks for the deep audit |
| Cost on an unchanged store | Read + hash (offline: below 1% of worker) | ~0 |
| Contract change | Relaxes "fresh validation every call" to "fresh validation of every changed byte" | Relaxes it to "validation on request" |
| Public schema | Unchanged (counters may be added) | New input field on `get_server_health`, a public-schema change |

The content-digest cache keeps the defence-in-depth property that the flag
gives up. If a flag is still wanted, it should be **opt-in deep** on top of the
cache (force a full re-scan), not **opt-out**.

### 4.4 A smaller step that needs no relaxation

`_Sanitizer._sanitize_segment` (`sanitization.py:207-319`) is a pure function
of `(text, known_secrets)`. It writes no counters; `_sanitize_string` records
the categories it returns. A memo keyed by `(known_secrets, text)` that lives
for **one validation job** and is discarded afterwards performs the same
detection for every string and key. It only stops rescanning identical text
within the same call. No verdict survives the call.

Offline (full store, single run, monkeypatched in a scratch process only):

| | Baseline | Per-job memo |
| --- | --- | --- |
| Worker time (435 plans) | 21.12 s | **4.48 s** (4.7×) |
| Verdicts | n/a | **identical for all 435** |
| Distinct segments / hit ratio | n/a | 17,579 / 97.9% |
| Transient memory (text only, estimate) | n/a | ≈ 1.3 MB, freed at job end |

The synthetic plans are more alike than real ones, so the hit ratio is an
upper estimate. The key share alone (550k of 827k items, from a small key
vocabulary) still points to a gain of at least about 2×.

## 5. Dependency index (question 4)

### 5.1 Where 36 s goes (Source)

| Step | Where |
| --- | --- |
| TTLs 600 s / 3,600 s; 8 fenced build attempts | `dependency/index.py:30-31`, `:38` |
| `get`: current / soft-stale (served + background refresh) / foreground wait | `dependency/index.py:264-337` |
| Single-flight build | `dependency/index.py:339-363` |
| Invalidate is all-or-nothing, after every governed apply or rollback | `dependency/index.py:654-659`; `governance/service.py:10714-10717` |
| Build timeout 300 s | `dependency/authority.py:19` |
| Prewarm once, 45 s after start; no periodic refresh | `dependency/runtime.py:81-104` |
| Per-id REST `GET /config/automation/config/{id}`, all gathered | `dependency/provider.py:395-413` |
| `Semaphore(8)` and queue-wait metric | `dependency/provider.py:229-239`, `:951-957` |
| New `aiohttp.ClientSession` per request; no conditional headers | `clients/rest.py:104` |
| Parse loop with no yields | `dependency/provider.py:418-696` |
| Blueprint YAML read synchronously | `dependency/provider.py:457-458`, `:987-1021` |
| Scripts via the read gateway, 60 s budget | `dependency/script_sources.py:179-241` |
| Per-document `configuration_fingerprint` after the fetch | `dependency/extraction.py:269-272` |

Upstream, Home Assistant Core `2026.10.1` (exact-version source, per ADR-021;
the **installed** Core version is not known to this session):

* `homeassistant/components/config/view.py:86-91`: the GET handler does
  `async with self.mutation_lock: current = await self.read_config(hass)`.
* `view.py:161-163`, `:246-249`: `read_config` runs an executor job that calls
  `load_yaml` on the **whole** file on every request.
* So per-id reads are serialized by HA, each costs O(file), and N reads cost
  O(N²). Saves and deletes from the HA UI take the same lock (`view.py:128`,
  `:145`), so they wait behind an index
  build.
* HA returns no `ETag` or `Last-Modified` for this view, so conditional refetch
  is not available.
* `homeassistant/components/automation/__init__.py:1252-1273`: the websocket
  command `automation/config` (`entity_id`, admin) returns the in-memory
  `raw_config` of the **loaded** automation, with no file parse and no
  mutation lock.

### 5.2 Model check

Model: all 113 requests are queued at t=0, Engineering admits 8 at a time, and
HA serves them one by one in `s` seconds each. Request *i* (0-based) then waits
about `(i − 7)·s` for i ≥ 8. Mean wait = `s · Σ₁¹⁰⁵ j / 113 ≈ 49.2 s`; max =
`105 s`. The live mean of 13.6 s and max of 29 s **both** give s ≈ 0.277 s.
113 × 0.277 ≈ 31 s of fetching, which leaves about 5 s for parsing, scripts and
blueprints out of the ~36 s total. Two consequences:

* Changing Engineering's concurrency, or reusing a session, cannot shorten the
  fetch. Concurrency only moves the wait from Engineering's semaphore into HA's
  lock.
* Per-automation caching on Engineering's side cannot avoid the fetch, because
  HA offers no cheap change signal per automation.

### 5.3 Options

| Option | Fetch cost (est.) | Semantics | Risks |
| --- | --- | --- | --- |
| E1. Websocket `automation/config` per entity | Expected O(N) in-memory serialization; **not measured** (needs a probe) | **Loaded** config (what runs), not the file | Differs from the file after an edit without reload; needs a reviewed websocket read path (provider boundary, ADR-021 exact-version evidence); automations without an entity are not covered |
| E2. One bounded read of `automations.yaml` | One parse ≈ one current request (~0.28 s) | File (same as today) | New file-read surface; YAML tags (`!secret`, `!include`) must not resolve secrets; the current per-id view behaviour must be matched exactly |
| E3. Event-driven invalidation (`automation_reloaded`, config-change events) and background refresh before the soft TTL | Same as the fetch it uses | Unchanged | With today's REST fetch it holds HA's config lock ~31 s every ≤ 600 s; only acceptable after E1 or E2 |
| E4. `/api/states` `last_updated` as a change hint | Cheap | Superset or unrelated signal | Reloads and enable/disable move it; edits without a reload do not. **Rejected** as sole freshness proof |
| E5. Per-document extraction reuse by `configuration_fingerprint` | Saves parse CPU only | Unchanged | Does not touch the 31 s |
| E6. Lower concurrency / shared session | None | Unchanged | No effect (section 5.2) |

### 5.4 Staleness and HAMCP-195

`governance/helper_dependency.py:850-866` requires `freshness == "current"` (age ≤ the
600 s soft TTL); otherwise it adds `dependency_index_not_fresh`, which blocks
execution (`:87-95`, `:1020-1029`, `:1927-1933`). `assess` (`:1681-1801`)
rebuilds inline, input_boolean plan creation passes `refresh=True`
(`service.py:3640-3642`), and post-lock preflight always performs a fenced full
rebuild (`f3/operational_strategies.py:782-830`). Entity-metadata plans follow
the same path (`governance/entity_metadata_disclosure.py:251-301`).

For HAMCP-195's gated toggle this means:

* At today's fetch cost, any toggle more than 10 minutes after the last build,
  or after any governed change, pays a ~36 s foreground rebuild. A fenced
  post-lock rebuild may pay it again.
* A soft-stale index must not be accepted for the gate. HAMCP-195's
  fail-closed coverage rule depends on "current". Freshness has to come from a
  cheaper fetch (E1/E2), optionally kept warm by E3, not from a longer TTL.
* E1 changes *what* the gate analyses: loaded rather than file config. For a
  "what will this toggle trigger right now" question, loaded config is
  arguably the more accurate input, but it is a product decision (**D3**).

The audit-baseline capture (`audit_baseline/capture_contracts.py:13`, `:35-40`,
`:129-144`; `clients/audit_baseline.py:145-150`; per-config SHA-256 in
`audit_baseline/capture_provider.py:91-97`, `:189-193`) fetches through the same per-id REST
view. It can supply **evidence** about content, but not a cheaper **change
signal**: it pays the same O(N²) fetch. (The task brief calls this the
"HAMCP-150 loaded-automation baseline". Register row HAMCP-150 is the beta.6
acceptance record; the capture code is the `audit_baseline` package.)

## 6. Event-loop impact and HAMCP-172 (question 5)

Offline: an `asyncio` loopback TCP echo, sequential round trips for 2 s, run
while `_validate_health_records` validated the 435-plan store in
`asyncio.to_thread` exactly as `service.py:12797` does:

| Condition | Round trips in 2 s | Median | p99 | Max |
| --- | --- | --- | --- | --- |
| Idle | 49,698 | 0.039 ms | 0.065 ms | 0.7 ms |
| During worker (21.5 s), switch interval 5 ms (default) | 154 | 10.5 ms | 21.0 ms | 25.9 ms |
| During worker (22.1 s), switch interval 1 ms | 747 | 3.4 ms | 5.4 ms | 12.4 ms |

With a 1 ms loop ticker: the thread worker gave 3,436 ticks in 21.5 s (p99 gap
6.85 ms). A one-worker `ProcessPoolExecutor` (pickling included) gave 19,707
ticks in 22.1 s (p99 1.33 ms), with one 116 ms stall while the arguments were
serialized. Lowering the switch interval only trades convoy latency against
worker throughput, so it is not recommended as a fix.

The live 32 s worker therefore slows every loop wakeup by about one switch
interval for 32 s. That covers MCP requests, approval web, the F3 recovery
loop and HA websocket traffic, and it explains the reported sluggishness
without any lock contention. Separately:

* The dependency parse loop (`dependency/provider.py:418-696`) and blueprint reads
  (`:457-458`) run synchronously on the loop. Their production duration
  (`parsing_indexing_time_ms`) is not known to this session.
* The periodic recovery pass ran as **one 0.39 s owner-loop stall** every 30 s
  offline (production ≈ 0.6 s by calibration). The startup pass ran as one
  4.2 s stall. The deep-health on-loop phases add ≈ 1.8 s per call, with single
  stalls up to 0.67 s. The 5.0 s sweep budget (`runtime.py:152`) bounds the
  pass, not its stalls.
* **HAMCP-172** (synchronous `StreamHandler` logging, `logging_config.py`) is a
  different failure: a stalled sink blocks the loop and shutdown outright. A
  queue-based handler does not remove the GIL convoy, and a process-isolated
  worker does not fix logging. They are independent. Measure their combined
  acceptance with the same heartbeat and round-trip probe.

## 7. Options, recommendation and PR split

### 7.1 Options with measured or estimated gains

All offline on the 435-plan store. The production projections apply the 1.5×
calibration from section 2.4 and are labelled **Model**.

| Option | Offline effect | Production projection (Model) | Semantics change | Owner decision |
| --- | --- | --- | --- | --- |
| A. Child-first orphan sweep | 239 → 0 parent reads per pass; −113 ms of sync loop time per pass | ≈ −73k reads per 3 h; −150-170 ms loop stall every 30 s | None (read order only) | No |
| B. Per-job segment memo | Worker 21.1 s → 4.5 s; verdicts identical | Validation ≈ 32 s → ≈ 7 s; health ≈ 35 s → ≈ 10 s | None | D5 (confirm) |
| C. Content-digest verdict cache (warm) | Validation replaced by read + SHA-256 of all plans: 38-59 ms | Health ≈ 35 s → ≈ 3 s, the remaining on-loop phases | Validation of changed bytes only | **D1** |
| D. Process worker | Same 22 s wall; loop p99 tick gap 6.85 → 1.33 ms; ticks 3,436 → 19,707; one 116 ms stall (argument pickling) | Removes the convoy for any cold scan | New execution boundary | **D2** |
| On-loop phases (snapshot, projection, assembly, overlay) | ≈ 1.8 s; longest stall 0.67 s | ≈ 2.7 s | Separate follow-up | No |
| E1. Websocket loaded config | Not measured | 36 s → expected seconds (needs probe 6) | File → loaded | **D3** |
| E2. Single `automations.yaml` read | Not measured | 31 s fetch → ≈ 0.3 s parse | New file-read path | **D3/D4** |

B and C compose. With both, a cold call after a restart pays about 7 s, and a
warm call pays the digest pass plus the on-loop phases.

### 7.2 Recommendation

1. **PR-A: child-first orphan sweep** (no owner decision; protected runtime path).
   Evaluate the child-side short-circuits (`record is None`,
   `record.dispatch_intent is not None`) and the backoff skip before reading
   the parent. Read the parent only when the remaining predicate can be true,
   or when a readiness fault exists. The decisions are unchanged; only the
   order of reads changes. Caveat: today, a corrupt parent makes the sweep
   raise on every pass. After the change, it is detected only by the callers
   that still read it (health, listing, readiness). Tests must pin that
   corruption still surfaces.
2. **PR-B: per-job segment memo** (owner confirmation **D5**; no verdict
   survives a call). Byte-identical sanitized output and verdict on
   adversarial fixtures: secrets split across keys and values, marker-bearing
   strings, every pattern category, and the fail-closed exception path.
3. **PR-C: content-digest verdict cache** (owner decision **D1**). Repositories
   return the digest of the bytes they deserialized. The cache is in memory,
   stores only `safe` results, and is keyed by
   `(canonical path, sha256, detector_version, keyed digest of sensitive_values)`.
   Health counters report hits, misses and full re-scans. A periodic or
   on-request forced full re-scan keeps the deep audit available.
4. **PR-D: process-isolated validation** (owner decision **D2**; architecture).
   Only needed if B and C together still leave a cold-start convoy that matters
   (first call after a restart, or a sensitive-set change).
5. **PR-E: automation-config source** (owner decisions **D3/D4**), then
   **PR-F: event-driven invalidation and warm refresh**, the prerequisite for
   HAMCP-195's fast path.

Not recommended: a stat-only cache (section 4.2); `/api/states` as a freshness
proof (E4); retuning concurrency (E6); a longer soft TTL for the HAMCP-195 gate.

### 7.3 Files (all runtime paths are **protected**)

| PR | Files | Protected? |
| --- | --- | --- |
| A | `f3_runtime/runtime.py` (`_reconcile_orphaned_children`) | **Yes** |
| B | `sanitization.py` or `governance/resources.py`, `governance/service.py` (`_validate_health_records`) | **Yes** |
| C | `governance/storage.py`, `governance/task_storage.py` (digest of bytes parsed), `governance/service.py` (cache and counters), `docs/HEALTH_PERFORMANCE.md` | **Yes** (docs: no) |
| D | `governance/service.py` (worker executor), possibly `application.py` (pool lifecycle) | **Yes** |
| E | `dependency/provider.py`, a websocket or file client under `clients/`, `dependency/index.py` | **Yes** |
| F | `dependency/index.py`, `dependency/runtime.py` (subscription and refresh), `governance/helper_dependency.py` | **Yes** |

Each PR also needs the release-declaration and version work required by the
repository workflow (`.release/`, `config.yaml`: **protected**), handled by
the release process, not by this report.

### 7.4 Tests each PR needs

* **A:** decisions identical to the current order on a matrix (terminal
  zero-dispatch, dispatched, missing child, `dispatch_intent` set, in backoff,
  readiness fault present); `get()` count per pass drops to the predicate-needed
  set; corrupt parent still surfaces through readiness or health; cursor and
  fairness unchanged.
* **B:** sanitizer output and categories byte-identical with and without the
  memo across all pattern families and marker interleavings; memo discarded
  after each job (no cross-call state); exception inside a segment still fails
  closed.
* **C:** same-size edit with restored mtime → re-scan and refusal; inode reuse
  → re-scan; torn write → fence refusal, no cached verdict; sensitive-set change
  → flush; detector version bump → flush; unsafe verdicts never cached; restart
  cold; counters truthful; forced deep re-scan works.
* **D:** worker crash or timeout fails closed; cancellation and drain semantics
  of `docs/HEALTH_PERFORMANCE.md` preserved; loop heartbeat bound during
  validation.
* **E/F:** semantic equivalence fixtures for file and loaded config (or the
  disclosed difference, per D3); fail closed on partial coverage; HAMCP-195
  gate refuses soft-stale; HA UI save not blocked behind a refresh.

## 8. Owner decisions

| ID | Decision | Options | Report's recommendation |
| --- | --- | --- | --- |
| **D1** | Relax "async health performs fresh validation every call" (`docs/HEALTH_PERFORMANCE.md`)? | (a) keep; (b) in-memory content-digest verdict cache; (c) `deep_storage_audit` opt-out flag | (b), with an opt-in forced deep re-scan |
| **D2** | Allow a process-isolated validation worker (a new execution boundary)? | yes / defer | Defer until B and C are measured |
| **D3** | Dependency analysis of **loaded** automation config (websocket) instead of **file** config (REST view)? | file / loaded / both with a disclosed diff | Decide together with HAMCP-195's design |
| **D4** | Allow a new bounded file-read path for `automations.yaml` if D3 = file? | yes / no | Only with a reviewed YAML-tag policy |
| **D5** | Confirm that a per-job memo of a pure function is not "caching" under the current contract | confirm / treat as D1 | Confirm |
| **D6** | Any persisted-format change (for example persisting verdicts or terminal-parent facts)? | none / allow | None; everything above is in memory |

## 9. Read-only probes requested from the architect

All read-only, no plan or task creation:

1. Plan directory: file count, byte total, and contract-v2 count (calibrates
   section 2.4).
2. `get_server_health`: `terminal_records_deserialized`, `full_history_scan_count`
   and `rehydration_attempts` deltas across two calls 60 s apart with no
   activity (attributes section 3 exactly).
3. F3 namespace: child envelope count, manifest count, distinct parents with
   terminal zero-dispatch state.
4. Dependency index metrics after one build: `parsing_indexing_time_ms`,
   per-phase fetch timings, script and blueprint counts.
5. Installed Home Assistant Core version (ADR-021: the upstream analysis used
   `2026.10.1` source).
6. Optional, if D3 is under consideration: time one websocket
   `automation/config` call against one REST config GET for the same
   automation.

## 10. Limitations and follow-up evidence

* All timing numbers come from offline synthetic stores. The plans are more
  self-similar than real household plans, which inflates the memo hit ratio in
  [section 4.4](#44-a-smaller-step-that-needs-no-relaxation). Treat 4.7× as an
  upper-side estimate until it is measured on real plans.
* The synthetic store models applied configuration plans only. Operational,
  helper and entity-metadata tasks were not generated.
* Websocket `automation/config` cost (E1) was not measured.
* E-1/E-2 (Antigravity) were not available. If they contain measurements or
  claims, reconcile them against sections 2-5 before any PR is scoped.
* Scratch tooling was disposable and is not committed:
  * a store builder that drives the real `ChangeGovernanceService` and F3
    runtime with fake gateways from `tests/test_f3_runtime_integration.py`;
  * a profiler for health phases, deserialization deltas, worker breakdown,
    cProfile, cache-key cost and recovery passes;
  * a loopback convoy probe;
  * an option-gain script.

  These can be reproduced from this description and committed as tooling in a
  separate task if wanted.
