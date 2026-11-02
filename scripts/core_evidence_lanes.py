"""Release-parameterized, independently withheld disposable lane groups."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys

from core_release_evidence import ROOT, canonical, context, digest, groups, slot


def output_dir():
    path = Path(os.environ["CORE_EVIDENCE_RESULTS"])
    path.mkdir(parents=True, exist_ok=True)
    return path


def record(name, passed, receipt=None):
    value = context()
    result = {"model": "core-evidence-group-v1", "group": name, "passed": passed,
              "run": value["run"], "release": value["provenance"]["version"],
              "context_sha256": os.environ["CORE_EVIDENCE_CONTEXT_SHA256"],
              "profiles": groups()[name], "receipt": receipt}
    (output_dir() / (name + ".json")).write_bytes(canonical(result))


async def run_specialized(configured, core_runtime):
    """All groups run; a failed group cannot silently lend evidence to another."""
    value = context(); binding = slot(value)
    from alarmo_inspection_contract_acceptance import run_disposable as integration
    from automation_baseline_contract_acceptance import run_disposable as baseline
    from entity_metadata_contract_acceptance import run_disposable as metadata
    from configuration_delete_contract_acceptance import run_disposable as delete
    import seq13_contract_acceptance as seq13
    import seq15_contract_acceptance as seq15
    args = {"expected_image": binding["image"], "core_version": binding["version"]}
    work = {
        "integration_inspection": lambda: integration(configured, core_runtime,
            fixture_receipt=Path(os.environ["REAL_HA_ALARMO_FIXTURE_RECEIPT"]), **args),
        "automation_baseline": lambda: baseline(configured, **args),
        "entity_metadata": lambda: metadata(configured, **args),
        "configuration_delete": lambda: delete(configured, **args),
    }
    for name in seq13.LANES:
        work[name] = lambda name=name: seq13.run_disposable(name, configured, **args)
    for name in seq15.LANES:
        work[name] = lambda name=name: seq15.run_disposable(name, configured, **args)
    for name, run in work.items():
        try:
            result = await run()
            if result.get("result") != "PASS":
                raise ValueError("lane result refused")
            record(name, True, result)
        except Exception as exc:
            # No exception text, endpoint, token, household or raw config in summary.
            record(name, False, {"failure_type": type(exc).__name__})
            print("WITHHELD: " + name, flush=True)


def main():
    value = context()
    if value is None:
        raise ValueError("evidence lanes require an admitted run binding")
    if sys.argv[1:] == ["run"]:
        # Existing harness enforces loopback, exact container, fixture ownership,
        # exact image, per-lane cleanup and independent absence readbacks.
        try:
            result = subprocess.run([sys.executable, str(ROOT / "scripts/real_ha_contract_tests.py")],
                                    timeout=2100)
            record("general", result.returncode == 0, {"exit_code": result.returncode})
        except subprocess.TimeoutExpired:
            record("general", False, {"failure_type": "TimeoutExpired"})
    elif sys.argv[1:] == ["cleanup"]:
        b = slot(value); names = ["beta25-real-ha-" + b["lane"], "beta25-real-ha-writer-" + b["lane"],
                                 "beta25-real-ha-upstream-" + b["lane"]]
        containers = subprocess.run(["docker", "ps", "-a", "--format", "{{.Names}}"],
                                    capture_output=True, check=True, timeout=30).stdout.decode().splitlines()
        networks = subprocess.run(["docker", "network", "ls", "--format", "{{.Name}}"],
                                  capture_output=True, check=True, timeout=30).stdout.decode().splitlines()
        if set(names) & set(containers) or "beta-rc2-contract-" + b["lane"] in networks:
            raise ValueError("owned disposable residue remains")
        (output_dir() / "cleanup.json").write_bytes(canonical({"clean": True, "run": value["run"],
            "context_sha256": os.environ["CORE_EVIDENCE_CONTEXT_SHA256"]}))
    else:
        raise ValueError("unknown evidence lane command")


if __name__ == "__main__":
    main()
