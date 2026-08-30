"""Bind Beta 53 staging to exact replay and upstream source authority."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = (
    ROOT
    / "tests"
    / "fixtures"
    / "dependency"
    / "hamcp089_beta52_standard_helper_replay_v1.json"
)
ACCEPTANCE = ROOT / "docs" / "V2_2_0_BETA53_ACCEPTANCE.md"
RELEASE_NOTES = ROOT / "docs" / "V2_2_0_BETA53_RELEASE_NOTES.md"


class Beta53AcceptanceAuthorityTests(unittest.TestCase):
    def test_documents_resolve_exact_beta53_in_staged_or_materialized_state(self):
        context_path = ROOT / "scripts" / "codex-context.py"
        spec = importlib.util.spec_from_file_location(
            "_beta53_context_authority", context_path
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        context = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(context)
        resolution = context.resolve_documents(ROOT, "2.2.0-beta.53")
        self.assertEqual("exact", resolution["resolution_status"])
        self.assertEqual(
            "docs/V2_2_0_BETA53_ACCEPTANCE.md",
            resolution["active_acceptance_document"],
        )
        self.assertEqual(
            "docs/V2_2_0_BETA53_RELEASE_NOTES.md",
            resolution["active_release_notes"],
        )

        next_version = ROOT / ".release" / "next-version"
        config = (
            ROOT / "hass_mcp_engineering_beta" / "config.yaml"
        ).read_text(encoding="utf-8")
        if next_version.exists():
            acceptance_text = ACCEPTANCE.read_text(encoding="utf-8")
            release_notes_text = RELEASE_NOTES.read_text(encoding="utf-8")
            self.assertIn("Beta 53 stages", acceptance_text)
            self.assertIn(
                "Engineering continues to advertise 2.2.0-beta.52",
                acceptance_text,
            )
            self.assertIn("Beta 53 stages", release_notes_text)
            self.assertIn(
                "Engineering remains advertised as 2.2.0-beta.52",
                release_notes_text,
            )
            self.assertEqual(
                "2.2.0-beta.53",
                next_version.read_text(encoding="utf-8").strip(),
            )
            self.assertIn('version: "2.2.0-beta.52"', config)
            return

        for path in (ACCEPTANCE, RELEASE_NOTES):
            text = path.read_text(encoding="utf-8")
            self.assertIn("Beta 53 is materialized", text)
            self.assertIn("Engineering now advertises", text)
            self.assertIn("2.2.0-beta.53", text)
            self.assertNotIn(
                "Engineering remains advertised as 2.2.0-beta.52",
                text,
            )
            self.assertNotIn(
                "Engineering continues to advertise 2.2.0-beta.52",
                text,
            )

        self.assertIn('version: "2.2.0-beta.53"', config)
        self.assertIn(
            'SERVER_VERSION = "2.2.0-beta.53"',
            (
                ROOT
                / "hass_mcp_engineering_beta"
                / "ha_mcp_engineering"
                / "version.py"
            ).read_text(encoding="utf-8"),
        )
        self.assertIn(
            'BETA_VERSION = "2.2.0-beta.53"',
            (ROOT / "scripts" / "validate_addon_metadata.py").read_text(
                encoding="utf-8"
            ),
        )

    def test_fixture_hash_self_fingerprint_and_provenance_are_exact(self):
        raw = FIXTURE.read_bytes()
        self.assertEqual(
            "144e194992f3d50d72cd978b9975647c9492c0f2056d6b33eb11360c3db831bd",
            hashlib.sha256(raw).hexdigest(),
        )
        value = json.loads(raw)
        expected = value["provenance"].pop("self_fingerprint")
        canonical = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        self.assertEqual(
            "c501621df35e5f3ee2c44528b87bd56b58dcbfa4bb7983228647caa48da52a22",
            expected,
        )
        self.assertEqual(expected, hashlib.sha256(canonical).hexdigest())
        self.assertEqual(
            "1b23baab38715ff9958e544d61ca8ac5dd208812",
            value["provenance"]["source_build_sha"],
        )

    def test_upstream_tag_and_file_authority_is_pinned(self):
        text = ACCEPTANCE.read_text(encoding="utf-8")
        for version, commit in (
            ("2026.7.2", "f9122fb28dd30d3833b3b313924befbc82157f97"),
            ("2026.8.0", "4a9dce13f61d03960ad5d2710e2af9fd2a78af54"),
            ("2026.8.1", "53998d7710b4ac280658511c24a2a3e2651f9873"),
        ):
            self.assertIn(version, text)
            self.assertIn(commit, text)
            self.assertIn(
                f"core/blob/{version}/homeassistant/helpers/entity_registry.py",
                text,
            )
            self.assertIn(
                f"core/blob/{version}/homeassistant/components/config/entity_registry.py",
                text,
            )

    def test_documents_preserve_interpretation_and_authority_boundaries(self):
        text = ACCEPTANCE.read_text(encoding="utf-8")
        for required in (
            "does not claim",
            "input class",
            "no raw production capture",
            "does not authorize merge",
            "helper-dependency-risk-v12",
            "Persisted v3-v11 plans remain readable and hash-stable",
        ):
            self.assertIn(required.lower(), text.lower())


if __name__ == "__main__":
    unittest.main()
