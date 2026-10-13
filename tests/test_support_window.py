"""Offline controls for the CI support window and its matrix derivation.

Rotation among Cores already in the window edits only ``ci/support-window.yaml``;
a brand-new Core first needs its exact binding in the one reviewed list,
``scripts/core_lane_bindings.py`` (contract amendment, Josh 2026-10-11). Every
compatibility lane matrix in ``.github/workflows/ci.yml`` is derived from it by
``scripts/support_window.py``. These tests load the committed window, check the
derived rows for both tiers, and prove that malformed or unsupported windows
refuse instead of producing a matrix. Nothing here reads admission, registry or
HOLD state beyond the committed reviewed registry, and nothing touches a network.
"""

from __future__ import annotations

import copy
import dataclasses
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

from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tests") not in sys.path:
    sys.path.append(str(ROOT / "tests"))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))
from support.support_window_matrix import registry_matrix, support_window  # noqa: E402
import core_lane_bindings  # noqa: E402
from support import core_bindings  # noqa: E402

SCRIPT = ROOT / "scripts" / "support_window.py"
WINDOW = ROOT / "ci" / "support-window.yaml"
CORE_IMAGE = re.compile(
    r"^ghcr\.io/home-assistant/home-assistant:[0-9]{4}\.[0-9]{1,2}\.[0-9]{1,3}@sha256:[0-9a-f]{64}$"
)
HA_MCP_IMAGE = re.compile(r"^ghcr\.io/homeassistant-ai/ha-mcp:8\.5\.0@sha256:[0-9a-f]{64}$")
# 8.6.0 stays in the reviewed registry while on production HOLD (#2695); tests
# that need a populated ha-mcp candidate copy its exact pin from that registry.
HELD_HA_MCP = "8.6.0"


def held_ha_mcp_candidate(registry, **fields):
    """A populated ha-mcp candidate pin copied from the reviewed registry (test-only)."""

    row = next(r for r in registry["include"] if r["upstream_version"] == HELD_HA_MCP)
    return {"version": row["upstream_version"], "image_index_digest": row["image_index_digest"],
            "source_commit": row["source_commit"],
            "addon_runtime": [{"kind": "candidate_power", "profile": "reference", "architectures": ["amd64", "arm64"]}],
            **fields}


def expected_addon_rows(sw, window, tier, codes):
    """Add-on rows from the window's non-empty ha-mcp slots and the acceptance script's pairs."""

    core = window["core"]["current"]["version"]
    rows = []
    for slot in sw.ha_mcp_slots(window, sw.TIERS[tier]):
        pin = window["ha_mcp"][slot]
        for lane in pin["addon_runtime"]:
            if lane["kind"] != "candidate_power":
                continue
            resource = (codes.version_code(pin["version"]) + codes.core_code(core, pin["version"])
                        + codes.profile_code(pin["version"], lane["profile"]))
            rows += [(pin["version"], slot, architecture, lane["profile"], core, resource, sw.RUNNERS[architecture])
                     for architecture in lane["architectures"]]
    return rows


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
    def test_committed_window_loads_with_distinct_reviewed_slots(self):
        # Expectations follow the committed window's non-empty Core slots, so an
        # empty candidate or a rotation among reviewed Cores edits no test.
        self.assertEqual(self.window["schema_version"], 1)
        slots = self.sw.core_slots(self.window, self.sw.SLOTS)
        self.assertIn("current", slots)
        self.assertIn("rollback", slots)
        self.assertEqual(set(self.sw.SLOTS) - set(slots), {s for s in self.sw.SLOTS if self.window["core"][s] is None})
        self.assertLessEqual(set(self.sw.SLOTS) - set(slots), {"candidate"})
        versions = [self.window["core"][slot]["version"] for slot in slots]
        self.assertEqual(len(set(versions)), len(versions))
        for slot in slots:
            core = self.window["core"][slot]
            binding = core_lane_bindings.reviewed_lane_binding(core["version"])
            with self.subTest(slot=slot):
                self.assertIsNotNone(binding)
                self.assertEqual(binding.image_index_digest, core["image_index_digest"])
        # The same holds for ha-mcp: current and rollback are always present and
        # only the candidate may be empty (HAMCP-173).
        ha_mcp_slots = self.sw.ha_mcp_slots(self.window, self.sw.SLOTS)
        self.assertIn("current", ha_mcp_slots)
        self.assertIn("rollback", ha_mcp_slots)
        self.assertLessEqual(set(self.sw.SLOTS) - set(ha_mcp_slots), {"candidate"})
        ha_mcp = [self.window["ha_mcp"][slot]["version"] for slot in ha_mcp_slots]
        self.assertEqual(len(set(ha_mcp)), len(ha_mcp))
        for slot in ha_mcp_slots:
            with self.subTest(slot=slot):
                self.assertTrue(self.window["ha_mcp"][slot]["addon_runtime"])

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
        ha_mcp = self.window["ha_mcp"]["current"]["version"]
        self.assertEqual(
            [(r["support_slot"], r["ha_version"], r["ha_mcp_version"]) for r in rows["real_ha"]],
            [(slot, self.window["core"][slot]["version"], ha_mcp)
             for slot in self.sw.core_slots(self.window, ("current", "candidate"))],
        )
        # Add-on rows pair the non-empty ha-mcp slots with core.current through
        # the add-on acceptance script's own pairing authority; an empty Core or
        # ha-mcp candidate adds no row.
        self.assertEqual(
            [(r["upstream_version"], r["support_slot"], r["architecture"], r["candidate_profile"],
              r["candidate_core_version"], r["candidate_resource_code"], r["runner"]) for r in rows["addon"]],
            expected_addon_rows(self.sw, self.window, "pr", self.sw.acceptance_codes()),
        )
        self.assertTrue(rows["addon"])
        self.assertTrue(all(r["candidate_power"] is True for r in rows["addon"]))
        by_version = {r["upstream_version"]: r for r in self.registry["include"]}
        self.assertEqual([(r["upstream_version"], r["support_slot"]) for r in rows["gateway"]],
                         [(self.window["ha_mcp"][slot]["version"], slot)
                          for slot in self.sw.ha_mcp_slots(self.window, ("current", "candidate"))])
        if self.window["ha_mcp"]["candidate"] is None:
            for kind in ("addon", "gateway"):
                with self.subTest(kind=kind):
                    self.assertNotIn("candidate", {r["support_slot"] for r in rows[kind]})
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
        self.assertEqual([r["ha_version"] for r in full["real_ha"][len(pr["real_ha"]):]],
                         [self.window["core"]["rollback"]["version"]])
        rollback = self.window["ha_mcp"]["rollback"]
        self.assertEqual([r["upstream_version"] for r in full["gateway"][len(pr["gateway"]):]],
                         [rollback["version"]])
        self.assertEqual(full["addon"][len(pr["addon"]):], [
            {"upstream_version": rollback["version"], "support_slot": "rollback",
             **{k: v for k, v in lane.items() if k != "kind"}}
            for lane in rollback["addon_runtime"]])
        self.assertEqual(full["addon"][-1]["addon_amd64_index_digest"],
                         "sha256:366361a088089f3cb55b51318d86eeb968f3deb913a41397d741075da879c2fb")

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

    def test_alarmo_follows_every_core_slot_with_its_provenance(self):
        # HAMCP-159: Alarmo is a property of each Core slot, not of one pinned
        # Core. Every derived row runs it, bound to that slot's committed source.
        for tier in ("pr", "full"):
            for row in self.sw.real_ha_matrix(self.window, tier)["include"]:
                fixture = ROOT / "tests" / "fixtures" / (
                    "core_" + row["ha_version"].replace(".", "_") + "_lane_provenance.json")
                provenance = json.loads(fixture.read_text(encoding="utf-8"))
                with self.subTest(tier=tier, slot=row["support_slot"]):
                    self.assertIs(row["alarmo"], True)
                    self.assertEqual(row["ha_version"], self.window["core"][row["support_slot"]]["version"])
                    self.assertEqual(row["core_source_commit"], provenance["source_commit"])
                    self.assertEqual(row["core_source_sha256"], provenance["source_archive_sha256"])
                    self.assertRegex(row["core_source_commit"], r"^[0-9a-f]{40}$")
                    self.assertRegex(row["core_source_sha256"], r"^[0-9a-f]{64}$")
                    self.assertIn("@" + provenance["image_index_digest"], row["ha_image"])

    def test_alarmo_slot_binds_exactly_each_window_core(self):
        self.assertEqual(self.sw.ALARMO_SLOTS, self.sw.SLOTS)
        for slot in self.sw.core_slots(self.window, self.sw.SLOTS):
            core = self.window["core"][slot]
            provenance = json.loads((ROOT / "tests" / "fixtures" / (
                "core_" + core["version"].replace(".", "_") + "_lane_provenance.json")).read_text(encoding="utf-8"))
            with self.subTest(slot=slot):
                self.assertEqual(self.sw.alarmo_slot(core["version"], ROOT), {
                    "slot": slot, "version": core["version"], "lane": "ha-" + core["version"].replace(".", "-"),
                    "image": f"ghcr.io/home-assistant/home-assistant:{core['version']}@{core['image_index_digest']}",
                    "image_index_digest": core["image_index_digest"],
                    "source_commit": provenance["source_commit"],
                    "source_archive_sha256": provenance["source_archive_sha256"],
                    "provenance": provenance})

    def test_alarmo_slot_refuses_unknown_prefix_family_and_non_string(self):
        # 2026.9.2 and 2026.9.3 have committed provenance but are not window slots.
        for version in ("2026.9.3", "2026.9.2", "2026.11.0", "2026.10", "2026.10.", "2026.10.*", "2026.9",
                        "2026.9.*", "2026", "", " 2026.10.0", "2026.10.0 ", "2026.10.0\n", "v2026.10.0",
                        "2026.10.00", "ha-2026-10-0", None, 2026.9, 2026, b"2026.10.0", ["2026.10.0"],
                        {"version": "2026.10.0"}):
            with self.subTest(version=version), self.assertRaisesRegex(self.sw.Refusal,
                                                                       "^alarmo_core_not_in_window_slot$"):
                self.sw.alarmo_slot(version, ROOT)


class TempRootFixture(SupportWindowFixture):
    """``load(root)`` reads a temp copy of the window and Core provenance fixtures."""

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



class RefusalTests(TempRootFixture):
    """Every malformed window refuses."""

    def test_unchanged_copy_loads(self):
        self.assertEqual(self.sw.load(self.root), self.window)

    def test_missing_window_refuses(self):
        (self.root / "ci" / "support-window.yaml").unlink()
        self.assertRefuses("window_unavailable")

    def test_duplicate_key_refuses(self):
        path = self.root / "ci" / "support-window.yaml"
        path.write_text(path.read_text(encoding="utf-8") + "schema_version: 1\n", encoding="utf-8")
        self.assertRefuses("duplicate_key")
        line = '    version: "' + self.base["core"]["current"]["version"] + '"\n'
        text = WINDOW.read_text(encoding="utf-8").replace(line, line + line, 1)
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
        # Only candidates may be empty (explicit null); see EmptyCandidateTests.
        for slot in ("current", "rollback"):
            with self.subTest(slot=slot):
                self.mutate(lambda w: w["core"].update({slot: None}))
                self.assertRefuses("core_" + slot + "_keys")
                self.mutate(lambda w: w["ha_mcp"].update({slot: None}))
                self.assertRefuses("ha_mcp_" + slot + "_keys")
        self.mutate(lambda w: w["core"].pop("candidate"))
        self.assertRefuses("core_slots")
        for value in ({}, [], "", "null", False):
            with self.subTest(candidate=value):
                self.mutate(lambda w: w["core"].update(candidate=value))
                self.assertRefuses("core_candidate_keys")
                self.mutate(lambda w: w["ha_mcp"].update(candidate=value))
                self.assertRefuses("ha_mcp_candidate_keys")
        self.mutate(lambda w: w["core"]["current"].update(tag="latest"))
        self.assertRefuses("core_current_keys")

    def test_bad_version_digest_commit_and_archive_refuse(self):
        self.mutate(lambda w: w["core"]["current"].update(version="latest"))
        self.assertRefuses("core_current_version")
        for digest in ("sha256:" + "A" * 64, "sha256:" + "a" * 63, "a" * 64, "latest", 1):
            with self.subTest(digest=digest):
                self.mutate(lambda w: w["core"]["rollback"].update(image_index_digest=digest))
                self.assertRefuses("core_rollback_digest")
                self.mutate(lambda w: w["ha_mcp"].update(
                    candidate=held_ha_mcp_candidate(self.registry, image_index_digest=digest)))
                self.assertRefuses("ha_mcp_candidate_digest")
        self.mutate(lambda w: w["ha_mcp"]["current"].update(source_commit="main"))
        self.assertRefuses("ha_mcp_current_source")
        self.mutate(lambda w: w["ha_mcp"]["rollback"].update(source_archive_sha256="x" * 64))
        self.assertRefuses("ha_mcp_rollback_archive")
        self.mutate(lambda w: w["ha_mcp"]["current"].pop("source_archive_sha256"))
        self.assertRefuses("ha_mcp_current_archive_required")

    def test_non_distinct_versions_refuse(self):
        for slot in ("candidate", "rollback"):
            with self.subTest(slot=slot):
                self.mutate(lambda w: w["core"].update({slot: dict(w["core"]["current"])}))
                self.assertRefuses("core_slot_versions_not_distinct")
        for slot in ("candidate", "rollback"):
            with self.subTest(slot=slot):
                self.mutate(lambda w: w["ha_mcp"].update(
                    {slot: held_ha_mcp_candidate(self.registry, version=w["ha_mcp"]["current"]["version"])}))
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

    def fixture(self, slot):
        return self.root / "tests" / "fixtures" / core_lane_bindings.provenance_name(
            self.base["core"][slot]["version"])

    def test_core_provenance_missing_or_mismatched_refuses(self):
        current = self.fixture("current")
        original = current.read_text(encoding="utf-8")
        current.unlink()
        self.assertRefuses("core_current_lane_provenance_missing")
        entry = json.loads(original)
        current.write_text(json.dumps({**entry, "image_index_digest": "sha256:" + "0" * 64}), encoding="utf-8")
        self.assertRefuses("core_current_provenance_mismatch")
        current.write_text(original, encoding="utf-8")
        self.sw.load(self.root)
        # The window may not disagree with the reviewed binding, even when a
        # provenance fixture is rewritten to agree with the window.
        self.mutate(lambda w: w["core"]["current"].update(image_index_digest="sha256:" + "0" * 64))
        current.write_text(json.dumps({**entry, "image_index_digest": "sha256:" + "0" * 64}), encoding="utf-8")
        self.assertRefuses("core_current_binding_mismatch")
        current.write_text(original, encoding="utf-8")
        self.write(self.base)
        rollback = self.fixture("rollback")
        entry = json.loads(rollback.read_text(encoding="utf-8"))
        entry["version"] = "2026.9.3"
        rollback.write_text(json.dumps(entry), encoding="utf-8")
        self.assertRefuses("core_rollback_provenance_mismatch")

    def test_core_version_without_reviewed_binding_or_provenance_refuses(self):
        digest = self.base["core"]["current"]["image_index_digest"]
        # Generated to be absent from the reviewed list (HAMCP-159 R2).
        unreviewed = core_bindings.unreviewed_version("2026.11.0")
        self.mutate(lambda w: w["core"]["current"].update(version=unreviewed))
        self.assertRefuses("core_current_binding_unreviewed")
        # A reviewed binding still needs the committed lane provenance fixture.
        binding = core_lane_bindings.CoreBinding(digest, core_lane_bindings.CHILD_DEVICE_NAME_PARTS, True, "8.5.0")
        with patch.dict(core_lane_bindings.REVIEWED_CORE_BINDINGS, {unreviewed: binding}):
            self.assertRefuses("core_current_lane_provenance_missing")
        # A binding without a disposable lane can never occupy a window slot.
        for version in ("2026.9.0", "2026.9.1"):
            with self.subTest(version=version):
                self.mutate(lambda w: w["core"]["current"].update(version=version))
                self.assertIsNotNone(core_lane_bindings.reviewed_core_binding(version))
                self.assertRefuses("core_current_binding_unreviewed")

    def test_registry_cross_check_refuses(self):
        window = self.window
        with self.assertRaisesRegex(self.sw.Refusal, "^registry_matrix_malformed$"):
            self.sw.gateway_matrix(window, "pr", {"rows": []})
        with self.assertRaisesRegex(self.sw.Refusal, "^registry_matrix_malformed$"):
            self.sw.gateway_matrix(window, "pr", [])
        duplicated = {"include": self.registry["include"] + self.registry["include"][:1]}
        with self.assertRaisesRegex(self.sw.Refusal, "^registry_matrix_duplicate$"):
            self.sw.gateway_matrix(window, "pr", duplicated)
        # A populated candidate is cross-checked like any other slot.
        held = copy.deepcopy(window)
        held["ha_mcp"]["candidate"] = held_ha_mcp_candidate(self.registry)
        absent = {"include": [r for r in self.registry["include"] if r["upstream_version"] != HELD_HA_MCP]}
        with self.assertRaisesRegex(self.sw.Refusal, "^ha_mcp_candidate_not_in_reviewed_registry$"):
            self.sw.gateway_matrix(held, "pr", absent)
        held["ha_mcp"]["candidate"]["source_commit"] = "0" * 40
        with self.assertRaisesRegex(self.sw.Refusal, "^ha_mcp_candidate_registry_mismatch$"):
            self.sw.gateway_matrix(held, "pr", self.registry)
        # Rollback is only cross-checked on the tier that runs it.
        rollback = window["ha_mcp"]["rollback"]["version"]
        no_rollback = {"include": [r for r in self.registry["include"] if r["upstream_version"] != rollback]}
        self.assertEqual(len(self.sw.gateway_matrix(window, "pr", no_rollback)["include"]),
                         len(self.sw.ha_mcp_slots(window, ("current", "candidate"))))
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
        window = self.mutate(lambda w: w.update(
            core={**w["core"], "current": {"version": "2026.9.2",
                                           "image_index_digest": provenance["image_index_digest"]}},
            ha_mcp={**w["ha_mcp"], "candidate": held_ha_mcp_candidate(self.registry)}))
        loaded = self.sw.load(self.root)
        self.assertEqual(loaded["core"]["current"], window["core"]["current"])
        with self.assertRaisesRegex(self.sw.Refusal, "^addon_candidate_pair_unsupported$"):
            self.sw.addon_matrix(loaded, "pr")

    def test_held_ha_mcp_on_committed_current_core_is_not_a_reviewed_pair(self):
        # HAMCP-173: the acceptance script has no (core.current, 8.6.0) pair, so
        # re-populating the candidate with the held 8.6.0 refuses instead of
        # advertising add-on lanes the runtime gate does not admit.
        self.mutate(lambda w: w["ha_mcp"].update(candidate=held_ha_mcp_candidate(self.registry)))
        loaded = self.sw.load(self.root)
        with self.assertRaisesRegex(self.sw.Refusal, "^addon_candidate_pair_unsupported$"):
            self.sw.addon_matrix(loaded, "pr")
        codes = self.sw.acceptance_codes()
        with self.assertRaisesRegex(codes.Refusal, "^core_pair_invalid$"):
            codes.core_code(self.base["core"]["current"]["version"], HELD_HA_MCP)

    def test_output_newline_refuses(self):
        path = self.root / "github-output"
        with self.assertRaisesRegex(self.sw.Refusal, "^output_newline$"):
            self.sw.write_outputs(path, {"tier": "pr\nmatrix={}"})


    def test_alarmo_provenance_digest_mismatch_refuses(self):
        # A provenance fixture that disagrees with the window can never bind Alarmo.
        current = self.base["core"]["current"]["version"]
        fixture = self.root / "tests" / "fixtures" / ("core_" + current.replace(".", "_") + "_lane_provenance.json")
        entry = json.loads(fixture.read_text(encoding="utf-8"))
        self.assertEqual(self.sw.alarmo_slot(current, self.root)["provenance"], entry)
        for field, value in (("image_index_digest", "sha256:" + "0" * 64), ("version", "2026.10.9")):
            with self.subTest(field=field):
                fixture.write_text(json.dumps({**entry, field: value}), encoding="utf-8")
                self.assertRefuses("core_current_provenance_mismatch")
                with self.assertRaisesRegex(self.sw.Refusal, "^core_current_provenance_mismatch$"):
                    self.sw.alarmo_slot(current, self.root)
        loaded_window = copy.deepcopy(self.base)
        for field, value in (("source_commit", "main"), ("source_commit", "0" * 39),
                             ("source_archive_sha256", "A" * 64), ("source_archive_sha256", None)):
            with self.subTest(field=field, value=value):
                fixture.write_text(json.dumps({**entry, field: value}), encoding="utf-8")
                with self.assertRaisesRegex(self.sw.Refusal, "^core_current_provenance_mismatch$"):
                    self.sw.alarmo_slot(current, self.root)
                with self.assertRaisesRegex(self.sw.Refusal, "^core_current_provenance_mismatch$"):
                    self.sw.real_ha_matrix(loaded_window, "pr", self.root)


def reviewed_core(version):
    """A window Core entry copied from the one reviewed binding list."""

    return {"version": version,
            "image_index_digest": core_lane_bindings.reviewed_lane_binding(version).image_index_digest}


class RotationTests(TempRootFixture):
    """Contract amendment (Josh, 2026-10-11): rotation among reviewed Cores edits
    only the window; a brand-new Core needs one reviewed binding first."""

    NEW_BINDING = core_lane_bindings.CoreBinding(
        "sha256:" + "c" * 64, core_lane_bindings.CHILD_DEVICE_NAME_PARTS, True, "8.5.0")

    def setUp(self):
        super().setUp()
        # The synthetic brand-new Core is 2026.10.2 while that release has no
        # reviewed binding, otherwise the next patch without one, computed per
        # test so a later real binding never collides with it (HAMCP-159 R2).
        self.new_version = core_bindings.unreviewed_version("2026.10.2")
        self.NEW = {"version": self.new_version, "image_index_digest": "sha256:" + "c" * 64}
        self.NEW_PROVENANCE = {"version": self.new_version, "image_index_digest": "sha256:" + "c" * 64,
                               "source_commit": "d" * 40, "source_archive_sha256": "e" * 64,
                               "provenance": "synthetic offline rotation fixture; not a real Core"}
        self.new_fixture = self.root / "tests" / "fixtures" / core_lane_bindings.provenance_name(self.new_version)

    def rotate_existing(self):
        # (a) candidate -> current, current -> rollback, rollback dropped, no new candidate.
        return self.mutate(lambda w: w["core"].update(
            current=reviewed_core("2026.10.1"), rollback=reviewed_core("2026.10.0"), candidate=None))

    def add_new_candidate(self):
        window = self.mutate(lambda w: w["core"].update(
            current=reviewed_core("2026.10.0"), candidate=dict(self.NEW), rollback=reviewed_core("2026.9.4")))
        self.new_fixture.write_text(json.dumps(self.NEW_PROVENANCE), encoding="utf-8")
        return window

    def test_a_rotation_among_reviewed_cores_needs_only_the_window(self):
        self.rotate_existing()
        window = self.sw.load(self.root)
        expected = {"current": "2026.10.1", "rollback": "2026.10.0"}
        self.assertIsNone(window["core"]["candidate"])
        for tier, slots in (("pr", ("current",)), ("full", ("current", "rollback"))):
            rows = self.sw.real_ha_matrix(window, tier, self.root)["include"]
            with self.subTest(tier=tier):
                self.assertEqual([(r["support_slot"], r["ha_version"]) for r in rows],
                                 [(slot, expected[slot]) for slot in slots])
                self.assertTrue(all(r["alarmo"] is True for r in rows))
        for slot, version in expected.items():
            with self.subTest(slot=slot):
                binding = self.sw.alarmo_slot(version, self.root)
                self.assertEqual((binding["slot"], binding["version"]), (slot, version))
                self.assertEqual(binding["image_index_digest"],
                                 core_lane_bindings.reviewed_lane_binding(version).image_index_digest)
        # The dropped rollback Core has left the window, so Alarmo refuses it.
        with self.assertRaisesRegex(self.sw.Refusal, "^alarmo_core_not_in_window_slot$"):
            self.sw.alarmo_slot("2026.9.4", self.root)
        # HAMCP-173: the add-on acceptance pairing authority now lists each
        # non-empty ha-mcp slot with the rotated current Core, so add-on rows
        # derive on it without any other edit.
        codes = self.sw.acceptance_codes()
        for tier in ("pr", "full"):
            rows = self.sw.addon_matrix(window, tier)["include"]
            with self.subTest(tier=tier):
                self.assertEqual(
                    [(r["upstream_version"], r["support_slot"], r["architecture"], r["candidate_profile"],
                      r["candidate_core_version"], r["candidate_resource_code"], r["runner"])
                     for r in rows if r.get("candidate_power")],
                    expected_addon_rows(self.sw, window, tier, codes))
                self.assertTrue(all(r["candidate_core_version"] == expected["current"]
                                    for r in rows if r.get("candidate_power")))

    def test_b_unreviewed_new_core_is_refused_before_any_matrix(self):
        self.add_new_candidate()
        self.assertIsNone(core_lane_bindings.reviewed_core_binding(self.new_version))
        # A committed provenance fixture does not vouch for an unreviewed Core.
        self.assertRefuses("core_candidate_binding_unreviewed")
        for version in (self.new_version, "2026.10.0", "2026.9.4"):
            with self.subTest(version=version), self.assertRaisesRegex(
                    self.sw.Refusal, "^core_candidate_binding_unreviewed$"):
                self.sw.alarmo_slot(version, self.root)

    def test_b_unreviewed_new_core_command_line_writes_no_matrix(self):
        self.add_new_candidate()
        scripts = self.root / "scripts"
        scripts.mkdir()
        for name in ("support_window.py", "core_lane_bindings.py"):
            shutil.copy2(ROOT / "scripts" / name, scripts / name)
        output = self.root / "github-output"
        for event in ("pull_request", "schedule"):
            with self.subTest(event=event):
                result = subprocess.run([sys.executable, str(scripts / "support_window.py"), "--event", event,
                                         "--github-output", str(output)], cwd=self.root, capture_output=True,
                                        text=True, timeout=120)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stderr, "Support window refused: core_candidate_binding_unreviewed\n")
                self.assertEqual(result.stdout, "")
                self.assertFalse(output.exists())

    def test_c_new_core_passes_once_its_binding_is_in_the_reviewed_list(self):
        self.add_new_candidate()
        with patch.dict(core_lane_bindings.REVIEWED_CORE_BINDINGS, {self.new_version: self.NEW_BINDING}):
            window = self.sw.load(self.root)
            expected = {"current": "2026.10.0", "candidate": self.new_version, "rollback": "2026.9.4"}
            for tier, slots in (("pr", ("current", "candidate")), ("full", self.sw.SLOTS)):
                rows = self.sw.real_ha_matrix(window, tier, self.root)["include"]
                with self.subTest(tier=tier):
                    self.assertEqual([(r["support_slot"], r["ha_version"]) for r in rows],
                                     [(slot, expected[slot]) for slot in slots])
                    self.assertTrue(all(r["alarmo"] is True for r in rows))
            candidate = self.sw.real_ha_matrix(window, "pr", self.root)["include"][1]
            self.assertEqual((candidate["core_source_commit"], candidate["core_source_sha256"], candidate["lane"]),
                             ("d" * 40, "e" * 64, "ha-" + self.new_version.replace(".", "-")))
            self.assertEqual(self.sw.alarmo_slot(self.new_version, self.root)["provenance"], self.NEW_PROVENANCE)
            # A binding that disagrees with the window digest is refused.
            other = dataclasses.replace(self.NEW_BINDING, image_index_digest="sha256:" + "f" * 64)
            with patch.dict(core_lane_bindings.REVIEWED_CORE_BINDINGS, {self.new_version: other}):
                self.assertRefuses("core_candidate_binding_mismatch")
        # Removing the binding again restores the refusal: the list, not the window, decides.
        self.assertRefuses("core_candidate_binding_unreviewed")

    def test_c_reviewed_new_core_still_needs_matching_provenance(self):
        self.add_new_candidate()
        path = self.new_fixture
        with patch.dict(core_lane_bindings.REVIEWED_CORE_BINDINGS, {self.new_version: self.NEW_BINDING}):
            path.write_text(json.dumps({**self.NEW_PROVENANCE, "image_index_digest": "sha256:" + "f" * 64}),
                            encoding="utf-8")
            self.assertRefuses("core_candidate_provenance_mismatch")
            with self.assertRaisesRegex(self.sw.Refusal, "^core_candidate_provenance_mismatch$"):
                self.sw.alarmo_slot(self.new_version, self.root)
            path.unlink()
            self.assertRefuses("core_candidate_lane_provenance_missing")
            with self.assertRaisesRegex(self.sw.Refusal, "^core_candidate_lane_provenance_missing$"):
                self.sw.alarmo_slot("2026.10.0", self.root)


class EmptyCandidateTests(TempRootFixture):
    """``core.candidate: null`` removes the candidate Core lane and nothing else."""

    def test_null_core_candidate_loads_and_drops_only_the_candidate_lane(self):
        former = self.base["core"]["candidate"]
        self.mutate(lambda w: w["core"].update(candidate=None))
        if former is not None:
            # The candidate's provenance is no longer required either.
            (self.root / "tests" / "fixtures" / core_lane_bindings.provenance_name(former["version"])).unlink()
        window = self.sw.load(self.root)
        self.assertIsNone(window["core"]["candidate"])
        self.assertEqual(self.sw.core_slots(window, self.sw.SLOTS), ("current", "rollback"))
        pr = self.sw.real_ha_matrix(window, "pr", self.root)["include"]
        full = self.sw.real_ha_matrix(window, "full", self.root)["include"]
        self.assertEqual([(r["support_slot"], r["ha_version"]) for r in pr],
                         [("current", self.base["core"]["current"]["version"])])
        self.assertEqual([(r["support_slot"], r["ha_version"]) for r in full],
                         [("current", self.base["core"]["current"]["version"]),
                          ("rollback", self.base["core"]["rollback"]["version"])])
        self.assertTrue(all(r["alarmo"] is True for r in full))
        if former is not None:
            with self.assertRaisesRegex(self.sw.Refusal, "^alarmo_core_not_in_window_slot$"):
                self.sw.alarmo_slot(former["version"], self.root)
        for slot in ("current", "rollback"):
            version = self.base["core"][slot]["version"]
            with self.subTest(slot=slot):
                self.assertEqual(self.sw.alarmo_slot(version, self.root)["slot"], slot)
        # ha-mcp slots, gateway rows and add-on rows are unaffected.
        self.assertEqual(self.sw.gateway_matrix(window, "pr", self.registry), self.sw.gateway_matrix(
            self.window, "pr", self.registry))
        self.assertEqual(self.sw.addon_matrix(window, "full"), self.sw.addon_matrix(self.window, "full"))

    def test_only_candidates_may_be_empty(self):
        # current and rollback are never optional, for either component, even
        # when both candidates are empty.
        for component in ("core", "ha_mcp"):
            for slot in ("current", "rollback"):
                with self.subTest(component=component, slot=slot):
                    self.mutate(lambda w: (w["core"].update(candidate=None), w["ha_mcp"].update(candidate=None),
                                           w[component].update({slot: None})))
                    self.assertRefuses(component + "_" + slot + "_keys")
            with self.subTest(component=component, missing="candidate"):
                self.mutate(lambda w: w[component].pop("candidate"))
                self.assertRefuses(component + "_slots")
                self.mutate(lambda w: w[component].update(candidate=None, extra=None))
                self.assertRefuses(component + "_slots")


class EmptyHaMcpCandidateTests(TempRootFixture):
    """``ha_mcp.candidate: null`` (owner decision, Josh 2026-10-13, HAMCP-173)
    removes every candidate gateway and add-on job and requires nothing for it."""

    def populated(self):
        window = copy.deepcopy(self.base)
        window["ha_mcp"]["candidate"] = held_ha_mcp_candidate(self.registry)
        return window

    def test_null_ha_mcp_candidate_loads_and_creates_no_candidate_jobs(self):
        self.mutate(lambda w: w["ha_mcp"].update(candidate=None))
        window = self.sw.load(self.root)
        self.assertIsNone(window["ha_mcp"]["candidate"])
        self.assertEqual(self.sw.ha_mcp_slots(window, self.sw.SLOTS), ("current", "rollback"))
        for tier, slots in (("pr", ("current",)), ("full", ("current", "rollback"))):
            gateway = self.sw.gateway_matrix(window, tier, self.registry)["include"]
            addon = self.sw.addon_matrix(window, tier)["include"]
            with self.subTest(tier=tier):
                self.assertEqual([(r["support_slot"], r["upstream_version"]) for r in gateway],
                                 [(slot, window["ha_mcp"][slot]["version"]) for slot in slots])
                self.assertEqual({r["support_slot"] for r in addon}, set(slots))
                self.assertNotIn(HELD_HA_MCP, {r["upstream_version"] for r in gateway + addon})
                # Every non-empty slot in the tier still runs at least one add-on lane.
                for slot in slots:
                    self.assertTrue(any(r["support_slot"] == slot for r in addon))
                # Real-HA lanes always pair with ha_mcp.current and are unaffected.
                self.assertEqual(self.sw.real_ha_matrix(window, tier, self.root),
                                 self.sw.real_ha_matrix(self.base, tier, self.root))

    def test_emptying_a_populated_candidate_drops_only_its_rows(self):
        populated = self.populated()
        empty = copy.deepcopy(populated)
        empty["ha_mcp"]["candidate"] = None
        for tier in ("pr", "full"):
            with self.subTest(tier=tier):
                before = self.sw.gateway_matrix(populated, tier, self.registry)["include"]
                after = self.sw.gateway_matrix(empty, tier, self.registry)["include"]
                self.assertEqual(after, [r for r in before if r["support_slot"] != "candidate"])
                self.assertIn("candidate", {r["support_slot"] for r in before})

    def test_null_ha_mcp_candidate_requires_no_registry_row_or_reviewed_pair(self):
        self.mutate(lambda w: w["ha_mcp"].update(candidate=None))
        window = self.sw.load(self.root)
        absent = {"include": [r for r in self.registry["include"] if r["upstream_version"] != HELD_HA_MCP]}
        self.assertEqual(self.sw.gateway_matrix(window, "full", absent),
                         self.sw.gateway_matrix(window, "full", self.registry))
        codes = self.sw.acceptance_codes()
        calls = []

        class Recording:
            Refusal = codes.Refusal

            @staticmethod
            def version_code(version):
                calls.append(version)
                return codes.version_code(version)

            core_code = staticmethod(codes.core_code)
            profile_code = staticmethod(codes.profile_code)

        self.sw.addon_matrix(window, "full", Recording)
        self.assertEqual(sorted(set(calls)), sorted({window["ha_mcp"][slot]["version"]
                                                     for slot in ("current", "rollback")
                                                     if any(l["kind"] == "candidate_power"
                                                            for l in window["ha_mcp"][slot]["addon_runtime"])}))

    def test_every_non_empty_ha_mcp_slot_still_needs_an_addon_lane(self):
        for slot in self.sw.SLOTS:
            for change, category in ((lambda e: e.update(addon_runtime=[]), "ha_mcp_" + slot + "_addon_runtime"),
                                     (lambda e: e.pop("addon_runtime"), "ha_mcp_" + slot + "_keys")):
                with self.subTest(slot=slot, category=category):
                    window = self.populated()
                    change(window["ha_mcp"][slot])
                    self.write(window)
                    self.assertRefuses(category)
        self.write(self.populated())
        self.assertEqual(self.sw.ha_mcp_slots(self.sw.load(self.root), self.sw.SLOTS), self.sw.SLOTS)

    def test_both_candidates_empty_loads(self):
        self.mutate(lambda w: (w["core"].update(candidate=None), w["ha_mcp"].update(candidate=None)))
        window = self.sw.load(self.root)
        self.assertEqual(self.sw.core_slots(window, self.sw.SLOTS), ("current", "rollback"))
        self.assertEqual(self.sw.ha_mcp_slots(window, self.sw.SLOTS), ("current", "rollback"))


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
            window = support_window().load(ROOT)
            expected = (support_window().core_slots(window, ("current", "candidate"))
                        if key == "real_ha" else support_window().ha_mcp_slots(window, ("current", "candidate")))
            self.assertEqual({row["support_slot"] for row in parsed["include"]}, set(expected))

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
        window = support_window().load(ROOT)
        self.assertEqual([row["upstream_version"] for row in json.loads(values["gateway"])["include"]],
                         [window["ha_mcp"][slot]["version"]
                          for slot in support_window().ha_mcp_slots(window, support_window().SLOTS)])

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
