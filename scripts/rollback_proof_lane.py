"""HAMCP-377 rollback proof lane: the previous release reads what this one writes.

Source subject. The release being rolled back from writes stores with its own
unmodified code; the previous release, extracted from its tagged commit, reads
them in a separate interpreter. Nothing edits a store between the two. No
container, no socket, no Home Assistant: both sides drive their own offline
test harness over a synthetic provider, and every receipt says so.

Scenarios
  12a  registry   an entry carrying capability references the previous release
                  does not compile, built by the release's own candidate tool
                  on the published journal and test-signed by its own dry run
  12b  device     device-configuration lock records: a conflict hold, a crash
                  with the lease still live, the same crash after the lease
                  expired; plus the recovery tiers in ``RECOVERY_TIERS``
  12c  plans      scene-create and created-object rollback plans

``run`` writes one receipt per scenario. ``check`` re-classifies recorded
receipts. A receipt carries observations and the words derived from them; the
classifier derives the words again and compares both with the pinned
expectation, so a receipt cannot assert a result its observations do not show.

``compare-candidate`` compares the reference objects the previous release read
with those of a supplied registry candidate, byte for byte. Until a real
candidate exists the 12a receipt says so; the lane never builds a stand-in.

  python scripts/rollback_proof_lane.py run --out DIR --work NEW_DIR \\
      --reader-python PYTHON [--writer-python PYTHON] [--writer-commit SHA] \\
      [--real-candidate FILE]
  python scripts/rollback_proof_lane.py check DIR
  python scripts/rollback_proof_lane.py compare-candidate DIR FILE [--record]
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/hamcp377_rollback_proof"
WRITER_DRIVER = FIXTURES / "tip_writer.py"
READER_DRIVER = FIXTURES / "previous_reader.py"

SCHEMA = "hamcp377-rollback-proof-receipt-v1"
ROW = "HAMCP-377"
PREVIOUS_VERSION = "2.4.2-beta.7"
PREVIOUS_TAG = "v2.4.2-beta.7"
PREVIOUS_TAG_OBJECT = "998e9021e5f187b0f375bff9b4b867243fd270c0"
PREVIOUS_COMMIT = "b614be43f8c56ae06fdec0ac0bcb479b9da39715"
# Published Core release registry journal (sequence 17), by content.
PUBLISHED_JOURNAL_BLOB = "59c4100473fd7722b4b8e44ac5061bef9d599c87"
PUBLISHED_JOURNAL_SHA256 = "3f79fce76484ff9f9a9c070defe4d1d13865ccbfd24fda0ab8724515c8cc6a5d"
PUBLISHED_SEQUENCE = 17
PUBLISHED_EXPIRES_AT = "2027-02-05T06:16:34Z"
# The committed public half of the registry key, by the sha256 of its raw bytes.
PUBLISHED_PUBLIC_KEY = "docs/evidence/hamcp222/core-public-key.txt"
PUBLISHED_KEY_SHA256 = "7793e80f480b573fbef4e7ca762ff9d1ac783fee1e253db7c35ba8fe21f63dac"
CORE_VERSION = "2026.11.0"
# 12a. The tool and command that prepare a real registry candidate, and the release's own
# key-free dry run that test-signs one. The lane runs both unmodified from the writer tree.
CANDIDATE_TOOL = "scripts/prepare_core_release_registry.py"
CANDIDATE_OPERATION = "extend-capabilities"
CANDIDATE_MODEL = "core-registry-extension-candidate-v1"
TEST_SIGNING_TOOL = "scripts/core_registry_one_tap.py"
TEST_SIGNING_OPERATION = "dry-run-sign"
CANDIDATE_FILE = "core-candidate.json"
SIGNED_JOURNAL = "dry-run/ha-core-release-registry.json"
REFERENCE_FIELDS = ("adapter_id", "capability_id", "contract_fingerprint", "profile_id",
                    "profile_version")
COMPARISON_PENDING = "not yet available: the real candidate does not exist"
COMPARISON_SCOPE = (
    "The canonical JSON bytes of each capability reference object of the Core "
    + CORE_VERSION + " entry, in order. Signature fields belong to envelopes and the journal, "
    "not to reference objects; they, the timestamps and the evidence digests are not compared.")
MAX_CANDIDATE_BYTES = 8 * 1024 * 1024
LOCK = "hass_mcp_engineering_beta/requirements.lock"
REGISTRY_RECORDS = "tests/fixtures/core_2026_10_device_registry/records.json"
DRIVER_TIMEOUT_SECONDS = 900

SYNTHETIC_PROVIDER_LIMITATION = (
    "Synthetic provider: both releases ran their own offline test harness. No Home Assistant "
    "Core, add-on container, Supervisor, MCP transport or Ingress page took part, so this "
    "receipt shows what the previous release's code does with these records, not what a "
    "deployed add-on does end to end.")
# What else each scenario's receipt must say about itself, word for word. A receipt states
# exactly its scenario's list: one that drops or rewords a limitation is not a PASS.
REGISTRY_LIMITATIONS = (
    "The candidate's review evidence is a placeholder, and the tool stamps the time "
    "it runs. Its evidence digests, generated_at and expires_at therefore differ "
    "from those of any real candidate; its reference objects are what the "
    "comparison covers.",
    "The test signature comes from the release's own dry run, which re-signs the "
    "published envelopes and the candidate with a key it generates and does not "
    "keep. The previous release read that journal under that key, not under the "
    "published key.",
    "The lane identifies a supplied real candidate by its sha256 only. It does not "
    "establish that the file is the candidate that was or will be signed.")
PLAN_LIMITATIONS = (
    "The objects the previous release deleted were placed in its synthetic "
    "Core by the reader harness under the ids the writer's plans name. They "
    "are not the objects the current release created.",)
LIMITATIONS = {"12a": [SYNTHETIC_PROVIDER_LIMITATION, *REGISTRY_LIMITATIONS],
               "12b": [SYNTHETIC_PROVIDER_LIMITATION],
               "12c": [SYNTHETIC_PROVIDER_LIMITATION, *PLAN_LIMITATIONS]}
# The receipt schema is closed at the top level and in its `lane` record: a key these sets do
# not name is a statement nothing checks, so a receipt carrying one is not a PASS.
SUBJECTS = ("source",)
RECEIPT_KEYS = frozenset({
    "schema", "row", "scenario", "title", "subject", "result", "failures", "summary",
    "limitations", "started_at", "ended_at", "reader", "writer", "runs", "trees_distinct",
    "drivers", "lane", "data", "cases"})
SCENARIO_KEYS = {
    "12a": frozenset({"candidate_tool", "test_signing_tool", "real_candidate_comparison"}),
    "12b": frozenset({"recovery"}),
    "12c": frozenset({"source_facts", "reader_harness"})}
LANE_KEYS = frozenset({"checkout_commit", "commands", "containers", "drivers_modified",
                       "network", "note"})

# 12a. The candidate appends these to the 32 published references.
RULED_ADDED = (
    "core.marketplace_read", "core.security_metadata_read",
    "core.device_configuration_entity_operation", "core.scene_create_delete_operation",
    "core.created_helper_delete_operation", "core.thread_matter_topology_read")
NOT_ADMITTED = tuple(item for item in RULED_ADDED if item != "core.marketplace_read")
MARKETPLACE = "core.marketplace_read"
MARKETPLACE_FINGERPRINT = "sha256:9393b59801d97786e3ac8cf2b44c1aec121198568bfb017cabaa5691acc0cfeb"
PUBLISHED_REFERENCES = 32
EXPECTED_REGISTRY = {"admitted": 33, "not_admitted": 5, "quarantined": 0,
                     "marketplace_admitted": True}

# 12b. behaviour: what the held switch got. scope: what an unrelated switch got.
# governed: the governed runtime's execution readiness over the same store.
EXPECTED_DEVICE = {
    "hold": {"behaviour": "block", "scope": "held_entity_only", "governed": "ready",
             "lease": ["conflict_hold"]},
    "in_flight_active": {"behaviour": "block", "scope": "held_entity_only", "governed": "ready",
                         "lease": ["active"]},
    "in_flight_expired": {"behaviour": "block", "scope": "unrelated_entity_also_blocked",
                          "governed": "faulted", "lease": ["expired_pending_recovery"]},
}
EXPECTED_AFTER_RECOVERY = {"behaviour": "proceed", "scope": "none_blocked", "governed": "ready",
                           "lease": []}

# 12c. Refusal for scene and helper; execution recorded for automation and script.
REFUSAL_CODE = "policy_snapshot_mismatch"
EXPECTED_PLANS = {
    "scene_pending": "refused", "scene_approved": "refused", "scene_applied": "refused",
    "scene_rollback_approved": "refused", "helper_rollback_approved": "refused",
    "automation_rollback_approved": "executed", "script_rollback_approved": "executed",
    "automation_rollback_pending": "approved_then_executed",
    "automation_created": "no_rollback_plan_no_write",
}
EXECUTION_RECORDED = ("automation_rollback_approved", "script_rollback_approved",
                      "automation_rollback_pending")
PLAN_CALLS = ("get_plan", "approve", "apply", "rollback_change")

REQUIRED_CASES = {"12a": ("registry",), "12b": tuple(EXPECTED_DEVICE),
                  "12c": tuple(EXPECTED_PLANS)}
REQUIRED_DATA = {"12a": ("registry",), "12b": ("device_hold", "device_crash"),
                 "12c": ("plans",)}
TITLES = {"12a": "registry entry with capability references the previous release does not compile",
          "12b": "device-configuration lock records left for the previous release",
          "12c": "scene-create and created-object rollback plans left for the previous release"}
READY = {"execution_ready": True, "faults": []}


class LaneError(RuntimeError):
    """The lane could not produce evidence. Never a PASS."""


# --- reference objects: what 12a's result is about, and how it is compared -------------------

def canonical(value: Any) -> bytes:
    """The registry's canonical JSON (``signed_registry.canonical_json``), standard library only."""

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def reference_digest(objects: list) -> str:
    return hashlib.sha256(b"\n".join(canonical(item) for item in objects)).hexdigest()


def _no_duplicates(pairs: list) -> dict:
    value = dict(pairs)
    if len(value) != len(pairs):
        raise ValueError("duplicate object key")
    return value


def _no_constant(name: str) -> None:
    raise ValueError(f"non-finite number {name}")


def candidate_reference_objects(raw: bytes) -> dict:
    """The reference objects a registry candidate file carries, read without trusting it.

    Accepts the candidate tool's output (a wrapper around one unsigned envelope), a signed
    journal (its newest envelope) or a bare envelope. The file must be canonical JSON, as the
    tool writes it, so the objects compared are the bytes in the file.
    """

    if len(raw) > MAX_CANDIDATE_BYTES:
        raise LaneError("the candidate file is larger than a registry journal can be")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicates,
                           parse_constant=_no_constant)
        if raw not in (canonical(value), canonical(value) + b"\n"):
            raise ValueError("not canonical JSON")
        if isinstance(value.get("envelope"), dict):
            shape, envelope = "candidate", value["envelope"]
        elif isinstance(value.get("envelopes"), list) and value["envelopes"]:
            shape, envelope = "signed journal", value["envelopes"][-1]
        elif isinstance(value.get("entries"), list):
            shape, envelope = "envelope", value
        else:
            raise ValueError("neither a candidate, a signed journal nor an envelope")
        entry, = (item for item in envelope["entries"] if item["version"] == CORE_VERSION)
        objects = entry["capabilities"]
        if not objects or any(
                not isinstance(item, dict) or tuple(sorted(item)) != REFERENCE_FIELDS
                or any(type(field) not in (str, int) for field in item.values())
                for item in objects):
            raise ValueError("a capability reference is not a five-field reference object")
        sequence = envelope["sequence"]
        if type(sequence) is not int:
            raise ValueError("the sequence is not an integer")
    except (AttributeError, KeyError, TypeError, UnicodeDecodeError, ValueError) as error:
        raise LaneError(f"the candidate file cannot be compared: {error}") from None
    return {"shape": shape, "sequence": sequence, "objects": objects,
            "sha256": hashlib.sha256(raw).hexdigest()}


def compare_real_candidate(objects: list, candidate: Path | None) -> dict:
    """Compare the lane's reference objects with a supplied candidate's, byte for byte.

    With no candidate the result says the comparison is not yet available. The lane never
    builds or approximates the real candidate, and it does not establish that a supplied
    file is the real one: the file is identified by its sha256 for a reviewer to match.
    """

    result = {"status": COMPARISON_PENDING, "compares": COMPARISON_SCOPE,
              "lane_reference_count": len(objects),
              "lane_reference_objects_sha256": reference_digest(objects)}
    if candidate is None:
        return result
    try:
        raw = candidate.read_bytes()
    except OSError as error:
        raise LaneError(f"the candidate file cannot be read: {type(error).__name__}") from None
    found = candidate_reference_objects(raw)
    theirs = found["objects"]
    differences = [
        {"index": index,
         "lane": objects[index] if index < len(objects) else None,
         "candidate": theirs[index] if index < len(theirs) else None}
        for index in range(max(len(objects), len(theirs)))
        if index >= len(objects) or index >= len(theirs)
        or canonical(objects[index]) != canonical(theirs[index])]
    result.update(
        status="compared", compared_at=utc_now(), candidate_file=candidate.name,
        candidate_sha256=found["sha256"], candidate_shape=found["shape"],
        candidate_sequence=found["sequence"], candidate_reference_count=len(theirs),
        candidate_reference_objects_sha256=reference_digest(theirs),
        identical=not differences, differences=differences,
        candidate_authenticity="not established by the lane: the file is identified by its "
                               "sha256 only")
    return result


def classify_comparison(receipt: dict) -> list[str]:
    """G14: the real-candidate comparison is pending, or recorded and byte-identical."""

    failures: list[str] = []
    comparison = receipt.get("real_candidate_comparison")
    observed = receipt["cases"]["registry"]["observed"]
    objects = observed["writer"]["reference_objects"]
    if (not isinstance(comparison, dict) or comparison.get("compares") != COMPARISON_SCOPE
            or comparison.get("lane_reference_count") != len(objects)
            or comparison.get("lane_reference_objects_sha256") != reference_digest(objects)):
        return ["G14: the real-candidate comparison is not recorded against the reference "
                "objects the previous release read"]
    if comparison.get("status") == COMPARISON_PENDING:
        if set(comparison) != {"status", "compares", "lane_reference_count",
                               "lane_reference_objects_sha256"}:
            failures.append("G14: a pending comparison carries a comparison result")
        return failures
    own = {observed["lane"]["candidate"]["sha256"], observed["lane"]["journal"]["sha256"]}
    digest = str(comparison.get("candidate_sha256"))
    if (comparison.get("status") != "compared" or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)):
        return ["G14: the real-candidate comparison is neither pending nor a recorded comparison"]
    if digest in own:
        failures.append("G14: the file compared is the lane's own test candidate, not a real one")
    if comparison.get("candidate_sequence") != PUBLISHED_SEQUENCE + 1:
        failures.append("G14: the file compared is not a sequence "
                        f"{PUBLISHED_SEQUENCE + 1} candidate")
    if (comparison.get("identical") is not True or comparison.get("differences") != []
            or comparison.get("candidate_reference_count") != len(objects)
            or comparison.get("candidate_reference_objects_sha256")
            != comparison["lane_reference_objects_sha256"]):
        failures.append("G14: the real candidate's reference objects are not byte-identical to "
                        "the ones the previous release read")
    return failures


# --- classification: pure functions over a receipt -------------------------------------------

def registry_words(case: dict) -> dict:
    reader = case["observed"]["reader"]
    route, selected = reader["route_status"], reader["selected"]
    admitted = [item for item in reader["entry_references"]
                if route.get(item, {}).get("available") is True
                and route[item].get("disposition") == "admitted" and item in selected]
    return {"admitted": len(admitted),
            "not_admitted": len(reader["entry_references"]) - len(admitted),
            "quarantined": reader["counts"].get("quarantined_count", 0)
            + len(reader["withheld"].get("quarantined_capabilities", [])),
            "marketplace_admitted": MARKETPLACE in admitted}


def classify_candidate_path(receipt: dict, require_exact: bool) -> list[str]:
    """G3: 12a's entry comes out of the candidate tool's own command, test key aside."""

    failures: list[str] = []
    observed = receipt["cases"]["registry"]["observed"]
    writer, lane = observed["writer"], observed["lane"]
    candidate, signed, tools = writer["candidate"], writer["test_signed"], writer["tools"]
    data = receipt["data"].get("registry", {})
    public_key = f"<writer-source>/{PUBLISHED_PUBLIC_KEY}"
    prepared = tools["candidate"]
    if (prepared["path"] != CANDIDATE_TOOL or prepared["operation"] != CANDIDATE_OPERATION
            or prepared["exit"] != 0 or prepared["arguments"] != [
                "--entry", "<data>/core-entry.json", "--evidence",
                "<data>/core-review-evidence.json", "--public-key", public_key,
                "--previous", "<published-journal>", "--output", f"<data>/{CANDIDATE_FILE}"]):
        failures.append(f"G3: the candidate was not built by {CANDIDATE_TOOL} "
                        f"{CANDIDATE_OPERATION} on the published journal")
    if (candidate["model"] != CANDIDATE_MODEL or candidate["operation"] != CANDIDATE_OPERATION
            or candidate["sequence"] != PUBLISHED_SEQUENCE + 1
            or candidate["previous_journal_sha256"] != PUBLISHED_JOURNAL_SHA256
            or candidate["public_key_sha256"] != PUBLISHED_KEY_SHA256
            or writer["published_public_key"] != {"path": PUBLISHED_PUBLIC_KEY,
                                                  "key_sha256": PUBLISHED_KEY_SHA256}
            or len(candidate["unchanged_entries"]) != candidate["entries"] - 1):
        failures.append("G3: the candidate is not the next sequence on the published journal "
                        "under the published public key with one changed entry")
    if not (candidate["sha256"] == candidate["reported_sha256"] == data.get("candidate_sha256")
            == lane["candidate"]["sha256"]) or candidate["file"] != CANDIDATE_FILE:
        failures.append("G3: the candidate in the data set is not the file the tool reported")
    objects = writer["reference_objects"]
    if ([item.get("capability_id") for item in objects] != writer["references"]
            or any(tuple(sorted(item)) != REFERENCE_FIELDS for item in objects)
            or not (reference_digest(objects) == lane["candidate"]["reference_objects_sha256"]
                    == lane["journal"]["reference_objects_sha256"])
            or lane["candidate"]["shape"] != "candidate"
            or lane["journal"]["shape"] != "signed journal"
            or not (lane["candidate"]["sequence"] == lane["journal"]["sequence"]
                    == candidate["sequence"])):
        failures.append("G3: the recorded reference objects are not the ones in the candidate "
                        "and in the test-signed journal")
    signing = tools["test_signing"]
    if (signing["path"] != TEST_SIGNING_TOOL or signing["operation"] != TEST_SIGNING_OPERATION
            or signing["exit"] != 0 or signing["arguments"] != [
                "--candidate", f"<data>/{CANDIDATE_FILE}", "--predecessor",
                "<published-journal>", "--public-key", public_key,
                "--output-dir", "<data>/dry-run"]
            or signed["file"] != SIGNED_JOURNAL or signed["sequence"] != candidate["sequence"]
            or signed["envelope_equals_candidate_envelope"] is not True
            or signed["entry_references_equal_candidate"] is not True
            or signed["predecessor_content_unchanged"] is not True):
        failures.append("G3: the test-signed journal differs from the candidate in more than "
                        "its signatures")
    if len(str(signed["key_sha256"])) != 64 or signed["key_sha256"] == PUBLISHED_KEY_SHA256:
        failures.append("G3: the test signature was not made with a key of the run's own")
    for name, path, operation, ran in (
            ("candidate_tool", CANDIDATE_TOOL, CANDIDATE_OPERATION, prepared),
            ("test_signing_tool", TEST_SIGNING_TOOL, TEST_SIGNING_OPERATION, signing)):
        tool = receipt.get(name) or {}
        if (tool.get("path") != path or tool.get("operation") != operation
                or tool.get("commit") != receipt["writer"].get("commit")
                or tool.get("source") != receipt["writer"].get("source")
                or tool.get("sha256") != ran["sha256"] or len(str(tool.get("blob_run"))) != 40
                or (require_exact and tool.get("blob_run") != tool.get("blob_at_commit"))):
            failures.append(f"G3: the receipt does not tie {path} as it ran to the writer commit")
    return failures


def classify_registry(receipt: dict, require_exact: bool = True) -> list[str]:
    failures: list[str] = []
    case = receipt["cases"].get("registry")
    if case is None:
        return failures
    writer, reader = case["observed"]["writer"], case["observed"]["reader"]
    references, previous = writer["references"], writer["previous_references"]
    if (writer["published_journal_sha256"] != PUBLISHED_JOURNAL_SHA256
            or writer["published_sequence"] != PUBLISHED_SEQUENCE
            or writer["published_expires_at"] != PUBLISHED_EXPIRES_AT
            or len(previous) != PUBLISHED_REFERENCES or references[:len(previous)] != previous
            or writer["sequence"] != PUBLISHED_SEQUENCE + 1):
        failures.append("G3: the entry was not built as an extension of the published entry on "
                        "the published journal")
    failures += classify_candidate_path(receipt, require_exact)
    if references[len(previous):] != list(RULED_ADDED) or writer["added"] != list(RULED_ADDED):
        failures.append("G3: the added references are not the ruled six in the ruled order")
    if (reader["entry_references"] != references or reader["entry_reference_objects_sha256"]
            != reference_digest(writer["reference_objects"])):
        failures.append("G3: the reader decoded a different reference list than the writer built")
    if not (writer["journal_sha256"] == reader["journal_sha256"]
            == receipt["data"].get("registry", {}).get("journal_sha256")
            == case["observed"]["lane"]["journal"]["sha256"]):
        failures.append("G2: the journal the reader loaded is not the journal the writer signed")
    failures += classify_comparison(receipt)
    route, selected = reader["route_status"], reader["selected"]
    unknown = [item for item in references if item not in reader["compiled"]]
    if sorted(unknown) != sorted(NOT_ADMITTED):
        failures.append("G4: the references the reader does not compile are not the expected five")
    for item in unknown:
        status = route.get(item, {})
        if (item in selected or status.get("available") is not False
                or status.get("disposition") == "admitted"):
            failures.append(f"G4: {item} is selected or routable on the previous release")
    known = [item for item in references if item in reader["compiled"]]
    for item in known:
        if (selected.get(item) != ["verified_compatibility", "positive"]
                or route.get(item, {}).get("available") is not True
                or route[item].get("disposition") != "admitted"):
            failures.append(f"G5: {item} is compiled by the previous release but not admitted")
    counts = reader["counts"]
    if (reader["refreshed"] is not True or reader["authority"] != {
            "entry_present": True, "positive_authority_current": True,
            "sequence": PUBLISHED_SEQUENCE + 1, "surface_denied": False}):
        failures.append("G5: the previous release did not accept the journal as current authority")
    if (counts.get("compatible_count") != len(known)
            or any(counts.get(key) for key in ("held_count", "quarantined_count",
                                               "unavailable_count", "withheld_count",
                                               "fallback_count"))
            or any(reader["withheld"].values())):
        failures.append("G5: admitting the entry held, quarantined or withheld a capability")
    marketplace = reader["marketplace"]
    if not (marketplace["entry_fingerprint"] == marketplace["compiled_fingerprint"]
            == MARKETPLACE_FINGERPRINT):
        failures.append("G6: core.marketplace_read is not bound to the pinned contract fingerprint")
    real_read = case["observed"].get("real_read", {})
    if receipt["subject"] == "source" and (real_read.get("exercised") is not False
                                           or not real_read.get("reason")):
        failures.append("G6: a source-subject receipt has no Core and cannot claim a real "
                        "marketplace read")
    derived = registry_words(case)
    if case.get("derived") != derived:
        failures.append("G5: the recorded counts are not the counts the observations give")
    if derived != EXPECTED_REGISTRY:
        failures.append(f"G5: observed {derived}, expected {EXPECTED_REGISTRY}")
    return failures


def device_words(observed: dict) -> dict:
    task = observed["task_id"]
    held, other = observed["held_switch"], observed["other_switch"]
    power, governed = observed["power"], observed["governed"]

    def blocked(result: dict) -> bool:
        return bool(result["ok"] and result["state"] == "failed_pre_dispatch"
                    and result["terminal_outcome"] == "lock_conflict"
                    and result["provider_attempt_count"] == 0 and result["provider_writes"] == 0
                    and result["blocking"]
                    and all(item["task_id"] == task for item in result["blocking"]))

    def done(result: dict) -> bool:
        return bool(result["ok"] and result["state"] == "succeeded_verified"
                    and result["provider_attempt_count"] == 1 and result["provider_writes"] == 1)

    kept = (power["locks_before"] == power["locks_after"]
            and governed["locks_before"] == governed["locks_after"]
            and power["locks_before"] == governed["locks_before"])
    if blocked(held) and kept and power["locks_before"]:
        behaviour = "block"
    elif done(held) and kept and not power["locks_before"]:
        behaviour = "proceed"
    else:
        behaviour = "unknown"
    if done(other):
        scope = "held_entity_only" if behaviour == "block" else "none_blocked"
    elif blocked(other):
        scope = "unrelated_entity_also_blocked"
    else:
        scope = "unknown"
    states, required = governed["readiness"], governed["require_execution_ready"]
    passed = all(item["ok"] for item in governed["passes"]) and len(states) == 3
    if passed and all(item == READY for item in states) and required["ok"]:
        readiness = "ready"
    elif (passed and all(item["execution_ready"] is False and item["faults"] for item in states)
          and not required["ok"]):
        readiness = "faulted"
    else:
        readiness = "unknown"
    return {"behaviour": behaviour, "scope": scope, "governed": readiness,
            "lease": sorted({item["lease_status"] for item in held["blocking"]})}


def classify_device_case(name: str, case: dict, expected: dict) -> list[str]:
    failures: list[str] = []
    derived = device_words(case["observed"])
    recorded = case.get("derived", {})
    for word, guard in (("behaviour", "G7"), ("scope", "G8"), ("governed", "G8"),
                        ("lease", "G7")):
        if recorded.get(word) != derived[word]:
            failures.append(f"{guard}: {name} records {word} {recorded.get(word)!r} but the "
                            f"observations give {derived[word]!r}")
        if derived[word] != expected[word]:
            failures.append(f"{guard}: {name} {word} is {derived[word]!r}, "
                            f"expected {expected[word]!r}")
    governed = case["observed"]["governed"]
    if not all(item["ok"] for item in governed["passes"]) or not governed["sdk_ok"]:
        failures.append(f"G11: {name}: the previous release's governed runtime did not load "
                        "the store")
    return failures


# 27a. Where the writer's operation died, and how the current release must have settled it:
# a task whose dispatch may have reached the provider is held for manual review, never
# reported as a verified success.
CRASH_POINT_27A = "provider call_service, after dispatch intent was recorded"
SETTLED_OUTCOME_27A = "manual_review_required"


def classify_tier_27a(tier: dict) -> list[str]:
    """Roll forward, let the current release settle the lock, release, roll back."""

    failures: list[str] = []
    if tier.get("exercised") is not True:
        return ["G9: 27a is recorded without exercised evidence"]
    steps = tier.get("steps") or {}
    crash, settle = steps.get("crash") or {}, steps.get("settle_release") or {}
    blocked, read = steps.get("blocked_before_recovery") or {}, steps.get(
        "previous_release_read") or {}
    if not (crash.get("died_in") and crash.get("locks") and crash.get("provider_writes") == 1):
        failures.append("G9: 27a has no crashed operation holding lock records to recover")
    if crash.get("died_in") != CRASH_POINT_27A:
        failures.append("G9: 27a's crash is not at the pinned crash point")
    if not blocked.get("observed") or blocked["observed"].get("task_id") != crash.get("task_id"):
        failures.append("G9: 27a does not show the previous release blocked on this store "
                        "before the recovery")
    else:
        failures += classify_device_case("27a before recovery", blocked,
                                         EXPECTED_DEVICE["in_flight_expired"])
    if not (settle.get("task_id") == crash.get("task_id") and settle.get("recovery_passes")
            and settle.get("settled", {}).get("terminal") is True
            and settle.get("settled", {}).get("provider_writes") == 0):
        failures.append("G9: 27a does not show the current release settling the crashed task "
                        "without dispatching again")
    release = settle.get("release") or {}
    if not (str(release.get("status", "")).startswith("hold_released")
            and release.get("dispatch") == "none" and release.get("provider_writes") == 0
            and release.get("locks_after") == [] and "release_hold" in str(release.get("path"))):
        failures.append("G9: 27a does not show the hold released through the current release's "
                        "own hold-release path with no lock record left")
    review = settle.get("review")
    if not (settle.get("settled", {}).get("normalized_outcome") == SETTLED_OUTCOME_27A
            and release.get("terminal_outcome") == SETTLED_OUTCOME_27A
            and isinstance(review, dict) and review.get("manual_review_reason")
            and review.get("hold_binding")):
        failures.append(f"G9: 27a does not show the crashed task settled to {SETTLED_OUTCOME_27A} "
                        "with its review record")
    if not read.get("observed"):
        failures.append("G9: 27a claims recovery without the previous release reading the result")
        return failures
    if read["observed"].get("task_id") != crash.get("task_id"):
        failures.append("G9: 27a's read is not of the crashed task's store")
    return failures + classify_device_case(
        "27a after recovery", read, EXPECTED_AFTER_RECOVERY)


RECOVERY_TIERS: dict[str, Callable[[dict], list[str]]] = {"27a": classify_tier_27a}
DEFAULT_RECOVERY_TIERS = ("27a",)


def classify_device(receipt: dict, tiers: tuple[str, ...]) -> list[str]:
    failures: list[str] = []
    for name, expected in EXPECTED_DEVICE.items():
        if name in receipt["cases"]:
            failures += classify_device_case(name, receipt["cases"][name], expected)
    recovery = receipt.get("recovery", {})
    for tier in tiers:
        if recovery.get(tier, {}).get("exercised") is not True:
            failures.append(f"G9: recovery tier {tier} is required and was not exercised")
    for tier, record in recovery.items():
        if tier not in RECOVERY_TIERS:
            failures.append(f"G9: recovery tier {tier} is recorded but has no classifier")
        else:
            failures += RECOVERY_TIERS[tier](record)
    return failures


def plan_target(observed: dict) -> dict:
    plans = observed["plans"]
    return plans.get("rollback") or plans.get("plan") or plans["created"]


def plan_outcome(observed: dict) -> str:
    target, writes = plan_target(observed), observed["provider_writes"]
    calls = target["calls"]
    if (all(calls.get(name) and not calls[name]["ok"] and calls[name]["code"] == REFUSAL_CODE
            for name in PLAN_CALLS) and not writes):
        return "refused"
    operations = target.get("operations") or []
    delete = (["rest", "DELETE", f"/config/{operations[0][1]}/config/{operations[0][2]}"]
              if len(operations) == 1 and operations[0][0] == "delete" else None)
    apply = calls.get("apply") or {}
    applied = bool(delete and apply.get("ok") and apply.get("status") == "applied"
                   and apply.get("writes") == [delete] and writes == [delete])
    approve, decision = calls.get("approve") or {}, calls.get("approval_decision")
    if (applied and target["writer_status"] == "awaiting_approval" and approve.get("ok")
            and approve.get("status") == "approval_pending" and decision and decision["ok"]
            and decision["status"] == "approved"):
        return "approved_then_executed"
    if applied and target["writer_status"] == "approved" and decision is None:
        return "executed"
    rollback = calls.get("rollback_change") or {}
    if (set(observed["plans"]) == {"created"} and not writes and not rollback.get("ok")
            and rollback.get("code") == "rollback_not_available"):
        return "no_rollback_plan_no_write"
    return "unknown"


def classify_plans(receipt: dict) -> list[str]:
    failures: list[str] = []
    for mode, expected in EXPECTED_PLANS.items():
        case = receipt["cases"].get(mode)
        if case is None:
            continue
        observed, recorded = case["observed"], case.get("derived", {}).get("outcome")
        derived = plan_outcome(observed)
        if mode in EXECUTION_RECORDED and "refused" in (recorded, derived):
            failures.append(f"G10: {mode} says the previous release refused an automation or "
                            "script rollback plan; it executes them")
        if recorded != derived:
            failures.append(f"G10: {mode} records {recorded!r} but the observations give "
                            f"{derived!r}")
        if derived != expected:
            failures.append(f"G10: {mode} is {derived!r}, expected {expected!r}")
        if expected == "refused" and observed["provider_writes"]:
            failures.append(f"G10: {mode} was refused yet the provider was written")
        if (observed["startup_assertions"] or observed["ready_after_startup"] != READY
                or observed["ready_final"] != READY
                or not all(item["ok"] for item in observed["decode"].values())
                or not observed["decode"]):
            failures.append(f"G11: {mode}: the previous release did not load the store and "
                            "stay ready")
    return failures


BEHAVIOUR = {
    "block": "control_power on the switch the device-configuration task holds is refused with "
             "lock_conflict before any provider attempt, and the lock records are unchanged",
    "proceed": "control_power on the previously held switch succeeds and is verified",
    "unknown": "what control_power does on the held switch was not established"}
SCOPE = {"held_entity_only": "control_power on an unrelated switch succeeds",
         "unrelated_entity_also_blocked": "control_power on an unrelated switch is refused with "
                                          "lock_conflict as well",
         "none_blocked": "control_power on an unrelated switch succeeds",
         "unknown": "what control_power does on an unrelated switch was not established"}
DEVICE_CASE = {"hold": "Conflict hold left by a settled device-configuration task",
               "in_flight_active": "Crash during a device-configuration operation, lease "
                                   "still live",
               "in_flight_expired": "Crash during a device-configuration operation, lease "
                                    "expired"}


def device_sentence(label: str, case: dict) -> str:
    words, governed = device_words(case["observed"]), case["observed"]["governed"]
    if words["governed"] == "ready":
        readiness = "the governed runtime reports execution ready"
    elif words["governed"] == "faulted":
        faults = sorted({fault for item in governed["readiness"] for fault in item["faults"]})
        readiness = (f"the governed runtime reports execution not ready ({', '.join(faults)}) "
                     f"after three recovery passes and refuses with "
                     f"{governed['require_execution_ready']['code']}")
    else:
        readiness = "the governed runtime's readiness was not established"
    return (f"{label}: on the previous release {BEHAVIOUR[words['behaviour']]}; "
            f"{SCOPE[words['scope']]}; {readiness}.")


def summarize(receipt: dict) -> list[str]:
    """Plain sentences computed from the observations, never written by hand."""

    scenario, cases, lines = receipt["scenario"], receipt["cases"], []
    if scenario == "12a" and "registry" in cases:
        words, reader = registry_words(cases["registry"]), cases["registry"]["observed"]["reader"]
        unknown = [item for item in reader["entry_references"] if item not in reader["compiled"]]
        lines.append(
            f"The previous release read a signed journal whose entry carries "
            f"{len(reader['entry_references'])} capability references: {words['admitted']} "
            f"admitted{' including ' + MARKETPLACE if words['marketplace_admitted'] else ''}, "
            f"{words['not_admitted']} not admitted ({', '.join(sorted(unknown))}), "
            f"{words['quarantined']} quarantined.")
        writer, tool = cases["registry"]["observed"]["writer"], receipt.get("candidate_tool", {})
        signed = writer["test_signed"]
        same = all(signed[key] is True for key in (
            "envelope_equals_candidate_envelope", "entry_references_equal_candidate",
            "predecessor_content_unchanged"))
        lines.append(
            f"The entry was built by {tool.get('path')} {tool.get('operation')} at commit "
            f"{tool.get('commit')} as sequence {writer['candidate']['sequence']} on the "
            f"published sequence {writer['published_sequence']} journal under the published "
            f"public key, and test-signed by {writer['tools']['test_signing']['path']} "
            f"{writer['tools']['test_signing']['operation']} with a key generated in that dry "
            f"run; the signed content "
            f"{'equals' if same else 'does not equal'} the candidate and the published content.")
        comparison = receipt.get("real_candidate_comparison", {})
        if comparison.get("status") == "compared":
            verdict = ("byte-identical to" if comparison.get("identical") is True
                       else f"different at {len(comparison.get('differences') or [])} "
                            f"positions from")
            lines.append(
                f"Comparison with the real sequence {PUBLISHED_SEQUENCE + 1} candidate: the "
                f"{comparison.get('lane_reference_count')} reference objects are {verdict} "
                f"those of the supplied {comparison.get('candidate_shape')} file with sha256 "
                f"{comparison.get('candidate_sha256')}.")
        else:
            lines.append(f"Comparison with the real sequence {PUBLISHED_SEQUENCE + 1} "
                         f"candidate: {comparison.get('status')}.")
    if scenario == "12b":
        lines += [device_sentence(label, cases[name]) for name, label in DEVICE_CASE.items()
                  if name in cases]
        tier = receipt.get("recovery", {}).get("27a", {})
        steps = tier.get("steps") or {}
        if tier.get("exercised") and all(step in steps for step in (
                "blocked_before_recovery", "settle_release", "previous_release_read")):
            lines.append(device_sentence(
                "Recovery 27a, before (the same crash, read before rolling forward)",
                steps["blocked_before_recovery"]))
            settle = steps["settle_release"]
            lines.append(
                f"Recovery 27a, roll forward: the current release settled the crashed task to "
                f"{settle['settled']['normalized_outcome']} with "
                f"{settle['settled']['provider_writes']} further provider writes, and its "
                f"hold-release path returned {settle['release']['status']} leaving "
                f"{len(settle['release']['locks_after'])} lock records.")
            lines.append(device_sentence("Recovery 27a, after (rolled back again)",
                                         steps["previous_release_read"]))
            before = device_words(steps["blocked_before_recovery"]["observed"])
            after = device_words(steps["previous_release_read"]["observed"])
            if ((before["behaviour"], before["governed"]) == ("block", "faulted")
                    and (after["behaviour"], after["governed"]) == ("proceed", "ready")):
                lines.append(
                    "Recovery 27a, observed: on this store the previous release ran no governed "
                    "control before the roll forward (execution not ready, control_power "
                    "refused) and ran it after (execution ready, control_power verified).")
    if scenario == "12c":
        outcomes: dict[str, list[str]] = {}
        for mode in sorted(cases):
            outcomes.setdefault(plan_outcome(cases[mode]["observed"]), []).append(mode)
        text = {
            "refused": "refused on get, approve, apply and rollback with " + REFUSAL_CODE
                       + ", no provider write",
            "executed": "applied by the previous release as an ordinary delete: one provider "
                        "DELETE each",
            "approved_then_executed": "approved on the previous release and then applied as an "
                                      "ordinary delete: one provider DELETE",
            "no_rollback_plan_no_write": "no rollback plan in the store: nothing to execute, "
                                         "no provider write",
            "unknown": "outcome not established"}
        lines += [f"{', '.join(modes)}: {text[outcome]}." for outcome, modes in
                  sorted(outcomes.items())]
        facts = receipt.get("source_facts", {})
        lines.append(f"References to created_identity in the previous release's runtime source: "
                     f"{facts.get('reader_created_identity_references')}; in the writer's: "
                     f"{facts.get('writer_created_identity_references')}.")
    return lines


CLASSIFIERS = {"12a": lambda receipt, tiers, exact: classify_registry(receipt, exact),
               "12b": lambda receipt, tiers, exact: classify_device(receipt, tiers),
               "12c": lambda receipt, tiers, exact: classify_plans(receipt)}


def classify(receipt: dict, *, require_exact: bool = True,
             recovery_tiers: tuple[str, ...] = DEFAULT_RECOVERY_TIERS) -> list[str]:
    """Every reason this receipt is not a PASS. Empty means PASS."""

    try:
        return _classify(receipt, require_exact, recovery_tiers)
    except (AttributeError, IndexError, KeyError, TypeError, ValueError) as error:
        return [f"G13: the receipt is malformed ({type(error).__name__}: {error})"]


def _classify(receipt: dict, require_exact: bool, recovery_tiers: tuple[str, ...]) -> list[str]:
    failures: list[str] = []
    scenario = receipt.get("scenario")
    if receipt.get("schema") != SCHEMA or scenario not in CLASSIFIERS:
        return [f"G13: not a {SCHEMA} receipt for a known scenario"]
    reader, writer = receipt["reader"], receipt["writer"]
    if ({key: reader.get(key) for key in ("version", "tag", "tag_object", "commit",
                                          "archive_commit")}
            != {"version": PREVIOUS_VERSION, "tag": PREVIOUS_TAG,
                "tag_object": PREVIOUS_TAG_OBJECT, "commit": PREVIOUS_COMMIT,
                "archive_commit": PREVIOUS_COMMIT}):
        failures.append("G1: the reader is not the previous release at its tagged commit")
    if reader.get("source") != "git-archive" or reader.get("isolated") is not True:
        failures.append("G1: the reader did not run isolated from a git archive of its commit")
    if not writer.get("version") or writer.get("version") == reader.get("version"):
        failures.append("G1: the writer is not a different release from the reader")
    if writer.get("version") != writer.get("declared_version"):
        failures.append("G1: the writer's runtime version is not its tree's declared version")
    for run in receipt.get("runs", []):
        side = reader if run["side"] == "reader" else writer
        if run["version"] != side.get("version") or run["package_in_own_tree"] is not True:
            failures.append(f"G1: {run['side']} step {run['step']} ran code from outside its "
                            "own source tree")
    if receipt.get("trees_distinct") is not True:
        failures.append("G2: the writer and the reader shared a source tree")
    if not any(run["side"] == "writer" for run in receipt.get("runs", [])) or not any(
            run["side"] == "reader" for run in receipt.get("runs", [])):
        failures.append("G2: the receipt does not show both a writer step and a reader step")
    if require_exact:
        if (writer.get("source") != "git-archive" or writer.get("archive_commit")
                != writer.get("commit") or len(str(writer.get("commit"))) != 40):
            failures.append("G1: the writer source is not a git archive of one exact commit")
        for side, record in (("reader", reader), ("writer", writer)):
            dependencies = record.get("dependencies") or {}
            if dependencies.get("mismatches") != [] or not dependencies.get("pins"):
                failures.append(f"G1: the {side} interpreter does not hold its tree's locked "
                                "dependencies")
    data = receipt.get("data", {})
    for name in REQUIRED_DATA[scenario]:
        if name not in data:
            failures.append(f"G2: data set {name} is not recorded")
    for name, record in data.items():
        for part in (record, *record.get("earlier", [])):
            if not part.get("files") or not (
                    part.get("after_write") == part.get("before_read") == part.get("after_read")
                    and part.get("after_write")):
                failures.append(f"G2: data set {name} changed between the writer and the end "
                                "of the read")
    lane = receipt.get("lane", {})
    if lane.get("containers") != 0 or lane.get("network") != "none":
        failures.append("G12: a source-subject run starts no container and opens no network")
    if require_exact and (len(str(lane.get("checkout_commit"))) != 40
                          or lane.get("drivers_modified") != []):
        failures.append("G2: the drivers that ran are not the drivers of one exact commit")
    if SYNTHETIC_PROVIDER_LIMITATION not in receipt.get("limitations", []):
        failures.append("G13: the receipt does not state the synthetic-provider limitation")
    elif receipt.get("limitations") != LIMITATIONS[scenario]:
        failures.append("G13: the receipt's limitations are not exactly the ones scenario "
                        f"{scenario} must state")
    for where, record, known in (("", receipt, RECEIPT_KEYS | SCENARIO_KEYS[scenario]),
                                 ("'s lane record", lane, LANE_KEYS)):
        unknown = sorted(str(key) for key in set(record) - known)
        if unknown:
            failures.append(f"G13: the receipt{where} carries keys its schema does not define: "
                            + ", ".join(unknown))
    for name in REQUIRED_CASES[scenario]:
        if name not in receipt.get("cases", {}):
            failures.append(f"G13: case {name} is missing")
    failures += CLASSIFIERS[scenario](receipt, recovery_tiers, require_exact)
    if receipt.get("summary") != summarize(receipt):
        failures.append("G8: the receipt's summary is not the summary its observations give")
    return failures


def gate(receipts: list[dict], *, subject: str = "source", require_exact: bool = True,
         recovery_tiers: tuple[str, ...] = DEFAULT_RECOVERY_TIERS) -> list[str]:
    """The case manifest: every scenario present once, recorded PASS, PASS when re-classified.

    A receipt of a subject or scenario this lane does not know, and a second receipt for one
    scenario, are failures: neither may sit beside the receipts and go unread.
    """

    failures: list[str] = []
    seen: dict[tuple, int] = {}
    for receipt in receipts:
        named = (receipt.get("subject"), receipt.get("scenario"))
        if named[0] not in SUBJECTS:
            failures.append(f"G13: a receipt names the unknown subject {named[0]!r}")
        elif named[1] not in CLASSIFIERS:
            failures.append(f"G13: a receipt names the unknown scenario {named[1]!r}")
        seen[named] = seen.get(named, 0) + 1
    failures += [f"G13: more than one {named[0]} receipt for scenario {named[1]}"
                 for named, count in seen.items()
                 if count > 1 and named[0] in SUBJECTS and named[1] in CLASSIFIERS]
    found = {receipt.get("scenario"): receipt for receipt in receipts
             if receipt.get("subject") == subject}
    for scenario in CLASSIFIERS:
        receipt = found.get(scenario)
        if receipt is None:
            failures.append(f"G13: no {subject} receipt for scenario {scenario}")
            continue
        again = classify(receipt, require_exact=require_exact, recovery_tiers=recovery_tiers)
        failures += [f"{scenario}: {item}" for item in again]
        if receipt.get("result") != "PASS" or receipt.get("failures") != []:
            failures.append(f"G13: the {scenario} receipt does not record PASS")
    return failures


# --- projection: driver output to the observations a receipt keeps ---------------------------

def observe_registry(writer: dict, reader: dict, lane: dict) -> dict:
    selected = {item: [selection["source"], selection["status"]]
                for selection in reader["selections"] for item in selection["capability_ids"]}
    reconcile = reader["reconcile"]
    fingerprints = {item["capability_id"]: item["contract_fingerprint"]
                    for item in reader["entry_references"]}
    return {
        "writer": {key: writer[key] for key in (
            "published_journal_sha256", "published_sequence", "published_expires_at",
            "published_public_key", "tools", "inputs", "candidate", "reference_objects",
            "test_signed", "journal_sha256", "sequence", "previous_references", "added",
            "references", "compiled_profiles")},
        "lane": lane,
        "reader": {
            "refreshed": reader["refreshed"].get("value") if reader["refreshed"]["ok"] else False,
            "journal_sha256": reader["journal_sha256"], "authority": reader["authority"],
            "compiled": sorted(reader["compiled"]),
            "entry_references": [item["capability_id"] for item in reader["entry_references"]],
            "entry_reference_objects_sha256": reference_digest(reader["entry_references"]),
            "selected": selected,
            "route_status": {item: {key: status.get(key) for key in (
                "available", "disposition", "reason_codes")}
                for item, status in reconcile["route_status"].items()},
            "counts": reconcile["counts"], "withheld": reconcile["withheld"],
            "recovery_state": reconcile["recovery_state"],
            "before_refresh_compatible_count": reader["before_refresh_compatible_count"],
            "marketplace": {"entry_fingerprint": fingerprints.get(MARKETPLACE),
                            "compiled_fingerprint": reader["compiled"].get(MARKETPLACE)},
            "core": reader["core"]},
        "real_read": {"exercised": False,
                      "reason": "the source subject has no Home Assistant Core; the real "
                                "marketplace read belongs to the image subject"}}


def observe_device(reader: dict) -> dict:
    power, governed = reader["power"], reader["governed"]

    def switch(result: dict) -> dict:
        value = result.get("value") or {}
        return {"entity_id": result["entity_id"], "ok": result["ok"],
                "state": value.get("state"), "terminal_outcome": value.get("terminal_outcome"),
                "provider_attempt_count": value.get("provider_attempt_count"),
                "provider_writes": result["provider_writes"],
                "error": result.get("error"),
                "blocking": [{key: item.get(key) for key in (
                    "lock_key", "lease_status", "task_id", "release")}
                    for item in value.get("blocking_tasks") or []]}

    required = governed["require_execution_ready"]
    return {
        "task_id": reader["task_id"], "now": reader["now"],
        "held_switch": switch(power["held_switch"]), "other_switch": switch(power["other_switch"]),
        "power": {"provider": power["provider"], "locks_before": power["locks_before"],
                  "locks_after": power["locks_after"], "files": power["files"],
                  "provider_writes": power["provider_writes"]},
        "governed": {
            "driver": governed["driver"], "sdk_ok": bool(governed["sdk"].get("ok", True)),
            "passes": [{"trigger": item["trigger"], "ok": item["ok"],
                        "code": (item.get("error") or {}).get("code")}
                       for item in governed["passes"]],
            "readiness": [{"execution_ready": item.get("execution_ready"),
                           "faults": item.get("faults")} for item in governed["readiness"]],
            "require_execution_ready": {"ok": required["ok"],
                                        "code": (required.get("error") or {}).get("code"),
                                        "text": (required.get("error") or {}).get("text")},
            "runtime_status": governed["snapshot"].get("runtime_status"),
            "provider_calls": governed["snapshot"].get("provider_calls"),
            "locks_before": governed["locks_before"], "locks_after": governed["locks_after"],
            "files": governed["files"]}}


def observe_plans(mode: dict) -> dict:
    def call(result: dict | None) -> dict | None:
        if result is None:
            return None
        value = result.get("value") or {}
        return {"ok": result["ok"], "code": (result.get("error") or {}).get("code"),
                "status": value.get("status"), "writes": result["provider_writes"],
                **({"rollback_plan_id": value["rollback_plan_id"]}
                   if value.get("rollback_plan_id") else {})}

    def ready(state: dict) -> dict:
        return {"execution_ready": state.get("execution_ready"), "faults": state.get("faults")}

    listing = mode["list_plans"]
    return {
        "startup_assertions": mode["startup_assertions"],
        "ready_after_startup": ready(mode["readiness_after_startup"]),
        "ready_final": ready(mode["readiness_final"]),
        "decode": {plan: {"ok": item["ok"], "status": item.get("status"),
                          "error": (item.get("error") or {}).get("text")}
                   for plan, item in mode["decode"].items()},
        "list_plans": listing.get("value") if listing["ok"] else listing.get("error"),
        "synthetic_core_objects": mode["synthetic_core_objects"],
        "plans": {role: {"plan_id": record["plan_id"], "writer_status": record["writer_status"],
                         "operations": record["operations"],
                         "calls": {name: call(record.get(name)) for name in (
                             "get_plan", "approve", "approval_decision", "apply",
                             "rollback_change", "status_after")}}
                  for role, record in mode["plans"].items()},
        "provider_writes": mode["provider_writes"], "files": mode["files"],
        "file_count": mode["file_count"]}


# --- driver ----------------------------------------------------------------------------------

DEPENDENCY_PROBE = r'''
import hashlib, importlib.metadata as metadata, json, platform, re, sys
lock = open(sys.argv[1], "rb").read()
norm = lambda name: re.sub(r"[-_.]+", "-", name).lower()
pins = dict(re.findall(r"(?m)^([A-Za-z0-9_.\-]+)==([^\s\\;]+)", lock.decode()))
have = {norm(item.metadata["Name"]): item.version for item in metadata.distributions()}
print(json.dumps({"python": platform.python_version(), "pins": len(pins),
                  "lock_sha256": hashlib.sha256(lock).hexdigest(),
                  "mismatches": sorted(f"{name}: locked {version}, installed {have.get(norm(name))}"
                                       for name, version in pins.items()
                                       if have.get(norm(name)) != version)}))
'''


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def manifest(root: Path) -> dict:
    """File count and one digest over every file's path and content."""

    digest, count = hashlib.sha256(), 0
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode() + b"\0"
                      + sha256_file(path).encode() + b"\n")
        count += 1
    return {"files": count, "digest": digest.hexdigest()}


def git(*arguments: str, data: bytes | None = None) -> bytes:
    result = subprocess.run(["git", "-C", str(ROOT), *arguments], input=data,
                            capture_output=True, check=False)
    if result.returncode:
        raise LaneError(f"git {' '.join(arguments[:2])} failed: "
                        f"{result.stderr.decode(errors='replace').strip()[:300]}")
    return result.stdout


def prerequisites(now: datetime | None = None) -> list[str]:
    """What this checkout lacks to run the lane. Empty when it can run."""

    missing = []
    expires = datetime.fromisoformat(PUBLISHED_EXPIRES_AT.replace("Z", "+00:00"))
    if (now or datetime.now(timezone.utc)) >= expires:
        missing.append(f"the pinned published journal expired at {PUBLISHED_EXPIRES_AT}; the "
                       "candidate tool extends only a current journal, so the lane needs the "
                       "journal now published pinned in its place")
    for name, expected in ((PREVIOUS_TAG, PREVIOUS_TAG_OBJECT),
                           (f"{PREVIOUS_TAG}^{{commit}}", PREVIOUS_COMMIT),
                           (f"{PUBLISHED_JOURNAL_BLOB}^{{blob}}", PUBLISHED_JOURNAL_BLOB)):
        try:
            found = git("rev-parse", "--verify", "--quiet", name).decode().strip()
        except (LaneError, OSError) as error:
            missing.append(f"{name}: {error}")
            continue
        if found != expected:
            missing.append(f"{name} is {found}, expected {expected}")
    return missing


def extract(commit: str, destination: Path) -> str:
    """``git archive`` one commit into a new directory; return the archive's commit id."""

    archive = git("archive", "--format=tar", commit)
    recorded = git("get-tar-commit-id", data=archive).decode().strip()
    destination.mkdir(parents=True, exist_ok=False)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(destination, filter="data")
    return recorded


def declared_version(tree: Path) -> str | None:
    for line in (tree / "hass_mcp_engineering_beta/config.yaml").read_text(
            encoding="utf-8").splitlines():
        if line.startswith("version:"):
            return line.split(":", 1)[1].strip().strip("\"'")
    return None


class Lane:
    """One run: two source trees, two interpreters, one work directory."""

    def __init__(self, work: Path, reader_python: str, writer_python: str,
                 writer_commit: str | None, real_candidate: Path | None = None) -> None:
        if work.exists() and any(work.iterdir()):
            raise LaneError(f"work directory {work} is not empty")
        missing = prerequisites()
        if missing:
            raise LaneError("checkout cannot run the lane: " + "; ".join(missing))
        self.work = work
        self.real_candidate = real_candidate
        self.python = {"reader": reader_python, "writer": writer_python}
        for name in ("data", "read", "raw", "tmp"):
            (work / name).mkdir(parents=True, exist_ok=True)
        self.tree = {"reader": work / "reader-source"}
        archive_commit = extract(PREVIOUS_COMMIT, self.tree["reader"])
        self.reader = {"version": None, "tag": PREVIOUS_TAG, "tag_object": PREVIOUS_TAG_OBJECT,
                       "commit": PREVIOUS_COMMIT, "archive_commit": archive_commit,
                       "source": "git-archive", "isolated": True,
                       "declared_version": declared_version(self.tree["reader"]),
                       "flags": ["-B", "-I"]}
        if writer_commit:
            commit = git("rev-parse", "--verify", f"{writer_commit}^{{commit}}").decode().strip()
            self.tree["writer"] = work / "writer-source"
            self.writer = {"commit": commit, "source": "git-archive",
                           "archive_commit": extract(commit, self.tree["writer"])}
        else:
            self.tree["writer"] = ROOT
            self.writer = {"commit": git("rev-parse", "HEAD").decode().strip(),
                           "source": "worktree", "archive_commit": None,
                           "worktree_clean": not git("status", "--porcelain").strip()}
        self.writer.update(version=None, flags=["-B", "-I"],
                           declared_version=declared_version(self.tree["writer"]))
        for side, record in (("reader", self.reader), ("writer", self.writer)):
            record["dependencies"] = self.dependencies(side)
        (work / "published-journal.json").write_bytes(
            git("cat-file", "blob", PUBLISHED_JOURNAL_BLOB))
        if sha256_file(work / "published-journal.json") != PUBLISHED_JOURNAL_SHA256:
            raise LaneError("the published journal blob does not have the pinned sha256")
        self.runs: list[dict] = []
        self.commands: list[list[str]] = []

    def environment(self) -> dict:
        return {"PATH": os.environ.get("PATH", ""), "TMPDIR": str(self.work / "tmp"),
                "HOME": str(self.work / "tmp"), "LANG": "C.UTF-8",
                "PYTHONDONTWRITEBYTECODE": "1"}

    def dependencies(self, side: str) -> dict:
        result = subprocess.run(
            [self.python[side], "-B", "-I", "-c", DEPENDENCY_PROBE, str(self.tree[side] / LOCK)],
            capture_output=True, text=True, check=False, env=self.environment(), timeout=120)
        if result.returncode:
            raise LaneError(f"{side} interpreter probe failed: {result.stderr.strip()[-300:]}")
        return json.loads(result.stdout)

    def symbolic(self, value: str) -> str:
        for path, name in ((self.work, "<work>"), (ROOT, "<checkout>")):
            if value == str(path) or value.startswith(str(path) + os.sep):
                return name + value[len(str(path)):]
        return value

    def drive(self, side: str, step: str, *arguments: Any) -> dict:
        driver = READER_DRIVER if side == "reader" else WRITER_DRIVER
        argv = [self.python[side], "-B", "-I", str(driver), str(self.tree[side]),
                *map(str, arguments)]
        self.commands.append([f"<{side}-python>", *map(self.symbolic, argv[1:])])
        result = subprocess.run(argv, capture_output=True, text=True, check=False,
                                cwd=self.work / "tmp", env=self.environment(),
                                timeout=DRIVER_TIMEOUT_SECONDS)
        (self.work / "raw" / f"{step}.stdout").write_text(result.stdout, encoding="utf-8")
        (self.work / "raw" / f"{step}.stderr").write_text(result.stderr, encoding="utf-8")
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        if result.returncode or not lines:
            raise LaneError(f"{side} step {step} exited {result.returncode}: "
                            f"{result.stderr.strip()[-600:]}")
        try:
            output = json.loads(lines[-1])
        except json.JSONDecodeError as error:
            raise LaneError(f"{side} step {step} printed no JSON result") from error
        version, package = output[f"{side}_version"], Path(output[f"{side}_package"])
        record = self.reader if side == "reader" else self.writer
        record["flags"] = argv[1:argv.index(str(driver))]
        record["version"] = record["version"] or version
        self.runs.append({"side": side, "step": step, "version": version,
                          "package_in_own_tree": package.is_relative_to(
                              self.tree[side].resolve())})
        return output

    def written(self, name: str) -> dict:
        record = manifest(self.work / "data" / name)
        return {"files": record["files"], "after_write": record["digest"]}

    def read(self, record: dict, name: str, step: str, *arguments: Any) -> dict:
        """Run one reader step; hash the data set immediately before and after it."""

        root = self.work / "data" / name
        record["before_read"] = manifest(root)["digest"]
        try:
            return self.drive("reader", step, *arguments)
        finally:
            record["after_read"] = manifest(root)["digest"]

    def created_identity_references(self, side: str) -> int:
        package = self.tree[side] / "hass_mcp_engineering_beta/ha_mcp_engineering"
        return sum(path.read_text(encoding="utf-8").count("created_identity")
                   for path in package.rglob("*.py"))

    def receipt(self, scenario: str, started: str, body: dict, runs_from: int,
                commands_from: int, tiers: tuple[str, ...], require_exact: bool) -> dict:
        receipt = {
            "schema": SCHEMA, "row": ROW, "scenario": scenario, "title": TITLES[scenario],
            "subject": "source", "result": None, "failures": [],
            "started_at": started, "ended_at": utc_now(),
            "writer": dict(self.writer), "reader": dict(self.reader),
            "trees_distinct": self.tree["reader"].resolve() != self.tree["writer"].resolve(),
            "drivers": {path.name: sha256_file(path) for path in (
                WRITER_DRIVER, READER_DRIVER, Path(__file__).resolve())},
            "lane": {"containers": 0, "network": "none",
                     "note": "plain interpreter processes with a scrubbed environment; the "
                             "drivers open no socket and start no container",
                     "checkout_commit": git("rev-parse", "HEAD").decode().strip(),
                     "drivers_modified": git(
                         "status", "--porcelain", "--", *(str(path.relative_to(ROOT)) for path in (
                             WRITER_DRIVER, READER_DRIVER, Path(__file__).resolve()))
                     ).decode().split("\n")[:-1],
                     "commands": self.commands[commands_from:]},
            "runs": self.runs[runs_from:], **body,
            "limitations": [SYNTHETIC_PROVIDER_LIMITATION, *body.get("limitations", [])]}
        receipt["summary"] = summarize(receipt)
        receipt["failures"] = classify(receipt, require_exact=require_exact, recovery_tiers=tiers)
        receipt["result"] = "PASS" if not receipt["failures"] else "FAIL"
        return receipt

    # --- scenarios ---------------------------------------------------------------------------

    def tool(self, path: str, operation: str) -> dict:
        """Which file ran as a registry tool, and that it is the writer commit's file."""

        file = self.tree["writer"] / path
        return {"path": path, "operation": operation, "commit": self.writer["commit"],
                "source": self.writer["source"], "sha256": sha256_file(file),
                "blob_run": git("hash-object", "--no-filters", str(file)).decode().strip(),
                "blob_at_commit": git("rev-parse", "--verify",
                                      f"{self.writer['commit']}:{path}").decode().strip()}

    def registry(self) -> dict:
        root = self.work / "data/registry"
        writer = self.drive("writer", "12a-write", "registry", root,
                            self.work / "published-journal.json")
        data = {"registry": {**self.written("registry"),
                             "candidate_sha256": sha256_file(root / CANDIDATE_FILE),
                             "journal_sha256": sha256_file(root / SIGNED_JOURNAL)}}
        own = {}
        for name, file in (("candidate", CANDIDATE_FILE), ("journal", SIGNED_JOURNAL)):
            found = candidate_reference_objects((root / file).read_bytes())
            own[name] = {"file": file, "sha256": found["sha256"], "shape": found["shape"],
                         "sequence": found["sequence"],
                         "reference_objects_sha256": reference_digest(found["objects"])}
        reader = self.read(data["registry"], "registry", "12a-read", "registry", root,
                           self.work / "read/registry", self.tree["reader"] / REGISTRY_RECORDS)
        case = {"data_set": "registry", "observed": observe_registry(writer, reader, own)}
        case["derived"] = registry_words(case)
        return {
            "data": data, "cases": {"registry": case},
            "candidate_tool": self.tool(CANDIDATE_TOOL, CANDIDATE_OPERATION),
            "test_signing_tool": self.tool(TEST_SIGNING_TOOL, TEST_SIGNING_OPERATION),
            "real_candidate_comparison": compare_real_candidate(
                writer["reference_objects"], self.real_candidate),
            "limitations": list(REGISTRY_LIMITATIONS)}

    def device_case(self, record: dict, name: str, case: str, now: datetime, task: str) -> dict:
        reader = self.read(record, name, f"12b-read-{case}", "device", self.work / "data" / name,
                           self.work / "read" / case, now.isoformat(), task)
        observed = observe_device(reader)
        return {"data_set": name, "observed": observed, "derived": device_words(observed)}

    def device(self, tiers: tuple[str, ...]) -> dict:
        data: dict = {}
        hold = self.drive("writer", "12b-write-hold", "device_hold", self.work / "data/device_hold")
        data["device_hold"] = self.written("device_hold")
        crash = self.drive("writer", "12b-write-crash", "device_crash",
                           self.work / "data/device_crash")
        data["device_crash"] = self.written("device_crash")
        started = datetime.fromisoformat(crash["started_at"])
        lease = min(datetime.fromisoformat(item["lease_expires_at"]) for item in crash["locks"])
        day = timedelta(days=1)
        cases = {
            "hold": self.device_case(data["device_hold"], "device_hold", "hold",
                                     datetime.fromisoformat(hold["clock"]) + day, hold["task_id"]),
            "in_flight_active": self.device_case(
                data["device_crash"], "device_crash", "in_flight_active",
                started + (lease - started) / 3, crash["task_id"]),
            "in_flight_expired": self.device_case(
                data["device_crash"], "device_crash", "in_flight_expired", lease + day,
                crash["task_id"])}
        for name, summary in (("hold", hold), ("in_flight_active", crash),
                              ("in_flight_expired", crash)):
            cases[name]["writer"] = {key: summary.get(key) for key in (
                "scenario", "entity_id", "action", "task_id", "started_at", "clock", "died_in",
                "result", "provider_writes", "provider")}
            cases[name]["writer"]["locks"] = [
                [item["key"], item["mode"], item["task_id"], item["generation"],
                 item["conflict_hold"], item["lease_expires_at"]] for item in summary["locks"]]
        recovery = {tier: getattr(self, f"tier_{tier}")(data) for tier in tiers}
        return {"data": data, "cases": cases, "recovery": recovery}

    def tier_27a(self, data: dict) -> dict:
        """Roll forward: the current release settles the crashed task and releases the hold."""

        name = "device_recovery_27a"
        root = self.work / "data" / name
        crash = self.drive("writer", "12b-27a-write-crash", "device_crash", root)
        earlier = self.written(name)
        lease = max(datetime.fromisoformat(item["lease_expires_at"]) for item in crash["locks"])
        length = int((lease - datetime.fromisoformat(crash["started_at"])).total_seconds())
        blocked = self.device_case(earlier, name, "27a-before",
                                   lease + timedelta(seconds=length), crash["task_id"])
        settle = self.drive("writer", "12b-27a-settle-release", "device_settle_release", root,
                            crash["task_id"], length * 10)
        data[name] = {**self.written(name), "earlier": [earlier]}
        read = self.device_case(data[name], name, "27a", datetime.fromisoformat(settle["clock"])
                                + timedelta(seconds=120), crash["task_id"])
        return {
            "exercised": True,
            "title": "roll forward to the current release, let it settle the lock, release the "
                     "hold there, then roll back",
            "limitation": "The hold was released by calling the current release's service "
                          "methods with a synthetic Ingress principal. The Ingress HTTP layer "
                          "and its administrator check were not exercised.",
            "steps": {
                "crash": {"task_id": crash["task_id"], "died_in": crash["died_in"],
                          "provider_writes": crash["provider_writes"],
                          "locks": [[item["key"], item["mode"], item["lease_expires_at"]]
                                    for item in crash["locks"]]},
                "blocked_before_recovery": blocked,
                "settle_release": {
                    "task_id": settle["task_id"], "advance_seconds": length * 10,
                    "recovery_passes": settle["recovery_passes"],
                    "settled": {"terminal": settle["settled"]["terminal"],
                                "normalized_outcome": settle["settled"]["normalized_outcome"],
                                "provider_writes": settle["settled"]["provider_writes"],
                                "locks": [[item["key"], item["mode"], item["conflict_hold"]]
                                          for item in settle["settled"]["locks"]]},
                    "review": {key: settle["review"].get(key) for key in (
                        "record_generation", "manual_review_reason", "comparison",
                        "hold_binding")},
                    "release": {
                        "path": settle["release"]["path"],
                        "principal": settle["release"]["principal"],
                        **{key: settle["release"]["result"].get(key) for key in (
                            "status", "dispatch", "terminal_outcome", "audit_recorded",
                            "audit_unrecorded_reason", "readback_matches_requested_state")},
                        "provider_writes": settle["release"]["provider_writes"],
                        "locks_after": settle["release"]["locks"]}},
                "previous_release_read": read}}

    def plans(self) -> dict:
        root = self.work / "data/plans"
        writer = self.drive("writer", "12c-write", "plans", root)
        if not writer["harness_ok"]:
            raise LaneError(f"the writer harness failed: {writer['harness_errors']}")
        summary = self.work / "raw/12c-writer-summary.json"
        summary.write_text(json.dumps(writer, sort_keys=True), encoding="utf-8")
        data = {"plans": self.written("plans")}
        reader = self.read(data["plans"], "plans", "12c-read", "plans", root,
                           self.work / "read/plans", summary)
        cases = {}
        for mode, seen in sorted(reader["modes"].items()):
            observed = observe_plans(seen)
            cases[mode] = {"data_set": "plans", "writer": writer["modes"][mode],
                           "observed": observed, "derived": {"outcome": plan_outcome(observed)}}
        return {"data": data, "cases": cases,
                "limitations": list(PLAN_LIMITATIONS),
                "source_facts": {
                    "reader_created_identity_references":
                        self.created_identity_references("reader"),
                    "writer_created_identity_references":
                        self.created_identity_references("writer")},
                "reader_harness": {"ok": reader["harness_ok"], "errors": reader["harness_errors"]}}

    def run(self, scenarios: tuple[str, ...], tiers: tuple[str, ...],
            require_exact: bool) -> list[dict]:
        receipts = []
        for scenario in scenarios:
            started, runs_from, commands_from = utc_now(), len(self.runs), len(self.commands)
            body = {"12a": self.registry, "12b": lambda: self.device(tiers),
                    "12c": self.plans}[scenario]()
            receipts.append(self.receipt(scenario, started, body, runs_from, commands_from, tiers,
                                         require_exact))
        return receipts


def write_receipts(receipts: list[dict], out: Path) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for receipt in receipts:
        path = out / f"{receipt['scenario']}-{receipt['subject']}.json"
        path.write_text(json.dumps(receipt, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        paths.append(path)
    return paths


def read_receipts(directory: Path) -> tuple[list[dict], list[str]]:
    """Every JSON file in a receipt directory, and each reason a file is not one receipt.

    A receipt's file name is fixed by its content (`<scenario>-<subject>.json`, as
    `write_receipts` names it), so a second file cannot stand in for a receipt under
    another name.
    """

    receipts, failures = [], []
    for path in sorted(directory.glob("*.json")):
        try:
            receipt = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            failures.append(f"G13: {path.name} cannot be read as a receipt "
                            f"({type(error).__name__})")
            continue
        if not isinstance(receipt, dict):
            failures.append(f"G13: {path.name} is not a receipt")
            continue
        if path.name != f"{receipt.get('scenario')}-{receipt.get('subject')}.json":
            failures.append(f"G13: {path.name} is not the file name of the receipt it holds")
        receipts.append(receipt)
    return receipts, failures


def load_receipts(directory: Path) -> list[dict]:
    return read_receipts(directory)[0]


def record_comparison(directory: Path, candidate: Path, record: bool,
                      tiers: tuple[str, ...]) -> tuple[dict, list[str]]:
    """Compare a recorded 12a receipt's reference objects with a supplied candidate file."""

    path = directory / "12a-source.json"
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
        objects = receipt["cases"]["registry"]["observed"]["writer"]["reference_objects"]
    except (OSError, KeyError, TypeError, ValueError) as error:
        raise LaneError(f"no usable 12a receipt in {directory}: {type(error).__name__}") from None
    receipt["real_candidate_comparison"] = compare_real_candidate(objects, candidate)
    receipt["summary"] = summarize(receipt)
    receipt["failures"] = classify(receipt, recovery_tiers=tiers)
    receipt["result"] = "PASS" if not receipt["failures"] else "FAIL"
    if record:
        write_receipts([receipt], directory)
    return receipt["real_candidate_comparison"], receipt["failures"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="write, read and classify; one receipt per scenario")
    run.add_argument("--out", type=Path, required=True)
    run.add_argument("--work", type=Path, required=True, help="new or empty scratch directory")
    run.add_argument("--reader-python", required=True,
                     help="interpreter holding the previous release's locked dependencies")
    run.add_argument("--writer-python", default=sys.executable)
    run.add_argument("--writer-commit",
                     help="write from a git archive of this commit instead of the checkout")
    run.add_argument("--scenario", action="append", choices=sorted(CLASSIFIERS))
    run.add_argument("--real-candidate", type=Path,
                     help="a real registry candidate to compare 12a's reference objects with")
    check = commands.add_parser("check", help="re-classify recorded receipts")
    check.add_argument("directory", type=Path)
    compare = commands.add_parser(
        "compare-candidate",
        help="compare a recorded 12a receipt's reference objects with a real candidate file")
    compare.add_argument("directory", type=Path)
    compare.add_argument("candidate", type=Path)
    compare.add_argument("--record", action="store_true",
                         help="write the comparison into the 12a receipt")
    for command in (run, check, compare):
        command.add_argument("--recovery-tier", action="append", choices=sorted(RECOVERY_TIERS),
                             help="recovery tiers required of 12b (default: every tier)")
    options = parser.parse_args(argv)
    tiers = tuple(options.recovery_tier or DEFAULT_RECOVERY_TIERS)
    if options.command == "check":
        failures = gate(load_receipts(options.directory), recovery_tiers=tiers)
        failures = read_receipts(options.directory)[1] + failures
    elif options.command == "compare-candidate":
        try:
            comparison, failures = record_comparison(
                options.directory, options.candidate, options.record, tiers)
        except LaneError as error:
            print(f"rollback proof lane: no comparison made: {error}", file=sys.stderr)
            return 2
        print(json.dumps(comparison, indent=1, sort_keys=True))
        failures = [f"12a: {item}" for item in failures]
    else:
        exact = bool(options.writer_commit)
        try:
            lane = Lane(options.work.resolve(), options.reader_python, options.writer_python,
                        options.writer_commit, options.real_candidate)
            receipts = lane.run(tuple(options.scenario or sorted(CLASSIFIERS)), tiers, exact)
        except (LaneError, OSError, subprocess.TimeoutExpired) as error:
            print(f"rollback proof lane: no evidence produced: {error}", file=sys.stderr)
            return 2
        for path in write_receipts(receipts, options.out.resolve()):
            print(path)
        failures = [f"{receipt['scenario']}: {item}" for receipt in receipts
                    for item in receipt["failures"]]
    for failure in failures:
        print(failure, file=sys.stderr)
    print("rollback proof lane: " + ("FAIL" if failures else "PASS"))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
