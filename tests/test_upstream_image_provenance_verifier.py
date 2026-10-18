"""Offline tests for the upstream ha-mcp attestation payload verifier (HAMCP-202).

Fixtures are synthetic BuildKit OCI indexes, attestation manifests and in-toto
statements served by an in-memory registry transport.  No network, Docker or
credentials are used.
"""

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
from urllib.parse import quote, unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "verify_upstream_image_provenance.py"
SPEC = importlib.util.spec_from_file_location(
    "verify_upstream_image_provenance", SCRIPT_PATH
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
REGISTRY = MODULE.registry

VERSION = "8.5.0"
SOURCE_COMMIT = "1" * 40
WORKFLOW_SHA = "2" * 40
RUN_ID = "23456789012"
RUN_ATTEMPT = "1"
BUILDER_ID = (
    f"https://github.com/homeassistant-ai/ha-mcp/actions/runs/{RUN_ID}"
    f"/attempts/{RUN_ATTEMPT}"
)
CREATED = "2026-09-30T12:00:00Z"
PLATFORM_DIGESTS = {
    "linux/amd64": f"sha256:{'a' * 64}",
    "linux/arm64": f"sha256:{'b' * 64}",
}
PLATFORM_SIZES = {"linux/amd64": 2001, "linux/arm64": 2002}


def json_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest_of(raw):
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


def request_arguments():
    return {
        "build-arg:BUILD_VERSION": VERSION,
        "label:org.opencontainers.image.created": CREATED,
        "label:org.opencontainers.image.revision": WORKFLOW_SHA,
        "label:org.opencontainers.image.source": MODULE.SOURCE_URL,
        "label:org.opencontainers.image.version": VERSION,
        "vcs:revision": SOURCE_COMMIT,
        "vcs:source": MODULE.SOURCE_URL,
    }


def subjects(platform, digest):
    suffix = f"?platform={quote(platform, safe='')}"
    return [
        {
            "name": f"pkg:docker/ghcr.io/homeassistant-ai/ha-mcp@{tag}{suffix}",
            "digest": {"sha256": digest.removeprefix("sha256:")},
        }
        for tag in (VERSION, "8.5", "8", "latest", "stable", VERSION)
    ]


def slsa_statement(platform, digest):
    return {
        "_type": "https://in-toto.io/Statement/v0.1",
        "predicateType": MODULE.SLSA_PREDICATE_TYPE,
        "subject": subjects(platform, digest),
        "predicate": {
            "buildDefinition": {
                "buildType": MODULE.BUILD_TYPE,
                "externalParameters": {
                    "request": {
                        "frontend": "dockerfile.v0",
                        "args": request_arguments(),
                        "root": {
                            "request": {
                                "frontend": "dockerfile.v0",
                                "args": request_arguments(),
                            }
                        },
                    }
                },
                "internalParameters": {
                    "github_repository": "homeassistant-ai/ha-mcp",
                    "github_repository_id": "1056618941",
                    "github_repository_owner_id": "232192432",
                    "github_server_url": "https://github.com",
                    "github_workflow_ref": MODULE.WORKFLOW_REF,
                    "github_workflow_sha": WORKFLOW_SHA,
                    "github_ref": "refs/heads/master",
                    "github_ref_protected": "true",
                    "github_runner_environment": "github-hosted",
                    "github_run_id": RUN_ID,
                    "github_run_attempt": RUN_ATTEMPT,
                },
            },
            "runDetails": {
                "builder": {"id": BUILDER_ID},
                "metadata": {
                    "buildkit_metadata": {
                        "vcs": {
                            "source": MODULE.SOURCE_URL,
                            "revision": SOURCE_COMMIT,
                        }
                    }
                },
            },
        },
    }


def sbom_statement(platform, digest):
    return {
        "_type": "https://in-toto.io/Statement/v0.1",
        "predicateType": MODULE.SPDX_PREDICATE_TYPE,
        "subject": subjects(platform, digest),
        "predicate": {"spdxVersion": "SPDX-2.3"},
    }


class FakeRegistryTransport:
    def __init__(self, manifests, blobs):
        self.manifests = manifests
        self.blobs = blobs
        self.requests = []

    def request(self, method, url, headers, response_limit):
        self.requests.append((method, url, dict(headers), response_limit))
        if url.startswith("https://ghcr.io/token?"):
            return REGISTRY.HttpResponse(
                status=200,
                headers={},
                body=b'{"token":"synthetic-anonymous-token"}',
            )
        parsed = urlsplit(url)
        reference = unquote(parsed.path.rsplit("/", 1)[-1])
        if parsed.hostname == "ghcr.io" and "/manifests/" in parsed.path:
            raw = self.manifests.get(reference)
            if raw is None:
                return REGISTRY.HttpResponse(status=404, headers={}, body=b"")
            return REGISTRY.HttpResponse(
                status=200,
                headers={"docker-content-digest": reference},
                body=raw,
            )
        if parsed.hostname == "ghcr.io" and "/blobs/" in parsed.path:
            return REGISTRY.HttpResponse(
                status=307,
                headers={"location": f"https://storage.invalid/{reference}"},
                body=b"",
            )
        if parsed.hostname == "storage.invalid":
            if "Authorization" in headers:
                raise AssertionError("registry authorization crossed the redirect")
            raw = self.blobs.get(reference)
            if raw is None:
                return REGISTRY.HttpResponse(status=404, headers={}, body=b"")
            return REGISTRY.HttpResponse(status=200, headers={}, body=raw)
        raise AssertionError(f"unexpected URL: {url}")


def fixture(
    *,
    legacy=False,
    sbom=False,
    statement_mutator=None,
    raw_statement_mutator=None,
    attestation_mutator=None,
    index_mutator=None,
):
    """Return (index_digest, transport) for a synthetic two-platform release."""

    manifests = {}
    blobs = {}
    entries = []
    attestation_entries = []
    for platform, digest in PLATFORM_DIGESTS.items():
        operating_system, architecture = platform.split("/")
        entries.append(
            {
                "mediaType": MODULE.IMAGE_MEDIA_TYPE,
                "digest": digest,
                "size": PLATFORM_SIZES[platform],
                "platform": {"architecture": architecture, "os": operating_system},
            }
        )
        statements = [(MODULE.SLSA_PREDICATE_TYPE, slsa_statement(platform, digest))]
        if sbom:
            statements.append(
                (MODULE.SPDX_PREDICATE_TYPE, sbom_statement(platform, digest))
            )
        layers = []
        for predicate_type, statement in statements:
            if statement_mutator is not None:
                statement_mutator(platform, predicate_type, statement)
            raw = json_bytes(statement)
            if raw_statement_mutator is not None:
                raw = raw_statement_mutator(platform, predicate_type, raw)
            blobs[digest_of(raw)] = raw
            layers.append(
                {
                    "mediaType": MODULE.IN_TOTO_MEDIA_TYPE,
                    "digest": digest_of(raw),
                    "size": len(raw),
                    "annotations": {"in-toto.io/predicate-type": predicate_type},
                }
            )
        if legacy:
            config = json_bytes({"architecture": "unknown", "os": "unknown"})
            attestation = {
                "schemaVersion": 2,
                "mediaType": MODULE.IMAGE_MEDIA_TYPE,
                "config": {
                    "mediaType": MODULE.LEGACY_CONFIG_MEDIA_TYPE,
                    "digest": digest_of(config),
                    "size": len(config),
                },
                "layers": layers,
            }
        else:
            attestation = {
                "schemaVersion": 2,
                "mediaType": MODULE.IMAGE_MEDIA_TYPE,
                "artifactType": MODULE.ATTESTATION_ARTIFACT_TYPE,
                "config": dict(MODULE.EMPTY_CONFIG),
                "layers": layers,
                "subject": {
                    "mediaType": MODULE.IMAGE_MEDIA_TYPE,
                    "digest": digest,
                    "size": PLATFORM_SIZES[platform],
                },
            }
        if attestation_mutator is not None:
            attestation_mutator(platform, attestation)
        raw_attestation = json_bytes(attestation)
        manifests[digest_of(raw_attestation)] = raw_attestation
        attestation_entries.append(
            {
                "mediaType": MODULE.IMAGE_MEDIA_TYPE,
                "digest": digest_of(raw_attestation),
                "size": len(raw_attestation),
                "annotations": {
                    "vnd.docker.reference.digest": digest,
                    "vnd.docker.reference.type": "attestation-manifest",
                },
                "platform": {"architecture": "unknown", "os": "unknown"},
            }
        )
    index = {
        "schemaVersion": 2,
        "mediaType": MODULE.INDEX_MEDIA_TYPE,
        "manifests": entries + attestation_entries,
    }
    if index_mutator is not None:
        index_mutator(index)
    raw_index = json_bytes(index)
    manifests[digest_of(raw_index)] = raw_index
    return digest_of(raw_index), FakeRegistryTransport(manifests, blobs)


def run(index_digest, transport, *, version=VERSION, source_commit=SOURCE_COMMIT):
    return MODULE.verify(
        index_digest=index_digest,
        version=version,
        source_commit=source_commit,
        transport=transport,
    )


class UpstreamProvenanceAcceptanceTests(unittest.TestCase):
    def test_current_buildkit_attestations_are_verified_per_platform(self):
        index_digest, transport = fixture()
        result = run(index_digest, transport)
        self.assertEqual(
            result,
            {
                "schema": "hamcp.upstream_image_provenance.v1",
                "image_repository": "ghcr.io/homeassistant-ai/ha-mcp",
                "image_index_digest": index_digest,
                "version": VERSION,
                "source_commit": SOURCE_COMMIT,
                "source_repository": "homeassistant-ai/ha-mcp",
                "workflow_ref": MODULE.WORKFLOW_REF,
                "workflow_sha": WORKFLOW_SHA,
                "builder_id": BUILDER_ID,
                "attested_labels": {
                    "created": CREATED,
                    "revision": WORKFLOW_SHA,
                    "source": MODULE.SOURCE_URL,
                    "version": VERSION,
                },
                "platform_digests": PLATFORM_DIGESTS,
                "attested_platforms": ["linux/amd64", "linux/arm64"],
                "slsa_provenance": "present_per_platform",
                "sbom": "absent",
                "statement_signature": "unsigned_buildkit_in_toto",
                "label_evidence": "informational_only",
            },
        )
        # Every statement blob was fetched through the redirect without auth.
        storage = [url for _m, url, _h, _l in transport.requests if "storage.invalid" in url]
        self.assertEqual(len(storage), 2)

    def test_legacy_buildkit_attestations_are_verified(self):
        index_digest, transport = fixture(legacy=True)
        self.assertEqual(run(index_digest, transport)["workflow_sha"], WORKFLOW_SHA)

    def test_sbom_is_reported_only_when_attested_for_every_platform(self):
        index_digest, transport = fixture(sbom=True)
        self.assertEqual(run(index_digest, transport)["sbom"], "present")

    def test_unselected_platforms_are_attested_but_not_emitted(self):
        def add_platform(index):
            digest = f"sha256:{'c' * 64}"
            index["manifests"].append(
                {
                    "mediaType": MODULE.IMAGE_MEDIA_TYPE,
                    "digest": digest,
                    "size": 2003,
                    "platform": {"architecture": "s390x", "os": "linux"},
                }
            )

        # An extra platform without an attestation is refused outright.
        index_digest, transport = fixture(index_mutator=add_platform)
        with self.assertRaisesRegex(
            MODULE.VerificationError, "UPSTREAM_ATTESTATION_MISSING_OR_UNBOUND"
        ):
            run(index_digest, transport)

    def test_cli_writes_result_and_refuses_with_reason(self):
        index_digest, transport = fixture()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "provenance.json"
            with mock.patch.object(REGISTRY, "UrllibTransport", return_value=transport):
                code = MODULE.main(
                    [
                        "--index-digest",
                        index_digest,
                        "--version",
                        VERSION,
                        "--source-commit",
                        SOURCE_COMMIT,
                        "--output",
                        str(output),
                    ]
                )
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output.read_text())["workflow_sha"], WORKFLOW_SHA)

            refused = Path(directory) / "refused.json"
            with mock.patch.object(REGISTRY, "UrllibTransport", return_value=transport):
                with mock.patch("sys.stderr") as stderr:
                    code = MODULE.main(
                        [
                            "--index-digest",
                            index_digest,
                            "--version",
                            VERSION,
                            "--source-commit",
                            "3" * 40,
                            "--output",
                            str(refused),
                        ]
                    )
            self.assertEqual(code, 1)
            self.assertFalse(refused.exists())
            written = "".join(call.args[0] for call in stderr.write.call_args_list)
            self.assertIn("UPSTREAM_PROVENANCE_COMMIT_MISMATCH", written)


class UpstreamProvenanceRefusalTests(unittest.TestCase):
    def assertRefused(self, reason, **fixture_options):
        overrides = {
            key: fixture_options.pop(key)
            for key in ("version", "source_commit", "digest_override")
            if key in fixture_options
        }
        index_digest, transport = fixture(**fixture_options)
        index_digest = overrides.pop("digest_override", index_digest)
        with self.assertRaises(MODULE.VerificationError) as caught:
            run(index_digest, transport, **overrides)
        self.assertEqual(str(caught.exception), reason)

    def test_label_only_index_without_attestations_is_refused(self):
        def strip(index):
            index["manifests"] = [
                entry for entry in index["manifests"] if "annotations" not in entry
            ]

        self.assertRefused("UPSTREAM_ATTESTATION_MISSING_OR_UNBOUND", index_mutator=strip)

    def test_missing_platform_attestation_is_refused(self):
        def drop_one(index):
            index["manifests"] = [
                entry
                for entry in index["manifests"]
                if entry.get("annotations", {}).get("vnd.docker.reference.digest")
                != PLATFORM_DIGESTS["linux/arm64"]
            ]

        self.assertRefused("UPSTREAM_ATTESTATION_MISSING_OR_UNBOUND", index_mutator=drop_one)

    def test_attestation_referencing_unknown_digest_is_refused(self):
        def rebind(index):
            index["manifests"][-1]["annotations"]["vnd.docker.reference.digest"] = (
                f"sha256:{'9' * 64}"
            )

        self.assertRefused("UPSTREAM_ATTESTATION_MISSING_OR_UNBOUND", index_mutator=rebind)

    def test_duplicate_attestation_reference_is_refused(self):
        def duplicate(index):
            index["manifests"].append(copy.deepcopy(index["manifests"][-1]))

        self.assertRefused("UPSTREAM_ATTESTATION_AMBIGUOUS", index_mutator=duplicate)

    def test_duplicate_platform_is_refused(self):
        def duplicate(index):
            entry = copy.deepcopy(index["manifests"][0])
            entry["digest"] = f"sha256:{'c' * 64}"
            index["manifests"].insert(1, entry)

        self.assertRefused("UPSTREAM_INDEX_PLATFORM_AMBIGUOUS", index_mutator=duplicate)

    def test_missing_required_platform_is_refused(self):
        def drop_arm64(index):
            index["manifests"] = [
                entry
                for entry in index["manifests"]
                if entry.get("platform", {}).get("architecture") != "arm64"
                and entry.get("annotations", {}).get("vnd.docker.reference.digest")
                != PLATFORM_DIGESTS["linux/arm64"]
            ]

        self.assertRefused("UPSTREAM_REQUIRED_PLATFORM_MISSING", index_mutator=drop_arm64)

    def test_extra_annotation_or_wrong_attestation_platform_is_refused(self):
        def extra(index):
            index["manifests"][-1]["annotations"]["org.example"] = "x"

        def platform(index):
            index["manifests"][-1]["platform"] = {"architecture": "amd64", "os": "linux"}

        self.assertRefused("UPSTREAM_INDEX_ANNOTATIONS_INVALID", index_mutator=extra)
        self.assertRefused("UPSTREAM_ATTESTATION_PLATFORM_INVALID", index_mutator=platform)

    def test_index_digest_must_match_fetched_bytes(self):
        index_digest, transport = fixture()
        other = f"sha256:{'8' * 64}"
        transport.manifests[other] = transport.manifests[index_digest]
        with self.assertRaisesRegex(MODULE.VerificationError, "UPSTREAM_INDEX_DIGEST_MISMATCH"):
            run(other, transport)
        with self.assertRaisesRegex(MODULE.VerificationError, "UPSTREAM_INDEX_DIGEST_INVALID"):
            run("latest", transport)

    def test_attestation_subject_must_match_platform_descriptor(self):
        def mutate(platform, attestation):
            if platform == "linux/arm64":
                attestation["subject"]["digest"] = PLATFORM_DIGESTS["linux/amd64"]

        self.assertRefused("UPSTREAM_ATTESTATION_SUBJECT_MISMATCH", attestation_mutator=mutate)

    def test_attestation_without_slsa_or_with_unknown_predicate_is_refused(self):
        def unknown(platform, attestation):
            attestation["layers"][0]["annotations"]["in-toto.io/predicate-type"] = (
                "https://example.invalid/predicate"
            )

        def sbom_only(platform, attestation):
            attestation["layers"] = attestation["layers"][1:]

        self.assertRefused(
            "UPSTREAM_ATTESTATION_PREDICATE_TYPE_INVALID", attestation_mutator=unknown
        )
        self.assertRefused(
            "UPSTREAM_SLSA_PROVENANCE_MISSING", sbom=True, attestation_mutator=sbom_only
        )

    def test_statement_predicate_type_must_match_annotation(self):
        def mutate(platform, predicate_type, statement):
            statement["predicateType"] = MODULE.SPDX_PREDICATE_TYPE

        self.assertRefused(
            "UPSTREAM_STATEMENT_PREDICATE_TYPE_MISMATCH", statement_mutator=mutate
        )

    def test_every_subject_must_name_the_platform_digest(self):
        def wrong_digest(platform, predicate_type, statement):
            if platform == "linux/arm64":
                statement["subject"][3]["digest"]["sha256"] = "a" * 64

        def wrong_platform(platform, predicate_type, statement):
            if platform == "linux/amd64":
                statement["subject"][0]["name"] = statement["subject"][0]["name"].replace(
                    "linux%2Famd64", "linux%2Farm64"
                )

        def missing_version(platform, predicate_type, statement):
            statement["subject"] = [
                subject
                for subject in statement["subject"]
                if f"@{VERSION}?" not in subject["name"]
            ]

        def extra_key(platform, predicate_type, statement):
            statement["subject"][0]["annotations"] = {}

        self.assertRefused(
            "UPSTREAM_STATEMENT_SUBJECT_DIGEST_MISMATCH", statement_mutator=wrong_digest
        )
        self.assertRefused("UPSTREAM_STATEMENT_SUBJECT_INVALID", statement_mutator=wrong_platform)
        self.assertRefused(
            "UPSTREAM_STATEMENT_VERSION_SUBJECT_MISSING", statement_mutator=missing_version
        )
        self.assertRefused("UPSTREAM_STATEMENT_SUBJECT_INVALID", statement_mutator=extra_key)

    def test_source_commit_must_match_attested_vcs_revision(self):
        self.assertRefused("UPSTREAM_PROVENANCE_COMMIT_MISMATCH", source_commit="3" * 40)

        def vcs(platform, predicate_type, statement):
            statement["predicate"]["runDetails"]["metadata"]["buildkit_metadata"]["vcs"][
                "source"
            ] = "https://github.com/example/fork"

        self.assertRefused("UPSTREAM_PROVENANCE_SOURCE_MISMATCH", statement_mutator=vcs)

    def test_requested_version_must_match_attested_version(self):
        self.assertRefused("UPSTREAM_STATEMENT_VERSION_SUBJECT_MISSING", version="8.5.1")

    def test_builder_identity_fields_are_exact(self):
        cases = {
            "github_repository": "example/ha-mcp",
            "github_repository_id": "1",
            "github_repository_owner_id": "1",
            "github_workflow_ref": (
                "homeassistant-ai/ha-mcp/.github/workflows/other.yml@refs/heads/master"
            ),
            "github_ref": "refs/heads/feature",
            "github_ref_protected": "false",
            "github_runner_environment": "self-hosted",
            "github_run_id": "1",
            "github_run_attempt": "2",
        }
        for key, value in cases.items():
            def mutate(platform, predicate_type, statement, key=key, value=value):
                statement["predicate"]["buildDefinition"]["internalParameters"][key] = value

            with self.subTest(field=key):
                self.assertRefused(
                    "UPSTREAM_PROVENANCE_IDENTITY_MISMATCH", statement_mutator=mutate
                )

        def builder(platform, predicate_type, statement):
            statement["predicate"]["runDetails"]["builder"]["id"] = (
                "https://github.com/example/ha-mcp/actions/runs/1/attempts/1"
            )

        def build_type(platform, predicate_type, statement):
            statement["predicate"]["buildDefinition"]["buildType"] = "https://example.invalid"

        def workflow_sha(platform, predicate_type, statement):
            statement["predicate"]["buildDefinition"]["internalParameters"][
                "github_workflow_sha"
            ] = "main"

        self.assertRefused("UPSTREAM_PROVENANCE_BUILDER_INVALID", statement_mutator=builder)
        self.assertRefused("UPSTREAM_PROVENANCE_BUILD_TYPE_INVALID", statement_mutator=build_type)
        self.assertRefused(
            "UPSTREAM_PROVENANCE_IDENTITY_INVALID", statement_mutator=workflow_sha
        )

    def test_attested_request_arguments_must_agree(self):
        mutations = {
            "label:org.opencontainers.image.revision": SOURCE_COMMIT,
            "label:org.opencontainers.image.version": "8.5.1",
            "build-arg:BUILD_VERSION": "8.5.1",
            "vcs:revision": "3" * 40,
            "vcs:source": "https://github.com/example/fork",
        }
        for key, value in mutations.items():
            for location in ("args", "root"):
                def mutate(platform, predicate_type, statement, key=key, value=value, location=location):
                    request = statement["predicate"]["buildDefinition"][
                        "externalParameters"
                    ]["request"]
                    target = request["args"] if location == "args" else request["root"]["request"]["args"]
                    target[key] = value

                with self.subTest(argument=key, location=location):
                    self.assertRefused(
                        "UPSTREAM_PROVENANCE_REQUEST_MISMATCH", statement_mutator=mutate
                    )

    def test_label_revision_and_version_must_be_attested(self):
        for key in (
            "label:org.opencontainers.image.revision",
            "label:org.opencontainers.image.version",
        ):
            def mutate(platform, predicate_type, statement, key=key):
                request = statement["predicate"]["buildDefinition"]["externalParameters"][
                    "request"
                ]
                request["args"].pop(key)
                request["root"]["request"]["args"].pop(key)

            with self.subTest(argument=key):
                self.assertRefused(
                    "UPSTREAM_PROVENANCE_REQUEST_MISMATCH", statement_mutator=mutate
                )

    def test_platform_statements_must_bind_the_same_build(self):
        def mutate(platform, predicate_type, statement):
            if platform == "linux/arm64":
                internal = statement["predicate"]["buildDefinition"]["internalParameters"]
                internal["github_run_id"] = "23456789013"
                statement["predicate"]["runDetails"]["builder"]["id"] = BUILDER_ID.replace(
                    RUN_ID, "23456789013"
                )

        self.assertRefused("UPSTREAM_PROVENANCE_PLATFORM_INCONSISTENT", statement_mutator=mutate)

    def test_statement_type_and_json_strictness(self):
        def statement_type(platform, predicate_type, statement):
            statement["_type"] = "https://in-toto.io/Statement/v9"

        def duplicate_key(platform, predicate_type, raw):
            return raw.replace(b'{"_type":', b'{"_type":"x","_type":', 1)

        self.assertRefused("UPSTREAM_STATEMENT_TYPE_INVALID", statement_mutator=statement_type)
        self.assertRefused(
            "JSON_DUPLICATE_KEY", raw_statement_mutator=duplicate_key
        )

    def test_tampered_statement_blob_is_refused(self):
        index_digest, transport = fixture()
        layer_digest = next(iter(transport.blobs))
        raw = transport.blobs[layer_digest]
        transport.blobs[layer_digest] = raw.replace(WORKFLOW_SHA.encode(), b"3" * 40)
        with self.assertRaisesRegex(
            MODULE.VerificationError, "REGISTRY_EVIDENCE_DIGEST_MISMATCH"
        ):
            run(index_digest, transport)

    def test_missing_statement_blob_is_refused(self):
        index_digest, transport = fixture()
        transport.blobs.clear()
        with self.assertRaisesRegex(MODULE.VerificationError, "REGISTRY_EVIDENCE_UNAVAILABLE"):
            run(index_digest, transport)


class NoLabelEvidenceTests(unittest.TestCase):
    def test_verifier_never_reads_image_configs_or_labels(self):
        index_digest, transport = fixture()
        run(index_digest, transport)
        fetched = {unquote(urlsplit(url).path.rsplit("/", 1)[-1]) for _m, url, _h, _l in transport.requests}
        for digest in PLATFORM_DIGESTS.values():
            self.assertNotIn(digest, fetched)

    def test_source_does_not_parse_labels(self):
        source = SCRIPT_PATH.read_text(encoding="utf-8")
        self.assertNotIn("Config.Labels", source)
        self.assertNotIn('"config"]["Labels"', source)
        self.assertNotIn("Labels", source)
        self.assertNotIn("docker image inspect", source)


if __name__ == "__main__":
    unittest.main()
