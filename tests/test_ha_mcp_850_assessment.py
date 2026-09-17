"""Offline controls for the separate, synthetic exact-image assessment lane."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("assessment850", ROOT / "scripts/ha_mcp_850_container_assessment.py")
lane = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lane)


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.env = dict(GITHUB_ACTIONS="true", GITHUB_REPOSITORY="jeter-1/hass-mcp-admin",
                        GITHUB_REF=lane.BRANCH, GITHUB_RUN_ID="1234", GITHUB_RUN_ATTEMPT="1")

    def test_both_architectures(self):
        for arch in ("amd64", "arm64"):
            self.assertEqual(lane.execution_guard(self.env, arch), "h850-1234-1-" + arch)

    def test_refuses_other_repositories_branches_and_non_ci(self):
        for field, value in (("GITHUB_REPOSITORY", "other/repo"), ("GITHUB_REF", "refs/heads/main"),
                             ("GITHUB_ACTIONS", "false"), ("GITHUB_RUN_ID", "../x"),
                             ("GITHUB_RUN_ATTEMPT", "1\n")):
            with self.subTest(field=field), self.assertRaises(lane.Refusal):
                lane.execution_guard({**self.env, field: value}, "amd64")

    def test_missing_identity_refuses(self):
        for field in self.env:
            env = self.env.copy()
            del env[field]
            with self.subTest(field=field), self.assertRaises(lane.Refusal):
                lane.execution_guard(env, "amd64")

    def test_unsupported_architecture_refuses(self):
        with self.assertRaises(lane.Refusal):
            lane.execution_guard(self.env, "arm/v7")


class CatalogTests(unittest.TestCase):
    def test_complete_multiple_pages(self):
        a, b = {"name": "first"}, {"name": "second"}
        self.assertEqual(lane.complete_catalog([{"tools": [a], "nextCursor": "next"}, {"tools": [b]}]), [a, b])

    def test_incomplete_chain_refuses(self):
        for pages in ([{"tools": [], "nextCursor": "unread"}],
                      [{"tools": []}, {"tools": []}],
                      [{"tools": [], "nextCursor": "same"}, {"tools": [], "nextCursor": "same"}, {"tools": []}]):
            with self.subTest(pages=pages), self.assertRaises(lane.Refusal):
                lane.complete_catalog(pages)

    def test_duplicates_refuse(self):
        with self.assertRaisesRegex(lane.Refusal, "catalog_duplicate"):
            lane.complete_catalog([{"tools": [{"name": "same"}, {"name": "same"}]}])

    def test_malformed_tool_refuses(self):
        for tools in ([None], [{}], [{"name": 1}], [{"name": ""}]):
            with self.subTest(tools=tools), self.assertRaises(lane.Refusal):
                lane.complete_catalog([{"tools": tools}])

    def test_bounds_refuse(self):
        for pages in ([], [{"tools": []}] * 17,
                      [{"tools": [{"name": str(i)} for i in range(513)]}],
                      [{"tools": [{"name": "large", "description": "x" * lane.MAX_BYTES}]}]):
            with self.assertRaises(lane.Refusal):
                lane.complete_catalog(pages)

    def test_512_tools_are_complete(self):
        self.assertEqual(len(lane.complete_catalog([{"tools": [{"name": str(i)} for i in range(512)]}])), 512)


class EvidenceTests(unittest.TestCase):
    def test_small_structured_and_text_success(self):
        expected = {"success": True, "data": {"state": "off"}}
        self.assertEqual(lane.payload({"structuredContent": expected}), expected)
        self.assertEqual(lane.payload({"content": [{"type": "text", "text": json.dumps(expected)}]}), expected)

    def test_tool_errors_do_not_become_success(self):
        for value in ({"isError": True}, {"structuredContent": {"success": False}},
                      {"structuredContent": {"data": "not-success"}}):
            with self.assertRaises(lane.Refusal):
                lane.payload(value)

    def test_state_uses_its_existing_metadata_envelope(self):
        expected = {"data": {"entity_id": lane.FAN, "state": "off"}, "metadata": {}}
        self.assertEqual(lane.payload({"structuredContent": expected}, "ha_get_state"), expected)
        with self.assertRaises(lane.Refusal):
            lane.payload({"structuredContent": expected}, "ha_call_service")
        for invalid in ({"data": {}}, {"data": {"entity_id": "other", "state": "off"}},
                        {**expected, "success": False}):
            with self.assertRaises(lane.Refusal):
                lane.payload({"structuredContent": invalid}, "ha_get_state")

    def test_existing_evidence_is_preserved(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            lane.save(folder, "receipt.json", {"status": "FAIL"})
            before = (folder / "receipt.json").read_bytes()
            with self.assertRaises(FileExistsError):
                lane.save(folder, "receipt.json", {"status": "PASS"})
            self.assertEqual((folder / "receipt.json").read_bytes(), before)

    def test_oversized_evidence_is_not_written(self):
        with tempfile.TemporaryDirectory() as name:
            with self.assertRaisesRegex(lane.Refusal, "evidence_bound"):
                lane.save(Path(name), "huge.json", {"data": "x" * lane.MAX_BYTES})
            self.assertEqual(list(Path(name).iterdir()), [])

    def test_command_failures_do_not_expose_captured_output(self):
        with patch.object(lane.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "SYNTHETIC_SECRET", "SYNTHETIC_SECRET")):
            with self.assertRaises(lane.Refusal) as error:
                lane.docker("pull", "fixed")
            self.assertEqual(str(error.exception), "docker_command_failed")

    def test_command_timeout_is_finite(self):
        with patch.object(lane.subprocess, "run", side_effect=subprocess.TimeoutExpired("synthetic", 15)) as run:
            with self.assertRaises(subprocess.TimeoutExpired):
                lane.docker("info", timeout=15)
            self.assertEqual(run.call_args.kwargs["timeout"], 15)
            self.assertEqual(run.call_args.args[0][:3], ["docker", "--host", "unix:///var/run/docker.sock"])


class CleanupTests(unittest.TestCase):
    identity = "h850-1234-1-amd64"

    def test_invalid_resource_names_never_reach_docker(self):
        with patch.object(lane, "docker") as docker:
            with self.assertRaises(lane.Refusal):
                lane.cleanup("other-container")
            docker.assert_not_called()

    def test_foreign_owner_is_not_stopped(self):
        with patch.object(lane, "docker", return_value=subprocess.CompletedProcess([], 0, "foreign\n", "")) as docker:
            with self.assertRaisesRegex(lane.Refusal, "cleanup_owner_mismatch"):
                lane.cleanup(self.identity)
            self.assertEqual(docker.call_count, 1)

    def test_absence_is_checked_against_available_daemon(self):
        def fake(*args, **kwargs):
            return subprocess.CompletedProcess([], 1 if args[0] == "inspect" or args[:2] == ("network", "inspect") else 0, "", "")
        with patch.object(lane, "docker", side_effect=fake) as docker:
            records = lane.cleanup(self.identity)
            self.assertEqual(len(records), 4)
            self.assertTrue(all(r["status"] == "absent" for r in records))
            self.assertFalse(any(c.args[0] in ("stop", "rm") for c in docker.call_args_list))

    def test_daemon_failure_does_not_claim_cleanup(self):
        def fake(*args, **kwargs):
            if args[0] == "info":
                raise lane.Refusal("docker_command_failed")
            return subprocess.CompletedProcess([], 1, "", "")
        with patch.object(lane, "docker", side_effect=fake), self.assertRaises(lane.Refusal):
            lane.cleanup(self.identity)

    def test_retained_resources_refuse(self):
        def fake(*args, **kwargs):
            if args[0] == "inspect" or args[:2] == ("network", "inspect"):
                return subprocess.CompletedProcess([], 1, "", "")
            return subprocess.CompletedProcess([], 0, "remaining" if args[0] == "ps" else "", "")
        with patch.object(lane, "docker", side_effect=fake), self.assertRaisesRegex(lane.Refusal, "containers_retained"):
            lane.cleanup(self.identity)


class WorkflowTests(unittest.TestCase):
    def test_closed_scope_and_always_cleanup(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/ha-mcp-850-container-assessment.yml").read_text())
        self.assertEqual(workflow[True], {"push": {"branches": [lane.BRANCH.removeprefix("refs/heads/")]}})
        self.assertEqual(workflow["permissions"], {"contents": "read"})
        self.assertFalse(workflow["concurrency"]["cancel-in-progress"])
        job = workflow["jobs"]["exact-containers"]
        self.assertEqual(job["timeout-minutes"], 30)
        self.assertEqual({entry["architecture"] for entry in job["strategy"]["matrix"]["include"]}, {"amd64", "arm64"})
        for step in job["steps"]:
            if "uses" in step:
                self.assertRegex(step["uses"], r"@[a-f0-9]{40}$")
            if step.get("name", "").startswith(("Always reconcile", "Remove disposable", "Retain bounded")):
                self.assertEqual(step["if"], "${{ always() }}")
        rendered = json.dumps(workflow)
        self.assertNotIn("secrets.", rendered)
        self.assertNotIn("packages: write", rendered)
        self.assertNotIn("workflow_dispatch", rendered)

    def test_inputs_are_digest_pinned_and_complete(self):
        pins = json.loads(lane.PINS.read_bytes())
        self.assertEqual(pins["version"], "8.5.0")
        self.assertEqual(pins["upstream_source"], "311d6dc273fb4e9a5b8cde0de15f69472a64fe44")
        self.assertRegex(pins["core_image"], r"2026\.9\.2@sha256:[a-f0-9]{64}$")
        self.assertEqual(set(pins["images"]), {"amd64", "arm64"})
        for images in pins["images"].values():
            self.assertEqual(set(images), {"standalone", "addon"})
            for image in images.values():
                self.assertRegex(image["image"], r"@sha256:[a-f0-9]{64}$")
                for field in ("configuration", "manifest"):
                    self.assertRegex(image[field], r"^sha256:[a-f0-9]{64}$")
