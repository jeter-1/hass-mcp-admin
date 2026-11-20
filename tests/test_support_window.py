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
HA_MCP_IMAGE = re.compile(r"^ghcr\.io/homeassistant-ai/ha-mcp:9\.2\.0@sha256:[0-9a-f]{64}$")
# HAMCP-437: the reviewed registry is reduced to ha-mcp 9.2.0 (I2a-D), so tests
# that need a populated ha-mcp candidate or rollback use synthetic, unmistakably
# fake pins and add the matching rows to a copy of the reviewed registry.
SYNTHETIC_CANDIDATE = "9.3.0"
SYNTHETIC_ROLLBACK = "9.1.9"


def synthetic_registry_row(version, fill):
    """A synthetic reviewed-registry row (test input only, never real evidence)."""

    return {"upstream_version": version, "upstream_image": "ghcr.io/homeassistant-ai/ha-mcp@sha256:" + fill * 64,
            "image_index_digest": "sha256:" + fill * 64, "image_revision": fill * 40, "source_commit": fill * 40,
            "architecture_image_digests": {"linux/amd64": "sha256:" + "a" * 64, "linux/arm64": "sha256:" + "b" * 64}}


SYNTHETIC_ROWS = {SYNTHETIC_CANDIDATE: synthetic_registry_row(SYNTHETIC_CANDIDATE, "9"),
                  SYNTHETIC_ROLLBACK: synthetic_registry_row(SYNTHETIC_ROLLBACK, "7")}


def synthetic_ha_mcp_pin(version, **fields):
    """A populated ha-mcp slot pin that matches its synthetic registry row."""

    row = SYNTHETIC_ROWS[version]
    return {"version": row["upstream_version"], "image_index_digest": row["image_index_digest"],
            "source_commit": row["source_commit"], **fields}


def registry_with_synthetic():
    """The reviewed registry plus the synthetic candidate and rollback rows."""

    registry = copy.deepcopy(registry_matrix())
    registry["include"] += [copy.deepcopy(row) for row in SYNTHETIC_ROWS.values()]
    return registry


def without(registry, version):
    return {"include": [r for r in registry["include"] if r["upstream_version"] != version]}


class SupportWindowFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sw = support_window()
        cls.window = cls.sw.load(ROOT)
        cls.registry = registry_with_synthetic()

    def derive(self, tier, window=None):
        window = self.window if window is None else window
        return {
            "real_ha": self.sw.real_ha_matrix(window, tier)["include"],
            "strict_full": self.sw.strict_full_matrix(window, tier, self.registry)["include"],
            "registry": self.sw.registry_rows(window, tier, self.registry),
        }


class CommittedWindowTests(SupportWindowFixture):
    def test_committed_window_loads_with_distinct_reviewed_slots(self):
        # Expectations follow the committed window's non-empty slots, so an
        # empty candidate or rollback, or a rotation among reviewed Cores, edits
        # no test. Only current is required (HAMCP-406 made rollback optional).
        self.assertEqual(self.window["schema_version"], 1)
        for component, slots_of in (("core", self.sw.core_slots), ("ha_mcp", self.sw.ha_mcp_slots)):
            slots = slots_of(self.window, self.sw.SLOTS)
            with self.subTest(component=component):
                self.assertEqual(slots[0], "current")
                self.assertEqual(set(self.sw.SLOTS) - set(slots),
                                 {s for s in self.sw.SLOTS if self.window[component][s] is None})
                versions = [self.window[component][slot]["version"] for slot in slots]
                self.assertEqual(len(set(versions)), len(versions))
        for slot in self.sw.core_slots(self.window, self.sw.SLOTS):
            core = self.window["core"][slot]
            binding = core_lane_bindings.reviewed_lane_binding(core["version"])
            with self.subTest(slot=slot):
                self.assertIsNotNone(binding)
                self.assertEqual(binding.image_index_digest, core["image_index_digest"])
        # The retired add-on runtime lanes left no declaration behind.
        for slot in self.sw.ha_mcp_slots(self.window, self.sw.SLOTS):
            with self.subTest(slot=slot):
                self.assertNotIn("addon_runtime", self.window["ha_mcp"][slot])
        self.assertNotIn("admission", self.window)

    def test_committed_window_is_the_deployed_2026_11_0_and_9_2_0_pair(self):
        # HAMCP-437 (release 2.9.0): the deployed pair, with every candidate and
        # rollback empty (HAMCP-406, restore-first recovery).
        self.assertEqual(self.window["core"]["current"]["version"], "2026.11.0")
        self.assertEqual(self.window["ha_mcp"]["current"]["version"], "9.2.0")
        for component in ("core", "ha_mcp"):
            for slot in ("candidate", "rollback"):
                with self.subTest(component=component, slot=slot):
                    self.assertIsNone(self.window[component][slot])
        reviewed = {r["upstream_version"]: r for r in registry_matrix()["include"]}["9.2.0"]
        current = self.window["ha_mcp"]["current"]
        self.assertEqual((current["image_index_digest"], current["source_commit"]),
                         (reviewed["image_index_digest"], reviewed["source_commit"]))

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
        # Every non-empty ha-mcp slot in the tier is cross-checked against its
        # reviewed registry row verbatim.
        by_version = {r["upstream_version"]: r for r in self.registry["include"]}
        self.assertEqual(rows["registry"], {slot: by_version[self.window["ha_mcp"][slot]["version"]]
                                            for slot in self.sw.ha_mcp_slots(self.window, ("current", "candidate"))})
        # The strict-full lane runs ha_mcp.current, once per architecture.
        self.assertEqual([(r["configuration"], r["architecture"], r["runner"], r["support_slot"], r["state"])
                          for r in rows["strict_full"]],
                         [("91_only", arch, self.sw.RUNNERS[arch], "current", "active") for arch in ("amd64", "arm64")])
        for row in rows["strict_full"]:
            self.assertEqual(row["peers"], {"candidate": by_version[ha_mcp]})

    def test_full_tier_adds_exactly_the_rollback_slot(self):
        # The committed window has no rollback, so the full tier adds nothing.
        pr, full = self.derive("pr"), self.derive("full")
        self.assertEqual(full, pr)
        # A populated rollback adds exactly its real-HA row and its registry
        # cross-check; the strict-full lane stays on ha_mcp.current.
        window = copy.deepcopy(self.window)
        window["core"]["rollback"] = reviewed_core("2026.10.3")
        window["ha_mcp"]["rollback"] = synthetic_ha_mcp_pin(SYNTHETIC_ROLLBACK)
        pr, full = self.derive("pr", window), self.derive("full", window)
        self.assertEqual(full["real_ha"][: len(pr["real_ha"])], pr["real_ha"])
        self.assertEqual([(r["support_slot"], r["ha_version"]) for r in full["real_ha"][len(pr["real_ha"]):]],
                         [("rollback", "2026.10.3")])
        self.assertEqual(set(full["registry"]) - set(pr["registry"]), {"rollback"})
        self.assertEqual(full["registry"]["rollback"], SYNTHETIC_ROWS[SYNTHETIC_ROLLBACK])
        self.assertEqual(full["strict_full"], pr["strict_full"])

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
        # 2026.10.3 and 2026.9.4 have committed provenance but are not window slots.
        for version in ("2026.10.3", "2026.9.4", "2026.11.1", "2026.11", "2026.11.", "2026.11.*", "2026.1",
                        "2026.1.*", "2026", "", " 2026.11.0", "2026.11.0 ", "2026.11.0\n", "v2026.11.0",
                        "2026.11.00", "ha-2026-11-0", None, 2026.11, 2026, b"2026.11.0", ["2026.11.0"],
                        {"version": "2026.11.0"}):
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
        self.mutate(lambda w: w["ha_mcp"].pop("rollback"))
        self.assertRefuses("ha_mcp_slots")
        # Only current is never optional (explicit null); candidate and rollback
        # may be empty, see EmptyCandidateTests and EmptyRollbackTests.
        self.mutate(lambda w: w["core"].update(current=None))
        self.assertRefuses("core_current_keys")
        self.mutate(lambda w: w["ha_mcp"].update(current=None))
        self.assertRefuses("ha_mcp_current_keys")
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
                self.mutate(lambda w: w["core"].update(rollback={"version": "2026.10.3", "image_index_digest": digest}))
                self.assertRefuses("core_rollback_digest")
                self.mutate(lambda w: w["ha_mcp"].update(
                    candidate=synthetic_ha_mcp_pin(SYNTHETIC_CANDIDATE, image_index_digest=digest)))
                self.assertRefuses("ha_mcp_candidate_digest")
        self.mutate(lambda w: w["ha_mcp"]["current"].update(source_commit="main"))
        self.assertRefuses("ha_mcp_current_source")
        self.mutate(lambda w: w["ha_mcp"].update(
            rollback=synthetic_ha_mcp_pin(SYNTHETIC_ROLLBACK, source_archive_sha256="x" * 64)))
        self.assertRefuses("ha_mcp_rollback_archive")
        self.mutate(lambda w: w["ha_mcp"]["current"].pop("source_archive_sha256"))
        self.assertRefuses("ha_mcp_current_archive_required")

    def test_non_distinct_versions_refuse(self):
        for slot in ("candidate", "rollback"):
            with self.subTest(slot=slot):
                self.mutate(lambda w: w["core"].update({slot: dict(w["core"]["current"])}))
                self.assertRefuses("core_slot_versions_not_distinct")
                self.mutate(lambda w: w["ha_mcp"].update({slot: {k: v for k, v in w["ha_mcp"]["current"].items()
                                                                 if k != "source_archive_sha256"}}))
                self.assertRefuses("ha_mcp_slot_versions_not_distinct")

    def test_retired_addon_runtime_refuses_in_every_slot(self):
        # HAMCP-437 lane ruling A: the add-on runtime lanes are retired, so a
        # slot that still declares addon_runtime refuses, whatever its value.
        lane = [{"kind": "candidate_power", "profile": "reference", "architectures": ["amd64", "arm64"]}]
        pins = {"current": lambda w: w["ha_mcp"]["current"],
                "candidate": lambda w: synthetic_ha_mcp_pin(SYNTHETIC_CANDIDATE),
                "rollback": lambda w: synthetic_ha_mcp_pin(SYNTHETIC_ROLLBACK)}
        for slot in self.sw.SLOTS:
            for value in (lane, [], None, {}):
                with self.subTest(slot=slot, value=value):
                    self.mutate(lambda w: w["ha_mcp"].update({slot: {**pins[slot](w), "addon_runtime": value}}))
                    self.assertRefuses("ha_mcp_" + slot + "_addon_runtime_retired")
            # Any other unknown key still refuses as before.
            with self.subTest(slot=slot, key="unknown"):
                self.mutate(lambda w: w["ha_mcp"].update({slot: {**pins[slot](w), "addon_detail_profile": "x"}}))
                self.assertRefuses("ha_mcp_" + slot + "_keys")
        # With the declaration gone the same populated window loads.
        self.mutate(lambda w: w["ha_mcp"].update(candidate=pins["candidate"](w), rollback=pins["rollback"](w)))
        self.assertEqual(self.sw.ha_mcp_slots(self.sw.load(self.root), self.sw.SLOTS), self.sw.SLOTS)

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
        current.write_text(json.dumps({**entry, "version": "2026.9.3"}), encoding="utf-8")
        self.assertRefuses("core_current_provenance_mismatch")

    def test_core_version_without_reviewed_binding_or_provenance_refuses(self):
        digest = self.base["core"]["current"]["image_index_digest"]
        # Generated to be absent from the reviewed list (HAMCP-159 R2).
        unreviewed = core_bindings.unreviewed_version("2026.11.0")
        self.mutate(lambda w: w["core"]["current"].update(version=unreviewed))
        self.assertRefuses("core_current_binding_unreviewed")
        # A reviewed binding still needs the committed lane provenance fixture.
        binding = core_lane_bindings.CoreBinding(digest, core_lane_bindings.CHILD_DEVICE_NAME_PARTS, True, "9.2.0")
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
        for derive in (self.sw.registry_rows, self.sw.strict_full_matrix):
            with self.subTest(derive=derive.__name__):
                with self.assertRaisesRegex(self.sw.Refusal, "^registry_matrix_malformed$"):
                    derive(window, "pr", {"rows": []})
                with self.assertRaisesRegex(self.sw.Refusal, "^registry_matrix_malformed$"):
                    derive(window, "pr", [])
                duplicated = {"include": self.registry["include"] + self.registry["include"][:1]}
                with self.assertRaisesRegex(self.sw.Refusal, "^registry_matrix_duplicate$"):
                    derive(window, "pr", duplicated)
                # A populated candidate is cross-checked like any other slot, and
                # the strict-full lane refuses with it.
                held = copy.deepcopy(window)
                held["ha_mcp"]["candidate"] = synthetic_ha_mcp_pin(SYNTHETIC_CANDIDATE)
                with self.assertRaisesRegex(self.sw.Refusal, "^ha_mcp_candidate_not_in_reviewed_registry$"):
                    derive(held, "pr", without(self.registry, SYNTHETIC_CANDIDATE))
                held["ha_mcp"]["candidate"]["source_commit"] = "0" * 40
                with self.assertRaisesRegex(self.sw.Refusal, "^ha_mcp_candidate_registry_mismatch$"):
                    derive(held, "pr", self.registry)
                # Rollback is only cross-checked on the tier that runs it.
                rolled = copy.deepcopy(window)
                rolled["ha_mcp"]["rollback"] = synthetic_ha_mcp_pin(SYNTHETIC_ROLLBACK)
                no_rollback = without(self.registry, SYNTHETIC_ROLLBACK)
                self.assertEqual(derive(rolled, "pr", no_rollback), derive(window, "pr", self.registry))
                with self.assertRaisesRegex(self.sw.Refusal, "^ha_mcp_rollback_not_in_reviewed_registry$"):
                    derive(rolled, "full", no_rollback)
                for field, value in (("image_index_digest", "sha256:" + "0" * 64), ("source_commit", "0" * 40)):
                    with self.subTest(field=field):
                        tampered = copy.deepcopy(self.registry)
                        next(r for r in tampered["include"] if r["upstream_version"] == "9.2.0")[field] = value
                        with self.assertRaisesRegex(self.sw.Refusal, "^ha_mcp_current_registry_mismatch$"):
                            derive(window, "pr", tampered)
                with self.assertRaisesRegex(self.sw.Refusal, "^ha_mcp_current_not_in_reviewed_registry$"):
                    derive(window, "pr", without(self.registry, "9.2.0"))

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
        "sha256:" + "c" * 64, core_lane_bindings.CHILD_DEVICE_NAME_PARTS, True, "9.2.0")

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
        # Real-HA lanes still pair every Core slot with ha_mcp.current.
        self.assertEqual({r["ha_mcp_version"] for r in self.sw.real_ha_matrix(window, "full", self.root)["include"]},
                         {window["ha_mcp"]["current"]["version"]})

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
        populated = self.mutate(lambda w: w["core"].update(candidate=reviewed_core("2026.10.3")))
        before = self.sw.load(self.root)
        self.assertEqual([r["support_slot"] for r in self.sw.real_ha_matrix(before, "pr", self.root)["include"]],
                         ["current", "candidate"])
        self.mutate(lambda w: w["core"].update(candidate=None))
        # The candidate's provenance is no longer required either.
        (self.root / "tests" / "fixtures" / core_lane_bindings.provenance_name("2026.10.3")).unlink()
        window = self.sw.load(self.root)
        self.assertIsNone(window["core"]["candidate"])
        kept = tuple(slot for slot in ("current", "rollback") if populated["core"][slot] is not None)
        self.assertEqual(self.sw.core_slots(window, self.sw.SLOTS), kept)
        pr = self.sw.real_ha_matrix(window, "pr", self.root)["include"]
        full = self.sw.real_ha_matrix(window, "full", self.root)["include"]
        self.assertEqual([(r["support_slot"], r["ha_version"]) for r in pr],
                         [("current", self.base["core"]["current"]["version"])])
        self.assertEqual([(r["support_slot"], r["ha_version"]) for r in full],
                         [(slot, self.base["core"][slot]["version"]) for slot in kept])
        self.assertTrue(all(r["alarmo"] is True for r in full))
        with self.assertRaisesRegex(self.sw.Refusal, "^alarmo_core_not_in_window_slot$"):
            self.sw.alarmo_slot("2026.10.3", self.root)
        for slot in kept:
            version = self.base["core"][slot]["version"]
            with self.subTest(slot=slot):
                self.assertEqual(self.sw.alarmo_slot(version, self.root)["slot"], slot)
        # ha-mcp slots, registry cross-checks and the strict-full lane are unaffected.
        for tier in self.sw.TIERS:
            with self.subTest(tier=tier):
                self.assertEqual(self.sw.registry_rows(window, tier, self.registry),
                                 self.sw.registry_rows(before, tier, self.registry))
                self.assertEqual(self.sw.strict_full_matrix(window, tier, self.registry),
                                 self.sw.strict_full_matrix(before, tier, self.registry))

    def test_current_is_never_optional(self):
        # current is never optional, for either component, even when every
        # candidate and rollback is empty (HAMCP-406 made rollback optional).
        empty = lambda w: [w[c].update(candidate=None, rollback=None) for c in ("core", "ha_mcp")]
        for component in ("core", "ha_mcp"):
            with self.subTest(component=component, slot="current"):
                self.mutate(lambda w: (empty(w), w[component].update(current=None)))
                self.assertRefuses(component + "_current_keys")
            # An empty slot is an explicit null: a missing key still refuses.
            for slot in ("candidate", "rollback"):
                with self.subTest(component=component, missing=slot):
                    self.mutate(lambda w: (empty(w), w[component].pop(slot)))
                    self.assertRefuses(component + "_slots")
            with self.subTest(component=component, extra="slot"):
                self.mutate(lambda w: w[component].update(candidate=None, extra=None))
                self.assertRefuses(component + "_slots")


class EmptyHaMcpCandidateTests(TempRootFixture):
    """``ha_mcp.candidate: null`` (owner decision, Josh 2026-10-13, HAMCP-173)
    removes every candidate registry cross-check and requires nothing for it."""

    def populated(self):
        window = copy.deepcopy(self.base)
        window["ha_mcp"]["candidate"] = synthetic_ha_mcp_pin(SYNTHETIC_CANDIDATE)
        return window

    def test_null_ha_mcp_candidate_loads_and_creates_no_candidate_jobs(self):
        self.mutate(lambda w: w["ha_mcp"].update(candidate=None))
        window = self.sw.load(self.root)
        self.assertIsNone(window["ha_mcp"]["candidate"])
        kept = tuple(slot for slot in ("current", "rollback") if self.base["ha_mcp"][slot] is not None)
        self.assertEqual(self.sw.ha_mcp_slots(window, self.sw.SLOTS), kept)
        for tier in self.sw.TIERS:
            with self.subTest(tier=tier):
                self.assertEqual(set(self.sw.registry_rows(window, tier, self.registry)),
                                 set(kept) & set(self.sw.TIERS[tier]))
                # The strict-full lane and the real-HA lanes run ha_mcp.current only.
                self.assertEqual({r["support_slot"] for r in self.sw.strict_full_matrix(
                    window, tier, self.registry)["include"]}, {"current"})
                self.assertEqual(self.sw.real_ha_matrix(window, tier, self.root),
                                 self.sw.real_ha_matrix(self.base, tier, self.root))

    def test_emptying_a_populated_candidate_drops_only_its_rows(self):
        populated = self.populated()
        empty = copy.deepcopy(populated)
        empty["ha_mcp"]["candidate"] = None
        for tier in self.sw.TIERS:
            with self.subTest(tier=tier):
                before = self.sw.registry_rows(populated, tier, self.registry)
                after = self.sw.registry_rows(empty, tier, self.registry)
                self.assertEqual(after, {k: v for k, v in before.items() if k != "candidate"})
                self.assertIn("candidate", before)
                self.assertEqual(self.sw.strict_full_matrix(empty, tier, self.registry),
                                 self.sw.strict_full_matrix(populated, tier, self.registry))

    def test_null_ha_mcp_candidate_requires_no_registry_row(self):
        self.mutate(lambda w: w["ha_mcp"].update(candidate=None))
        window = self.sw.load(self.root)
        absent = without(self.registry, SYNTHETIC_CANDIDATE)
        for derive in (self.sw.registry_rows, self.sw.strict_full_matrix):
            with self.subTest(derive=derive.__name__):
                self.assertEqual(derive(window, "full", absent), derive(window, "full", self.registry))

    def test_every_ha_mcp_slot_populated_loads(self):
        window = self.populated()
        window["ha_mcp"]["rollback"] = synthetic_ha_mcp_pin(SYNTHETIC_ROLLBACK)
        self.write(window)
        loaded = self.sw.load(self.root)
        self.assertEqual(self.sw.ha_mcp_slots(loaded, self.sw.SLOTS), self.sw.SLOTS)
        self.assertEqual(set(self.sw.registry_rows(loaded, "full", self.registry)), set(self.sw.SLOTS))

    def test_both_candidates_empty_loads(self):
        self.mutate(lambda w: (w["core"].update(candidate=None), w["ha_mcp"].update(candidate=None)))
        window = self.sw.load(self.root)
        for component, slots_of in (("core", self.sw.core_slots), ("ha_mcp", self.sw.ha_mcp_slots)):
            with self.subTest(component=component):
                self.assertEqual(slots_of(window, self.sw.SLOTS),
                                 tuple(s for s in ("current", "rollback") if self.base[component][s] is not None))


class EmptyRollbackTests(TempRootFixture):
    """``rollback: null`` (Josh 2026-11-19, HAMCP-406, D1: restore-first
    recovery, amending HAMCP-173) removes the rollback lanes of that component
    and requires nothing for them; current is still required."""

    def setUp(self):
        super().setUp()
        # A synthetic Core later than the committed current, reviewed only while
        # patched in, so a rotation can fill the empty rollback (HAMCP-159 R2).
        self.later = core_bindings.unreviewed_version("2026.11.1")
        self.LATER_BINDING = core_lane_bindings.CoreBinding(
            "sha256:" + "c" * 64, core_lane_bindings.CHILD_DEVICE_NAME_PARTS, True, "9.2.0")
        (self.root / "tests" / "fixtures" / core_lane_bindings.provenance_name(self.later)).write_text(json.dumps({
            "version": self.later, "image_index_digest": "sha256:" + "c" * 64, "source_commit": "d" * 40,
            "source_archive_sha256": "e" * 64, "provenance": "synthetic offline rotation fixture; not a real Core"}),
            encoding="utf-8")

    def test_committed_window_has_null_rollbacks_and_loads(self):
        # Ruling 1 positive: both rollbacks are null and the window loads.
        window = self.sw.load(self.root)
        self.assertIsNone(window["core"]["rollback"])
        self.assertIsNone(window["ha_mcp"]["rollback"])
        self.assertEqual(self.sw.real_ha_matrix(window, "full", self.root),
                         self.sw.real_ha_matrix(window, "pr", self.root))
        self.assertEqual(self.sw.registry_rows(window, "full", self.registry),
                         self.sw.registry_rows(window, "pr", self.registry))

    def test_null_core_rollback_loads_and_drops_only_the_rollback_lane(self):
        populated = self.mutate(lambda w: w["core"].update(rollback=reviewed_core("2026.10.3")))
        before = self.sw.load(self.root)
        self.assertEqual(self.sw.alarmo_slot("2026.10.3", self.root)["slot"], "rollback")
        self.mutate(lambda w: w["core"].update(rollback=None))
        # The rollback's provenance is no longer required either.
        (self.root / "tests" / "fixtures" / core_lane_bindings.provenance_name("2026.10.3")).unlink()
        window = self.sw.load(self.root)
        self.assertIsNone(window["core"]["rollback"])
        kept = tuple(slot for slot in ("current", "candidate") if populated["core"][slot] is not None)
        self.assertEqual(self.sw.core_slots(window, self.sw.SLOTS), kept)
        for tier in self.sw.TIERS:
            rows = self.sw.real_ha_matrix(window, tier, self.root)["include"]
            with self.subTest(tier=tier):
                self.assertEqual([(r["support_slot"], r["ha_version"]) for r in rows],
                                 [(slot, self.base["core"][slot]["version"]) for slot in kept])
                self.assertEqual(self.sw.registry_rows(window, tier, self.registry),
                                 self.sw.registry_rows(before, tier, self.registry))
                self.assertEqual(self.sw.strict_full_matrix(window, tier, self.registry),
                                 self.sw.strict_full_matrix(before, tier, self.registry))
        with self.assertRaisesRegex(self.sw.Refusal, "^alarmo_core_not_in_window_slot$"):
            self.sw.alarmo_slot("2026.10.3", self.root)

    def test_null_ha_mcp_rollback_loads_and_creates_no_rollback_jobs(self):
        self.mutate(lambda w: w["ha_mcp"].update(rollback=synthetic_ha_mcp_pin(SYNTHETIC_ROLLBACK)))
        before = self.sw.load(self.root)
        self.assertIn("rollback", self.sw.registry_rows(before, "full", self.registry))
        self.mutate(lambda w: w["ha_mcp"].update(rollback=None))
        window = self.sw.load(self.root)
        self.assertIsNone(window["ha_mcp"]["rollback"])
        kept = tuple(slot for slot in ("current", "candidate") if self.base["ha_mcp"][slot] is not None)
        self.assertEqual(self.sw.ha_mcp_slots(window, self.sw.SLOTS), kept)
        # No registry row is required for the empty slot.
        absent = without(self.registry, SYNTHETIC_ROLLBACK)
        for tier in self.sw.TIERS:
            with self.subTest(tier=tier):
                self.assertEqual(set(self.sw.registry_rows(window, tier, absent)), set(kept) & set(self.sw.TIERS[tier]))
                self.assertEqual(self.sw.strict_full_matrix(window, tier, absent),
                                 self.sw.strict_full_matrix(before, tier, self.registry))
                # Real-HA lanes always pair with ha_mcp.current and are unaffected.
                self.assertEqual(self.sw.real_ha_matrix(window, tier, self.root),
                                 self.sw.real_ha_matrix(self.base, tier, self.root))

    def test_full_tier_is_current_only_when_candidate_and_rollback_are_empty(self):
        self.mutate(lambda w: [w[c].update(candidate=None, rollback=None) for c in ("core", "ha_mcp")])
        window = self.sw.load(self.root)
        for component in ("core", "ha_mcp"):
            with self.subTest(component=component):
                slots = (self.sw.core_slots if component == "core" else self.sw.ha_mcp_slots)(window, self.sw.SLOTS)
                self.assertEqual(slots, ("current",))
        for tier in self.sw.TIERS:
            with self.subTest(tier=tier):
                real_ha = self.sw.real_ha_matrix(window, tier, self.root)["include"]
                strict_full = self.sw.strict_full_matrix(window, tier, self.registry)["include"]
                self.assertEqual([(r["support_slot"], r["ha_version"]) for r in real_ha],
                                 [("current", self.base["core"]["current"]["version"])])
                self.assertEqual(list(self.sw.registry_rows(window, tier, self.registry)), ["current"])
                self.assertEqual({(r["support_slot"], r["peers"]["candidate"]["upstream_version"])
                                  for r in strict_full},
                                 {("current", self.base["ha_mcp"]["current"]["version"])})

    def test_distinct_versions_still_apply_to_the_non_empty_slots(self):
        for component in ("core", "ha_mcp"):
            with self.subTest(component=component):
                self.mutate(lambda w: w[component].update(
                    rollback=None, candidate=copy.deepcopy(w[component]["current"])))
                self.assertRefuses(component + "_slot_versions_not_distinct")

    def test_rotation_fills_an_empty_rollback(self):
        # Core: a later reviewed current arrives and the former current becomes rollback.
        former = self.base["core"]["current"]
        self.assertEqual(self.sw.core_slots(self.sw.load(self.root), self.sw.SLOTS), ("current",))
        self.mutate(lambda w: w["core"].update(
            current={"version": self.later, "image_index_digest": "sha256:" + "c" * 64},
            candidate=None, rollback=dict(former)))
        self.assertRefuses("core_current_binding_unreviewed")
        with patch.dict(core_lane_bindings.REVIEWED_CORE_BINDINGS, {self.later: self.LATER_BINDING}):
            window = self.sw.load(self.root)
            self.assertEqual([(r["support_slot"], r["ha_version"])
                              for r in self.sw.real_ha_matrix(window, "full", self.root)["include"]],
                             [("current", self.later), ("rollback", former["version"])])
            self.assertEqual(self.sw.alarmo_slot(former["version"], self.root)["slot"], "rollback")
        # ha-mcp: an empty rollback is filled the same way by the former current.
        self.write(self.base)
        current = self.base["ha_mcp"]["current"]
        self.assertEqual(self.sw.ha_mcp_slots(self.sw.load(self.root), self.sw.SLOTS), ("current",))
        self.mutate(lambda w: w["ha_mcp"].update(
            candidate=None, rollback=copy.deepcopy(current),
            current=synthetic_ha_mcp_pin(SYNTHETIC_CANDIDATE, source_archive_sha256="6" * 64)))
        window = self.sw.load(self.root)
        self.assertEqual({slot: row["upstream_version"]
                          for slot, row in self.sw.registry_rows(window, "full", self.registry).items()},
                         {"current": SYNTHETIC_CANDIDATE, "rollback": current["version"]})
        # The strict-full lane follows the new current.
        self.assertEqual({r["peers"]["candidate"]["upstream_version"]
                          for r in self.sw.strict_full_matrix(window, "full", self.registry)["include"]},
                         {SYNTHETIC_CANDIDATE})


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
        self.assertEqual([line.split("=", 1)[0] for line in lines], ["tier", "real_ha"])
        values = dict(line.split("=", 1) for line in lines)
        self.assertEqual(values["tier"], "pr")
        parsed = json.loads(values["real_ha"])
        self.assertEqual(values["real_ha"], json.dumps(parsed, sort_keys=True, separators=(",", ":")))
        window = support_window().load(ROOT)
        self.assertEqual({row["support_slot"] for row in parsed["include"]},
                         set(support_window().core_slots(window, ("current", "candidate"))))

    def test_registry_matrix_adds_strict_full_and_admission_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = Path(tmp) / "registry.json"
            registry.write_text(json.dumps(registry_matrix()), encoding="utf-8")
            output = Path(tmp) / "github-output"
            result = self.run_cli("--event", "schedule", "--registry-matrix", str(registry),
                                  "--github-output", str(output))
            self.assertEqual(result.returncode, 0, result.stderr)
            lines = output.read_text(encoding="utf-8").splitlines()
        self.assertEqual([line.split("=", 1)[0] for line in lines], ["tier", "real_ha", "strict_full", "admission"])
        values = dict(line.split("=", 1) for line in lines)
        self.assertEqual(values["tier"], "full")
        window = support_window().load(ROOT)
        self.assertEqual([(row["architecture"], row["peers"]["candidate"]["upstream_version"])
                          for row in json.loads(values["strict_full"])["include"]],
                         [(arch, window["ha_mcp"]["current"]["version"]) for arch in ("amd64", "arm64")])
        # The committed window has no admission key, so no admission row.
        self.assertEqual(json.loads(values["admission"]), {"include": []})

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
