# HAMCP-301 C-iv and HAMCP-308: oversized path provenance and truthful bound disclosure (design)

Status: **design for architect review. No runtime change.**
Every option choice in this document is **pending Josh**. Nothing here is a
Josh decision except the one the contract relays (§0).

Source paths are relative to `hass_mcp_engineering_beta/ha_mcp_engineering/`
unless they start with `tests/` or `docs/`. Every claim is labelled:

- **V**: verified by reading source at the base revision, or by running the
  offline reproduction in §1.5.
- **I**: inference. It is not proven by source and is not an observation of
  the live system.

No live Home Assistant or deployed MCP endpoint was accessed. Live facts come
only from the contract (architect, 2026-11-04) and are marked "(contract)".

## 0. Baseline and authority

| Item | Value |
| --- | --- |
| Base (`origin/main`, fetched 2026-11-04) | `7aa4c92db3b8f76cd8a38f011937dd3905d5c04a`, the merge of PR #361 (HAMCP-306) |
| Head at the time of writing | the same commit, plus this document only |
| Branch | `agent/HAMCP-301-civ` |
| Engineering version | `2.4.2-beta.2` (`config.yaml`, `version.py`, `validate_addon_metadata.py`) |
| Active acceptance | `docs/V2_4_2_BETA2_ACCEPTANCE.md`, resolution `exact` |
| Staged release | none (`resolution_status: missing`) |
| Instructions loaded | root `AGENTS.md`, `hass_mcp_engineering_beta/AGENTS.md`, `tests/AGENTS.md` |
| Prior work | `docs/evidence/hamcp301/HAMCP-301-diagnosis.md` (Part 1). Its construct classes C1-C14 (§2.2) and options A/B/C (§5) are reused. C-iv is a new sub-option of C. |

Authority, as relayed by the contract:

- **[DECISION] Josh, 2026-11-04** (architect chat): next lane work is C-iv +
  HAMCP-308.

Facts relayed by the contract (not re-observed here):

- `front_door_lock_bedtime` has a 143-byte bed-sensor `configuration_path:`
  context.
- `close_garage_both_in_bed` has two 134-byte logbook `data.entity_id`
  contexts. The entity ids are intact.
- The same automations are among the 36 opaque consumers (214 unresolved
  dynamic references).
- `front_door_lock_bedtime` is safety-critical. Under Josh's hard line it keeps
  every helper elevated even after C-iv.

### 0.1 Summary

- **The defect (V).**
  - A structured exact entity reference carries a context entry
    `configuration_path:<path>` (`dependency/extraction.py:514`). It is only a
    copy of the obligation's own `config_path`.
  - When the path is deep, that entry exceeds the per-value cap of 128 bytes.
  - The cap is applied to every context entry
    (`dependency/models.py:275-278`, `118-120`), so this sets `context_lost`.
    `context_lost` counts as lost target detail (`models.py:316-318`) and turns
    the obligation into a coverage failure (`models.py:319-327`). The entity id
    itself is intact.
  - Any coverage failure projects `coverage_failure` for **every** helper
    (`governance/helper_dependency.py:667-668`). This happens before the
    non-causal-relation check, so even a lock `service_target` or logbook
    `action_data` elevates unrelated helpers.
- **Recommended C-iv (option 1, pending Josh).**
  - Treat the own-path context entry as bounded provenance. When it is
    oversized, replace it with `configuration_path_sha256:<digest>` and do not
    set target-detail loss.
  - Every other loss stays a coverage failure:
    - entity, selector or domain values;
    - semantic context tokens;
    - non-own paths;
    - sanitization of the path.
  - Offline, the prototype turns the reproduced shapes into their true
    projections (§1.5). The entity identity is unchanged. Fingerprints change
    only for the affected obligations.
- **HAMCP-308 (pending Josh).** Record a bounding cause per loss, giving:
  - the field;
  - the kind (`per_value` / `aggregate` / `sanitization`);
  - the constant name and value;
  - the observed size, for size loss only.

  `bounded_sources` then reports that cause instead of
  `unclassified / null`, and keeps the extraction `reason_code`. Sanitization
  loss reports no number. The disclosure stays outside approval material.
- **Gate (V, filed as HAMCP-310).** A sequence-level `- condition:` step gets
  relation `action_target`, which is non-causal.
  - Such a step reading a helper therefore projects
    `proven_dependency_neutral` instead of `exact_dependency`.
  - Today a deep step of that kind is (accidentally) a coverage failure. C-iv
    would turn it neutral.
  - HAMCP-310 should land before or with C-iv.
- **Live outcome (I).** C-iv alone does not make either helper standard:
  - `front_door_lock_bedtime` remains an opaque safety-critical consumer;
  - `guest_mode` has exact lock-automation dependencies.

  See §5.

## 1. Consumers of the `configuration_path:` context (Q1)

### 1.1 Producers (V)

| Producer | Line | Obligation | Notes |
| --- | --- | --- | --- |
| Coverage-failure obligation | `dependency/extraction.py:314` | `coverage_failure` already | C-iv changes nothing. It is already a failure for its own reason. |
| Blueprint source | `dependency/extraction.py:430-433` | blueprint source | Constant path `$.use_blueprint.path` (27 bytes). It cannot exceed the cap. |
| Structured exact entity reference (`add_structured`) | `dependency/extraction.py:490-516`, context at `514` | `reason_code="structured_exact_entity_reference"`, `lock_projection="exact"`, `exact_entity_ids=(entity_id,)` | **The C-iv target.** Both contract cases are this producer. |
| `state_changed` event trigger | `dependency/extraction.py:634-637` | plus `event_type:state_changed` | Paths are trigger-level, so they are shallow (I). |
| `call_service` event trigger | `dependency/extraction.py:785-788` | plus the event token | Same as above (I). |
| Blueprint input | `dependency/extraction.py:930-933` | plus `blueprint_input:<name>` | The path is within the blueprint. Depth is bounded by the blueprint (I). |

Context is merged (union, the path entry preserved) at:

- `extraction.py:1448` (blueprint discharge);
- `1722` (`expand` binding);
- `1811`, `1836`, `2007`, `2038`, `2070` (bound contexts);
- `1877` (label binding).

None of these changes `config_path` (V).

Ledger obligations rebuild context from ledger tokens
(`dependency/obligation_ledger.py:5397`). Those tokens are code-generated and
contain no `configuration_path:` entry (V).

### 1.2 Consumers (V)

No code parses the `configuration_path:` entry. Every consumer either matches
other, semantic tokens or copies the whole tuple.

| Concern | Consumer | Line | Uses the path entry? |
| --- | --- | --- | --- |
| Identity | `DependencyObligation.config_path`, a separate field, not bounded at the model | `dependency/models.py:152-193` | No. Identity comes from `config_path`, `source_*`, and `evidence_id`. |
| Identity | `evidence_id`, built from the raw path | `dependency/models.py:681`, `dependency/extraction.py:453-460`, `496` | No (it uses `config_path`). |
| Identity / drift | `expression_fingerprint`, which includes the path | `dependency/extraction.py:490-516` | No (the path is folded in by the producer). |
| Fingerprint | `obligation_material` includes `config_path`, `context_provenance` (whole tuple) and `evidence_bounded` | `dependency/models.py:636-671` (`666`) | Copied, never parsed. |
| Fingerprint | `obligation_fingerprint` = hash of the material | `dependency/models.py:674-677` | Through the material. |
| Fingerprint | the index fingerprint sorts all obligation fingerprints and the overflow fingerprint | `dependency/models.py:817`, `dependency/index.py:75-99` | Through the material. |
| Attribution | `_obligation_targets_helper`: coverage first (`667`), non-causal neutral (`669-677`), exact inclusion (`678-685`) | `governance/helper_dependency.py:653-706` | No, but the coverage-failure flag that the path entry causes wins. |
| Attribution | the referenced-sources function skips non-causal relations | `governance/helper_dependency.py:405-425` (`411`) | No. |
| Attribution | F3 lock retention, using the same non-causal set | `f3_configuration/locks.py:24` | No. |
| Semantic tokens | `expand` binding: `entity_selector_transform:expand` | `dependency/extraction.py:1722-1726` | Other tokens only. |
| Semantic tokens | label binding: `entity_set_producer:*` | `dependency/extraction.py:1877-1880` | Other tokens only. |
| Semantic tokens | label selector authority | `dependency/provider.py:1566` | Other tokens only. |
| Semantic tokens | input_select label projection | `governance/input_select_dependency.py:98-120` | Other tokens only. |
| Semantic tokens | membership producers | `governance/entity_metadata_disclosure.py:28-31`, `157-158` | Other tokens only. |
| Proof precondition | input_select proof requires `not item.evidence_bounded` | `governance/input_select_dependency.py:84-87` | Indirect, through `evidence_bounded`. |
| Disclosure | `_project_obligation` copies `context_provenance` (`739`) and `configuration_path` truncated to 256 characters (`718`) | `governance/helper_dependency.py:711-752` | Copied. |
| Disclosure | `bounded_sources` (keys by `reason_code`; unknown → `unclassified`) | `dependency/bounded_sources.py:140-143`, `153-219`; blanket warning `242` | No; lists the source because the obligation is a coverage failure. |
| Drift (persisted plans) | the plan binds `evidence_fingerprint` and `dependency_index_fingerprint` | `governance/service.py:4620`, `4976`, `8049`, `8110` | Through the fingerprints. |
| Tests | no test asserts the text of a `configuration_path:` context entry | `grep` over `tests/` | No. |

### 1.3 Can it be bounded provenance without failing an intact exact reference? (V for the source facts, I for the safety argument)

**Yes.**

- The entry is a duplicate of `config_path`, which is present unbounded on the
  same object and already feeds:
  - `evidence_id` (`models.py:681`; the producer passes `path` at `extraction.py:453-460`);
  - `obligation_material` (`models.py:636-671`);
  - the producer's `expression_fingerprint` (`extraction.py:490-516`).

  Replacing the oversized copy with a digest of `config_path` therefore loses
  no identity, attribution or drift information. Drift in the path still
  changes `config_path`, the digest, `evidence_id` and the fingerprint.
- The precedent exists for dynamic references: `helper_dependency.py:297-301`
  replaces a `config_path` over 256 bytes with
  `"oversized_sha256:" + stable_hash(...)`, and that does not become a coverage
  failure.
- The governance projection already truncates the displayed path to 256
  characters (`helper_dependency.py:718`). A truncated display form in context
  is therefore redundant. The recommended design stores only the digest in
  context and leaves display to `configuration_path` in the projection.

### 1.4 What must stay a coverage failure (V for current behaviour, design for the proposal)

| Loss | Today | After C-iv | Why |
| --- | --- | --- | --- |
| An entity value is oversized, sanitized, or dropped by the aggregate (`exact_lost`) | failure | **failure** | The entity identity is lost. An exact claim rests on it. |
| `literal_selectors` loss (`selector_lost`) | failure | **failure** | Selector detail is target authority. |
| `possible_entity_domains` loss (`domain_lost`) | failure | **failure** | Domain closure is exclusion authority. |
| A semantic context token is oversized or dropped by the aggregate (`entity_set_producer:*`, `entity_selector_transform:*`, `*_provenance:*`) | failure | **failure** | These tokens are matched by code (§1.2). Losing one changes what binding is allowed. |
| A `configuration_path:` entry that is **not** equal to the obligation's own `config_path` | failure | **failure** | It is not provably a duplicate (for example after a merge). |
| The own-path entry altered by sanitization (`safe != original`) | failure | **failure** (recommended) | Sanitization means the path carries secret-shaped material. Size-only is the narrow fix; widening it is a separate decision. |
| Aggregate context overflow (1,024 bytes) caused by the path entry | failure | **failure**, but the digest form (58 bytes) makes this practically unreachable from the path alone (I) | The aggregate bound stays authoritative. |
| The own-path entry is over 128 bytes, unchanged by the sanitizer | **failure** | **bounded provenance** | The C-iv change. |
| Text fields (`external_template_name`, `source_name`, `source_state`) over 256 | `evidence_bounded` only, not target loss (`models.py:299-308`, `316-318`) | unchanged | Already non-failing. |

Residual, not widened by C-iv (I): `config_path` itself is not sanitized at the
model, and is copied raw into `evidence_id`, the material and the projection.
This predates C-iv. C-iv does not expose more of it, because the digest
replaces a copy.

### 1.5 Offline reproduction (V)

Scratch scripts (not committed; under the ignored `.artifacts/hamcp301-civ/`)
use the real extraction (`dependency/extraction.py`) and helper projection
(`governance/helper_dependency.py`) at the base. They build synthetic shapes:

- **bedtime:** `repeat` → nested `choose` → a `choose` condition on
  `binary_sensor.bed_occupied`, with a `lock.lock` targeting
  `lock.front_door`;
- **garage:** a `logbook.log` with `data.entity_id: cover.garage_left` inside
  `choose` sequences;
- each at nesting depths 2, 5 and 6.

The helpers are `input_boolean.engineering_acceptance` and
`input_boolean.guest_mode`. Neither is referenced.

**Baseline.**

| Shape | Obligation | Path bytes | Context bytes | Outcome | Helper projection (both helpers) | `bounded_sources` |
| --- | --- | --- | --- | --- | --- | --- |
| bedtime d2 | bed sensor `choose_condition` | 109 | **128** | `exact_dependency` | `proven_target_exclusion` | — |
| bedtime d2 | `lock.front_door` `service_target` | 114 | 133 | `coverage_failure`, context → `sha256:a9f3…` | `coverage_failure` | `structured_exact_entity_reference / unclassified / null` |
| bedtime d5 | bed sensor `choose_condition` | 175 | 194 | `coverage_failure` | `coverage_failure` | same |
| bedtime d5 | `lock.front_door` `service_target` | 180 | 199 | `coverage_failure` | `coverage_failure` | same |
| garage d2 | `cover.garage_left` `action_data` | 71 | 90 | `exact_dependency` | `proven_dependency_neutral` | — |
| garage d5 | `cover.garage_left` `action_data` | 137 | 156 | `coverage_failure` | `coverage_failure` | `structured_exact_entity_reference / unclassified / null` |
| d6 | (same pattern) | 159-202 | 178-221 | `coverage_failure` | `coverage_failure` | same |

What this shows:

- A context of exactly 128 bytes is retained. 129+ bytes fails.
- The failed entry becomes a bare `sha256:` digest. The `configuration_path`
  label is lost.
- The `bounded_sources` reason is the extraction reason with no bound.
  `bound_for` has no row for it (`bounded_sources.py:140-143`, `208`).

**C-iv option 1 prototype.**

The prototype monkeypatches `_bind_bounded_evidence`. For each context entry
`v`:

- if `v == f"configuration_path:{self.config_path}"`, and the sanitizer leaves
  `v` unchanged, and `v` exceeds 128 bytes,
- then `v` is replaced with
  `"configuration_path_sha256:" + _evidence_digest(config_path)`
  before the original bind.

Results at every depth:

- bed sensor `choose_condition` → `exact_dependency`, helpers
  `proven_target_exclusion`;
- lock `service_target` and logbook `action_data` → `exact_dependency`,
  helpers `proven_dependency_neutral`;
- `limit_exceeded=False`, `evidence_bounded=False`;
- `bounded_sources` empty;
- `exact_entity_ids` unchanged.

**Negatives under the prototype (V).**

| # | Case | Result |
| --- | --- | --- |
| N1 | Sequence-level `- condition: state` on `input_boolean.guest_mode` at a deep path (154 bytes) | relation `action_target` → **`proven_dependency_neutral`** (see §1.6; this is HAMCP-310) |
| N2 | Oversized entity id | `coverage_failure`, `exact_entity_ids=('sha256:04e8…',)` |
| N3 | A `configuration_path:` entry that is not the obligation's own path, over 128 bytes | `coverage_failure` |
| N4 | Semantic tokens pushed past the 1,024-byte aggregate | `coverage_failure`, terminal `omitted:sha256:…` |
| N5 | Own path containing sanitizer-altered (secret-shaped) content, over 128 bytes | `coverage_failure` |
| N6 | `dataclasses.replace()` on a C-iv-bounded obligation (re-runs `__post_init__`) | idempotent; the fingerprint is stable across replace |

### 1.6 Pre-existing finding that C-iv would widen: HAMCP-310 (V)

`_relation_for` (`dependency/extraction.py:3285-3311`) maps a path ending in an
`actions` / `sequence` step to `action_target`. This includes a sequence-level
condition step (`- condition: state`, `entity_id: input_boolean.guest_mode`).

`action_target` is in `_NON_CAUSAL_RELATIONS`
(`governance/helper_dependency.py:78-79`). It is skipped at `411` and projected
neutral at `669-677`. F3 uses the same set (`f3_configuration/locks.py:24`).

A sequence condition step is causal: it gates the remaining actions. Today:

- a shallow step projects `proven_dependency_neutral` (wrong);
- a deep step is a coverage failure (accidentally conservative).

Top-level, `choose` and `if` conditions project `exact_dependency` correctly
(V, `seqcond.py`).

C-iv would turn the deep case neutral too (N1). **Recommendation: fix
HAMCP-310 before or in the same release as C-iv** (gate, pending Josh).

The full-plan effect on live helpers was not reproduced (I).

## 2. C-iv options (Q2)

Invariants for every option:

- An exact reference never loses its entity identity (`exact_entity_ids`
  unchanged; `exact_lost` still fails).
- `guest_mode` stays `safety_critical` (§5).
- No new write, dispatch or fallback reachability.

### Option 1 (recommended): own-path provenance digest, size-only

- **Change.**
  - In `DependencyObligation._bind_bounded_evidence`
    (`dependency/models.py:257-327`), before bounding the context, apply a
    replacement to each context entry `v`. The entry must meet all three
    conditions:
    - `v == "configuration_path:" + self.config_path`;
    - the sanitizer returns it unchanged;
    - it exceeds `MAX_OBLIGATION_VALUE_BYTES`.

    Replace it with `"configuration_path_sha256:" + _evidence_digest(self.config_path)`
    (58 bytes).
  - Do not set `context_lost` for it. Everything else goes through
    `_bounded_evidence_values` unchanged.
- **Safety argument.**
  - The replaced value is provably a copy of an unbounded field that remains
    on the object and in the material.
  - Nothing parses it (§1.2).
  - The digest keeps it drift-sensitive.
  - Every loss listed in §1.4 still fails.
  - The rule is idempotent:
    - the digest form is under the cap and passes the sanitizer, so a re-run
      of `__post_init__` under `replace()` is a no-op (N6);
    - `evidence_bounded` and `limit_exceeded` persist, because `replace()`
      copies them and bind returns early (`models.py:310-311`).
- **`evidence_bounded`: a sub-decision (pending Josh).**
  - (1a, recommended) Leave `evidence_bounded=False` for a path-only digest.
    The input_select proof (`input_select_dependency.py:84-87`) and the
    approval material then treat the obligation as fully evidenced, which it
    is.
  - (1b) Set `evidence_bounded=True` without coverage failure. This is more
    conservative, but it blocks the input_select proof path for these
    obligations and adds a disclosure-only signal to approval material.
- **Fingerprint and drift compatibility.**
  - `obligation_material` changes only for obligations whose own-path entry
    exceeds 128 bytes:
    - their context value changes from `sha256:<d1>` to
      `configuration_path_sha256:<d2>`;
    - `limit_exceeded` changes;
    - `outcome`, `target_selector_scope` and `lock_projection` change.
  - Every other obligation's fingerprint is byte-identical (I, from
    `models.py:636-671`; no field added).
  - Index fingerprints and the binding `evidence_fingerprint` change on any
    install that has such obligations. That is intended, because the
    classification changed.
  - Plans created before the upgrade carry the old fingerprints. They are
    expected to fail revalidation and must be re-planned (I; the plan binds
    `evidence_fingerprint`, `governance/service.py:4620`, `4976`). That is the
    normal behaviour for any classification change.
  - No persisted index exists (V: `dependency/index.py` performs no file I/O).
    No stored-format migration is needed.

### Option 2: drop the redundant `configuration_path:` context entry entirely

- **Change.** Remove `configuration_path:{path}` from the producers at
  `extraction.py:514`, `634-637`, `785-788`, `930-933` (and `430-433`, `314`).
- **Safety argument.**
  - This is equivalent in information: `config_path` remains.
  - It is simpler, because no special case is needed in the bind.
- **Compatibility.**
  - Every obligation fingerprint that carried the entry changes, which is
    every structured reference. The index fingerprint and every helper binding
    `evidence_fingerprint` therefore change once on upgrade, and every
    in-flight plan goes stale once.
  - The public `context_provenance` values in projections change
    (`helper_dependency.py:739`).
  - Risk: unknown external clients reading it. There is no in-repo consumer
    (§1.2).
- **Not recommended for this lane.** The blast radius is larger than the
  defect.

### Option 3: a prefix-specific higher cap (for example 512 bytes for `configuration_path:`)

- **Rejected as primary.** It moves the cliff without removing it: depth up
  to 64 (`extraction.py:101`) gives paths well beyond 512 bytes.
- It still loses the entry to a bare digest when hit.
- Fallback only if Josh prefers no digest form.

### Option 4: raise `MAX_OBLIGATION_VALUE_BYTES` globally

- **Rejected.** It widens the bound for entity ids, selectors and
  secret-bearing template literals (`models.py:45-50` comment). That is a
  security-sensitive change with no need.

## 3. HAMCP-308: the minimal truthful disclosure (Q3)

### 3.1 Today (V)

- `bounded_source_entries` keys by `(source, reason_code)`
  (`bounded_sources.py:153-200`).
- `_entry` looks up `bound_for(reason_code)` (`208`), which knows only
  analysis-limit reason codes (`73-137`).
- An evidence-bounded obligation keeps its extraction reason
  (`structured_exact_entity_reference`), so it reports
  `bound: unclassified, bound_value: null`.
- The warnings say "exceeded dependency-analysis bounds"
  (`bounded_sources.py:242`) and "exceeded dependency-analysis coverage
  bounds" (`dependency/provider.py:1770`). They say this even when the loss was
  sanitization, which is not a bound.
- After binding, the original value is gone. A later pass cannot recover its
  size, so the cause must be captured at first bind.

### 3.2 Design (pending Josh)

1. **Capture.** `_bounded_evidence_values` / `_bounded_evidence_text` return a
   cause per loss, and `_bind_bounded_evidence` stores a bounded tuple
   `evidence_bound_causes` on the obligation. Each cause records:
   - `field`: one of `exact_entity_ids`, `literal_selectors`,
     `possible_entity_domains`, `context_provenance`,
     `external_template_name`, `source_name`, `source_state`;
   - `kind`: one of
     - `per_value`;
     - `aggregate`;
     - `sanitization`;
     - `provenance_digest` (C-iv, non-failing);
   - `bound`: the constant name, for example `MAX_OBLIGATION_VALUE_BYTES`,
     `MAX_OBLIGATION_CONTEXT_AGGREGATE_BYTES`, `MAX_OBLIGATION_TEXT_BYTES`;
   - `bound_value`: the constant's value, for example 128 or 1024;
   - `observed_bytes`: the original UTF-8 size for `per_value`; the
     would-be total for `aggregate`; **absent for `sanitization`**.

   The sanitizer check runs first (`models.py:115-117`), so a value that is
   both secret-shaped and oversize is reported as `sanitization`, with no
   number. Its length is not disclosed.

   The tuple is capped at 8 causes, de-duplicated by
   `(field, kind, bound)`, plus an omitted count.

   On a `replace()` re-run nothing is lost, and bind returns before touching
   it, so the causes persist (same mechanism as `evidence_bounded`).
2. **Exclude from approval material.**
   - `evidence_bound_causes` is not added to `obligation_material`
     (`models.py:636-671`), so fingerprints are unaffected by HAMCP-308.
   - The loss itself is already fingerprinted through the digest values and
     `evidence_bounded`.
   - `_bounded_sources` in the binding is already disclosure-only
     (`helper_dependency.py:1687-1708`, `2280-2295`).
3. **Disclose.**
   - `_entry` keeps `reason_code` (the extraction reason, unchanged) and adds
     `bounding: [...]`, built from the causes of the obligations behind that
     `(source, reason)` key.
   - When causes exist, `bound` / `bound_value` take the first failing cause:
     - `context_provenance / per_value / MAX_OBLIGATION_VALUE_BYTES / 128`,
       with `observed_bytes: 143` for the bedtime case (contract);
     - or `bound: sanitization, bound_value: null`.

     Otherwise `bound_for(reason_code)` applies as today.
   - The entry key and `bounded_source_total` semantics stay unchanged, so
     counts remain comparable.
   - Field names and kinds are code constants. Only `source_id` /
     `source_entity_id` pass through `_shown`, as today.
4. **Warnings.**
   - Sanitization-only loss says "could not be ruled out because evidence was
     sanitized". It does not say "exceeded … bounds".
   - Size loss names the bound. This applies at `bounded_sources.py:242` and
     `provider.py:1770`.
5. **C-iv interaction.**
   - A C-iv provenance digest is not a coverage failure, so it never appears
     in `bounded_sources`.
   - Its cause (`provenance_digest`) is retained on the obligation for
     diagnosis only.
   - Whether to project that cause in `obligation_evidence` is a sub-decision
     (pending Josh). Default: do not project it, which keeps the projection
     unchanged.

What HAMCP-308 does **not** do:

- change any classification;
- fabricate a bound for reasons that have none (they stay `unclassified`);
- expose an original value or its prefix.

## 4. The 214 references for the three automations (Q4)

### 4.1 What can be said offline (V)

- The 214 is `target_relevant_dynamic_reference_count`, which counts retained
  obligations whose helper projection is `bounded_semantic_opaque`
  (`governance/helper_dependency.py:1337-1351`, `1636`).
- **Coverage failures are not in the 214.** The path-only failures that C-iv
  fixes are therefore counted separately, under `obligation_coverage_failure`
  / `automation_inventory_incomplete`.
- A structured exact reference that C-iv restores projects as exact,
  exclusion or neutral, never opaque. C-iv therefore cannot add to the 214
  from that producer.
- An event or blueprint-input obligation restored from a path-only failure
  could project opaque if its true outcome is opaque. That would add to the
  214 (I; the depth of such paths is unknown).
- The per-automation split of the 214, and its construct classes, **cannot be
  determined offline**. It needs the live configuration.
  - The binding retains up to 256 relevant obligations
    (`MAX_RELEVANT_OBLIGATIONS`, `helper_dependency.py:60-61`).
  - Each retained obligation carries:
    - `source_object_id`;
    - `configuration_path`;
    - `reason_code`;
    - `relation`;
    - `obligation_kind`;
    - `target_outcome`.

    (`helper_dependency.py:711-752`, `1517-1566`.)

  With 214 opaque plus the exact and failed obligations, the retained list is
  likely complete (I). If it is clipped, the binding says so.

### 4.2 What would resolve them or prove them helper-neutral (by class; from the diagnosis §2.2, §5)

| Class | What resolves or neutralises it for a helper |
| --- | --- |
| C1 / C10 unknown provenance or receiver | Candidate closure in the template: the variable is bound to literals or to a reviewed producer. Otherwise, a configuration change by Josh that uses literal entity ids. |
| C2 bare `states` iteration | Nothing. It is opaque by construction. Only a configuration change helps. |
| C3 entity-set producers | Membership evidence (Option A/B). A literal `label_entities` is already resolved. |
| C4 registry lookups | A reviewed registry-lookup semantics (Option B). |
| C5 unregistered globals, filters and tests | A registry entry proving the output cannot be an entity id (diagnosis §2.3, Option B). |
| C6 dynamic dispatch | Reviewed dispatch closure (Option B3). |
| C7 / C8 callable provenance, external templates | A review of the callable, or analysable content. |
| C9 trigger context | A closed trigger entity set (literal `entity_id` triggers). |
| C11 parse error | Fix the template. |
| C12 blueprints | Local blueprint analysis or resolved inputs. |
| C13 unbounded event triggers | An `event_data` filter. |
| C14 incomplete `expand` / label | A complete state inventory (diagnosis §1; Option A). |

Under Josh's hard line (contract), `front_door_lock_bedtime` keeps every helper
elevated while **any** of its obligations is opaque for that helper. Every one
of its opaque obligations must therefore be resolved or proven neutral, not
most of them.

### 4.3 Read-only observation for the architect (no writes)

1. Read the binding of plan `40d49c72`, or a new plan-only helper plan, for
   `input_boolean.engineering_acceptance` (`inspect_governance_record` /
   `get_change_plan`).
2. From `obligation_evidence`:
   - filter `source_object_id` ∈
     {`front_door_lock_bedtime`, `close_garage_both_in_bed`,
     `garage_open_30m_away_auto_close_fallback`};
   - histogram `(target_outcome, reason_code, obligation_kind, relation)`;
   - map each `reason_code` to C1-C14 (diagnosis §2.2).

   Also confirm that the list is not clipped (`retained` versus total).
3. Read `bounded_sources` in the same binding:
   - confirm that the three entries are `structured_exact_entity_reference`;
   - confirm that the third automation's failure is path-only. Its path
     contexts are not measured in the contract.
4. Run `get_automation_config` for the three automations. Read the templates
   at the opaque obligations' `configuration_path`s, and the paths of the
   failed obligations, to measure byte length.
5. Check (HAMCP-310) whether any of the 36 consumers has a sequence-level
   `- condition:` step that references `input_boolean.guest_mode` or
   `input_boolean.engineering_acceptance`.

## 5. Projected live result (Q5) (I, conditional on §4.3)

Assumption: the third bounded automation's failure is also path-only. If it is
not, the coverage reasons below remain.

| Item | Today (contract / Part 1) | After C-iv alone | After C-iv + front-door slice |
| --- | --- | --- | --- |
| `obligation_coverage_failure`, `automation_inventory_incomplete` (from the 3 bounded automations) | present | clear, if all 3 are path-only | clear |
| `front_door_lock_bedtime` in the relevant set | yes: coverage failure and opaque | yes: opaque (`opaque_downstream_obligations`, `action_profile_semantic_incomplete`, `helper_dependency.py:902`, `1419-1445`) | no, only if every opaque obligation for the helper is resolved or proven neutral |
| Other opaque consumers | 35 | 35 | 35 |
| `entity_registry_malformed_relevant_record` | present | present (unrelated to C-iv; diagnosis §4) | present |
| `engineering_acceptance` | `elevated_admin`, consequence `unknown`, potential `safety_critical` | **unchanged class**: `elevated_admin`; potential stays `safety_critical` through the opaque lock consumer (`helper_dependency.py:1046`) | still `elevated_admin` while 35 opaque consumers remain. Potential drops from `safety_critical` only if no remaining consumer has a lock or safety-critical profile (unknown). |
| `guest_mode` | `physical_consequence`, `safety_critical` (exact obligations to lock automations) | **unchanged**: `safety_critical` | **unchanged**: `safety_critical` (the exact dependency remains; HAMCP-310 can only add exact obligations) |

C-iv is therefore a correctness fix: it removes a false coverage failure. It
does not change the plan class for either helper by itself.

## 6. PR split, decisions, Evidence and mutation scope (Q6)

### 6.1 Recommended split (pending Josh)

| PR | Content | Protected paths to declare | Key tests |
| --- | --- | --- | --- |
| 1. HAMCP-310 | A sequence-level `condition` step gets a causal relation (for example `sequence_condition`), excluded from `_NON_CAUSAL_RELATIONS` in both governance and F3 | `dependency/extraction.py`, `governance/helper_dependency.py`, `f3_configuration/locks.py` | shallow and deep sequence conditions on a helper → `exact_dependency`; action `target` / `data` stay neutral; F3 retains the lock |
| 2. C-iv option 1 (+ HAMCP-308 if bundled) | Own-path digest in `_bind_bounded_evidence`; HAMCP-308 causes and disclosure | `dependency/models.py`, `dependency/bounded_sources.py`, `dependency/provider.py` (warning text) | N2-N6; bedtime and garage shapes → true projections; idempotence under `replace()`; unaffected-obligation fingerprints are byte-identical; `guest_mode` stays `safety_critical`; 308: per-value, aggregate and sanitization causes, no number for sanitization, `reason_code` preserved, disclosure excluded from `evidence_fingerprint` |
| 3. front-door slice | After §4.3: the specific reviewed narrowing for the classes found (Option B pieces) | depends on the classes (`dependency/obligation_ledger.py`, semantics registry) | `front_door_lock_bedtime`-shaped fixture stays elevated until every obligation is resolved |

HAMCP-308 can be a separate PR after C-iv if Josh prefers smaller diffs. It
touches the same `_bind_bounded_evidence` site, so bundling avoids two reviews
of the same function.

Each PR is a 2.4.2 beta release PR under the merge-order and Evidence rule
recorded in the diagnosis (§8 there).

### 6.2 Evidence and mutation scope

- **Evidence.** One final `-Tier Evidence` run per PR, when it is next in
  line. Not run for this design PR (contract: no Evidence run).
- **Mutation.**
  - PR 2: `_bind_bounded_evidence` and `_bounded_evidence_values`
    (`models.py:94-135`, `257-327`), and `_entry` / `bound_for`
    (`bounded_sources.py:140-143`, `202-219`).
  - PR 1: `_relation_for` (`extraction.py:3285-3311`) and
    `_obligation_targets_helper` (`helper_dependency.py:653-706`).
  - Survivors must be killed or justified.

### 6.3 Decisions pending Josh

1. The C-iv option: **1** (recommended), 2, or 3.
2. Sub-decision 1a or 1b: whether a path-only digest sets `evidence_bounded`.
3. Whether sanitization-altered own paths stay a coverage failure
   (recommended: yes).
4. Whether HAMCP-310 gates C-iv (recommended: yes, the same or an earlier
   release).
5. HAMCP-308 bundled with C-iv or separate; whether to project
   `provenance_digest` causes in `obligation_evidence`.
6. Whether to request the §4.3 observation now, which is the prerequisite for
   the front-door slice.

## 7. Limits

- Every live number here is relayed by the contract. None was observed.
- The third bounded automation's failure cause is unmeasured.
- The reproduction is synthetic. It exercises the real extraction and
  projection at the base, but not a full plan build, live registry or state
  inventory.
- The fingerprint and plan-revalidation effects are inferred from source. They
  were not exercised end to end.
- No runtime, test, version or release file is changed by this PR.
