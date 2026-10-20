"""Offline probe: shipped classifier on the anonymized PR001 fixtures (no live HA).

Usage: python -I probe_pr001.py <repo>/hass_mcp_engineering_beta <fixture dir>
Environment: CPython 3.12, hass_mcp_engineering_beta/requirements.lock (--require-hashes).
Base: origin/main f69bf8c (2.4.1-beta.24). The fixtures are anonymized structural
transforms of the owner's PR001 example (see README.md); this probe shows what the
shipped policy does with that structure today.
"""
import pathlib, sys
import yaml
sys.path.insert(0, sys.argv[1])
from ha_mcp_engineering.governance.service import ChangeGovernanceService
from ha_mcp_engineering.governance.models import ConfigurationOperation
from ha_mcp_engineering.governance.normalize import stable_hash
from ha_mcp_engineering.governance.resources import structured_resource_diff, normalize_resource_config
from ha_mcp_engineering.governance.policy import configuration_operation_policy, aggregate_policy_classifications
from ha_mcp_engineering.governance.configuration_eligibility import derive_proof

FIXTURES = pathlib.Path(sys.argv[2])
PLAN = [("op1", "automation", "pr001_op1_open_warning"),
        ("op2", "automation", "pr001_op2_departure_close"),
        ("op3", "script", "garage_close_attempt"),
        ("op4", "automation", "pr001_op4_evening_alert")]

def load(name):
    return yaml.safe_load((FIXTURES / name).read_text())

built = []
for i, (op_id, rtype, target) in enumerate(PLAN):
    cur, prop = load(f"{op_id}_before.yaml"), load(f"{op_id}_after.yaml")
    diff = structured_resource_diff(rtype, cur, prop)
    risk = ChangeGovernanceService._configuration_risk(op_id, rtype, "update", diff, prop)
    built.append(ConfigurationOperation(
        operation_id=op_id, order=i, depends_on=[] if i == 0 else [PLAN[i - 1][0]],
        resource_type=rtype, action="update", target_id=target, helper_type=None,
        proposed_config=prop, current_config=cur,
        normalized_proposed_config=normalize_resource_config(rtype, prop),
        normalized_current_config=normalize_resource_config(rtype, cur),
        current_state_fingerprint=stable_hash(cur), proposed_config_hash=stable_hash(prop),
        normalization_version=1, risk=risk, validation_results={"valid": True}))
    print(f"== {op_id} {rtype} changed_fields:", [f["field"] for f in diff["changed_fields"]])

classes = [configuration_operation_policy(o) for o in built]
for o, c in zip(built, classes):
    triggers = sorted({e["trigger"] for e in o.risk.evidence})
    garage = sorted(e["field"] for e in o.risk.evidence if e["trigger"] == "garage_cover_target")
    print(f"-- {o.operation_id}: risk={o.risk.level.value} apply_allowed={o.risk.apply_allowed}")
    print("   reasons:", o.risk.reasons)
    print("   triggers:", triggers)
    print(f"   garage_cover_target fields ({len(garage)}):", garage)
    print("   policy:", c.policy_class.value, c.risk_delta.value, c.physical_consequence.value, c.reason_codes)
agg = aggregate_policy_classifications(classes)
print("== plan aggregate:", agg.policy_class.value, agg.risk_delta.value, agg.physical_consequence.value, agg.reason_codes)
print("== derive_proof(4 operations):", derive_proof(built))
