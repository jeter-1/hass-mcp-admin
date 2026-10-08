"""RC1 canonical Engineering release-transition regressions."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

from awesomeversion import AwesomeVersion


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


RELEASE_TRANSITION = load_module(
    "rc1_release_transition",
    ROOT / "scripts" / "release_transition.py",
)
PROMOTION = load_module(
    "rc1_promote_next_release",
    ROOT / "scripts" / "promote_next_release.py",
)
METADATA = load_module(
    "rc1_validate_addon_metadata",
    ROOT / "scripts" / "validate_addon_metadata.py",
)
CONTEXT = load_module(
    "rc1_codex_context",
    ROOT / "scripts" / "codex-context.py",
)


class Rc1ReleaseTransitionTests(unittest.TestCase):
    def test_repository_rc1_staging_or_materialization_resolves_exactly(self):
        target = "2.2.0-rc.1"
        marker = ROOT / PROMOTION.NEXT_VERSION_PATH
        versions = set(PROMOTION.authoritative_versions(ROOT).values())
        documents = "\n".join(
            (ROOT / relative).read_text(encoding="utf-8")
            for relative in (
                "docs/V2_2_0_RC1_ACCEPTANCE.md",
                "docs/V2_2_0_RC1_RELEASE_NOTES.md",
            )
        )
        resolution = CONTEXT.resolve_documents(ROOT, target)
        self.assertEqual(resolution["resolution_status"], "exact")
        self.assertEqual(
            resolution["active_release_notes"],
            "docs/V2_2_0_RC1_RELEASE_NOTES.md",
        )
        self.assertEqual(
            resolution["active_acceptance_document"],
            "docs/V2_2_0_RC1_ACCEPTANCE.md",
        )
        # A later release's authoring marker does not put historical RC1 back
        # into its beta.58 staging state.
        if versions == {"2.2.0-beta.58"}:
            self.assertTrue(marker.exists())
            self.assertEqual(versions, {"2.2.0-beta.58"})
            self.assertEqual(marker.read_text(encoding="utf-8"), target + "\n")
            self.assertIn("staged source candidate", documents)
            self.assertIn("advertised Engineering source remains", documents)
        else:
            self.assertTrue(
                versions == {target}
                or all(
                    AwesomeVersion(version) > AwesomeVersion(target)
                    for version in versions
                )
            )
            self.assertIn("materialized source candidate", documents)
            self.assertIn("advertised Engineering source is", documents)
            self.assertIn("`.release/next-version` has been consumed", documents)

    def test_beta58_to_rc1_is_accepted_by_both_release_consumers(self):
        PROMOTION.validate_sequenced_transition(
            "2.2.0-beta.58",
            "2.2.0-rc.1",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            declaration = root / METADATA.NEXT_VERSION_PATH
            declaration.parent.mkdir(parents=True)
            declaration.write_text("2.2.0-rc.1\n", encoding="utf-8")
            self.assertEqual(
                METADATA.staged_release_version(root, "2.2.0-beta.58"),
                "2.2.0-rc.1",
            )

    def _metadata_transition(self, current: str, candidate: str) -> str:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            declaration = root / METADATA.NEXT_VERSION_PATH
            declaration.parent.mkdir(parents=True)
            declaration.write_text(candidate + "\n", encoding="utf-8")
            return METADATA.staged_release_version(root, current)

    def test_canonical_lifecycle_is_identical_for_both_consumers(self):
        accepted = (
            ("2.2.0-beta.58", "2.2.0-beta.59"),
            ("2.2.0-beta.58", "2.2.0-rc.1"),
            ("2.2.0-rc.1", "2.2.0-rc.2"),
            ("2.2.0-rc.2", "2.2.0-rc.3"),
            ("2.2.0-rc.2", "2.2.0"),
            ("2.4.0-rc.1", "2.4.1-beta.1"),
            ("2.4.9-rc.3", "2.4.10-beta.1"),
            ("2.2.0", "2.2.1"),
            ("2.2.1", "2.2.2"),
            ("2.2.9", "2.2.10"),
            ("2.2.0", "2.2.1-beta.1"),
            ("2.2.0", "2.3.0-beta.1"),
        )
        for current, candidate in accepted:
            with self.subTest(current=current, candidate=candidate):
                RELEASE_TRANSITION.validate_release_transition(
                    current, candidate
                )
                PROMOTION.validate_sequenced_transition(current, candidate)
                self.assertEqual(
                    self._metadata_transition(current, candidate), candidate
                )

    def test_skips_downgrades_and_noncanonical_forms_fail_for_both_consumers(self):
        rejected = (
            ("2.2.0-beta.58", "2.2.0-beta.58"),
            ("2.2.0-beta.58", "2.2.0-beta.60"),
            ("2.2.0-beta.58", "2.2.0-rc.2"),
            ("2.2.0-beta.58", "2.2.0"),
            ("2.2.0-rc.1", "2.2.0-beta.59"),
            ("2.2.0-rc.1", "2.2.0-rc.1"),
            ("2.2.0-rc.1", "2.2.0-rc.3"),
            ("2.2.0-rc.1", "2.3.0-rc.2"),
            ("2.4.0-rc.1", "2.4.0-beta.1"),
            ("2.4.0-rc.1", "2.4.1-beta.2"),
            ("2.4.0-rc.1", "2.4.2-beta.1"),
            ("2.4.0-rc.1", "2.5.0-beta.1"),
            ("2.4.0-rc.1", "3.0.0-beta.1"),
            ("2.4.0-rc.1", "2.4.1-rc.1"),
            ("2.4.0-rc.1", "2.4.1"),
            ("2.4.0-rc.1", "2.3.9-beta.1"),
            ("2.4.0-rc.1", "2.4.1-beta.01"),
            ("2.4.0-rc.1", "2.4.1-beta.1+build"),
            ("2.2.0", "2.2.0"),
            ("2.2.1", "2.2.0"),
            ("2.2.0", "2.2.2"),
            ("2.2.0", "2.3.0"),
            ("2.2.0", "3.0.0"),
            ("2.2.0", "2.2.1-rc.1"),
            ("2.2.0", "2.2.01"),
            ("2.2.0", "2.2.1+build"),
            ("2.2.0", "2.2.0-beta.1"),
            ("2.2.0", "2.2.1-beta.2"),
            ("2.2.0", "2.1.9-beta.1"),
            ("2.2.0-beta.58", "2.2.0-rc1"),
            ("2.2.0-beta.58", "2.2.0-rc1-dev1"),
            ("2.2.0-beta.58", "2.2.0-rc2-dev1"),
            ("2.2.0-beta.58", "2.2.0-rc.01"),
            ("2.2.0-beta.58", "2.2.0-rc.1+build"),
            ("02.2.0-beta.58", "2.2.0-rc.1"),
        )
        for current, candidate in rejected:
            with self.subTest(current=current, candidate=candidate):
                with self.assertRaises(
                    RELEASE_TRANSITION.ReleaseTransitionError
                ):
                    RELEASE_TRANSITION.validate_release_transition(
                        current, candidate
                    )
                with self.assertRaises(PROMOTION.PromotionError):
                    PROMOTION.validate_sequenced_transition(current, candidate)
                with self.assertRaises(METADATA.MetadataValidationError):
                    self._metadata_transition(current, candidate)

    def test_next_patch_beta_materializes_without_a_ga_release(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative, text in (
                ("hass_mcp_engineering_beta/config.yaml", 'version: "2.4.0-rc.1"\n'),
                (
                    "hass_mcp_engineering_beta/ha_mcp_engineering/version.py",
                    'SERVER_VERSION = "2.4.0-rc.1"\n',
                ),
                ("scripts/validate_addon_metadata.py", 'BETA_VERSION = "2.4.0-rc.1"\n'),
                (".release/next-version", "2.4.1-beta.1\n"),
                (
                    "docs/V2_4_1_BETA1_RELEASE_NOTES.md",
                    "# Engineering 2.4.1-beta.1 release notes\n",
                ),
                (
                    "docs/V2_4_1_BETA1_ACCEPTANCE.md",
                    "# Engineering 2.4.1-beta.1 acceptance\n",
                ),
            ):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
            self.assertEqual(
                PROMOTION.apply_candidate(root), ("2.4.0-rc.1", "2.4.1-beta.1")
            )
            self.assertEqual(
                set(PROMOTION.authoritative_versions(root).values()),
                {"2.4.1-beta.1"},
            )
            self.assertFalse((root / PROMOTION.NEXT_VERSION_PATH).exists())
            self.assertEqual(
                CONTEXT.resolve_documents(root, "2.4.1-beta.1")["resolution_status"],
                "exact",
            )


if __name__ == "__main__":
    unittest.main()
