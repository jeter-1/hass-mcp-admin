"""Offline probe of the exact dashboard call/verify_case bodies; no HA or Docker.

Synthetic run_case supplies two observations. On the second invocation it
corrupts or removes a retained first-window file. The production call returns
success without consuming those files. This tests receipt replay, not live
write reachability.
"""
import ast
import asyncio
import copy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "scripts"))
import dashboard_analysis_disposable_acceptance as dashboard
import alarmo_inspection_contract_acceptance as alarmo

tree = ast.parse((ROOT / "scripts/dashboard_analysis_disposable_acceptance.py").read_text())
nodes = {n.name: n for n in ast.walk(tree)
         if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


async def probe(mode):
    with tempfile.TemporaryDirectory(prefix="dashboard-receipt-") as temporary:
        out = Path(temporary)
        fixture = out / "fixture"
        fixture.mkdir()
        (fixture / "__init__.py").write_text("# synthetic observer\n")
        lane = SimpleNamespace(out=out, fixture=fixture)
        observed = {"overflow": False, "expired": False, "core_version": "2026.9.4",
                    "coverage": {"synthetic": True},
                    "observer_sha256": dashboard.digest((fixture / "__init__.py").read_bytes()),
                    "baseline_store_hashes": {}, "final_store_hashes": {}, "events": []}
        wire = {"mcp": [], "native_http": [], "native_ws": []}
        calls = 0

        async def run_case(name, arguments, expected, native_reads, upstream_read):
            nonlocal calls
            calls += 1
            current = copy.deepcopy(observed)
            if calls == 1:
                current["events"] = [{"kind": "storage_delay_save", "origin": "background",
                                      "store": "repairs.issue_registry"}]
            else:
                discarded = out / (name + "-discarded-observation.json")
                discarded_wire = out / (name + "-discarded-wire.json")
                assert discarded.exists() and discarded_wire.exists()
                if mode == "missing_observation":
                    discarded.unlink()
                elif mode == "illegal_event":
                    value = json.loads(discarded.read_text())
                    value["events"].append({"kind": "storage_write", "origin": "command",
                                            "store": "core.entity_registry"})
                    dashboard.save(discarded, value)
                elif mode == "illegal_wire":
                    dashboard.save(discarded_wire, {**wire, "native_http": ["POST /api/services/x/y"]})
            dashboard.save(out / (name + "-observation.json"), current)
            dashboard.save(out / (name + "-wire.json"), wire)
            value = {"success": True, "timing": {}}
            return (value, json.dumps(value), wire), current

        namespace = {**vars(dashboard), "lane": lane, "run_case": run_case,
                     "receipts": [], "discarded": [],
                     "RetryableObservation": alarmo.RetryableObservation,
                     "observe_retry_once_clean": alarmo.observe_retry_once_clean}
        module = ast.Module(body=[nodes["verify_case"], nodes["call"]], type_ignores=[])
        exec(compile(module, str(ROOT / "scripts/dashboard_analysis_disposable_acceptance.py"), "exec"), namespace)
        result = await namespace["call"]("synthetic", {}, True)
        assert result["success"] is True and calls == 2
        assert namespace["receipts"][0]["discarded_window"]["condition"] == "retryable_delayed_save"
        rejected_by_replay = False
        try:
            old = json.loads((out / "synthetic-discarded-observation.json").read_text())
            old_wire = json.loads((out / "synthetic-discarded-wire.json").read_text())
            namespace["verify_case"](({}, "", old_wire), old, True, 0, 0)
        except alarmo.RetryableObservation:
            pass  # valid discarded receipt, as in the control
        except (FileNotFoundError, RuntimeError):
            rejected_by_replay = True
        assert rejected_by_replay == (mode != "control")
        print(json.dumps({"variant": mode, "production_call_accepted": True,
                          "runs": calls, "explicit_offline_replay_rejects": rejected_by_replay}))


async def main():
    for mode in ("control", "missing_observation", "illegal_event", "illegal_wire"):
        await probe(mode)


asyncio.run(main())
