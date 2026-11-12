"""HAMCP-377: rollback case ids name the role (previous release), not a version.

The side_by_side rollback stage runs whichever release the workflow pins as the
previous one. A case id that carried a version would be wrong after the next
release, so none may. Offline: reads the two case tables only.
"""

from __future__ import annotations

import importlib.util
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_IN_NAME = re.compile(r"beta_?[0-9]|rc_?[0-9]|[0-9]_[0-9]+_[0-9]")


def _load_script(name: str):
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"{name}_hamcp377_case_ids", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve annotations through sys.modules
    spec.loader.exec_module(module)
    return module


lanes = _load_script("exact_image_admission_lanes")
governed = _load_script("exact_image_admission_91_governed")


class PreviousReleaseCaseIdTests(unittest.TestCase):
    def test_pattern_recognizes_a_version_bearing_id(self):
        # The two ids this replaced, and shapes a later release could bring.
        for name in ("sbs_governed_dashboard_rollback_beta6_85_legacy",
                     "sbs_typed_public_rollback_beta6_85_legacy",
                     "sbs_rollback_beta_7_85_legacy", "sbs_rollback_2_4_2_legacy",
                     "sbs_rollback_rc1_85_legacy"):
            with self.subTest(name=name):
                self.assertIsNotNone(VERSION_IN_NAME.search(name))
        for name in ("sbs_rollback_85_legacy_useful", "sbs_engineering_rollback_91_refused",
                     "sbs_stage_b2_91_strict_full_useful", "91_strict_admission_positive"):
            with self.subTest(name=name):
                self.assertIsNone(VERSION_IN_NAME.search(name))

    def test_no_case_id_carries_an_engineering_version(self):
        names = {case.name for cases in lanes.CASES.values() for case in cases}
        names.update(governed.ALL_CASES)
        self.assertEqual(sorted(name for name in names if VERSION_IN_NAME.search(name)), [])

    def test_governed_rollback_stage_uses_the_previous_release_ids(self):
        (rollback,) = (stage for stage in governed.STAGES if stage.rollback)
        self.assertEqual(rollback.cases, (
            "sbs_governed_dashboard_rollback_previous_release_85_legacy",
            "sbs_typed_public_rollback_previous_release_85_legacy"))
        side_by_side = {case.name: case.endpoint_form for case in lanes.CASES["side_by_side"]}
        for name in rollback.cases:
            with self.subTest(name=name):
                self.assertEqual(side_by_side.get(name), "legacy")
                self.assertIsNotNone(lanes.CASE_NAME.fullmatch(name))


if __name__ == "__main__":
    unittest.main()
