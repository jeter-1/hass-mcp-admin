"""HAMCP-378 downgrade harness: which runtime runs which created-object rollback plan.

Offline. Every tree is a ``git archive`` extraction of one commit, and every
record is written and read by that tree's own unmodified code through its own
synthetic test providers. No Home Assistant, socket, container or network.

    python scripts/hamcp378_downgrade_harness.py run --work DIR --python PY \
        [--tip REF] [--receipt FILE]

``run`` extracts three trees, then runs three cases, each as a writer process
and a reader process (``python -B -I`` on this file, so nothing is imported
from the checkout):

``tip_to_beta7``
    The tip writes created-object rollback plans; 2.4.2-beta.7 reads them.
    HAMCP-378 requires every pending and approved plan to be refused with no
    provider write.
``beta14_to_beta7``
    Control: 2.4.2-beta.14 (old form) writes, beta.7 reads. This reproduces the
    HAMCP-377 finding (beta.7 runs an automation or script rollback as an
    ordinary delete), so a refusal in the first case is not an artefact of the
    reader.
``beta14_to_tip``
    The tip reads old-form plans after an upgrade: what must be re-created and
    what history stays readable.

The writer and reader record; ``run`` classifies and writes the receipt.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest

BETA7 = ("beta7", "b614be43f8c56ae06fdec0ac0bcb479b9da39715", "2.4.2-beta.7")
BETA14 = ("beta14", "54627e35dcfd8e972d468aec8745fd918db53ad3", "2.4.2-beta.14")
CASES = (("tip_to_beta7", "tip", "beta7"), ("beta14_to_beta7", "beta14", "beta7"),
         ("beta14_to_tip", "beta14", "tip"))
PLAN_MODES = (
    "automation_created", "automation_rollback_pending", "automation_rollback_approved",
    "automation_rollback_applied", "script_rollback_pending", "script_rollback_approved",
    "helper_rollback_pending", "helper_rollback_approved", "scene_rollback_approved",
)
ROLLBACK_CHILD = "created_object_rollback_child"
PROCESS_SECONDS = 1800


def emit(value: dict) -> None:
    print(json.dumps(value, sort_keys=True, default=str), flush=True)


def identity(role: str) -> dict:
    import ha_mcp_engineering
    from ha_mcp_engineering.version import SERVER_VERSION

    return {f"{role}_version": SERVER_VERSION,
            f"{role}_package": str(Path(ha_mcp_engineering.__file__).resolve().parent)}


def problem(error: BaseException) -> dict:
    code = getattr(error, "code", None)
    details = getattr(error, "details", None)
    out = {"type": type(error).__name__, "code": getattr(code, "value", code),
           "text": str(error)[:300]}
    if isinstance(details, dict):
        out["details"] = {key: details[key] for key in sorted(details)[:16]
                          if isinstance(details[key], (str, int, bool, type(None), list))}
    return out


async def attempt(function, *arguments, **keywords) -> dict:
    try:
        value = function(*arguments, **keywords)
        if asyncio.iscoroutine(value):
            value = await value
        return {"ok": True, "value": value}
    except Exception as error:  # the run classifies; the process only records
        return {"ok": False, "error": problem(error)}


def file_hashes(root: Path) -> dict:
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(root.rglob("*"))
            if path.is_file() and path.suffix not in {".lock", ".jsonl"}}


def changed(before: dict, after: dict) -> dict:
    return {"added": sorted(set(after) - set(before)), "removed": sorted(set(before) - set(after)),
            "modified": sorted(key for key in set(before) & set(after) if before[key] != after[key])}


def stored_policy(root: Path, plan_id: str) -> dict:
    record = json.loads((root / "plans" / f"{plan_id}.json").read_text(encoding="utf-8"))
    decision = record.get("policy_decision") or {}
    return {"policy_version": decision.get("policy_version"),
            "policy_class": decision.get("policy_class"),
            "reason_codes": decision.get("reason_codes")}


# --- writer ---------------------------------------------------------------------------------

def write(out: Path) -> None:
    from tests.hamcp155_fixtures import automation_config, script_config
    from tests.hamcp329_298_fixtures import CreatedObjectServiceTestCase

    summary: dict = {**identity("writer"), "modes": {},
                     "provider": "synthetic store from the writer tree's "
                                 "tests/hamcp329_298_fixtures.py"}

    def create_operation(resource_type: str, target_id: str, config: dict) -> dict:
        return {"operation_id": "create_1", "resource_type": resource_type, "action": "create",
                "target_id": target_id, "proposed_config": config, "depends_on": []}

    class Writer(CreatedObjectServiceTestCase):
        mode = ""

        def keep(self, **roles) -> None:
            shutil.copytree(self.root, out / self.mode)
            summary["modes"][self.mode] = {role: self.facts(plan) for role, plan in roles.items()}

        def facts(self, plan: dict) -> dict:
            stored = self.service.get_plan(plan["plan_id"])
            return {"plan_id": stored["plan_id"], "plan_hash": stored["plan_hash"],
                    "status": stored.get("status"),
                    "policy": stored_policy(Path(self.root), stored["plan_id"]),
                    "operations": [[item.get("action"), item.get("resource_type"),
                                    item.get("target_id")]
                                   for item in stored.get("operations") or []]}

        async def created(self, resource_type: str) -> dict:
            if resource_type == "automation":
                return await self.applied(await self.configuration_plan(create_operation(
                    "automation", "porch_light", automation_config("porch_light"))))
            if resource_type == "script":
                return await self.applied(await self.configuration_plan(create_operation(
                    "script", "porch_script", script_config("porch_script"))))
            if resource_type == "helper":
                return await self.created_helper("input_boolean")
            return await self.created_scene()

        async def test_write(self) -> None:
            resource_type, _, state = self.mode.partition("_")
            created = await self.created(resource_type)
            if state == "created":
                self.keep(created=created)
                return
            _rollback, plan = await self.rollback_plan(
                created, approve=state != "rollback_pending")
            if state == "rollback_applied":
                result = await self.apply(plan)
                self.assertEqual(result["task_state"], "succeeded_verified", result)
            self.keep(created=created, rollback=plan)

    out.mkdir(parents=True, exist_ok=False)
    suite = unittest.TestSuite(
        type(f"Writer_{mode}", (Writer,), {"mode": mode})("test_write") for mode in PLAN_MODES)
    result = unittest.TextTestRunner(verbosity=0, stream=sys.stderr).run(suite)
    summary["harness_ok"] = result.wasSuccessful()
    summary["harness_errors"] = [text[-800:] for _test, text in result.errors + result.failures]
    emit(summary)


# --- reader ---------------------------------------------------------------------------------

def read(stores: Path, work: Path, summary: dict) -> None:
    import tests.hamcp069b_fixtures as base
    import tests.hamcp155_fixtures as base_155
    from tests.hamcp155_fixtures import REVIEWER, ConfigurationDeleteServiceTestCase
    from ha_mcp_engineering.governance.models import ChangePlan

    try:  # the tree's production composition when it has created-object support
        from tests.hamcp329_298_fixtures import CreatedObjectServiceTestCase as Harness
        harness = "tests/hamcp329_298_fixtures.py CreatedObjectServiceTestCase"
    except ImportError:
        Harness, harness = ConfigurationDeleteServiceTestCase, (
            "tests/hamcp155_fixtures.py ConfigurationDeleteServiceTestCase")
    out: dict = {**identity("reader"), "modes": {}, "harness": harness}

    class Reader(Harness):
        mode = ""

        async def asyncSetUp(self) -> None:
            self.copy = work / self.mode
            shutil.copytree(stores / self.mode, self.copy)
            self.seen: dict = {"startup_assertions": []}
            out["modes"][self.mode] = self.seen
            original = base.tempfile.TemporaryDirectory

            class Store:
                name = str(self.copy)

                def cleanup(self) -> None:
                    return None

            base.tempfile.TemporaryDirectory = lambda *_a, **_k: Store()
            try:
                await super().asyncSetUp()
            finally:
                base.tempfile.TemporaryDirectory = original

        async def restart_runtime(self) -> None:
            try:
                await super().restart_runtime()
            except AssertionError as error:
                self.seen["startup_assertions"].append(str(error)[:300])

        def provider_writes(self) -> list:
            """Every provider write, judged the same way in every tree.

            REST: any POST, PUT, PATCH or DELETE except the configuration
            check.  WebSocket: the fixture's registry writes, and any command
            whose type ends in a mutating verb (helper create, update, delete).
            """

            check = getattr(base_155, "CHECK_CONFIG", None)
            rest = [item for item in self.config_rest.requests
                    if item[0] in {"POST", "PUT", "PATCH", "DELETE"} and item != check]
            registry = {id(item) for item in self.ws.writes()}
            socket = [item for item in self.ws.commands if id(item) in registry
                      or str(item.get("type", "")).rpartition("/")[2]
                      in {"create", "update", "delete", "remove"}]
            return ([["rest", *map(str, item)] for item in rest]
                    + [["websocket", json.dumps(item, sort_keys=True, default=str)[:200]]
                       for item in socket])

        async def call(self, keep: tuple, function, *arguments, **keywords) -> dict:
            before = len(self.provider_writes())
            result = await attempt(function, *arguments, **keywords)
            if result["ok"]:
                value = result.pop("value")
                result["value"] = ({key: value.get(key) for key in keep if key in value}
                                   if isinstance(value, dict) else None)
                result["raw"] = value
            result["provider_writes"] = self.provider_writes()[before:]
            return result

        async def decide(self, plan_id: str, plan_hash: str, pending: dict) -> dict:
            """Finish the exact-hash external approval the request opened."""

            granted = pending
            for _ in range(3):
                if granted.get("status") != "approval_pending":
                    return granted
                review, csrf = await self.service.issue_external_csrf(
                    plan_id, granted["challenge_id"])
                safety = review.get("safety_critical_acknowledgement")
                acknowledgement = ({
                    "acknowledged_objects": tuple(row["object_ref"] for row in safety["objects"]),
                    "acknowledged_projection_hash": safety["projection_hash"]} if safety else {})
                granted = await self.service.decide_external_approval(
                    plan_id=plan_id, challenge_id=granted["challenge_id"],
                    expected_plan_hash=plan_hash, approval_kind=granted["approval_kind"],
                    approval_action=granted["approval_action"], csrf_nonce=csrf,
                    decision="approve", approver_principal=REVIEWER, **acknowledgement)
            return granted

        async def test_read(self) -> None:
            seen, before = self.seen, file_hashes(self.copy)
            seen["readiness_after_startup"] = self.runtime.readiness_state()
            seen["synthetic_core_objects"] = []
            for resource_type, target_id in (("automation", "porch_light"),
                                             ("script", "porch_script")):
                if self.mode.startswith(resource_type) and "applied" not in self.mode:
                    self.add(resource_type, target_id)
                    seen["synthetic_core_objects"].append([resource_type, target_id])
            seen["decode"] = {}
            for path in sorted((self.copy / "plans").glob("*.json")):
                decoded = await attempt(
                    lambda: ChangePlan.from_dict(json.loads(path.read_text(encoding="utf-8"))))
                seen["decode"][path.stem] = (
                    {"ok": True, "status": decoded["value"].status} if decoded["ok"] else decoded)
            listing = await self.call(("count", "partial", "projection_failure_count"),
                                      self.service.list_plans, limit=100)
            listing.pop("raw", None)
            seen["list_plans"] = listing
            seen["plans"] = {}
            # The rollback first: rolling back the forward plan may return it.
            for role, facts in sorted(summary["modes"][self.mode].items(),
                                      key=lambda item: item[0] != "rollback"):
                plan_id, plan_hash = facts["plan_id"], facts["plan_hash"]
                record: dict = {"plan_id": plan_id, "writer_status": facts["status"],
                                "operations": facts.get("operations")}
                record["get_plan"] = await self.call(
                    ("status", "plan_hash"), self.service.get_plan, plan_id)
                record["get_plan"].pop("raw", None)
                if role == "rollback" and facts["status"] != "applied":
                    record["approve"] = await self.call(
                        ("status", "approval_kind", "approval_action"),
                        self.service.approve, plan_id, plan_hash)
                    pending = record["approve"].pop("raw", None)
                    if isinstance(pending, dict) and pending.get("challenge_id"):
                        record["approval_decision"] = await self.call(
                            ("status", "approval_kind", "approval_action"),
                            self.decide, plan_id, plan_hash, pending)
                        record["approval_decision"].pop("raw", None)
                    record["apply"] = await self.call(
                        ("status", "task_state", "terminal_outcome"),
                        self.service.apply, plan_id, plan_hash)
                    record["apply"].pop("raw", None)
                if role == "created":
                    record["rollback_change"] = await self.call(
                        ("status", "rollback_plan_id", "replaced_rollback_plan_id"),
                        self.service.rollback_change, plan_id)
                    record["rollback_change"].pop("raw", None)
                final = await self.call(("status",), self.service.get_plan, plan_id)
                final.pop("raw", None)
                record["status_after"] = final
                seen["plans"][role] = record
            for name, function in (
                    ("recover_periodic", lambda: self.runtime.recover_once("periodic")),
                    ("reconcile_execution_tasks", self.service.reconcile_execution_tasks),
                    ("reconcile_operational_plans", self.service.reconcile_operational_plans)):
                result = await self.call((), function)
                result.pop("raw", None)
                result.pop("value", None)
                seen[name] = result
            seen["readiness_final"] = self.runtime.readiness_state()
            seen["provider_writes"] = self.provider_writes()
            seen["files"] = changed(before, file_hashes(self.copy))

    work.mkdir(parents=True, exist_ok=False)
    suite = unittest.TestSuite(
        type(f"Reader_{mode}", (Reader,), {"mode": mode})("test_read")
        for mode in sorted(summary["modes"]))
    result = unittest.TextTestRunner(verbosity=0, stream=sys.stderr).run(suite)
    out["harness_ok"] = result.wasSuccessful()
    out["harness_errors"] = [text[-800:] for _test, text in result.errors + result.failures]
    emit(out)


# --- run ------------------------------------------------------------------------------------

def git(*arguments: str) -> str:
    return subprocess.run(["git", *arguments], check=True, capture_output=True,
                          text=True).stdout.strip()


def extract(name: str, commit: str, work: Path) -> dict:
    """``git archive`` of one commit into its own new directory."""

    target = work / f"src-{name}"
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    with tempfile.TemporaryFile(dir=work) as archive:
        subprocess.run(["git", "archive", "--format=tar", commit], check=True, stdout=archive)
        archive.seek(0)
        with tarfile.open(fileobj=archive) as tar:
            tar.extractall(target, filter="data")
    config = (target / "hass_mcp_engineering_beta" / "config.yaml").read_text(encoding="utf-8")
    version = next(line.split(":", 1)[1].strip().strip('"')
                   for line in config.splitlines() if line.startswith("version:"))
    return {"name": name, "commit": commit, "declared_version": version, "path": target}


def process(python: str, role: str, tree: Path, *arguments: Path) -> dict:
    completed = subprocess.run(
        [python, "-B", "-I", str(Path(__file__).resolve()), role, str(tree), *map(str, arguments)],
        capture_output=True, text=True, timeout=PROCESS_SECONDS, check=False)
    lines = completed.stdout.strip().splitlines()
    if completed.returncode != 0 or not lines:
        raise SystemExit(f"{role} on {tree.name} exited {completed.returncode}: "
                         f"{completed.stderr.strip()[-1500:]}")
    result = json.loads(lines[-1])
    package = Path(result[{"write": "writer", "read": "reader"}[role] + "_package"])
    if tree.resolve() not in package.parents:
        raise SystemExit(f"{role} imported {package}, outside {tree}")
    if not result["harness_ok"]:
        raise SystemExit(f"{role} on {tree.name}: {result['harness_errors']}")
    return result


def codes(record: dict) -> list:
    return [[call, "ok" if record[call]["ok"] else record[call]["error"]["code"]]
            for call in ("get_plan", "approve", "approval_decision", "apply", "rollback_change",
                         "status_after") if record.get(call) is not None]


def classify(writer: dict, reader: dict) -> dict:
    """Per mode: what each call returned, and the provider writes it made."""

    result = {}
    for mode, seen in sorted(reader["modes"].items()):
        rollback = seen["plans"].get("rollback")
        created = seen["plans"]["created"]
        row = {
            "writer_rollback_reasons": (writer["modes"][mode].get("rollback") or {})
            .get("policy", {}).get("reason_codes"),
            "rollback_calls": codes(rollback) if rollback else None,
            "created_calls": codes(created),
            "rollback_change_of_created": (created.get("rollback_change") or {}).get("value"),
            "list_plans": seen["list_plans"].get("value") if seen["list_plans"]["ok"]
            else seen["list_plans"]["error"]["code"],
            "provider_writes": len(seen["provider_writes"]),
            "readiness": [seen["readiness_after_startup"], seen["readiness_final"]],
            "recovery": {name: "ok" if seen[name]["ok"] else seen[name]["error"]["code"]
                         for name in ("recover_periodic", "reconcile_execution_tasks",
                                      "reconcile_operational_plans")},
            "startup_assertions": seen["startup_assertions"],
        }
        if rollback is None:
            row["outcome"] = "no rollback plan"
        elif rollback["writer_status"] == "applied":
            row["outcome"] = ("history readable" if rollback["get_plan"]["ok"]
                              else f"history unreadable: {rollback['get_plan']['error']['code']}")
        elif row["provider_writes"]:
            row["outcome"] = "executed"
        elif all(not rollback[call]["ok"]
                 and rollback[call]["error"]["code"] == "policy_snapshot_mismatch"
                 for call in ("get_plan", "approve", "apply")):
            row["outcome"] = "refused: policy_snapshot_mismatch"
        else:
            row["outcome"] = "other"
        result[mode] = row
    return result


def run(arguments: argparse.Namespace) -> None:
    work = Path(arguments.work).resolve()
    work.mkdir(parents=True, exist_ok=True)
    tip_commit = git("rev-parse", f"{arguments.tip}^{{commit}}")
    trees = {name: extract(name, commit, work)
             for name, commit, _version in (BETA7, BETA14)}
    trees["tip"] = extract("tip", tip_commit, work)
    for name, commit, version in (BETA7, BETA14):
        if trees[name]["declared_version"] != version:
            raise SystemExit(f"{name} at {commit} declares {trees[name]['declared_version']}")
    receipt: dict = {
        "model": "hamcp378-downgrade-harness-v1",
        "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "trees": {name: {key: value for key, value in tree.items() if key != "path"}
                  for name, tree in sorted(trees.items())},
        "cases": {},
    }
    writers: dict = {}
    for case, writer_name, reader_name in CASES:
        if writer_name not in writers:
            stores = work / f"stores-{writer_name}"
            if stores.exists():
                shutil.rmtree(stores)
            writers[writer_name] = (stores, process(
                arguments.python, "write", trees[writer_name]["path"], stores))
        stores, writer = writers[writer_name]
        before = file_hashes(stores)
        summary = work / f"summary-{writer_name}.json"
        summary.write_text(json.dumps(writer, sort_keys=True), encoding="utf-8")
        reading = work / f"read-{case}"
        if reading.exists():
            shutil.rmtree(reading)
        reader = process(arguments.python, "read", trees[reader_name]["path"],
                         stores, reading, summary)
        if file_hashes(stores) != before:
            raise SystemExit(f"{case}: the reader changed the written stores")
        receipt["cases"][case] = {
            "writer": writer_name, "reader": reader_name,
            "writer_version": writer["writer_version"],
            "reader_version": reader["reader_version"],
            "reader_harness": reader["harness"],
            "modes": classify(writer, reader),
        }
        (work / f"observed-{case}.json").write_text(
            json.dumps({"writer": writer, "reader": reader}, indent=1, sort_keys=True,
                       default=str), encoding="utf-8")
    text = json.dumps(receipt, indent=1, sort_keys=True, default=str) + "\n"
    Path(arguments.receipt or work / "receipt.json").write_text(text, encoding="utf-8")
    print(text)


def main() -> None:
    if len(sys.argv) > 2 and sys.argv[1] in {"write", "read"}:
        tree = Path(sys.argv[2]).resolve()
        sys.path[:0] = [str(tree / "hass_mcp_engineering_beta"), str(tree / "tests"), str(tree)]
        os.chdir(tree)
        paths = [Path(item).resolve() for item in sys.argv[3:]]
        if sys.argv[1] == "write":
            write(paths[0])
        else:
            read(paths[0], paths[1], json.loads(paths[2].read_text(encoding="utf-8")))
        return
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    command = sub.add_parser("run")
    command.add_argument("--work", required=True)
    command.add_argument("--python", required=True)
    command.add_argument("--tip", default="HEAD")
    command.add_argument("--receipt")
    run(parser.parse_args())


if __name__ == "__main__":
    main()
