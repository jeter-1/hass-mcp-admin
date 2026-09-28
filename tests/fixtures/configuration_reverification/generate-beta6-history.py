"""Generate synthetic historical records through the exact beta.6 writer."""
import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import sys

WRITER=Path("/home/josh/repos/hass-mcp-admin/.artifacts/f028-historical-writer")
OUT=Path(__file__).resolve().parent / "beta6-synthetic-history"
assert not OUT.exists()
sys.path.insert(0,str(WRITER))
sys.path.insert(0,str(WRITER/"hass_mcp_engineering_beta"))
import ha_mcp_engineering.governance.normalize as normalizer
assert Path(normalizer.__file__).resolve().is_relative_to(WRITER)
from tests.test_f3_runtime_integration import F3ConfigurationActivationTests

async def main():
    case=F3ConfigurationActivationTests()
    await case.asyncSetUp()
    try:
        target="f028_synthetic_guard"
        current={"id":target,"alias":"Synthetic guard fixture","triggers":[{"trigger":"homeassistant","event":"start","id":"synthetic_start"}],"actions":[{"stop":"synthetic fixture"}],"mode":"single"}
        proposed=deepcopy(current)
        proposed["actions"]=[{"choose":[],"default":[{"condition":"trigger","id":["synthetic_start","synthetic_reconcile"]},{"stop":"synthetic fixture"}]}]
        case.gateway.configs.clear()
        case.gateway.configs[("automation",target)]=deepcopy(current)
        operations=[
            {"operation_id":"create_synthetic_helper","resource_type":"helper","helper_type":"input_boolean","action":"create","target_id":"input_boolean.f028_synthetic_marker","depends_on":[],"proposed_config":{"name":"F028 Synthetic Marker"}},
            {"operation_id":"update_synthetic_automation","resource_type":"automation","action":"update","target_id":target,"depends_on":["create_synthetic_helper"],"proposed_config":proposed},
        ]
        created=await case.service.create_configuration_plan(title="Synthetic F028 historical writer",description="Offline retained-format fixture; no household objects",operations=operations)
        await case.approve(created)
        applied=await case.service.apply(created["plan_id"],created["plan_hash"])
        task=case.service.task_repository.get(applied["task_id"])
        children=[case.runtime.children.get(d["child_id"]) for d in case.runtime.children.declarations_for_task(task.task_id)]
        assert task.state.value=="manual_review_required", task.state
        assert [r.normalized_outcome for r in children]==["succeeded_verified","verification_mismatch"]
        assert [r.dispatch_count for r in children]==[1,1]
        assert case.gateway.configs[("automation",target)]==proposed
        shutil.copytree(case.root,OUT)
        hashes={str(p.relative_to(OUT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in OUT.rglob("*") if p.is_file()}
        source_files=["hass_mcp_engineering_beta/ha_mcp_engineering/governance/normalize.py","hass_mcp_engineering_beta/ha_mcp_engineering/governance/service.py","hass_mcp_engineering_beta/ha_mcp_engineering/governance/task_models.py","hass_mcp_engineering_beta/ha_mcp_engineering/governance/task_storage.py","hass_mcp_engineering_beta/ha_mcp_engineering/f3_configuration/adapter.py","hass_mcp_engineering_beta/ha_mcp_engineering/f3_runtime/runtime.py","tests/test_f3_runtime_integration.py","tests/test_dev14_configuration_plans.py"]
        report={"source":"6e1c9f1412f97b1171bc38aafe6815ca8abe54eb","synthetic_only":True,"no_record_postediting":True,"plan_id":created["plan_id"],"plan_hash":created["plan_hash"],"task_id":task.task_id,"task_state":task.state.value,"child_outcomes":[r.normalized_outcome for r in children],"child_dispatch_counts":[r.dispatch_count for r in children],"record_files_sha256":hashes,"writer_source_sha256":{name:hashlib.sha256((WRITER/name).read_bytes()).hexdigest() for name in source_files},"limitations":"Exact writer with repository synthetic gateway and approval fixtures; not production or real human approval evidence"}
        Path(__file__).with_name("beta6-history-provenance.json").write_text(json.dumps(report,indent=2)+"\n")
        print(json.dumps({k:v for k,v in report.items() if k not in ["record_files_sha256","writer_source_sha256"]},indent=2))
    finally:
        await case.asyncTearDown()
asyncio.run(main())
