# Automation audit baseline — offline validation and comparison

## Scope

This increment provides a pure local validator/comparator for retained Home
Assistant automation baselines. It does **not** add an MCP tool, provider read,
background collection job, server-side persistence, Home Assistant request,
governance operation, release change, or deployment behavior.

The comparator answers only what retained baseline evidence can support. It does
not reconstruct automation bodies, execute templates, expand blueprints, infer
household identity from Home Assistant versions/entity names, or treat a
sequential capture as atomic.

## Baseline schemas

The validator accepts:

- `automation-audit-baseline-v1` — the versioned offline comparison contract.
- `ha-automation-configuration-baseline-v1` — the retained October 1 legacy
  artifact, normalized conservatively in memory before comparison.

Unsupported baseline schemas are rejected. Legacy normalization never edits the
source artifact and never upgrades missing evidence.

### `automation-audit-baseline-v1`

The format contains:

- `baseline_id`;
- optional `source_artifact` identity/hash when the record is derived from an
  older artifact;
- capture `started_at`, `ended_at`, and explicit `non_atomic` status;
- installation assurance (`established` plus a stable scoped identifier, or
  `unestablished` with no identifier);
- inventory scope, discovery method, declared count, limit/limit-reached state,
  omissions, completeness and limitations;
- one explicit fingerprint contract;
- canonical configuration IDs, entity IDs, mapping status, configuration digest,
  provider, per-record collection-time status and coverage;
- operational enabled state separately from configuration evidence;
- explicit inventory- and authority-drift observations with their limitations;
- closed, bounded scalar authority/structural metadata and limitations; arbitrary
  provider responses are not accepted in these fields.

Timestamps use `YYYY-MM-DDTHH:MM:SS[.fraction](Z|+HH:MM|-HH:MM)` with
one to six optional fractional digits and an explicit timezone. Capture start
must not follow capture end. Observed configuration and enabled-state timestamps
must fall within those inclusive capture bounds, compared as instants across
timezone offsets. `observed` requires a timestamp; `capture_interval_only` and
`unavailable` retain null timestamps. Contradictory times are rejected rather
than used to establish configuration or state changes. Legacy missing times
remain unavailable.

Installation identity is a comparison boundary. Core version, entity names,
registry sequence or similar environment facts are not installation identity.
Two baselines cannot produce definitive configuration classifications unless
both have established installation assurance with the same identifier.

## Fingerprint models

The exact model for newly constructed version-1 audit baselines is:

`automation-audit-config-json-python-v1`

For finite JSON-native values, its representation is:

```python
json.dumps(
    configuration_data,
    sort_keys=True,
    separators=(",", ":"),
    ensure_ascii=True,
    allow_nan=False,
).encode("utf-8")
```

followed by SHA-256 and the `sha256:<64 lowercase hex>` representation. Object
keys are sorted lexicographically at every object level, array order is preserved,
separators are compact, non-ASCII characters use Python `ensure_ascii=True`
escapes, finite JSON number types retain Python JSON rendering (`1` differs from
`1.0`), and non-finite or non-JSON values are rejected. This is not RFC 8785.
Any serialization or selected-field change requires a new model identifier.

The fingerprint input is the configuration **data object only**. Operational
on/off state, `last_triggered`, entity-registry metadata, and provider/timing/request
envelope fields are excluded. Raw configuration bodies are not stored by this
format.

### Retained October 1 fingerprint limitation

The retained artifact declares `ha-automation-config-fingerprint-v1` and states
SHA-256 over the complete configuration data object with sorted object keys,
preserved array order, compact JSON, preserved JSON scalar types and UTF-8 bytes.
That declaration does **not** settle Unicode escaping or the exact numeric encoder.
The raw configuration bodies and the original hashing implementation were not
retained, so those missing details cannot be recovered by recomputation.

Current source does not close that gap. At source revision
`bba6d5f6dc50c6768f3fb1c395200987b74c9ec2`, the reliability provider computes
a different fingerprint over `sanitize_untrusted_data(..., max_string=2000)` and
then uses Python `json.dumps(..., sort_keys=True, separators=(",", ":"),
default=str)`. That distinct sanitized/truncated input is not evidence that the
retained whole-configuration hashes used the same encoder or canonical bytes.

Therefore legacy normalization assigns the retained hashes the deliberately
incompatible model `legacy-haab-config-fingerprint-unresolved-v1`. It preserves
digest syntax and the stated input/exclusions without retroactively upgrading
their assurance. A future exact-model baseline cannot classify those legacy hashes
as `UNCHANGED` or `CHANGED` merely because the hex values happen to match.

The October 1 per-record digests cannot be independently recomputed from the
retained artifact. The validator can check digest syntax, record/accounting
structure, the artifact's retained internal material hash and capture assertions;
it cannot prove that each stored configuration digest was calculated from the
claimed raw configuration.

## Comparison semantics

The primary object identity is verified canonical `configuration_id` within the
same established installation and inventory scope.

For an identity present in both baselines, capture-fence authority continuity
must also be established for both baselines:

- `UNCHANGED` — both records are readable with complete unredacted/untruncated
  no-fallback coverage under the same supported fingerprint contract and the
  digests match.
- `CHANGED` — the same prerequisites hold and the digests differ.
- `UNKNOWN` — installation identity, mapping, readability, coverage, fingerprint
  compatibility or another decision-critical prerequisite is missing.

For an identity present only in the later baseline:

- `ADDED` requires the earlier inventory to prove absence within the same scope
  and to have no detected/unknown inventory drift at its capture fences.
- otherwise the result is `UNKNOWN`.

For an identity present only in the earlier baseline:

- `REMOVED` requires the later inventory to prove absence within the same scope
  and to have no detected/unknown inventory drift at its capture fences.
- otherwise the result is `UNKNOWN`.

A configuration read failure remains a present `UNKNOWN` record and never
becomes `REMOVED`. An inventory limit is not proof of absence. Reaching exactly
100 results from a 100-result inventory surface is therefore not complete
inventory evidence without an independent completeness mechanism.

A complete inventory must retain exactly its declared record count and report
zero known omissions. Missing records, positive or unknown omissions, and a
reached inventory limit contradict a complete declaration and are rejected.
Partial inventories retain their uncertainty; an empty complete inventory with
zero declared records can legitimately prove absence.

Entity-ID renames and enabled-state changes are reported separately when the
canonical mapping on both sides and the same installation and inventory scope
are established. Unknown or contradictory mappings cannot establish these
changes. They do not by themselves change the configuration classification.

Partial baselines do not poison unrelated records: comparable shared records can
still be `UNCHANGED` or `CHANGED` while other identities remain `UNKNOWN`.

Matching digests prove equality only of the captured representation. They do
not prove uninterrupted equality throughout the capture interval. An unchanged
blueprint reference/input document does not prove that the external blueprint
body or effective behavior remained unchanged. Without retained old
configuration bodies the comparator never claims field-level edits.

## Retained October 1 baseline

The retained baseline `haab-20261001-541ff78b9f25a8c8` requires conservative
normalization before it can be used by this contract:

- it does not contain an installation identity, so installation assurance stays
  `unestablished`;
- it reached the documented public automation-inventory bound of 100 while
  returning 100 records, so absence is not independently established and the
  normalized inventory is `partial`;
- per-record collection times were not retained, so those fields remain null
  with `unavailable` status;
- raw configuration bodies were not retained, so configuration hashes are not
  recomputed;
- the declared legacy fingerprint model is normalized to
  `legacy-haab-config-fingerprint-unresolved-v1`, not the new exact model, because
  Unicode escaping and exact numeric encoder behavior are not retained;
- the original internal material digest can be recomputed because all material
  used for that artifact-level digest is present.

A derived normalized artifact must reference the original file hash and internal
material hash, carry its own file hash, and leave those missing assurances
explicit. The original JSON/report remain immutable.

## Processing bounds

The offline validator/comparator uses fixed bounds:

| Resource | Bound |
| --- | ---: |
| Input file | 2 MiB per baseline |
| Records | 1,000 per baseline |
| JSON nesting | 64 levels |
| Parsed JSON nodes | 100,000 |
| String/key size | 16 KiB UTF-8 |
| Limitations | 128 entries, 1 KiB each |
| Comparison detail rows | 1,000 |
| Rendered comparison output | 1,000,000 bytes |

Duplicate JSON keys, non-finite numbers, invalid digests, duplicate canonical
configuration IDs, conflicting verified entity mappings, unsupported baseline
schemas and malformed required fields fail closed with fixed error codes. Error
text never includes rejected values or configuration content.

Both loaders bound file acquisition itself to 2 MiB plus one overflow-detection
byte before parsing. Escaped lone Unicode surrogates in keys or values are
rejected with a fixed error code; valid Unicode retains the exact fingerprint
serialization above.

Output accounting is deterministic. The summary always covers all comparison
records. Detail rows may be omitted only when bounded output/detail limits are
reached; `details_truncated` and `omitted_detail_count` then state exactly what
was omitted. The renderer serializes the fixed header once, each candidate detail
once while fitting, and the selected final report once. It does not repeatedly
serialize complete candidate reports to discover a fit.

## Offline command

```text
python scripts/compare_automation_baselines.py EARLIER.json LATER.json
```

Optional local output and bounds:

```text
python scripts/compare_automation_baselines.py \
  EARLIER.json LATER.json \
  --output comparison.json \
  --max-output-bytes 1000000 \
  --max-details 1000
```

The command reads only the two explicit local files and optionally writes one
local report. It contains no network client, Home Assistant client, provider
adapter, template execution or subprocess-based collection path.

## Native integration boundary — not delivered here

A later MCP/native implementation must be separately designed, reviewed,
released and accepted. At minimum it must:

- reuse admitted automation-configuration readers instead of creating another
  raw access path;
- establish complete automation inventory independently of
  `list_automations`' default/maximum 100-result boundary; hitting the limit is
  not completeness evidence;
- bind entity-registry identity in bounded batches and keep contradictions
  explicit;
- keep configuration-read concurrency bounded and preserve provider/authority
  attribution;
- capture authority/inventory fences around the sequential collection and
  disclose non-atomic limitations;
- freeze one sanitized capture so pagination and comparison perform zero new
  Home Assistant reads;
- preserve unreadable/partial/omitted records as `UNKNOWN`, not absence;
- define durable storage namespace, integrity model, retention, size limits,
  cancellation semantics, concurrent-job ownership and cleanup before adding
  server-side persistence or collection jobs;
- preserve no-fallback and existing provider-security boundaries;
- keep raw automation bodies, credentials and arbitrary provider responses out
  of retained comparison baselines unless a separately reviewed requirement
  explicitly changes that boundary.

This offline increment is not the native baseline/diff capability and must not be
reported as such.
