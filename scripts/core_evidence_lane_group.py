"""CI entry point of one lane group (HAMCP-443 D1, D2, D4): ``<group> --source-archive PATH``.

Runs the group's harness(es) with ``--mode ci`` against the run's verified context, the same
``context()`` binding the twelve run_specialized groups use, then writes ``<group>.json``
(core-evidence-group-v1) and ``cleanup-<group>.json`` into CORE_EVIDENCE_RESULTS for the
lane-groups artifact, and each process's D6a proof lines into the job summary; a failed proof
exits non-zero. Nothing here reads or keeps a raw or container log.
"""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import time

import core_created_object_local
import core_device_configuration_local
import core_helper_value_local
from core_evidence_lanes import output_dir, record
import core_lane_host
import core_marketplace_read_local
from core_release_evidence import EXTERNAL_DEPENDENCY, LANE_GROUPS, ROOT, canonical, context, read_json, slot
import core_security_metadata_local
import core_thread_matter_topology_a2_local
import core_thread_matter_topology_local
from marketplace_read_contract_acceptance import ExternalDependencyUnavailable

# The fixed group-to-harness map: each group's process(es), with the names each one owns.
HARNESSES = {
    "helper_value": ((core_helper_value_local, ("CORE", "UPSTREAM")),),
    "marketplace_read": ((core_marketplace_read_local, ("CORE",)),),
    "security_metadata": ((core_security_metadata_local, ("CORE",)),),
    "device_configuration_entity": ((core_device_configuration_local, ("CORE", "UPSTREAM", "STUB")),),
    "scene_create_delete": ((core_created_object_local, None),),
    "created_helper_delete": ((core_created_object_local, None),),
    # D1: A1 and A2 run as separate processes on separate networks; the group needs both.
    "thread_matter_topology": ((core_thread_matter_topology_local, ("CORE",)),
                               (core_thread_matter_topology_a2_local, ("CORE", "STUB"))),
}
# One deadline for all of a group's processes, inside the lane-groups job's own timeout.
GROUP_TIMEOUT = 1500
# A timed-out harness gets SIGTERM first: its handler tears down what it owns.
TEARDOWN_TIMEOUT = 120


def lanes(group):
    """(harness, extra argv, receipt name, owned containers, network) per process of the group."""
    result = []
    for module, names in HARNESSES[group]:
        if module is core_created_object_local:
            core, network = module.resource_names(group)
            result.append((module, ("--lane", group), group + ".json", (core,), network))
        else:
            result.append((module, (), module.RECEIPT, tuple(getattr(module, name) for name in names),
                           module.NETWORK))
    return result


def runtime_tree():
    """The Engineering runtime tree of this run's own checkout, never a pinned constant (7e)."""
    return subprocess.run(["git", "rev-parse", core_lane_host.RUNTIME_TREE], cwd=ROOT, check=True,
                          capture_output=True, timeout=30).stdout.decode().strip()


def process_dir(group, index):
    """The run directory of the group's index-th process, directly under the runner's temporary directory."""
    return Path(os.environ["RUNNER_TEMP"]) / ("lane-group-" + group + "-" + str(index))


def run_harness(argv, timeout):
    process = subprocess.Popen(argv, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    try:
        return process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.wait(timeout=TEARDOWN_TIMEOUT)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        raise


def verdict(group, module, receipt, returncode, ctx, tree):
    """D4: a process passes only on its own CI receipt for this run saying PASS. Anything else is
    a ValueError; only marketplace's code-owned store signal, in that receipt, is the retry failure."""
    binding = {"group": group, "mode": "ci", "pull": "digest", "source_commit": ctx["run"]["GITHUB_SHA"],
               "runtime_tree": tree, "core_version": slot(ctx)["version"]}
    if any(receipt.get(key) != value for key, value in binding.items()):
        raise ValueError("lane result refused")
    if (group == "marketplace_read" and receipt.get("result") == "WITHHELD"
            and receipt.get("failure_type") == ExternalDependencyUnavailable.__name__):
        return EXTERNAL_DEPENDENCY
    if (returncode != 0 or receipt.get("result") != "PASS" or receipt.get("cleanup", {}).get("clean") is not True
            or "failure" in receipt or "failure_type" in receipt
            or (module is core_created_object_local and receipt.get("lane") != group)):
        raise ValueError("lane result refused")
    return None


def run_group(group, archive, ctx, tree, *, run=None, clock=time.monotonic):
    """Every process runs; the group passes only if each one does (the thread A1/A2 AND)."""
    run = run or run_harness
    deadline, receipts, failure = clock() + GROUP_TIMEOUT, {}, None
    for index, (module, extra, receipt_name, _, _) in enumerate(lanes(group)):
        run_dir = process_dir(group, index)
        argv = [sys.executable, "-B", str(ROOT / "scripts" / (module.__name__ + ".py")), *extra,
                "--mode", "ci", "--pull", "digest", "--source-archive", str(archive), "--run-dir", str(run_dir)]
        try:
            remaining = deadline - clock()
            if remaining <= 0:
                # Never start a process the deadline would stop before its teardown handler exists.
                raise subprocess.TimeoutExpired(argv, 0)
            returncode = run(argv, remaining)
            path = run_dir / receipt_name
            if path.is_symlink() or not path.is_file():
                raise ValueError("lane receipt missing")
            receipt = read_json(path)
            signal = verdict(group, module, receipt, returncode, ctx, tree)
            if signal is not None:
                failure = failure or signal
            else:
                receipts[receipt_name[:-len(".json")]] = receipt
        except Exception as exc:
            # No exception text, endpoint, token, household or raw log in the receipt.
            failure = failure or type(exc).__name__
    return (False, {"failure_type": failure}) if failure is not None else (True, receipts)


def d6a(group):
    """Item 3 (HAMCP-443 part C): each process's D6a proof, read from its own receipt, as summary lines,
    and whether the row stands. A recorded proof that failed fails the row, and so does a PASS without
    a proved one. A lane that failed before its proof ran has no proof and is already not passed (D4)."""
    lines, stands = [], True
    for index, (_, _, receipt_name, _, _) in enumerate(lanes(group)):
        name, path = receipt_name[:-len(".json")], process_dir(group, index) / receipt_name
        try:
            receipt = read_json(path) if path.is_file() and not path.is_symlink() else {}
        except ValueError:
            receipt = {}
        receipt = receipt if isinstance(receipt, dict) else {}
        proof = receipt.get("d6a")
        if not isinstance(proof, dict):
            stands = stands and receipt.get("result") != "PASS"
            lines.append("D6a " + name + ": not reached; the lane failed before its proof and is not passed")
            continue
        stands = stands and proof.get("proved") is True
        lines.append(f"D6a {name}: network {proof.get('network')} internal={proof.get('internal')} "
                     f"proved={proof.get('proved')}")
        for container, seen in sorted((proof.get("containers") or {}).items()):
            lines.append(f"D6a {name}: {container} network_mode={seen.get('network_mode')} "
                         f"networks={','.join(seen.get('networks') or [])} "
                         f"metadata 169.254.169.254:80 {str(seen.get('metadata'))[:40]}"
                         + (f"; egress 1.1.1.1:443 {str(seen['egress'])[:40]}" if "egress" in seen else ""))
    return lines, stands


def summary(line):
    print(line, flush=True)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as file:
            file.write(line + "\n")


def owned_absent(group):
    """Every container and network the group's processes owned, each read absent from Docker."""
    containers = sorted({name for lane in lanes(group) for name in lane[3]})
    networks = sorted({lane[4] for lane in lanes(group)})
    present_containers = subprocess.run(["docker", "ps", "-a", "--format", "{{.Names}}"], capture_output=True,
                                        check=True, timeout=30).stdout.decode().splitlines()
    present_networks = subprocess.run(["docker", "network", "ls", "--format", "{{.Name}}"], capture_output=True,
                                      check=True, timeout=30).stdout.decode().splitlines()
    if set(containers) & set(present_containers) or set(networks) & set(present_networks):
        raise ValueError("owned disposable residue remains")
    return {"containers": containers, "networks": networks}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("group", choices=LANE_GROUPS)
    parser.add_argument("--source-archive", type=Path, required=True)
    args = parser.parse_args(argv)
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise ValueError("lane groups require GitHub Actions")
    ctx = context()
    if ctx is None:
        raise ValueError("evidence lanes require an admitted run binding")
    passed, receipt = run_group(args.group, args.source_archive, ctx, runtime_tree())
    owned = owned_absent(args.group)
    record(args.group, passed, receipt)
    (output_dir() / ("cleanup-" + args.group + ".json")).write_bytes(canonical({
        "clean": True, "run": ctx["run"], "context_sha256": os.environ["CORE_EVIDENCE_CONTEXT_SHA256"],
        "group": args.group, "owned": owned}))
    lines, stands = d6a(args.group)
    for line in lines:
        summary(line)
    print(("PASS: " if passed else "WITHHELD: ") + args.group, flush=True)
    # Fail closed: a failed D6a proof fails the row, so lanes never assembles from it.
    return 0 if stands else 1


if __name__ == "__main__":
    raise SystemExit(main())
