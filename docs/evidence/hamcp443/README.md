# HAMCP-443 (A): receipt comparisons for the local Core lanes

HAMCP-443 (A) changes the device lane's upstream URL check (D7) and factors
the seven local Core harnesses onto a shared lane host. Neither change may
alter what a lane proves. The proof is a re-run of each lane whose new receipt
is compared field by field with the lane's committed receipt
(design `~/handoffs/architect/designs/2026-11-20-hamcp-443-evidence-lanes-v3.md`,
review finding F7: byte-identical receipts are unattainable).

## What is here

| File | Purpose |
|---|---|
| `receipt-exclusions.json` | The declaration, committed before any lane runs: per lane, the committed receipt and its SHA-256, the leaves excluded from value equality, and the value changes the task expects. |
| `README.md` | This file. |

New receipts and comparison reports are added here as the lanes run. Only
receipt JSON and comparison reports are committed. Run directories, container
logs and host files never are: a container log can hold the disposable Core's
admin token.

## How the comparison works

`scripts/compare_core_receipts.py --lane <lane> --observed <receipt>` reads the
declaration, the committed receipt and the new receipt, and nothing else. It
refuses (exit 2) when the committed receipt's SHA-256 differs from the
declaration, when the lane's harness is missing, when a JSON input has a
duplicate key or a non-finite number, or when the declaration is malformed.

Everywhere in the receipt it compares dict key sets, list lengths and leaf
types (`true` is not `1`, `1` is not `1.0`). Then:

- A leaf that an exclusion pattern matches skips value equality only. Its
  differences are listed in the report under its class.
- A declared expected change must hold exactly the declared committed value
  in the committed receipt and exactly the declared observed value in the new
  one. A stale baseline, an unchanged value or any other value fails.
- Every other leaf must be equal.

Exit 0 means equal, exit 1 means at least one difference. Under the brief, any
difference outside the declaration is a STOP.

A pattern is a list of segments. A string matches that dict key, a list of
strings matches any one of them, `*` matches exactly one segment (a key or a
list index) and `**` matches zero or more. A literal never matches a list
index. Every pattern must match at least one leaf of the committed receipt, so
a pattern cannot silently go stale. An expected change may not sit under an
exclusion.

## Device lane (`device_configuration_entity`)

Committed receipt: `docs/evidence/hamcp366/device_configuration_entity.json`
(SHA-256 `710a6aa172ded0dd168eef8b9784456081db0f3f888985ba17a742bc1071f5dc`,
the R8 run on `d84d8d9e`). Of its leaves, 189 are excluded:

| Class | Leaves | Why the value changes every run |
|---|---|---|
| `run_identity` | 3 | `source_commit`, `started_utc`, `ended_utc`. |
| `host_admission` | 5 | The host's load, memory, container and heavy-job counts when the lane was admitted. |
| `docker_assigned` | 1 | `stub_host_entry.address`: Docker picks the lane network's subnet. `stub_host_entry.name` stays compared. |
| `core_run_ids` | 54 | Context, config entry, device and registry ids the disposable Core mints. |
| `engineering_task_ids` | 20 | Task ids Engineering mints, and `undo_of`, which names one. |
| `core_timestamps` | 42 | Registry `created_at`/`modified_at` and state `last_changed`/`last_updated`. |
| `derived_from_run_values` | 36 | Fingerprints and evidence hashes over observations that contain the ids and times above. |
| `timing` | 28 | `seconds`, `state_reads_after_send` and the held tasks' `final.observation_attempts`, which count polls before a wall-clock deadline. |

The first three classes are the brief's examples (source commit, timestamps,
run ids). `timing` goes beyond them; the PR lists every excluded value that
differed. The top-level `operations/*/observation_attempts` stay compared, as
do every status, result, refusal code and send count, the `changed` booleans
and `changed_fields`, the attribute and identifier SHA-256s, the Core and stub
image digests, the profile fingerprint, the journal and manifest hashes and
`authority_transition`. `tests/test_compare_core_receipts.py` pins a sample of
these as compared.

Expected changes (D7): the upstream image and its `config_digest` move from
ha-mcp 8.5.0 to the 9.2.0 child that `REVIEWED_CORE_BINDINGS["2026.11.0"]`
names since HAMCP-437 I2b (`ci/support-window.yaml`).

## Other lanes

`helper_value` has no committed receipt, so its first 9.2.0 receipt is
committed here as new evidence rather than compared. Marketplace (hamcp290),
security metadata (hamcp134), scene and created-helper delete (hamcp364) and
thread A1/A2 (hamcp231) are added to `receipt-exclusions.json` before their own runs.
