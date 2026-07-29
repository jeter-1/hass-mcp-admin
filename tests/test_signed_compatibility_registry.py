from __future__ import annotations

import ast
import base64
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
BETA = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(BETA))

from ha_mcp_engineering.signed_registry import (  # noqa: E402
    MAX_CLOCK_SKEW,
    AcceptedRegistryState,
    RegistryErrorCode,
    RegistryValidationError,
    ReviewedReleaseEntry,
    TrustAnchorStore,
    ValidationStatus,
    canonical_json,
    sha256_digest,
    validate_registry_envelope,
)
from tests.signed_registry_fixtures import (  # noqa: E402
    NOW,
    SECOND_DIGEST,
    TEST_KEY_ID,
    RegistrySigner,
    reviewed_entry,
    revocation,
)


COMPILED_REGISTRY = (
    BETA / "ha_mcp_engineering" / "upstream_release_registry.json"
)
POLICY_7142 = (
    BETA / "ha_mcp_engineering" / "upstream_tool_policy_7_14_2.json"
)
SIGNED_PACKAGE = (
    BETA / "ha_mcp_engineering" / "signed_registry"
)
RUNTIME_PACKAGE = BETA / "ha_mcp_engineering"


def issue_code(result) -> RegistryErrorCode:
    if len(result.issues) != 1:
        raise AssertionError(f"unexpected validation issues: {result}")
    return result.issues[0].code


def parsed(raw: bytes) -> dict:
    return json.loads(raw.decode("utf-8"))


class CanonicalRegistryTests(unittest.TestCase):
    def test_canonical_serialization_and_digest_are_deterministic(self):
        first = {
            "z": [{"beta": 2, "alpha": 1}],
            "a": "snowman \N{SNOWMAN}",
        }
        second = {
            "a": "snowman \N{SNOWMAN}",
            "z": [{"alpha": 1, "beta": 2}],
        }
        expected = (
            b'{"a":"snowman \xe2\x98\x83",'
            b'"z":[{"alpha":1,"beta":2}]}'
        )
        self.assertEqual(canonical_json(first), expected)
        self.assertEqual(canonical_json(second), expected)
        self.assertEqual(sha256_digest(first), sha256_digest(second))
        self.assertRegex(
            sha256_digest(first),
            r"^sha256:[0-9a-f]{64}$",
        )

    def test_envelope_field_order_is_independent_before_canonicalization(
        self,
    ):
        signer = RegistrySigner()
        unsigned = signer.unsigned()
        raw = signer.raw_with_order(
            unsigned,
            (
                "signature",
                "revocations",
                "entries",
                "key_id",
                "previous_registry_sha256",
                "expires_at",
                "generated_at",
                "sequence",
                "registry_id",
                "schema_version",
            ),
        )
        result = validate_registry_envelope(
            raw,
            trust_anchors=signer.trust_anchors,
            now=NOW,
        )
        self.assertEqual(result.status, ValidationStatus.ACCEPTED)
        self.assertEqual(
            result.envelope.canonical_unsigned
            if result.envelope
            else None,
            canonical_json(unsigned),
        )


class SignatureAndTrustTests(unittest.TestCase):
    def setUp(self) -> None:
        self.signer = RegistrySigner()

    def validate(self, raw: bytes, **kwargs):
        return validate_registry_envelope(
            raw,
            trust_anchors=self.signer.trust_anchors,
            now=NOW,
            **kwargs,
        )

    def test_valid_signature_and_content_digest(self):
        raw = self.signer.raw()
        result = self.validate(raw)
        self.assertTrue(result.accepted)
        self.assertEqual(result.status, ValidationStatus.ACCEPTED)
        self.assertIsNotNone(result.envelope)
        self.assertEqual(
            result.content_digest,
            result.envelope.content_digest
            if result.envelope
            else None,
        )
        self.assertEqual(result.issues, ())

    def test_invalid_signature_is_rejected(self):
        mapping = parsed(self.signer.raw())
        signature = bytearray(base64.b64decode(mapping["signature"]))
        signature[0] ^= 1
        mapping["signature"] = base64.b64encode(signature).decode(
            "ascii"
        )
        result = self.validate(canonical_json(mapping))
        self.assertFalse(result.accepted)
        self.assertEqual(
            issue_code(result),
            RegistryErrorCode.INVALID_SIGNATURE,
        )
        self.assertIsNone(result.content_digest)
        self.assertIsNone(result.envelope)

    def test_payload_tampering_after_signature_is_rejected(self):
        mapping = parsed(self.signer.raw())
        mapping["entries"][0]["catalog_fingerprint"] = "2" * 64
        result = self.validate(canonical_json(mapping))
        self.assertEqual(
            issue_code(result),
            RegistryErrorCode.INVALID_SIGNATURE,
        )

    def test_unknown_key_fails_closed(self):
        unknown = RegistrySigner(key_id="unknown-test-key")
        result = validate_registry_envelope(
            unknown.raw(),
            trust_anchors=self.signer.trust_anchors,
            now=NOW,
        )
        self.assertEqual(
            issue_code(result),
            RegistryErrorCode.UNKNOWN_KEY,
        )
        self.assertIsNone(result.content_digest)
        self.assertIsNone(result.envelope)

    def test_strict_public_anchor_base64_and_length(self):
        store = TrustAnchorStore.from_base64(
            {TEST_KEY_ID: self.signer.public_key_base64}
        )
        self.assertIsNotNone(store.lookup(TEST_KEY_ID))
        for invalid in ("not-base64!", base64.b64encode(b"x").decode()):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(
                    RegistryValidationError,
                    RegistryErrorCode.TRUST_ANCHOR_INVALID.value,
                ):
                    TrustAnchorStore.from_base64(
                        {TEST_KEY_ID: invalid}
                    )

    def test_no_production_private_signing_material_or_helper(self):
        sources = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(SIGNED_PACKAGE.glob("*.py"))
        )
        self.assertNotIn("Ed25519PrivateKey", sources)
        self.assertNotIn("private_bytes", sources)
        self.assertNotRegex(sources, r"\bdef sign\(")

    def test_runtime_does_not_load_registry_foundation(self):
        importers = []
        for path in sorted(RUNTIME_PACKAGE.rglob("*.py")):
            if SIGNED_PACKAGE in path.parents:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import) and any(
                    "signed_registry" in item.name
                    for item in node.names
                ):
                    importers.append(
                        path.relative_to(ROOT).as_posix()
                    )
                if (
                    isinstance(node, ast.ImportFrom)
                    and node.module is not None
                    and "signed_registry" in node.module
                ):
                    importers.append(
                        path.relative_to(ROOT).as_posix()
                    )
        self.assertEqual(importers, [])


class OrderingAndTimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.signer = RegistrySigner()
        first_result = validate_registry_envelope(
            self.signer.raw(),
            trust_anchors=self.signer.trust_anchors,
            now=NOW,
        )
        if first_result.envelope is None:
            raise AssertionError(first_result)
        self.first = first_result.envelope
        self.state = AcceptedRegistryState.from_envelope(self.first)

    def validate(self, raw: bytes, *, state=None):
        return validate_registry_envelope(
            raw,
            trust_anchors=self.signer.trust_anchors,
            now=NOW,
            accepted_state=state,
        )

    def test_lower_sequence_is_rejected_as_rollback(self):
        accepted = AcceptedRegistryState(
            registry_id=self.state.registry_id,
            sequence=2,
            content_digest=SECOND_DIGEST,
        )
        result = self.validate(self.signer.raw(), state=accepted)
        self.assertEqual(
            issue_code(result),
            RegistryErrorCode.ROLLBACK,
        )

    def test_same_sequence_same_digest_is_idempotent_replay(self):
        result = self.validate(
            canonical_json(self.first.to_mapping()),
            state=self.state,
        )
        self.assertTrue(result.accepted)
        self.assertEqual(
            result.status,
            ValidationStatus.IDEMPOTENT_REPLAY,
        )

    def test_same_sequence_different_digest_is_replay_conflict(self):
        raw = self.signer.raw(
            expires_at=NOW + timedelta(days=2),
        )
        result = self.validate(raw, state=self.state)
        self.assertEqual(
            issue_code(result),
            RegistryErrorCode.REPLAY_CONFLICT,
        )

    def test_higher_sequence_requires_previous_digest_chain(self):
        broken = self.signer.raw(
            sequence=2,
            previous_registry_sha256=SECOND_DIGEST,
        )
        result = self.validate(broken, state=self.state)
        self.assertEqual(
            issue_code(result),
            RegistryErrorCode.PREVIOUS_DIGEST_MISMATCH,
        )

        valid = self.signer.raw(
            sequence=2,
            previous_registry_sha256=self.state.content_digest,
        )
        accepted = self.validate(valid, state=self.state)
        self.assertEqual(
            accepted.status,
            ValidationStatus.ACCEPTED,
        )

    def test_expired_registry_is_rejected(self):
        raw = self.signer.raw(
            generated_at=NOW - timedelta(days=2),
            expires_at=NOW,
        )
        result = self.validate(raw)
        self.assertEqual(
            issue_code(result),
            RegistryErrorCode.EXPIRED,
        )

    def test_timestamp_beyond_bounded_future_skew_is_rejected(self):
        self.assertEqual(MAX_CLOCK_SKEW, timedelta(minutes=5))
        raw = self.signer.raw(
            generated_at=NOW + MAX_CLOCK_SKEW
            + timedelta(seconds=1),
            expires_at=NOW + timedelta(days=1),
        )
        result = self.validate(raw)
        self.assertEqual(
            issue_code(result),
            RegistryErrorCode.GENERATED_IN_FUTURE,
        )

    def test_noninitial_registry_cannot_bootstrap_without_chain_state(self):
        raw = self.signer.raw(
            sequence=2,
            previous_registry_sha256=SECOND_DIGEST,
        )
        result = self.validate(raw)
        self.assertEqual(
            issue_code(result),
            RegistryErrorCode.INITIAL_CHAIN_INVALID,
        )


class StrictModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.signer = RegistrySigner()

    def sign_and_validate(self, unsigned: dict):
        return validate_registry_envelope(
            canonical_json(self.signer.sign_mapping(unsigned)),
            trust_anchors=self.signer.trust_anchors,
            now=NOW,
        )

    def test_duplicate_entries_are_rejected(self):
        entry = reviewed_entry()
        unsigned = self.signer.unsigned(
            entries=[entry, deepcopy(entry)]
        )
        result = self.sign_and_validate(unsigned)
        self.assertEqual(
            issue_code(result),
            RegistryErrorCode.DUPLICATE_ENTRY,
        )

    def test_revocation_model_and_entry_contradiction(self):
        only_revocation = self.signer.unsigned(
            entries=[],
            revocations=[revocation()],
        )
        valid = self.sign_and_validate(only_revocation)
        self.assertEqual(valid.status, ValidationStatus.ACCEPTED)
        self.assertEqual(
            valid.envelope.revocations[0].entry_id
            if valid.envelope
            else None,
            revocation()["entry_id"],
        )
        self.assertEqual(
            valid.envelope.revocations[0].revocation_identity
            if valid.envelope
            else None,
            (
                revocation()["entry_id"],
                revocation()["server_name"],
                revocation()["version"],
                revocation()["image_index_digest"],
            ),
        )

        entry = reviewed_entry()
        contradictory = self.signer.unsigned(
            entries=[entry],
            revocations=[
                revocation(
                    entry_id=entry["entry_id"],
                    version=entry["version"],
                    image_index_digest=entry[
                        "image_index_digest"
                    ],
                )
            ],
        )
        rejected = self.sign_and_validate(contradictory)
        self.assertEqual(
            issue_code(rejected),
            RegistryErrorCode.ENTRY_REVOCATION_CONTRADICTION,
        )

    def test_duplicate_revocations_are_rejected(self):
        tombstone = revocation()
        unsigned = self.signer.unsigned(
            entries=[],
            revocations=[tombstone, deepcopy(tombstone)],
        )
        result = self.sign_and_validate(unsigned)
        self.assertEqual(
            issue_code(result),
            RegistryErrorCode.DUPLICATE_REVOCATION,
        )

    def test_malformed_digest_and_signature_fields_are_rejected(self):
        malformed_digest = self.signer.unsigned(
            sequence=2,
            previous_registry_sha256="not-a-digest",
        )
        digest_result = self.sign_and_validate(malformed_digest)
        self.assertEqual(
            issue_code(digest_result),
            RegistryErrorCode.PREVIOUS_DIGEST_INVALID,
        )

        malformed_image = self.signer.unsigned()
        malformed_image["entries"][0]["image_index_digest"] = (
            "sha256:not-hex"
        )
        image_result = self.sign_and_validate(malformed_image)
        self.assertEqual(
            issue_code(image_result),
            RegistryErrorCode.ENTRY_IMAGE_INVALID,
        )

        for signature, code in (
            ("not-base64!", RegistryErrorCode.SIGNATURE_ENCODING_INVALID),
            (
                base64.b64encode(b"short").decode("ascii"),
                RegistryErrorCode.SIGNATURE_LENGTH_INVALID,
            ),
        ):
            with self.subTest(code=code):
                mapping = self.signer.sign_mapping(
                    self.signer.unsigned()
                )
                mapping["signature"] = signature
                result = validate_registry_envelope(
                    canonical_json(mapping),
                    trust_anchors=self.signer.trust_anchors,
                    now=NOW,
                )
                self.assertEqual(issue_code(result), code)

    def test_unknown_schema_and_unknown_fields_are_rejected(self):
        unknown_schema = self.signer.unsigned(schema_version=2)
        schema_result = self.sign_and_validate(unknown_schema)
        self.assertEqual(
            issue_code(schema_result),
            RegistryErrorCode.UNKNOWN_SCHEMA_VERSION,
        )

        for mutate in (
            lambda value: value.update({"unexpected": True}),
            lambda value: value["entries"][0].update(
                {"unexpected": True}
            ),
            lambda value: value["entries"][0]["tool_contracts"][
                0
            ].update({"unexpected": True}),
        ):
            with self.subTest(mutate=mutate):
                unsigned = self.signer.unsigned()
                mutate(unsigned)
                result = self.sign_and_validate(unsigned)
                self.assertIn(
                    issue_code(result),
                    {
                        RegistryErrorCode.ENVELOPE_FIELDS_INVALID,
                        RegistryErrorCode.ENTRY_FIELDS_INVALID,
                        RegistryErrorCode.TOOL_CONTRACT_INVALID,
                    },
                )

    def test_duplicate_json_members_are_rejected(self):
        result = validate_registry_envelope(
            b'{"schema_version":1,"schema_version":1}',
            trust_anchors=self.signer.trust_anchors,
            now=NOW,
        )
        self.assertEqual(
            issue_code(result),
            RegistryErrorCode.DUPLICATE_JSON_MEMBER,
        )

    def test_validation_output_is_deterministic_and_bounded(self):
        invalid = canonical_json(
            {
                **self.signer.sign_mapping(self.signer.unsigned()),
                "unexpected": "raw untrusted registry content",
            }
        )
        first = validate_registry_envelope(
            invalid,
            trust_anchors=self.signer.trust_anchors,
            now=NOW,
        )
        second = validate_registry_envelope(
            invalid,
            trust_anchors=self.signer.trust_anchors,
            now=NOW,
        )
        self.assertEqual(first, second)
        self.assertEqual(first.envelope, None)
        self.assertEqual(first.content_digest, None)
        self.assertEqual(
            first.issues,
            (
                first.issues[0],
            ),
        )
        self.assertNotIn(
            b"raw untrusted registry content",
            repr(first).encode("utf-8"),
        )

    def test_compiled_release_evidence_projects_without_translation(self):
        before = COMPILED_REGISTRY.read_bytes()
        compiled = json.loads(before)
        release = next(
            item
            for item in compiled["releases"]
            if item["version"] == "7.14.2"
        )
        policy = json.loads(POLICY_7142.read_text(encoding="utf-8"))
        restrictions = {
            item["upstream_name"]: item["argument_restrictions"]
            for item in policy["tools"]
        }
        projected = deepcopy(release)
        projected["tool_contracts"] = [
            {
                "tool_name": name,
                **contract,
                "argument_restrictions": restrictions[name],
            }
            for name, contract in sorted(
                release["tool_contracts"].items()
            )
        ]
        projected["provider_argument_constraints"] = [
            {
                "provider_id": "upstream_dashboard",
                "tool_name": "ha_config_get_dashboard",
                "constraints_fingerprint": release[
                    "dashboard_attestation"
                ]["compiled_constraints_fingerprint"],
            }
        ]

        model = ReviewedReleaseEntry.from_mapping(projected)
        self.assertEqual(model.server_name, release["server_name"])
        self.assertEqual(model.version, release["version"])
        self.assertEqual(
            model.allowed_protocol_versions,
            tuple(release["allowed_protocol_versions"]),
        )
        self.assertEqual(
            model.source_commit,
            release["source_commit"],
        )
        self.assertEqual(
            model.image_index_digest,
            release["image_index_digest"],
        )
        self.assertEqual(
            dict(model.architecture_image_digests),
            release["architecture_image_digests"],
        )
        self.assertEqual(
            model.catalog_fingerprint,
            release["catalog_fingerprint"],
        )
        self.assertEqual(model.advertised_tool_count, 78)
        self.assertEqual(len(model.tool_contracts), 78)
        self.assertEqual(
            sum(
                item.reviewed_automatic_read
                for item in model.tool_contracts
            ),
            26,
        )
        self.assertEqual(COMPILED_REGISTRY.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
