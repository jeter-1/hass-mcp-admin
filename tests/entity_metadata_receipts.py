"""Offline synthetic HAMCP-179 entity metadata receipts.

Structurally valid inputs for the offline verifier only; they are never
execution evidence. Bindings come from the committed window and current source.
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "hass_mcp_engineering_beta")]
from alarmo_inspection_contract_acceptance import slot_binding  # noqa: E402
import entity_metadata_contract_acceptance as lane  # noqa: E402

LABELS = ["hamcp179_night", "hamcp179_outdoor"]
PREFLIGHT = {"aliases": [None], "area_id": None, "categories": {}, "hidden_by": None,
             "icon": None, "labels": [], "name": None}
UPDATED = {"aliases": ["Desk lamp", None, "Reading light"], "area_id": "hamcp179_study",
           "categories": {"helpers": "synthetic-category"}, "hidden_by": "user",
           "icon": "mdi:desk-lamp", "labels": LABELS, "name": "HAMCP179 Desk"}


def synthetic_receipt(core_version):
    binding = slot_binding(core_version)
    reference = lane.source_profile()
    governed = {"result": "PASS", "task_state": "succeeded_verified", "dispatch_count": 1, "ha_updates": 1,
                "preflight": dict(PREFLIGHT), "unmanaged_fields_preserved": True}
    scenarios = {
        "closed_field_update": {**governed, "expected": dict(UPDATED), "readback": dict(UPDATED)},
        "restore": {**governed, "expected": dict(PREFLIGHT), "readback": dict(PREFLIGHT)},
        "no_change": {"result": "PASS", "status": "no_change", "plan_created": False,
                      "provider_dispatch_occurred": False, "ha_updates": 0},
        "not_found": {"result": "PASS", "error_code": "entity_not_found", "plan_created": False,
                      "provider_dispatch_occurred": False, "ha_updates": 0},
        "area_requires_own_name": {
            "result": "PASS", "ha_updates": 0, "restored": True,
            "preconditions": {"device_bound": True, "device_has_parent": False, "own_name": False,
                              "original_name": False, "area": False},
            "planner": {"area_without_name": "entity_metadata_area_requires_own_name",
                        "clear_name_with_area": "entity_metadata_area_requires_own_name"},
            "raw": {"area_without_name": "invalid_info", "name_with_area": "accepted",
                    "clear_name_with_area": "invalid_info"}},
        "unknown_label": {"result": "PASS", "planner_reason": "entity_metadata_reference_not_found",
                          "ha_updates": 0, "raw_written": [LABELS[0], lane.UNKNOWN_LABEL],
                          "raw_readback": [LABELS[0]], "restored": True},
        "alias_normalization": {"result": "PASS", "planner_reason": "entity_metadata_invalid_value",
                                "ha_commands": 0, "raw_written": [" Lamp ", "", None],
                                "raw_readback": ["Lamp", None], "restored": True},
        "response_lost_applied": {"result": "PASS", "task_state": "succeeded_verified", "dispatch_count": 1,
                                  "ha_updates": 1, "indeterminate": True, "readback_applied": True,
                                  "replay_reused": True, "replay_updates": 0},
        "response_lost_unapplied": {"result": "PASS", "task_state": "failed_post_dispatch",
                                    "outcome": "verification_mismatch", "dispatch_count": 1, "ha_updates": 0,
                                    "indeterminate": True, "readback_unchanged": True,
                                    "replay": "duplicate_apply_attempt", "replay_updates": 0},
    }
    return {
        "result": "PASS", "core": core_version, "support_slot": binding["slot"], "core_image": binding["image"],
        "image_index_digest": binding["image_index_digest"], "image_receipt_sha256": "1" * 64,
        "engineering_source": lane.engineering_source(), **reference, "added_capability": dict(reference),
        "predecessor_capabilities_sha256": "2" * 64, "authority_transition": [21, 22],
        "authority_instances": "separate_ephemeral_registries", "production_authority": False,
        "provider_identity": "synthetic_disposable_lane",
        "predecessor_admission": {"acquired": False, "ha_requests": 0, "ha_updates": 0,
                                  "reason": "authority_unavailable", "control_capability_acquired": True},
        "engineering_command_types": sorted(lane.REVIEWED_COMMANDS), "synthetic_labels": list(LABELS),
        "scenarios": scenarios, "fixture_cleanup": True,
        "fixture_absence": dict.fromkeys(lane.FIXTURE_ABSENCE, True)}
