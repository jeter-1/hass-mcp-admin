"""Offline probe: shipped classifier on synthetic garage-style updates (no live HA)."""
# Usage: python -I probe_e012.py <repo>/hass_mcp_engineering_beta  (env: requirements.lock, CPython 3.12)
# Base: origin/main f69bf8c (2.4.1-beta.24). Synthetic entity ids only; no live access.
import copy, json, sys
sys.path.insert(0, sys.argv[1])
from ha_mcp_engineering.governance.service import ChangeGovernanceService as GovernanceService
from ha_mcp_engineering.governance.models import ConfigurationOperation
from ha_mcp_engineering.governance.normalize import stable_hash
from ha_mcp_engineering.governance.resources import structured_resource_diff, normalize_resource_config
from ha_mcp_engineering.governance.policy import configuration_operation_policy, aggregate_policy_classifications

COVER = "cover.synthetic_garage_door"
warn_current = {
    "alias": "Garage open warning", "mode": "single",
    "triggers": [{"trigger": "state", "entity_id": COVER, "to": "open", "for": "00:30:00"}],
    "conditions": [{"condition": "state", "entity_id": "input_boolean.synthetic_away", "state": "on"}],
    "actions": [
        {"action": "tts.speak", "continue_on_error": True, "target": {"entity_id": "tts.synthetic"},
         "data": {"media_player_entity_id": "media_player.synthetic", "message": "Garage open"}},
        {"action": "notify.mobile_app_synthetic_phone", "data": {"message": "Garage open"}},
        {"action": "logbook.log", "data": {"name": "Garage", "message": "warned", "entity_id": COVER}},
        {"wait_template": "{{ is_state('%s','closed') }}" % COVER, "timeout": "00:10:00"},
        {"action": "cover.close_cover", "target": {"entity_id": COVER}},
    ],
}
warn_proposed = copy.deepcopy(warn_current)
warn_proposed["mode"] = "restart"
a = warn_proposed["actions"]
a[0], a[1] = a[1], a[0]  # push before TTS
a[0]["data"]["message"] = "Garage has been open 30 minutes"

def op(i, rtype, cur, prop):
    diff = structured_resource_diff(rtype, cur, prop)
    risk = GovernanceService._configuration_risk(f"op{i}", rtype, "update", diff, prop)
    o = ConfigurationOperation(
        operation_id=f"op{i}", order=i, depends_on=[], resource_type=rtype, action="update",
        target_id=f"synthetic_{rtype}_{i}", helper_type=None,
        proposed_config=prop, current_config=cur,
        normalized_proposed_config=normalize_resource_config(rtype, prop), normalized_current_config=normalize_resource_config(rtype, cur),
        current_state_fingerprint=stable_hash(cur), proposed_config_hash=stable_hash(prop),
        normalization_version=1, risk=risk, validation_results={"valid": True})
    return o, diff

results = []
for label, ops in {
    "automation_only": [("automation", warn_current, warn_proposed)],
    "script_only": [("script", {"alias": "s", "sequence": warn_current["actions"]}, {"alias": "s", "sequence": warn_proposed["actions"]})],
    "description_only_automation": [("automation", warn_current, {**copy.deepcopy(warn_current), "description": "x"})],
}.items():
    built = [op(i, *spec) for i, spec in enumerate(ops)]
    classes = [configuration_operation_policy(o) for o, _ in built]
    agg = aggregate_policy_classifications(classes)
    print("==", label)
    for (o, diff), c in zip(built, classes):
        print("  changed_fields:", [f["field"] for f in diff["changed_fields"]])
        print("  risk:", o.risk.level.value, "apply_allowed:", o.risk.apply_allowed, "reasons:", o.risk.reasons)
        print("  triggers:", sorted({(e["trigger"], e["field"]) for e in o.risk.evidence}))
        print("  policy:", c.policy_class.value, c.physical_consequence.value, c.reason_codes)
    print("  AGG:", agg.policy_class.value, agg.reason_codes)
# mixed plan
built = [op(0, "automation", warn_current, warn_proposed), op(1, "script", {"alias": "s", "sequence": warn_current["actions"]}, {"alias": "s", "sequence": warn_proposed["actions"]})]
agg = aggregate_policy_classifications(configuration_operation_policy(o) for o, _ in built)
print("== mixed automation+script AGG:", agg.policy_class.value, agg.physical_consequence.value, agg.reason_codes)
# logbook-only reference: no cover service anywhere, only logbook.log data.entity_id = cover
lb_cur = {"alias": "Garage logbook", "mode": "single",
          "triggers": [{"trigger": "state", "entity_id": COVER, "to": "open"}],
          "actions": [{"action": "notify.mobile_app_synthetic_phone", "data": {"message": "open"}},
                      {"action": "logbook.log", "data": {"name": "Garage", "message": "opened", "entity_id": COVER}}]}
lb_prop = copy.deepcopy(lb_cur); lb_prop["actions"][0]["data"]["message"] = "Garage opened"
for rtype, cur, prop in [("automation", lb_cur, lb_prop),
                         ("script", {"alias": "s", "sequence": lb_cur["actions"]}, {"alias": "s", "sequence": lb_prop["actions"]}),
                         ("script", {"alias": "s", "mode": "single", "sequence": warn_current["actions"]}, {"alias": "s", "mode": "restart", "sequence": warn_current["actions"]})]:
    o, diff = op(9, rtype, cur, prop)
    c = configuration_operation_policy(o)
    print("== extra", rtype, [f["field"] for f in diff["changed_fields"]], o.risk.level.value, o.risk.apply_allowed,
          sorted({e["trigger"] for e in o.risk.evidence}), c.policy_class.value, c.physical_consequence.value, o.risk.reasons)
