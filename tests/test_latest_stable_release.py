"""Nightly latest-stable early warning: offline resolution and workflow boundaries."""

import importlib.util
from pathlib import Path
import re
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/latest-stable-early-warning.yml"
spec = importlib.util.spec_from_file_location("latest_stable_release", ROOT / "scripts/latest_stable_release.py")
latest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(latest)
window_spec = importlib.util.spec_from_file_location("support_window", ROOT / "scripts/support_window.py")
support_window = importlib.util.module_from_spec(window_spec)
window_spec.loader.exec_module(support_window)

DIGEST = "sha256:" + "a" * 64


def release(tag, draft=False, prerelease=False):
    return {"tag_name": tag, "draft": draft, "prerelease": prerelease}


class StableResolutionTests(unittest.TestCase):
    def setUp(self):
        self.window = support_window.load()

    def test_plain_stable_tags_are_accepted(self):
        self.assertEqual(latest.stable_version(release("2026.10.1"), "core"), "2026.10.1")
        self.assertEqual(latest.stable_version(release("v8.6.0"), "ha_mcp"), "8.6.0")

    def test_prereleases_drafts_and_suffixed_tags_are_refused(self):
        refused = (
            (release("2026.11.0b0", prerelease=True), "core_not_stable"),
            (release("2026.11.0", draft=True), "core_not_stable"),
            (release("2026.11.0b0"), "core_tag_not_plain_release"),
            (release("2026.11.0rc1"), "core_tag_not_plain_release"),
            (release("2026.11.0.dev1"), "core_tag_not_plain_release"),
            (release(None), "core_tag_not_plain_release"),
            ({"tag_name": "2026.11.0"}, "core_not_stable"),
            ([], "core_release_malformed"),
        )
        for payload, category in refused:
            with self.subTest(payload=payload):
                with self.assertRaisesRegex(latest.Refusal, "^" + category + "$"):
                    latest.stable_version(payload, "core")

    def test_classification_against_window(self):
        # Every non-empty slot classifies as itself; an empty candidate has none.
        for slot in support_window.core_slots(self.window, support_window.SLOTS):
            with self.subTest(slot=slot):
                version = self.window["core"][slot]["version"]
                self.assertEqual(latest.classify(version, self.window["core"]), slot)
        self.assertEqual(latest.classify("2026.11.0", self.window["core"]), "newer_than_window")
        self.assertEqual(latest.classify("2026.10.10", self.window["core"]), "newer_than_window")
        self.assertEqual(latest.classify("2026.9.3", self.window["core"]), "older_than_window")
        self.assertEqual(latest.classify("8.7.0", self.window["ha_mcp"]), "newer_than_window")

    def test_summary_pins_newest_core_with_current_reviewed_ha_mcp(self):
        result = latest.summary(self.window, "2026.11.0", "8.6.0", DIGEST, DIGEST)
        self.assertFalse(result["inside_window"])
        # 8.6.0 holds a window slot only while one declares it; with an empty
        # ha-mcp candidate (HOLD #2695, HAMCP-173) it is newer than the window.
        held = [slot for slot in support_window.ha_mcp_slots(self.window, support_window.SLOTS)
                if self.window["ha_mcp"][slot]["version"] == "8.6.0"]
        self.assertEqual(result["ha_mcp"]["window_slot"], held[0] if held else "newer_than_window")
        [row] = result["real_ha"]["include"]
        current = self.window["ha_mcp"]["current"]
        self.assertEqual(row["ha_image"], "ghcr.io/home-assistant/home-assistant:2026.11.0@" + DIGEST)
        self.assertEqual(row["ha_mcp_version"], current["version"])
        self.assertEqual(row["ha_mcp_source_commit"], current["source_commit"])
        self.assertEqual(row["ha_mcp_source_sha256"], current["source_archive_sha256"])
        newest = max((self.window["core"][slot]["version"]
                      for slot in support_window.core_slots(self.window, support_window.SLOTS)), key=latest.key)
        self.assertTrue(latest.summary(self.window, newest, "8.5.0", DIGEST, DIGEST)["inside_window"])

    def test_unpinned_core_image_is_refused(self):
        for digest in (None, "", "sha256:abc", "latest"):
            with self.subTest(digest=digest):
                with self.assertRaisesRegex(latest.Refusal, "core_digest_invalid"):
                    latest.real_ha_row(self.window, "2026.11.0", digest, "newer_than_window")

    def test_resolution_never_writes_window_or_registry_state(self):
        source = (ROOT / "scripts/latest_stable_release.py").read_text(encoding="utf-8")
        self.assertNotIn("write_text", source)
        self.assertNotIn("write_bytes", source)
        self.assertEqual(re.findall(r"\.open\(", source), [".open("])  # only the GitHub output file
        self.assertNotIn("registry", source.replace("signed registries", ""))


class EarlyWarningWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")
        cls.workflow = yaml.safe_load(cls.text)

    def test_nightly_and_manual_only_and_read_only(self):
        triggers = self.workflow[True]
        self.assertEqual(set(triggers), {"schedule", "workflow_dispatch"})
        self.assertEqual(self.workflow["permissions"], {"contents": "read"})
        for job in self.workflow["jobs"].values():
            self.assertNotIn("permissions", job)
            self.assertLessEqual(job["timeout-minutes"], 50)

    def test_not_a_required_check_and_not_part_of_ci(self):
        self.assertNotIn("validate", self.workflow["jobs"])
        ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertNotIn("latest_stable_release", ci)
        self.assertNotIn("latest-stable-early-warning", ci)

    def test_actions_and_images_are_pinned_and_nothing_is_published(self):
        for use in re.findall(r"uses:\s*(\S+)", self.text):
            self.assertRegex(use, r"^actions/[a-z-]+@[0-9a-f]{40}$")
        self.assertRegex(self.text, r"HA_FIXTURE_WRITER_IMAGE: \"[^\"]+@sha256:[0-9a-f]{64}\"")
        for forbidden in ("docker push", "docker login", "upload-artifact", "attest", "cosign",
                          "gh release", "secrets.", "packages: write", "contents: write",
                          "support-window.yaml >", "git push", "git commit"):
            self.assertNotIn(forbidden, self.text)

    def test_contract_lane_uses_resolved_digest_and_always_cleans_up(self):
        job = self.workflow["jobs"]["latest-stable-real-ha"]
        self.assertEqual(job["strategy"]["matrix"], "${{ fromJSON(needs.resolve_latest_stable.outputs.real_ha) }}")
        self.assertEqual(job["env"]["HA_CONTRACT_IMAGE"], "${{ matrix.ha_image }}")
        cleanup = job["steps"][-1]
        self.assertEqual(cleanup["if"], "always()")
        self.assertIn("docker network rm", cleanup["run"])
        self.assertIn("sha256sum --check --strict", self.text)

    def test_report_fails_on_out_of_window_or_contract_failure(self):
        report = self.workflow["jobs"]["report_latest_stable"]
        self.assertEqual(report["if"], "${{ always() }}")
        run = report["steps"][0]["run"]
        self.assertIn('test "$RESOLVE_RESULT" = success', run)
        self.assertIn('test "$CONTRACT_RESULT" = success', run)
        self.assertIn("inside_window", run)


if __name__ == "__main__":
    unittest.main()
