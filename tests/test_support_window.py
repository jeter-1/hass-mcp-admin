"""Offline controls for the CI support window and its matrix derivation.

``ci/support-window.yaml`` is the only file edited on rotation; every
compatibility lane matrix in ``.github/workflows/ci.yml`` is derived from it by
``scripts/support_window.py``. These tests load the committed window, check the
derived rows for both tiers, and prove that malformed or unsupported windows
refuse instead of producing a matrix. Nothing here reads admission, registry or
HOLD state beyond the committed reviewed registry, and nothing touches a network.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tests") not in sys.path:
    sys.path.append(str(ROOT / "tests"))
from support.support_window_matrix import registry_matrix, support_window  # noqa: E402

SCRIPT = ROOT / "scripts" / "support_window.py"
WINDOW = ROOT / "ci" / "support-window.yaml"
CORE_IMAGE = re.compile(
    r"^ghcr\.io/home-assistant/home-assistant:[0-9]{4}\.[0-9]{1,2}\.[0-9]{1,3}@sha256:[0-9a-f]{64}$"
)
HA_MCP_IMAGE = re.compile(r"^ghcr\.io/homeassistant-ai/ha-mcp:8\.5\.0@sha256:[0-9a-f]{64}$")


class SupportWindowFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sw = support_window()
        cls.window = cls.sw.load(ROOT)
        cls.registry = registry_matrix()

    def derive(self, tier):
        return {
            "real_ha": self.sw.real_ha_matrix(self.window, tier)["include"],
            "addon": self.sw.addon_matrix(self.window, tier)["include"],
            "gateway": self.sw.gateway_matrix(self.window, tier, self.registry)["include"],
        }


class CommittedWindowTests(SupportWindowFixture):
    def test_committed_window_loads_with_three_distinct_slots(self):
        self.assertEqual(self.window["schema_version"], 1)
        self.assertEqual(
            {slot: self.window["core"][slot]["version"] for slot in self.sw.SLOTS},
            {"current": "2026.10.0", "candidate": "2026.10.1", "rollback": "2026.9.4"},
        )
        self.assertEqual(
            {slot: self.window["ha_mcp"][slot]["version"] for slot in self.sw.SLOTS},
            {"current": "8.5.0", "candidate": "8.6.0", "rollback": "8.4.3"},
        )

    def test_window_header_documents_rotation(self):
        header = WINDOW.read_text(encoding="utf-8").split("schema_version:", 1)[0]
        self.assertIn("Rotation", header)
        self.assertTrue(all(line.startswith("#") or not line.strip() for line in header.splitlines()))

    def test_tier_for_maps_events_and_refuses_invalid_strings(self):
        self.assertEqual(self.sw.tier_for("pull_request"), "pr")
        for event in ("schedule", "workflow_dispatch", "push", "workflow_run"):
            with self.subTest(event=event):
                self.assertEqual(self.sw.tier_for(event), "full")
        for event in (None, "", "Pull_Request", "pull-request", "pull_request\n", "a" * 65, 1, ["pull_request"]):
            with self.subTest(event=event), self.assertRaisesRegex(self.sw.Refusal, "^event_invalid$"):
                self.sw.tier_for(event)

    def test_pr_tier_rows(self):
        rows = self.derive("pr")
        self.assertEqual(
            [(r["support_slot"], r["ha_version"], r["ha_mcp_version"]) for r in rows["real_ha"]],
            [("current", "2026.10.0", "8.5.0"), ("candidate", "2026.10.1", "8.5.0")],
        )
        self.assertEqual(
            [(r["upstream_version"], r["support_slot"], r["architecture"], r["candidate_profile"],
              r["candidate_core_version"], r["candidate_resource_code"], r["runner"]) for r in rows["addon"]],
            [
                ("8.5.0", "current", "amd64", "legacy", "2026.10.0", "850c10", "ubuntu-latest"),
                ("8.5.0", "current", "arm64", "legacy", "2026.10.0", "850c10", "ubuntu-24.04-arm"),
                ("8.6.0", "candidate", "amd64", "reference", "2026.10.0", "860c10r", "ubuntu-latest"),
                ("8.6.0", "candidate", "arm64", "reference", "2026.10.0", "860c10r", "ubuntu-24.04-arm"),
                ("8.6.0", "candidate", "amd64", "component", "2026.10.0", "860c10c", "ubuntu-latest"),
                ("8.6.0", "candidate", "arm64", "component", "2026.10.0", "860c10c", "ubuntu-24.04-arm"),
            ],
        )
        self.assertTrue(all(r["candidate_power"] is True for r in rows["addon"]))
        by_version = {r["upstream_version"]: r for r in self.registry["include"]}
        self.assertEqual([(r["upstream_version"], r["support_slot"]) for r in rows["gateway"]],
                         [("8.5.0", "current"), ("8.6.0", "candidate")])
        for row in rows["gateway"]:
            # Gateway rows are the reviewed registry rows verbatim, plus the slot.
            self.assertEqual({k: v for k, v in row.items() if k != "support_slot"},
                             by_version[row["upstream_version"]])
            self.assertEqual(row["image_index_digest"],
                             self.window["ha_mcp"][row["support_slot"]]["image_index_digest"])

    def test_full_tier_adds_exactly_the_rollback_slot(self):
        pr, full = self.derive("pr"), self.derive("full")
        for kind in ("real_ha", "addon", "gateway"):
            with self.subTest(kind=kind):
                self.assertEqual(full[kind][: len(pr[kind])], pr[kind])
                self.assertEqual({r["support_slot"] for r in full[kind][len(pr[kind]):]}, {"rollback"})
        self.assertEqual([r["ha_version"] for r in full["real_ha"][2:]], ["2026.9.4"])
        self.assertEqual([r["upstream_version"] for r in full["gateway"][2:]], ["8.4.3"])
        self.assertEqual(full["addon"][6:], [{
            "upstream_version": "8.4.3",
            "support_slot": "rollback",
            "addon_detail_profile": "live-8.4.3",
            "secret_path": "/beta58-synthetic/mcp",
            "addon_amd64_index_digest": "sha256:366361a088089f3cb55b51318d86eeb968f3deb913a41397d741075da879c2fb",
            "addon_amd64_manifest_digest": "sha256:13ed061af93d539b0872564ea340e4e683b5d437e5a520a3ac5d4e9732442b32",
            "addon_arm64_index_digest": "sha256:90e850213882d6c83dd4b2076546d34873b90e83800eadaccd94558936dd74b3",
            "addon_arm64_manifest_digest": "sha256:a51107e1a1f1f9dec9c52b9a9e714362a3f2374e80613d8c4810e8f54e43c41e",
        }])

    def test_every_real_ha_image_ref_is_tag_and_digest(self):
        for tier in ("pr", "full"):
            for row in self.derive(tier)["real_ha"]:
                with self.subTest(tier=tier, version=row["ha_version"]):
                    self.assertRegex(row["ha_image"], CORE_IMAGE)
                    self.assertIn(":" + row["ha_version"] + "@", row["ha_image"])
                    self.assertRegex(row["ha_mcp_image"], HA_MCP_IMAGE)
                    self.assertRegex(row["ha_mcp_source_commit"], r"^[0-9a-f]{40}$")
                    self.assertRegex(row["ha_mcp_source_sha256"], r"^[0-9a-f]{64}$")
                    self.assertEqual(row["lane"], "ha-" + row["ha_version"].replace(".", "-"))

    def test_unknown_tier_is_not_derivable(self):
        with self.assertRaises(KeyError):
            self.sw.real_ha_matrix(self.window, "nightly")

    def test_alarmo_bound_core_stays_in_window_until_explicitly_rotated(self):
        # Alarmo acceptance is bound to one Core by its own evidence model and
        # ci.yml gates its steps on that exact version. Rotating that Core out
        # of the window must fail here rather than silently drop the lane.
        ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        gated = set(re.findall(r"matrix\.ha_version == '([0-9.]+)'", ci))
        self.assertEqual(gated, {"2026.9.4"})
        acceptance = (ROOT / "scripts/alarmo_inspection_contract_acceptance.py").read_text(encoding="utf-8")
        self.assertIn('core_version"] == "2026.9.4"', acceptance)
        in_window = {self.window["core"][slot]["version"] for slot in self.sw.SLOTS}
        self.assertTrue(gated <= in_window, "Alarmo-bound Core left the window; re-bind or retire Alarmo explicitly")


class RefusalTests(SupportWindowFixture):
    """Every malformed window refuses; ``load(root)`` reads a temp repo copy."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "ci").mkdir()
        (self.root / "tests" / "fixtures").mkdir(parents=True)
        shutil.copy2(WINDOW, self.root / "ci" / "support-window.yaml")
        for fixture in (ROOT / "tests" / "fixtures").glob("core_*_lane_provenance.json"):
            shutil.copy2(fixture, self.root / "tests" / "fixtures" / fixture.name)
        self.base = yaml.safe_load(WINDOW.read_text(encoding="utf-8"))

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, window):
        (self.root / "ci" / "support-window.yaml").write_text(yaml.safe_dump(window, sort_keys=False),
                                                              encoding="utf-8")

    def assertRefuses(self, category):
        with self.assertRaises(self.sw.Refusal) as caught:
            self.sw.load(self.root)
        self.assertEqual(str(caught.exception), category)

    def mutate(self, change):
        window = copy.deepcopy(self.base)
        change(window)
        self.write(window)
        return window

    def test_unchanged_copy_loads(self):
        self.assertEqual(self.sw.load(self.root), self.window)

    def test_missing_window_refuses(self):
        (self.root / "ci" / "support-window.yaml").unlink()
        self.assertRefuses("window_unavailable")

    def test_duplicate_key_refuses(self):
        path = self.root / "ci" / "support-window.yaml"
        path.write_text(path.read_text(encoding="utf-8") + "schema_version: 1\n", encoding="utf-8")
        self.assertRefuses("duplicate_key")
        text = WINDOW.read_text(encoding="utf-8").replace(
            '    version: "2026.10.0"\n', '    version: "2026.10.0"\n    version: "2026.10.0"\n', 1)
        path.write_text(text, encoding="utf-8")
        self.assertRefuses("duplicate_key")

    def test_schema_and_missing_slot_refuse(self):
        self.mutate(lambda w: w.update(schema_version=2))
        self.assertRefuses("window_schema")
        self.mutate(lambda w: w.update(extra={}))
        self.assertRefuses("window_schema")
        self.mutate(lambda w: w["core"].pop("rollback"))
        self.assertRefuses("core_slots")
        self.mutate(lambda w: w["ha_mcp"].pop("candidate"))
        self.assertRefuses("ha_mcp_slots")
        self.mutate(lambda w: w["core"].update(candidate=None))
        self.assertRefuses("core_candidate_keys")
        self.mutate(lambda w: w["core"]["current"].update(tag="latest"))
        self.assertRefuses("core_current_keys")

    def test_bad_version_digest_commit_and_archive_refuse(self):
        self.mutate(lambda w: w["core"]["current"].update(version="latest"))
        self.assertRefuses("core_current_version")
        for digest in ("sha256:" + "A" * 64, "sha256:" + "a" * 63, "a" * 64, "latest", 1):
            with self.subTest(digest=digest):
                self.mutate(lambda w: w["core"]["rollback"].update(image_index_digest=digest))
                self.assertRefuses("core_rollback_digest")
                self.mutate(lambda w: w["ha_mcp"]["candidate"].update(image_index_digest=digest))
                self.assertRefuses("ha_mcp_candidate_digest")
        self.mutate(lambda w: w["ha_mcp"]["current"].update(source_commit="main"))
        self.assertRefuses("ha_mcp_current_source")
        self.mutate(lambda w: w["ha_mcp"]["rollback"].update(source_archive_sha256="x" * 64))
        self.assertRefuses("ha_mcp_rollback_archive")
        self.mutate(lambda w: w["ha_mcp"]["current"].pop("source_archive_sha256"))
        self.assertRefuses("ha_mcp_current_archive_required")

    def test_non_distinct_versions_refuse(self):
        self.mutate(lambda w: w["core"]["candidate"].update(w["core"]["current"]))
        self.assertRefuses("core_slot_versions_not_distinct")
        self.mutate(lambda w: w["ha_mcp"]["candidate"].update(version="8.5.0"))
        self.assertRefuses("ha_mcp_slot_versions_not_distinct")

    def test_addon_runtime_lanes_refuse_when_malformed(self):
        def power(**fields):
            return lambda w: w["ha_mcp"]["current"]["addon_runtime"][0].update(fields)

        self.mutate(lambda w: w["ha_mcp"]["current"].update(addon_runtime=[]))
        self.assertRefuses("ha_mcp_current_addon_runtime")
        self.mutate(power(kind="arbitrary"))
        self.assertRefuses("addon_runtime_kind")
        for architectures in (["riscv64"], [], ["amd64", "amd64"], "amd64"):
            with self.subTest(architectures=architectures):
                self.mutate(power(architectures=architectures))
                self.assertRefuses("addon_power_lane")
        self.mutate(power(runner="self-hosted"))
        self.assertRefuses("addon_power_lane")

        def live(**fields):
            return lambda w: w["ha_mcp"]["rollback"]["addon_runtime"][0].update(fields)

        for fields in ({"addon_detail_profile": "live-8.5.0"}, {"secret_path": "/../mcp"},
                       {"addon_arm64_index_digest": "latest"}, {"image": "ha-mcp:latest"}):
            with self.subTest(fields=fields):
                self.mutate(live(**fields))
                self.assertRefuses("addon_live_lane")

    def test_core_provenance_missing_or_mismatched_refuses(self):
        (self.root / "tests" / "fixtures" / "core_2026_10_1_lane_provenance.json").unlink()
        self.assertRefuses("core_candidate_lane_provenance_missing")
        shutil.copy2(ROOT / "tests" / "fixtures" / "core_2026_10_1_lane_provenance.json",
                     self.root / "tests" / "fixtures")
        self.mutate(lambda w: w["core"]["current"].update(image_index_digest="sha256:" + "0" * 64))
        self.assertRefuses("core_current_provenance_mismatch")
        self.write(self.base)
        fixture = self.root / "tests" / "fixtures" / "core_2026_9_4_lane_provenance.json"
        entry = json.loads(fixture.read_text(encoding="utf-8"))
        entry["version"] = "2026.9.3"
        fixture.write_text(json.dumps(entry), encoding="utf-8")
        self.assertRefuses("core_rollback_provenance_mismatch")

    def test_core_version_without_provenance_fixture_refuses(self):
        self.mutate(lambda w: w["core"]["candidate"].update(version="2026.11.0"))
        self.assertRefuses("core_candidate_lane_provenance_missing")

    def test_registry_cross_check_refuses(self):
        window = self.window
        with self.assertRaisesRegex(self.sw.Refusal, "^registry_matrix_malformed$"):
            self.sw.gateway_matrix(window, "pr", {"rows": []})
        with self.assertRaisesRegex(self.sw.Refusal, "^registry_matrix_malformed$"):
            self.sw.gateway_matrix(window, "pr", [])
        duplicated = {"include": self.registry["include"] + self.registry["include"][:1]}
        with self.assertRaisesRegex(self.sw.Refusal, "^registry_matrix_duplicate$"):
            self.sw.gateway_matrix(window, "pr", duplicated)
        absent = {"include": [r for r in self.registry["include"] if r["upstream_version"] != "8.6.0"]}
        with self.assertRaisesRegex(self.sw.Refusal, "^ha_mcp_candidate_not_in_reviewed_registry$"):
            self.sw.gateway_matrix(window, "pr", absent)
        # Rollback is only cross-checked on the tier that runs it.
        no_rollback = {"include": [r for r in self.registry["include"] if r["upstream_version"] != "8.4.3"]}
        self.assertEqual(len(self.sw.gateway_matrix(window, "pr", no_rollback)["include"]), 2)
        with self.assertRaisesRegex(self.sw.Refusal, "^ha_mcp_rollback_not_in_reviewed_registry$"):
            self.sw.gateway_matrix(window, "full", no_rollback)
        for field, value in (("image_index_digest", "sha256:" + "0" * 64), ("source_commit", "0" * 40)):
            with self.subTest(field=field):
                tampered = copy.deepcopy(self.registry)
                next(r for r in tampered["include"] if r["upstream_version"] == "8.5.0")[field] = value
                with self.assertRaisesRegex(self.sw.Refusal, "^ha_mcp_current_registry_mismatch$"):
                    self.sw.gateway_matrix(window, "pr", tampered)

    def test_unsupported_addon_core_pair_refuses(self):
        # core.current 2026.9.2 is a real, provenance-backed Core, but the
        # acceptance script does not pair ha-mcp 8.6.0 with it.
        self.assertTrue((self.root / "tests" / "fixtures" / "core_2026_9_2_lane_provenance.json").is_file())
        provenance = json.loads(
            (ROOT / "tests" / "fixtures" / "core_2026_9_2_lane_provenance.json").read_text(encoding="utf-8"))
        window = self.mutate(lambda w: w["core"]["current"].update(
            version="2026.9.2", image_index_digest=provenance["image_index_digest"]))
        loaded = self.sw.load(self.root)
        self.assertEqual(loaded["core"]["current"], window["core"]["current"])
        with self.assertRaisesRegex(self.sw.Refusal, "^addon_candidate_pair_unsupported$"):
            self.sw.addon_matrix(loaded, "pr")

    def test_output_newline_refuses(self):
        path = self.root / "github-output"
        with self.assertRaisesRegex(self.sw.Refusal, "^output_newline$"):
            self.sw.write_outputs(path, {"tier": "pr\nmatrix={}"})


class CommandLineTests(unittest.TestCase):
    def run_cli(self, *args, env=None):
        return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=ROOT, capture_output=True,
                              text=True, timeout=120, env=env)

    def test_pull_request_event_writes_compact_single_line_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "github-output"
            result = self.run_cli("--event", "pull_request", "--github-output", str(output))
            self.assertEqual(result.returncode, 0, result.stderr)
            lines = output.read_text(encoding="utf-8").splitlines()
        self.assertEqual([line.split("=", 1)[0] for line in lines], ["tier", "real_ha", "addon"])
        values = dict(line.split("=", 1) for line in lines)
        self.assertEqual(values["tier"], "pr")
        for key in ("real_ha", "addon"):
            parsed = json.loads(values[key])
            self.assertEqual(values[key], json.dumps(parsed, sort_keys=True, separators=(",", ":")))
            self.assertEqual({row["support_slot"] for row in parsed["include"]}, {"current", "candidate"})

    def test_registry_matrix_adds_gateway_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = Path(tmp) / "registry.json"
            registry.write_text(json.dumps(registry_matrix()), encoding="utf-8")
            output = Path(tmp) / "github-output"
            result = self.run_cli("--event", "schedule", "--registry-matrix", str(registry),
                                  "--github-output", str(output))
            self.assertEqual(result.returncode, 0, result.stderr)
            values = dict(line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines())
        self.assertEqual(values["tier"], "full")
        self.assertEqual([row["upstream_version"] for row in json.loads(values["gateway"])["include"]],
                         ["8.5.0", "8.6.0", "8.4.3"])

    def test_refusals_exit_one_without_writing_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "github-output"
            malformed = Path(tmp) / "registry.json"
            malformed.write_text('{"rows": []}', encoding="utf-8")
            for args, category in (
                (("--event", "Pull Request"), "event_invalid"),
                (("--event", "pull_request\nmalicious=1"), "event_invalid"),
                (("--event", "pull_request", "--registry-matrix", str(malformed)), "registry_matrix_malformed"),
                (("--event", "pull_request", "--registry-matrix", str(Path(tmp) / "missing.json")),
                 "window_unreadable"),
            ):
                with self.subTest(args=args):
                    result = self.run_cli(*args, "--github-output", str(output))
                    self.assertEqual(result.returncode, 1)
                    self.assertEqual(result.stderr, "Support window refused: " + category + "\n")
                    self.assertFalse(output.exists())

    def test_missing_event_refuses(self):
        env = {k: v for k, v in os.environ.items() if k != "GITHUB_EVENT_NAME"}
        result = self.run_cli(env=env)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, "Support window refused: event_invalid\n")


if __name__ == "__main__":
    unittest.main()
