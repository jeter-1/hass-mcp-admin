# HAMCP-315: helper-value evidence prerequisite

This is source review and a committed lane implementation, **not a PASS receipt**.
Architect review of this manifest/lane PR and Josh's merge precede execution.
No seq-17 candidate, owner signature or production admission is created here.

## Exact authority and scope

Target Core is **2026.11.0**, source commit
`74cf5a49c63ed8d4d87717844bf14f79c28c9cea`, archive SHA-256
`85f3c42c9f77aa45e01faff4471007beadf5cb50c4324f496a239139eec70ff1`.
The existing `scripts/core_lane_bindings.py` binding pins index
`sha256:c6c1ca9931036bf3a2c4cc088d8db25dfea3b33935a5ecc83c40349462e2f69e`
and reviewed ha-mcp **8.5.0**. The committed provenance fixture retains its
disposable entry ID; the signed predecessor retains its production entry ID.
All source and image provenance fields must agree. No binding change is needed.

The seq-16 journal bytes must hash to
`8df3665c8c27222541f99682dcc3b65b8f65d590b726f2f5bb0e74b55e71b82c`.
The lane verifies that journal with the committed **public verification key** and
requires its exact 31-reference Core entry. Disposable test authority is signed
only by a new in-memory ephemeral key, as in earlier contract lanes. The two
test authorities carry the original 31 references and those same references
plus `core.typed_helper_value_operation`; this is not signing seq 17.

The distinct `helper_value` group proves only that added profile. Existing
`core.typed_helper_operation` describes the governed helper adapter and cannot
lend its evidence. The new mapping never adds a profile to inherited authority:
`passing_subset` still intersects with the authenticated predecessor. Scheduled
full evidence does not invoke this opt-in local extension runner; a missing
helper group remains withheld. Automating future helper inheritance is outside
this prerequisite, and must not be inferred from this mapping.

## Source judgments for architect review

`core-helper-source-hashes.json` records hashes from the exact archive above,
not reconstructed source. The manifest expands from 337 to **456** paths (119
added, none removed), retaining every prior profile mapping. The helper profile
maps **148** files: all regular top-level files in the eight helper domains and
their button/number/select/text base domains, plus the shared Core state,
service, entity, collection, validation, restore, event, storage, time and
REST/WebSocket files used by these contracts. This follows the existing broad
component-scope inventory; metadata/translation files are deliberately included.
Historical HAMCP-279 measurements remain bound to their original 337 paths;
they do not establish inheritance under this expansion. Missing paths refuse.

**Open architect decision before merge:** the global inventory is now a union
across Core layouts. Exact archive inspection in `manifest-compatibility.json`
finds 21 listed files absent in 2026.10.3 and the retained
`homeassistant/helpers/condition.py` absent in 2026.11.0. Therefore even an
identical-source comparison refuses at either release. In particular,
`core_evidence_workflow.py:230-232` builds its positive derived acceptance
fixture from October source and will refuse under this manifest. The draft
preserves that refusal; it does not fabricate target hashes, narrow historical
coverage or silently skip the derived fixture. The architect must settle the
manifest/acceptance treatment before this prerequisite can merge. Local offline
test success does not mean that Actions acceptance or inheritance is green.

November splits service and entity implementations out of `__init__.py`. The
following judgments therefore include `entity.py`, `services.py`, `const.py`,
`services.yaml` and initialization, not only the older October entry points.
Paths below are relative to `homeassistant/components/` at the exact commit.

| Scope | Relied-on semantics and lane proof |
|---|---|
| `input_boolean/` | Explicit `turn_on`/`turn_off`, exact on/off state; no toggle. Both services run. |
| `input_select/`, `select/` | Existing option membership, one selected option; no options-list edits. Valid option succeeds, missing option sends no write. |
| `input_number/`, `number/` | Numeric state, live min/max/step, number service validation and native value update. Exact fractional value succeeds; out-of-range value refuses before dispatch. |
| `input_text/`, `text/` | Live length/pattern/mode and exact string state. Valid text succeeds; invalid text sends no write. Password redaction remains covered by offline runtime tests; no password fixture is exported. |
| `input_datetime/` | `date`/`time` service fields and advertised `has_date`/`has_time`; exact state-format readback. Combined date/time changes through the real service. |
| `input_button/`, `button/` | Press changes the timestamp through the base entity's press wrapper; a never-pressed `unknown` state is allowed. Readback must parse as a timestamp and differ from baseline. |
| `counter/` | Integer value and initial/step/min/max. Core increment/decrement clamp; reset uses initial; set_value validates configured bounds/step. Engineering deliberately refuses increment at the upper bound before dispatch. All four services and that refusal run. |
| `timer/` | Start/restart/resume, pause, cancel and finish transitions; `finishes_at`, duration/remaining and `last_transition`. Independent readback distinguishes cancelled/finished idle states. No timer-definition edit occurs. |
| Shared state/service/entity code | Exact single-entity target, registered service schema, state revision, Core identity probes and readback. Engineering uses its shipped typed provider and real ha-mcp transport with `wait=false`, `return_response=false`, `verbose=false`; there is no direct-HA mutation fallback. |

The actual declaration/fingerprint remains the shipped profile in
`ha_core_readmission/profiles.py`; this PR does not change it. These source
judgments are a basis for execution, not a substitute for the exact-image lane.

## Committed local execution, only after merge

Use a clean checkout of the merged prerequisite and its task Python environment.
The exact Core, ha-mcp and fixture-writer digest images must already be cached.
The runner never pulls. Missing images, occupied names/ports, a busy host,
incorrect input hashes or a dirty checkout refuse before starting the lane.
The existing `core_evidence_host.py` local mode handles owned root-created
configuration cleanup using its cached pinned image, `--pull never` and no
network. No sudo, host-root execution or ad-hoc helper replacement is used.

Example (paths are task-owned; choose a **new** run directory):

```bash
CORE_EVIDENCE_HOST_MODE=local .artifacts/venv/bin/python scripts/core_helper_value_local.py \
  --mode local --pull never \
  --journal .artifacts/inputs/seq16-journal.json \
  --source-archive .artifacts/inputs/core-2026.11.0.tar.gz \
  --run-dir "$PWD/.artifacts/seq17-helper-run-1"
```

Run under the task's user `systemd-run` unit with a 60-minute outer cap if needed;
the runner bounds its acceptance body to 30 minutes and performs cleanup on
failure/interruption. It records UTC start/end, host-idle CPU sample, source
commit, manifest/journal hashes, exact running image config digests and the
group result in `helper_value.json`. No other evidence group runs. The fresh
Core uses a committed synthetic YAML fixture on a new internal Docker network;
published ports bind only to 127.0.0.1. The onboarding token exists only in
memory and the temporary mode-600 upstream environment file (removed on startup).
Raw container logs/configuration/tokens are not evidence artifacts.

The lane executes all 15 allowed domain/service pairs and timer transition
variants through `HelperService` and `HelperProvider`. It proves signed-only
absence refuses before mutation; exact independent readback; live-value/target
refusals; duplicate suppression; lost response after a real write without replay;
an unavailable readback stays unverified; and reopening the durable store recovers
by reading without redispatch. Response/readback fault injection is explicit
instrumentation; it does not replace Core or ha-mcp behavior or invent success.
Offline tests separately cover safety-critical acknowledgement, stale authority,
provider drift, strict schemas, races and password-mode redaction.

Every success requires owned containers, network and configuration to be absent
and `cleanup.clean: true`. A group failure or cleanup failure is **WITHHELD**:
do not build seq 17, rerun a substitute, or claim the offline tests as Core evidence.
Keep the receipt and report the failure. Failure before resource creation is also
WITHHELD, with no lane PASS claimed. A hard kill can prevent cleanup; inspect owned
resources before any retry and do not infer absence from an inspection error.

## Candidate gate and security

After a passing merged lane, use the existing registry utility's
`extend-capabilities` operation and non-signing verifier. Preserve all old
references in order, all sibling entries and denial history. The new evidence
hash and mandatory envelope sequence/predecessor/time fields necessarily change.
Prepare locally, checkpoint both complete files, their SHA-256 values and envelope
diff, then stop for the architect's written approval of **both** hashes. Only that
approval permits one push to `registry-candidates/2026.11.0-seq17`; verify remote
blobs and report Josh's dry-run command. Do not dispatch it.

No production private key, GitHub environment/secret/ruleset, signing workflow,
runtime route/schema, stable-v1 file, version or release metadata changes here.
No live Home Assistant, deployment, publication, Ready action or main merge is
authorized by this evidence prerequisite. Recovery for this unmerged PR is to
leave it draft; reverting it later removes the new lane and source mapping, not
any signed registry authority. Main must not move during publication.
