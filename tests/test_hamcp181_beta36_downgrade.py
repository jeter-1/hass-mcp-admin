"""HAMCP-181 PR2: rollback to the shipped beta.36 reader, the supported target.

Owner decision (Josh, 2026-10-25): 2.4.1-beta.36 is the supported
governed-plan rollback target for 2.4.1-beta.37. beta.8 stays documented as a
fan/power/task-only floor (``tests/test_hamcp181_beta8_downgrade.py``): it
cannot decode any governed plan written since beta.29, a pre-existing and
accepted limitation.

The committed records in ``tests/fixtures/f3_hamcp181_candidate/pr2`` were
written by the PR2 candidate writer (see that directory's ``provenance.json``).
This module extracts the exact beta.36 source (tag ``v2.4.1-beta.36``, commit
``16b724e``) with ``git archive``, runs ``shipped_reader.py`` under it in a
separate ``-I`` interpreter over a copy of every store, and asserts that
beta.36 reads every plan, task, ``verification_summary`` and terminal outcome
without quarantine, rewrite or loss, and without a provider call.

The only write beta.36 makes is the append-only ``duplicate_apply_prevented``
task event that the driver's duplicate-apply probe provokes. The same driver
run under the current source shows the current reader making exactly the same
file changes. Every committed event, field and ``verification_summary`` key
survives it.
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
from typing import Any
import unittest

from tests.test_hamcp181_beta8_downgrade import (
    CANDIDATE_EXPECTED,
    DRIVER,
    DRIVER_TIMEOUT_SECONDS,
    FIXTURES,
    FORBIDDEN_FAULT_MARKERS,
    NOT_DISPATCHED_PHASES,
    ROOT,
    TYPED_EXPECTED,
    _calls,
    _errors,
    _git,
    _plan_file,
    _sha256,
    verified_provenance,
)


BETA36_COMMIT = "16b724ec0b1d0fa8aae9705df569704f27ad783e"
BETA36_TAG = "v2.4.1-beta.36"
BETA36_TAG_OBJECT = "a4ffc66ddf60095427a1f9f68f0d2c2121b94c3d"
BETA36_VERSION = "2.4.1-beta.36"
BETA36_MCP_PIN = "1.30.0"
RECOVERY_CURSOR = "f3-child-execution-v1/.recovery-declaration-cursor.json"
# beta.36's own dispatched_at bases: it predates provider_call_start_time.
BETA36_DISPATCH_BASES = {"no_dispatch_intent", "call_not_sent", "dispatch_intent_commit_time"}

RUN: dict[str, Any] = {}


def setUpModule() -> None:
    if shutil.which("git") is None:
        raise unittest.SkipTest("git is unavailable")
    if _git("rev-parse", "--verify", "--quiet", f"{BETA36_COMMIT}^{{commit}}") != BETA36_COMMIT:
        raise unittest.SkipTest(f"beta.36 commit {BETA36_COMMIT} is not in this clone")
    RUN["tag_object_type"] = _git("cat-file", "-t", BETA36_TAG_OBJECT)
    RUN["tag_object"] = _git("cat-file", "-p", BETA36_TAG_OBJECT)
    RUN["provenance"] = verified_provenance()

    temp = Path(tempfile.mkdtemp(prefix="hamcp181-beta36-downgrade-"))
    RUN["temp"] = temp
    archive = _git("archive", "--format=tar", BETA36_COMMIT, binary=True)
    if archive is None:
        raise AssertionError("git archive of beta.36 failed")
    RUN["archive_commit"] = subprocess.run(
        ["git", "-C", str(ROOT), "get-tar-commit-id"], input=archive,
        capture_output=True, check=True, timeout=120).stdout.decode().strip()
    source = temp / "beta36-source"
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(source, filter="data")
    RUN["source_config"] = (source / "hass_mcp_engineering_beta" / "config.yaml").read_text(
        encoding="utf-8")
    fixture_copy = temp / "fixtures"
    shutil.copytree(FIXTURES, fixture_copy)
    beta36_python = os.environ.get("HAMCP181_BETA36_PYTHON") or sys.executable
    RUN["result"] = _run_driver("beta.36", beta36_python, source, fixture_copy, temp)
    RUN["current"] = _run_driver("current", sys.executable, ROOT, fixture_copy, temp)
    RUN["fixture_copy_changed"] = sorted(
        relative for relative, digest in RUN["provenance"]["files"].items()
        if _sha256(fixture_copy / relative) != digest)


def _run_driver(label: str, interpreter: str, source: Path, fixtures: Path,
                temp: Path) -> dict[str, Any]:
    work = temp / f"work-{label}"
    work.mkdir()
    completed = subprocess.run(
        [interpreter, "-I", str(DRIVER), str(source), str(fixtures), str(work)],
        cwd=str(temp), capture_output=True, text=True, timeout=DRIVER_TIMEOUT_SECONDS,
        check=False)
    if completed.returncode != 0:
        raise AssertionError(
            f"{label} reader failed ({completed.returncode}):\n{completed.stderr[-4000:]}")
    return json.loads(completed.stdout)


def tearDownModule() -> None:
    temp = RUN.get("temp")
    if temp is not None:
        shutil.rmtree(temp, ignore_errors=True)


def _committed_task(shape: str) -> tuple[str, dict[str, Any]]:
    (path,) = (FIXTURES / shape / "store" / "execution-tasks-v1").glob("*.json")
    return (f"execution-tasks-v1/{path.name}", json.loads(path.read_text(encoding="utf-8")))


class Beta36ReaderIdentityTests(unittest.TestCase):
    def test_reader_is_the_exact_beta36_release(self):
        self.assertEqual(RUN["archive_commit"], BETA36_COMMIT)
        self.assertIn(f'version: "{BETA36_VERSION}"', RUN["source_config"])
        result = RUN["result"]
        self.assertTrue(result["reader_is_source"], result["reader_package"])
        if RUN["tag_object_type"] is not None:
            self.assertEqual(RUN["tag_object_type"], "tag")
            self.assertIn(f"object {BETA36_COMMIT}", RUN["tag_object"])
            self.assertIn(f"tag {BETA36_TAG}", RUN["tag_object"])

    def test_sdk_version_gate_shim_is_disclosed(self):
        sdk = RUN["result"]["mcp_sdk"]
        self.assertEqual(sdk["reader_pin"], BETA36_MCP_PIN)
        self.assertIs(sdk["version_gate_shimmed"], sdk["installed"] != BETA36_MCP_PIN)

    def test_fixture_copy_is_unchanged(self):
        self.assertEqual(RUN["fixture_copy_changed"], [])
        self.assertEqual(set(RUN["result"]["shapes"]),
                         set(CANDIDATE_EXPECTED) | set(TYPED_EXPECTED))


class Beta36GovernedRollbackTests(unittest.TestCase):
    """Section 4.3 item 3 for every governed candidate shape under beta.36."""

    def shapes(self):
        for shape in CANDIDATE_EXPECTED:
            with self.subTest(shape=shape):
                yield shape, RUN["result"]["shapes"][shape]

    def test_beta36_decodes_every_plan(self):
        for shape, observed in self.shapes():
            plan = _plan_file(shape, observed["plan_id"])
            self.assertEqual(observed["plan_decode"], {plan: None})

    def test_startup_and_restart_with_nothing_quarantined(self):
        for _shape, observed in self.shapes():
            self.assertIsNone(observed.get("startup_error"))
            self.assertIsNone(observed.get("restart_error"))
            for phase in ("startup", "after_duplicate", "restart"):
                snapshot = observed[phase]
                self.assertEqual(snapshot["quarantined"], [], phase)
                self.assertEqual(snapshot["plan_corruption_count"], 0, phase)
                self.assertEqual(snapshot["task_corruption_count"], 0, phase)
                self.assertEqual(snapshot["storage_corruption_count"], 0, phase)
                self.assertEqual(snapshot["readiness"]["faults"], [], phase)
                self.assertTrue(snapshot["readiness"]["execution_ready"], phase)
                self.assertTrue(snapshot["runtime_execution_ready"], phase)
                self.assertEqual(snapshot["runtime_corrupt_record_count"], 0, phase)
                self.assertEqual(snapshot["runtime_nonterminal_execution_count"], 0, phase)

    def test_no_execution_record_corruption_or_storage_fault(self):
        for _shape, observed in self.shapes():
            text = json.dumps([_errors(observed),
                               [observed[p].get("readiness") for p in ("startup", "restart")]])
            for marker in FORBIDDEN_FAULT_MARKERS:
                self.assertNotIn(marker, text)

    def test_plan_and_task_reads_return_each_record_and_outcome(self):
        for shape, observed in self.shapes():
            state, outcome, phase, plan_status = CANDIDATE_EXPECTED[shape]
            reads = observed["reads"]
            self.assertEqual(reads["get_change_plan"],
                             {"plan_id": observed["plan_id"], "status": plan_status})
            self.assertEqual(reads["list_change_plans"]["listed"],
                             [{"plan_id": observed["plan_id"], "status": plan_status}])
            expected = {"task_id": observed["task_id"], "plan_id": observed["plan_id"],
                        "state": state, "terminal_outcome": outcome,
                        "provider_dispatch_occurred": phase not in NOT_DISPATCHED_PHASES}
            (listed,) = reads["list_execution_tasks"]["listed"]
            for surface in (reads["get_execution_task"], listed):
                self.assertEqual({key: surface.get(key) for key in expected}, expected)

    def test_verification_summary_is_read_without_loss(self):
        for shape, observed in self.shapes():
            _relative, committed = _committed_task(shape)
            stored = committed["verification_summary"]
            view = observed["task_verification_summary"]
            self.assertIsInstance(view, dict)
            self.assertLessEqual(set(stored), set(view))
            for key, value in stored.items():
                if key == "children":
                    continue
                self.assertEqual(view[key], value, key)
            # beta.36 decorates each child with its own attribution; every
            # stored child key and value survives.
            self.assertEqual(len(view["children"]), len(stored["children"]))
            for stored_child, view_child in zip(stored["children"], view["children"]):
                self.assertEqual({key: view_child.get(key) for key in stored_child},
                                 stored_child)
            if committed["terminal_outcome"] == "failed_after_intent_not_dispatched":
                self.assertTrue(view.get("provider_call_not_sent")
                                or view.get("setter_call_not_sent"))
                self.assertIs(view["dispatch_performed"], False)

    def test_stored_task_outcome_and_summary_survive_the_session(self):
        for shape, observed in self.shapes():
            _relative, committed = _committed_task(shape)
            self.assertEqual(observed["final_stored_task"], {
                key: committed[key]
                for key in ("state", "terminal_outcome", "verification_summary")})

    def test_beta36_writes_only_the_append_only_duplicate_event(self):
        for shape, observed in self.shapes():
            relative, committed = _committed_task(shape)
            self.assertEqual(observed["startup_files"],
                             {"added": [RECOVERY_CURSOR], "modified": [], "removed": []})
            # Plan files and child execution records are never rewritten.
            self.assertEqual(observed["final_files"],
                             {"added": [RECOVERY_CURSOR], "modified": [relative], "removed": []})
            change = observed["final_task_changes"][relative]
            self.assertTrue(change["events_prefix_preserved"])
            self.assertEqual(change["other_changed_keys"], ["updated_at"])
            (appended,) = change["appended_events"]
            self.assertEqual(appended["event_type"], "duplicate_apply_prevented")
            self.assertEqual(appended["state_before"], committed["state"])
            self.assertEqual(appended["state_after"], committed["state"])
            self.assertEqual(set(appended["changes"]), {"state", "updated_at"})

    def test_duplicate_apply_refused_without_redispatch(self):
        for shape, observed in self.shapes():
            state, outcome, _phase, _plan_status = CANDIDATE_EXPECTED[shape]
            duplicate = observed["duplicate_apply"]
            if state == "succeeded_verified":
                self.assertTrue(duplicate["ok"], duplicate)
                result = duplicate["result"]
                self.assertIs(result["task_reused"], True)
                self.assertIs(result["redispatch_performed"], False)
                self.assertEqual(result["task_state"], state)
                self.assertEqual(result["terminal_outcome"], outcome)
            else:
                self.assertFalse(duplicate["ok"], duplicate)
                self.assertEqual(duplicate["error"]["code"], "duplicate_apply_attempt")

    def test_restart_reads_and_duplicate_make_zero_provider_calls(self):
        for _shape, observed in self.shapes():
            self.assertEqual(_calls(observed["startup"]["provider_calls"]), [])
            self.assertEqual(_calls(observed["after_reads_calls"]), [])
            self.assertEqual(_calls(observed["after_duplicate"]["provider_calls"]), [])
            self.assertEqual(_calls(observed["restart"]["provider_calls"]), [])
            for phase in ("startup", "after_duplicate", "restart"):
                for key, value in observed[phase]["transport"].items():
                    self.assertFalse(value, f"{phase} {key}={value}")

    def test_beta36_dispatch_time_is_its_own_display(self):
        # Rolling back returns the beta.36 display: beta.36 does not read the
        # stored provider_call_started_at (kept, see above), so sent shapes show
        # the intent commit time under beta.36's own disclosed basis.
        for _shape, observed in self.shapes():
            self.assertIn(observed["task_dispatch_time"]["dispatched_at_basis"],
                          BETA36_DISPATCH_BASES)

    def test_current_reader_makes_the_same_file_changes(self):
        current = RUN["current"]
        self.assertTrue(current["reader_is_source"], current["reader_package"])
        for shape, observed in self.shapes():
            now = current["shapes"][shape]
            self.assertEqual(now["startup_files"], observed["startup_files"])
            self.assertEqual(now["final_files"], observed["final_files"])
            for relative, change in observed["final_task_changes"].items():
                mine = now["final_task_changes"][relative]
                self.assertEqual(
                    {key: mine[key] for key in ("events_prefix_preserved", "other_changed_keys")},
                    {key: change[key] for key in ("events_prefix_preserved", "other_changed_keys")})
                self.assertEqual(
                    [(e["event_type"], e["state_before"], e["state_after"])
                     for e in mine["appended_events"]],
                    [(e["event_type"], e["state_before"], e["state_after"])
                     for e in change["appended_events"]])


class Beta36TypedRollbackTests(unittest.TestCase):
    def test_typed_records_meet_section_4_3_item_3(self):
        for shape, (state, outcome) in TYPED_EXPECTED.items():
            with self.subTest(shape=shape):
                observed = RUN["result"]["shapes"][shape]
                self.assertIsNone(observed.get("startup_error"))
                self.assertIsNone(observed.get("restart_error"))
                expected = {"task_id": observed["task_id"], "state": state,
                            "terminal_outcome": outcome, "provider_dispatch_occurred": True}
                self.assertEqual(observed["load"], {"ok": True, "value": True})
                self.assertEqual(observed["receipt"], expected)
                self.assertEqual(observed["get_execution_task"], {"success": True, "data": expected})
                self.assertEqual(observed["duplicate_control"], {"ok": True, "result": expected})
                for phase in ("startup", "after_duplicate", "restart"):
                    self.assertEqual(observed[phase]["quarantined"], [], phase)
                    self.assertEqual(observed[phase]["health"]["recovery_failures"], 0, phase)
                    self.assertEqual(observed[phase]["transport_calls"], [], phase)
                    self.assertEqual(observed[phase]["transport_writes"], 0, phase)
                    self.assertEqual(observed[phase]["method_calls"], [], phase)
                for files in (observed["startup_files"], observed["final_files"]):
                    self.assertEqual(files, {"added": [], "modified": [], "removed": []})


if __name__ == "__main__":
    unittest.main()
