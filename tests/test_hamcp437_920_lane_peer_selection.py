"""HAMCP-437: typed exact-image lane peer selection, re-pinned to ha-mcp 9.2.0.

Ported from ``LanePeerSelectionTests`` in the retired
``test_hamcp351_ha_mcp_912_admission`` module. The typed lane's single CI
candidate slot is 9.2.0 (HAMCP-371 S13-1): the pinned row selects each
architecture, and a row naming another version, a tag, or digests that
differ from the reviewed registry is refused. The retired ``pinned_912_row``
subcase read the 9.1.2 registry row; ``version_912`` keeps that refusal.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT / "scripts")]

_SPEC = importlib.util.spec_from_file_location(
    "exact_image_admission_91_typed_hamcp437", ROOT / "scripts/exact_image_admission_91_typed.py")
typed_lane = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = typed_lane
_SPEC.loader.exec_module(typed_lane)

# The pinned 9.2.0 image digests from docs/evidence/hamcp371/s4
# (index-9.2.0.raw.json and inspect-digest-9.2.0.txt).
INDEX_920 = "sha256:9de5bf6c4216142fdffe862900abe72dac8dfe9621dd0b2b050d132372b676ba"
PLATFORMS_920 = {
    "linux/amd64": "sha256:349a3ec6b2f4fa0797651b2c5d8985ca28d7a1028156eefba8d94fe51683fdb8",
    "linux/arm64": "sha256:5799ff6f0b85f01710b725792e32caf79255934a6e80b8af28373768d42e9a80",
}


class LanePeerSelection920Tests(unittest.TestCase):
    def row(self, **changes):
        release = typed_lane.reviewed_release("9.2.0")
        row = {"upstream_version": "9.2.0",
               "upstream_image": "ghcr.io/homeassistant-ai/ha-mcp@" + release.image_index_digest,
               "image_index_digest": release.image_index_digest,
               "architecture_image_digests": dict(release.architecture_image_digests_by_platform),
               "image_revision": release.image_revision, "source_commit": release.source_commit}
        row.update(changes)
        return {"candidate": row}

    def test_pinned_920_row_selects_each_architecture(self):
        for arch in ("amd64", "arm64"):
            with self.subTest(arch=arch):
                peer = typed_lane.select_peer_image(self.row(), arch)
                self.assertEqual(peer["image_index_digest"], INDEX_920)
                self.assertEqual(peer["platform_manifest_digest"], PLATFORMS_920["linux/" + arch])
                self.assertEqual(peer["version"], "9.2.0")

    def test_other_versions_tags_and_unpinned_digests_are_refused(self):
        other = "sha256:" + "1" * 64
        cases = {
            "version_912": (self.row(upstream_version="9.1.2"), "peer_version_not_9_2_0"),
            "version_911": (self.row(upstream_version="9.1.1"), "peer_version_not_9_2_0"),
            "version_910": (self.row(upstream_version="9.1.0"), "peer_version_not_9_2_0"),
            "version_921": (self.row(upstream_version="9.2.1"), "peer_version_not_9_2_0"),
            "tag_only": (self.row(upstream_image="ghcr.io/homeassistant-ai/ha-mcp:9.2.0"),
                         "peer_image_not_digest_pinned"),
            "other_index": (self.row(upstream_image="ghcr.io/homeassistant-ai/ha-mcp@" + other,
                                     image_index_digest=other), "peer_row_differs_from_reviewed_registry"),
            "other_platform": (self.row(architecture_image_digests={**PLATFORMS_920, "linux/amd64": other}),
                               "peer_row_differs_from_reviewed_registry"),
        }
        for label, (peers, code) in cases.items():
            with self.subTest(case=label):
                with self.assertRaisesRegex(Exception, code):
                    typed_lane.select_peer_image(peers, "amd64")


if __name__ == "__main__":
    unittest.main()
