from __future__ import annotations

import base64
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


ROOT = Path(__file__).resolve().parents[1]
BETA = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(BETA))

from ha_mcp_engineering.ha_mcp_readmission.registry import (  # noqa: E402
    TRUST_ANCHOR_KEY_ID,
    _parse_signed_journal,
)
from ha_mcp_engineering.signed_registry import TrustAnchorStore  # noqa: E402


SCRIPT = ROOT / "scripts" / "prepare_ha_mcp_release_registry_update.py"
SPEC = importlib.util.spec_from_file_location(
    "prepare_ha_mcp_release_registry_update", SCRIPT
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class PrepareHaMcpReleaseRegistryUpdateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.registry = self.root / "upstream-trust" / "registry.json"
        self.evidence = self.root / "docs" / "evidence"
        self.index = self.root / "docs" / "index.md"
        self.compat = self.root / ".compat"
        self.compat.mkdir()
        self.capture_path = self.compat / "capture.json"
        self.release_path = self.compat / "release.json"
        self.key = Ed25519PrivateKey.generate()
        self.key_text = base64.b64encode(
            self.key.private_bytes_raw()
        ).decode("ascii")
        self.now = datetime(2026, 9, 4, 21, tzinfo=timezone.utc)
        self.patchers = (
            patch.object(MODULE, "REGISTRY_PATH", self.registry),
            patch.object(MODULE, "EVIDENCE_DIRECTORY", self.evidence),
            patch.object(MODULE, "INDEX_PATH", self.index),
            patch.object(MODULE, "CAPTURE_PATH", self.capture_path),
            patch.object(MODULE, "RELEASE_EVIDENCE_PATH", self.release_path),
        )
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.environment = patch.dict(
            os.environ,
            {"HA_MCP_RELEASE_REGISTRY_SIGNING_KEY": self.key_text},
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self._write_inputs("8.4.3")

    def _write_inputs(self, version: str) -> None:
        version_marker = version.encode("ascii").hex().ljust(64, "0")[:64]
        capture = json.loads(
            (
                ROOT
                / "docs/evidence/upstream-read-compatibility"
                / "ha-mcp-8.4.3.json"
            ).read_text(encoding="utf-8")
        )
        capture["server_version"] = version
        self.capture_path.write_text(
            json.dumps(capture, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        self.release_path.write_text(
            json.dumps(
                {
                    "architecture_image_digests": {
                        "linux/amd64": "sha256:" + "1" * 64,
                        "linux/arm64": "sha256:" + "2" * 64,
                    },
                    "image_index_digest": "sha256:" + version_marker,
                    "image_revision": "4" * 40,
                    "source_commit": "5" * 40,
                    "source_tag": f"v{version}",
                    "source_tag_object": "6" * 40,
                    "source_tree": "7" * 40,
                    "version": version,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )

    def _journal(self):
        return _parse_signed_journal(
            self.registry.read_bytes(),
            trust_anchors=TrustAnchorStore(
                {TRUST_ANCHOR_KEY_ID: self.key.public_key()}
            ),
        )

    def test_initial_bootstrap_selects_binary_profile_and_no_dashboard(self) -> None:
        MODULE.prepare(version="8.4.3", operation="add", now=self.now)
        journal = self._journal()
        self.assertEqual(journal.accepted.sequence, 1)
        self.assertIsNone(journal.accepted.previous_registry_sha256)
        self.assertEqual(len(journal.accepted.entries), 1)
        entry = journal.accepted.entries[0]
        self.assertEqual(entry.version, "8.4.3")
        self.assertEqual(entry.policy_resource, "upstream_tool_policy_8_4_3.json")
        self.assertEqual(entry.dashboard_attestation.status, "quarantined")
        evidence = json.loads(
            (self.evidence / "ha-mcp-8.4.3.json").read_text(encoding="utf-8")
        )
        self.assertEqual(evidence["matched_read_count"], 25)
        self.assertEqual(evidence["withheld_reviewed_reads"], [])
        self.assertEqual(evidence["upstream_write_dispatches"], 0)
        self.assertEqual(evidence["fallback"], "none")

    def test_monotonic_linkage_duplicate_refusal_and_checkpoint(self) -> None:
        MODULE.prepare(version="8.4.3", operation="add", now=self.now)
        first = self._journal().accepted
        with self.assertRaisesRegex(SystemExit, "already has positive authority"):
            MODULE.prepare(version="8.4.3", operation="add", now=self.now)
        self._write_inputs("8.4.4")
        with patch.object(MODULE, "MAX_JOURNAL_ENVELOPES", 1):
            MODULE.prepare(version="8.4.4", operation="add", now=self.now)
        journal = self._journal()
        self.assertEqual(journal.accepted.sequence, 2)
        self.assertEqual(
            journal.accepted.previous_registry_sha256, first.content_digest
        )
        self.assertEqual(journal.checkpoint_sequence, 2)
        self.assertEqual(
            journal.checkpoint_previous_registry_sha256,
            first.content_digest,
        )

    def test_denial_only_revocation_is_retained_and_cannot_be_readded(self) -> None:
        MODULE.prepare(version="8.4.3", operation="add", now=self.now)
        evidence = self.evidence / "ha-mcp-8.4.3.json"
        evidence_before = evidence.read_bytes()
        MODULE.prepare(
            version="8.4.3",
            operation="revoke",
            revocation_reason="Synthetic test revocation.",
            now=self.now,
        )
        journal = self._journal()
        self.assertEqual(journal.accepted.entries, ())
        self.assertEqual(len(journal.accepted.revocations), 1)
        self.assertEqual(journal.accepted.revocations[0].version, "8.4.3")
        self.assertEqual(evidence.read_bytes(), evidence_before)
        with self.assertRaisesRegex(SystemExit, "revoked release cannot be re-added"):
            MODULE.prepare(version="8.4.3", operation="add", now=self.now)
        with self.assertRaisesRegex(SystemExit, "one exact positive entry"):
            MODULE.prepare(
                version="8.4.3",
                operation="revoke",
                revocation_reason="Duplicate synthetic revocation.",
                now=self.now,
            )

    def test_revocation_without_positive_authority_fails_closed(self) -> None:
        with self.assertRaisesRegex(SystemExit, "one exact positive entry"):
            MODULE.prepare(
                version="8.4.3",
                operation="revoke",
                revocation_reason="Synthetic bounded revocation.",
                now=self.now,
            )
        self.assertFalse(self.registry.exists())
        self.assertFalse(self.index.exists())

    def test_unsigned_existing_authority_cannot_be_revoked(self) -> None:
        MODULE.prepare(version="8.4.3", operation="add", now=self.now)
        malformed = json.loads(self.registry.read_text(encoding="utf-8"))
        malformed.pop("signature")
        self.registry.write_text(
            json.dumps(malformed, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        index_before = self.index.read_bytes()
        with self.assertRaisesRegex(RuntimeError, "registry_journal_invalid"):
            MODULE.prepare(
                version="8.4.3",
                operation="revoke",
                revocation_reason="Synthetic bounded revocation.",
                now=self.now,
            )
        self.assertEqual(self.index.read_bytes(), index_before)

    def test_add_and_revoke_change_only_their_exact_allowed_files(self) -> None:
        MODULE.prepare(version="8.4.3", operation="add", now=self.now)
        evidence = self.evidence / "ha-mcp-8.4.3.json"
        add_outputs = {
            path.relative_to(self.root).as_posix()
            for path in self.root.rglob("*")
            if path.is_file() and self.compat not in path.parents
        }
        self.assertEqual(
            add_outputs,
            {
                "upstream-trust/registry.json",
                "docs/evidence/ha-mcp-8.4.3.json",
                "docs/index.md",
            },
        )
        before = {
            path: path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file() and self.compat not in path.parents
        }
        MODULE.prepare(
            version="8.4.3",
            operation="revoke",
            revocation_reason="Synthetic bounded revocation.",
            now=self.now,
        )
        changed = {
            path.relative_to(self.root).as_posix()
            for path in self.root.rglob("*")
            if path.is_file()
            and self.compat not in path.parents
            and before.get(path) != path.read_bytes()
        }
        self.assertEqual(
            changed,
            {"upstream-trust/registry.json", "docs/index.md"},
        )

    def test_revocation_never_reads_release_or_runtime_evidence(self) -> None:
        MODULE.prepare(version="8.4.3", operation="add", now=self.now)
        with (
            patch.object(
                MODULE,
                "_release_evidence",
                side_effect=AssertionError("source evidence reached"),
            ),
            patch.object(
                MODULE,
                "_capture",
                side_effect=AssertionError("runtime capture reached"),
            ),
            patch.object(
                MODULE,
                "_select_entry",
                side_effect=AssertionError("profile selection reached"),
            ),
        ):
            MODULE.prepare(
                version="8.4.3",
                operation="revoke",
                revocation_reason="Synthetic bounded revocation.",
                now=self.now,
            )

    def test_revocation_reason_is_bounded_single_line_utf8(self) -> None:
        MODULE.prepare(version="8.4.3", operation="add", now=self.now)
        for reason, message in (
            (None, "bounded revocation reason"),
            ("", "bounded revocation reason"),
            ("   ", "bounded revocation reason"),
            ("x" * 513, "bounded revocation reason"),
            ("x\ny", "control character"),
            ("x\x01y", "control character"),
            ("x\u2028y", "one line"),
            ("\ud800", "not valid UTF-8"),
        ):
            with self.subTest(reason=repr(reason)), self.assertRaisesRegex(
                SystemExit, message
            ):
                MODULE.prepare(
                    version="8.4.3",
                    operation="revoke",
                    revocation_reason=reason,
                    now=self.now,
                )
        with self.assertRaisesRegex(SystemExit, "must be empty for add"):
            MODULE.prepare(
                version="8.4.4",
                operation="add",
                revocation_reason="not applicable",
                now=self.now,
            )

    def test_invalid_version_key_and_ambiguous_profile_fail_closed(self) -> None:
        with self.assertRaisesRegex(SystemExit, "exact stable semantic version"):
            MODULE.prepare(version="v8.4.3", operation="add", now=self.now)
        with patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(
            SystemExit, "signing key is missing"
        ):
            MODULE.prepare(version="8.4.3", operation="add", now=self.now)
        with patch.object(
            MODULE,
            "_signed_matching_capabilities",
            return_value=(),
        ), self.assertRaisesRegex(SystemExit, "unambiguous binary profile"):
            MODULE.prepare(version="8.4.3", operation="add", now=self.now)

    def test_mismatched_catalog_and_oversized_capture_fail_closed(self) -> None:
        capture = json.loads(self.capture_path.read_text(encoding="utf-8"))
        capture["catalog_fingerprint"] = "0" * 64
        self.capture_path.write_text(
            json.dumps(capture, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(SystemExit, "incomplete or mismatched"):
            MODULE.prepare(version="8.4.3", operation="add", now=self.now)
        self.assertFalse(self.registry.exists())

        self.capture_path.write_bytes(b"{" + b" " * MODULE.MAX_CAPTURE_BYTES)
        with self.assertRaisesRegex(SystemExit, "bounded evidence exceeds limit"):
            MODULE.prepare(version="8.4.3", operation="add", now=self.now)
        self.assertFalse(self.registry.exists())

    def test_registry_persistence_failure_cannot_activate_authority(self) -> None:
        real_replace = MODULE.os.replace
        calls = 0

        def fail_registry_write(source: Path, destination: Path) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("synthetic registry persistence failure")
            real_replace(source, destination)

        with patch.object(MODULE.os, "replace", side_effect=fail_registry_write):
            with self.assertRaisesRegex(OSError, "persistence failure"):
                MODULE.prepare(version="8.4.3", operation="add", now=self.now)
        self.assertFalse(self.registry.exists())
        self.assertFalse(self.index.exists())

    def test_failed_revocation_restores_registry_and_index(self) -> None:
        MODULE.prepare(version="8.4.3", operation="add", now=self.now)
        registry_before = self.registry.read_bytes()
        index_before = self.index.read_bytes()
        real_replace = MODULE.os.replace
        calls = 0

        def fail_registry_write(source: Path, destination: Path) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("synthetic revocation persistence failure")
            real_replace(source, destination)

        with patch.object(MODULE.os, "replace", side_effect=fail_registry_write):
            with self.assertRaisesRegex(OSError, "revocation persistence failure"):
                MODULE.prepare(
                    version="8.4.3",
                    operation="revoke",
                    revocation_reason="Synthetic bounded revocation.",
                    now=self.now,
                )
        self.assertEqual(self.registry.read_bytes(), registry_before)
        self.assertEqual(self.index.read_bytes(), index_before)

    def test_failed_staging_leaves_no_partial_state_or_temporary_file(self) -> None:
        MODULE.prepare(version="8.4.3", operation="add", now=self.now)
        registry_before = self.registry.read_bytes()
        index_before = self.index.read_bytes()
        real_mkstemp = MODULE.tempfile.mkstemp
        calls = 0

        def fail_second_stage(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("synthetic staging failure")
            return real_mkstemp(*args, **kwargs)

        with patch.object(MODULE.tempfile, "mkstemp", side_effect=fail_second_stage):
            with self.assertRaisesRegex(OSError, "staging failure"):
                MODULE.prepare(
                    version="8.4.3",
                    operation="revoke",
                    revocation_reason="Synthetic bounded revocation.",
                    now=self.now,
                )
        self.assertEqual(self.registry.read_bytes(), registry_before)
        self.assertEqual(self.index.read_bytes(), index_before)
        temporary_files = [
            path
            for directory in (self.registry.parent, self.index.parent)
            for path in directory.iterdir()
            if path.name.startswith(".")
        ]
        self.assertEqual(temporary_files, [])


if __name__ == "__main__":
    unittest.main()
