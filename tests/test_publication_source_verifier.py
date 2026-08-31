import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "verify_publication_source_image.py"
SPEC = importlib.util.spec_from_file_location(
    "verify_publication_source_image", SCRIPT_PATH
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)

REPOSITORY = "jeter-1/hass-mcp-admin"
OWNER = "jeter-1"
RELEASE_SHA = "1" * 40
WORKFLOW_SHA = "2" * 40
VERSION = "2.2.0-beta.53"
BUILD_TIME = "2026-08-31T10:10:06Z"
RUN_ID = "33379623142"
RUN_ATTEMPT = 1
SOURCE_URL = f"https://github.com/{REPOSITORY}"

PLATFORM_DIGESTS = {
    "linux/amd64": f"sha256:{'a' * 64}",
    "linux/arm64": f"sha256:{'b' * 64}",
    "linux/arm/v7": f"sha256:{'c' * 64}",
}
ATTESTATION_DIGESTS = (
    f"sha256:{'d' * 64}",
    f"sha256:{'e' * 64}",
    f"sha256:{'f' * 64}",
)


def json_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def valid_manifest():
    entries = []
    for platform, (os_name, architecture, variant, _environment) in (
        MODULE.REQUIRED_PLATFORMS.items()
    ):
        platform_value = {"architecture": architecture, "os": os_name}
        if variant is not None:
            platform_value["variant"] = variant
        entries.append(
            {
                "digest": PLATFORM_DIGESTS[platform],
                "mediaType": MODULE.IMAGE_MEDIA_TYPE,
                "platform": platform_value,
                "size": 1234,
            }
        )
    for digest, subject in zip(
        ATTESTATION_DIGESTS, PLATFORM_DIGESTS.values(), strict=True
    ):
        entries.append(
            {
                "annotations": {
                    "vnd.docker.reference.digest": subject,
                    "vnd.docker.reference.type": MODULE.ATTESTATION_TYPE,
                },
                "digest": digest,
                "mediaType": MODULE.IMAGE_MEDIA_TYPE,
                "platform": {"architecture": "unknown", "os": "unknown"},
                "size": 456,
            }
        )
    return {
        "manifests": entries,
        "mediaType": MODULE.INDEX_MEDIA_TYPE,
        "schemaVersion": 2,
    }


def required_labels():
    return MODULE._required_labels(RELEASE_SHA, VERSION, BUILD_TIME, SOURCE_URL)


def valid_images():
    result = {}
    for platform, (os_name, architecture, variant, _environment) in (
        MODULE.REQUIRED_PLATFORMS.items()
    ):
        image = {
            "architecture": architecture,
            "config": {"Labels": required_labels()},
            "os": os_name,
        }
        if variant is not None:
            image["variant"] = variant
        result[platform] = image
    return result


def valid_provenance():
    build_args = MODULE._required_build_args(
        RELEASE_SHA, VERSION, BUILD_TIME, SOURCE_URL
    )
    root_args = {
        **build_args,
        "vcs:localdir:context": MODULE.SOURCE_DIRECTORY,
        "vcs:localdir:dockerfile": MODULE.SOURCE_DIRECTORY,
        "vcs:revision": RELEASE_SHA,
        "vcs:source": SOURCE_URL,
    }
    internal = {
        "github_actor": OWNER,
        "github_event_name": "workflow_dispatch",
        "github_event_payload": {
            "inputs": {
                "expected_version": VERSION,
                "release_sha": RELEASE_SHA,
            },
            "ref": "refs/heads/main",
            "repository": {"full_name": REPOSITORY},
        },
        "github_job": "promote",
        "github_ref": "refs/heads/main",
        "github_ref_name": "main",
        "github_ref_protected": "true",
        "github_ref_type": "branch",
        "github_repository": REPOSITORY,
        "github_repository_owner": OWNER,
        "github_run_attempt": str(RUN_ATTEMPT),
        "github_run_id": RUN_ID,
        "github_server_url": "https://github.com",
        "github_triggering_actor": OWNER,
        "github_workflow": MODULE.WORKFLOW_NAME,
        "github_workflow_ref": (
            f"{REPOSITORY}/{MODULE.WORKFLOW_PATH}@refs/heads/main"
        ),
        "github_workflow_sha": WORKFLOW_SHA,
    }
    vcs = {
        "localdir:context": MODULE.SOURCE_DIRECTORY,
        "localdir:dockerfile": MODULE.SOURCE_DIRECTORY,
        "revision": RELEASE_SHA,
        "source": SOURCE_URL,
    }
    result = {}
    for platform in MODULE.REQUIRED_PLATFORMS:
        result[platform] = {
            "SLSA": {
                "buildDefinition": {
                    "buildType": MODULE.BUILD_TYPE,
                    "externalParameters": {
                        "request": {
                            "args": copy.deepcopy(build_args),
                            "root": {
                                "request": {"args": copy.deepcopy(root_args)}
                            },
                        }
                    },
                    "internalParameters": copy.deepcopy(internal),
                },
                "runDetails": {
                    "builder": {
                        "id": (
                            f"https://github.com/{REPOSITORY}/actions/runs/"
                            f"{RUN_ID}/attempts/{RUN_ATTEMPT}"
                        )
                    },
                    "metadata": {
                        "buildkit_completeness": {"request": True},
                        "buildkit_metadata": {"vcs": copy.deepcopy(vcs)},
                    },
                },
            }
        }
    return result


def valid_sbom():
    return {
        platform: {
            "SPDX": {
                "SPDXID": "SPDXRef-DOCUMENT",
                "dataLicense": "CC0-1.0",
                "packages": [{"SPDXID": "SPDXRef-Package-synthetic"}],
                "spdxVersion": "SPDX-2.3",
            }
        }
        for platform in MODULE.REQUIRED_PLATFORMS
    }


def valid_run_metadata():
    return {
        "actor": {"login": OWNER},
        "conclusion": "failure",
        "event": "workflow_dispatch",
        "head_branch": "main",
        "head_sha": WORKFLOW_SHA,
        "id": int(RUN_ID),
        "name": MODULE.WORKFLOW_NAME,
        "path": MODULE.WORKFLOW_PATH,
        "repository": {"full_name": REPOSITORY},
        "run_attempt": RUN_ATTEMPT,
        "status": "completed",
        "triggering_actor": {"login": OWNER},
    }


class PublicationSourceVerifierTests(unittest.TestCase):
    def write_evidence(
        self, root, *, provenance=None, sbom=None, images=None, manifest=None
    ):
        manifest_path = root / "manifest.json"
        manifest_raw = json_bytes(manifest or valid_manifest())
        manifest_path.write_bytes(manifest_raw)
        provenance_path = root / "provenance.json"
        provenance_path.write_bytes(json_bytes(provenance or valid_provenance()))
        sbom_path = root / "sbom.json"
        sbom_path.write_bytes(json_bytes(sbom or valid_sbom()))
        image_paths = {}
        for platform, value in (images or valid_images()).items():
            path = root / f"{platform.replace('/', '-')}.json"
            path.write_bytes(json_bytes(value))
            image_paths[platform] = path
        return (
            manifest_path,
            f"sha256:{hashlib.sha256(manifest_raw).hexdigest()}",
            provenance_path,
            sbom_path,
            image_paths,
        )

    def verify_source(
        self, root, *, provenance=None, sbom=None, images=None, manifest=None
    ):
        manifest_path, digest, provenance_path, sbom_path, image_paths = (
            self.write_evidence(
                root,
                provenance=provenance,
                sbom=sbom,
                images=images,
                manifest=manifest,
            )
        )
        output = root / "github-output"
        arguments = [
            "verify-source",
            "--manifest-json",
            str(manifest_path),
            "--provenance-json",
            str(provenance_path),
            "--sbom-json",
            str(sbom_path),
        ]
        for platform, path in image_paths.items():
            arguments.extend(("--image-json", f"{platform}={path}"))
        arguments.extend(
            (
                "--expected-digest",
                digest,
                "--expected-release-sha",
                RELEASE_SHA,
                "--expected-version",
                VERSION,
                "--expected-build-time",
                BUILD_TIME,
                "--expected-run-id",
                RUN_ID,
                "--expected-run-attempt",
                str(RUN_ATTEMPT),
                "--expected-workflow-sha",
                WORKFLOW_SHA,
                "--expected-event-name",
                "workflow_dispatch",
                "--repository",
                REPOSITORY,
                "--owner",
                OWNER,
                "--github-output",
                str(output),
            )
        )
        return MODULE.main(arguments), output, digest

    def test_exact_manifest_images_and_provenance_are_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result, output, digest = self.verify_source(root)
            self.assertEqual(result, 0)
            values = dict(
                line.split("=", 1)
                for line in output.read_text(encoding="utf-8").splitlines()
            )
            self.assertEqual(values["source_image_verified"], "true")
            self.assertEqual(values["manifest_digest"], digest)
            self.assertEqual(values["sbom_status"], "present")
            self.assertEqual(values["SOURCE_AMD64_DIGEST"], PLATFORM_DIGESTS["linux/amd64"])
            self.assertEqual(values["SOURCE_ARM64_DIGEST"], PLATFORM_DIGESTS["linux/arm64"])
            self.assertEqual(values["SOURCE_ARMV7_DIGEST"], PLATFORM_DIGESTS["linux/arm/v7"])

    def test_manifest_extraction_is_digest_bound_and_emits_safe_values(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = json_bytes(valid_manifest())
            manifest_path = root / "manifest.json"
            manifest_path.write_bytes(raw)
            digest = f"sha256:{hashlib.sha256(raw).hexdigest()}"
            output = root / "platforms.env"
            result = MODULE.main(
                [
                    "extract-manifest",
                    "--manifest-json",
                    str(manifest_path),
                    "--expected-digest",
                    digest,
                    "--output-env",
                    str(output),
                ]
            )
            self.assertEqual(result, 0)
            self.assertEqual(
                output.read_text(encoding="utf-8").splitlines(),
                [
                    f"SOURCE_AMD64_DIGEST={PLATFORM_DIGESTS['linux/amd64']}",
                    f"SOURCE_ARM64_DIGEST={PLATFORM_DIGESTS['linux/arm64']}",
                    f"SOURCE_ARMV7_DIGEST={PLATFORM_DIGESTS['linux/arm/v7']}",
                ],
            )
            wrong = MODULE.main(
                [
                    "extract-manifest",
                    "--manifest-json",
                    str(manifest_path),
                    "--expected-digest",
                    f"sha256:{'0' * 64}",
                    "--output-env",
                    str(root / "wrong.env"),
                ]
            )
            self.assertEqual(wrong, 1)

    def test_manifest_ambiguity_and_attestation_drift_fail_closed(self):
        cases = []
        missing_platform = valid_manifest()
        missing_platform["manifests"].pop(0)
        cases.append(missing_platform)
        duplicate_platform = valid_manifest()
        duplicate_platform["manifests"][1]["platform"] = copy.deepcopy(
            duplicate_platform["manifests"][0]["platform"]
        )
        cases.append(duplicate_platform)
        wrong_attestation = valid_manifest()
        wrong_attestation["manifests"][-1]["annotations"][
            "vnd.docker.reference.digest"
        ] = f"sha256:{'9' * 64}"
        cases.append(wrong_attestation)
        extra_manifest = valid_manifest()
        extra_manifest["manifests"].append(
            copy.deepcopy(extra_manifest["manifests"][0])
        )
        cases.append(extra_manifest)
        for manifest in cases:
            with self.subTest(case=len(manifest["manifests"])), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                raw = json_bytes(manifest)
                path = root / "manifest.json"
                path.write_bytes(raw)
                result = MODULE.main(
                    [
                        "extract-manifest",
                        "--manifest-json",
                        str(path),
                        "--expected-digest",
                        f"sha256:{hashlib.sha256(raw).hexdigest()}",
                        "--output-env",
                        str(root / "platforms.env"),
                    ]
                )
                self.assertEqual(result, 1)

    def test_every_source_authority_field_is_fail_closed(self):
        mutations = []

        images = valid_images()
        images["linux/arm64"]["config"]["Labels"][
            "org.opencontainers.image.revision"
        ] = "3" * 40
        mutations.append((None, images))

        for mutate in (
            lambda p: p["linux/amd64"]["SLSA"]["buildDefinition"][
                "externalParameters"
            ]["request"]["args"].__setitem__("build-arg:HAMCP_BUILD_TIME", "bad"),
            lambda p: p["linux/arm64"]["SLSA"]["buildDefinition"][
                "externalParameters"
            ]["request"]["root"]["request"]["args"].__setitem__(
                "vcs:revision", "3" * 40
            ),
            lambda p: p["linux/arm/v7"]["SLSA"]["buildDefinition"][
                "internalParameters"
            ].__setitem__("github_run_id", "999"),
            lambda p: p["linux/amd64"]["SLSA"]["runDetails"][
                "builder"
            ].__setitem__("id", "https://example.invalid/builder"),
            lambda p: p["linux/arm64"]["SLSA"]["runDetails"][
                "metadata"
            ]["buildkit_metadata"]["vcs"].__setitem__("source", "bad"),
            lambda p: p["linux/arm/v7"]["SLSA"]["buildDefinition"][
                "internalParameters"
            ]["github_event_payload"]["inputs"].__setitem__(
                "expected_version", "2.2.0-beta.54"
            ),
            lambda p: p["linux/amd64"]["SLSA"]["runDetails"][
                "metadata"
            ]["buildkit_completeness"].__setitem__("request", False),
            lambda p: p.pop("linux/arm64"),
        ):
            provenance = valid_provenance()
            mutate(provenance)
            mutations.append((provenance, None))

        for index, (provenance, images) in enumerate(mutations):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as directory:
                result, output, _digest = self.verify_source(
                    Path(directory), provenance=provenance, images=images
                )
                self.assertEqual(result, 1)
                self.assertFalse(output.exists())

    def test_sbom_must_be_bounded_spdx_for_every_platform(self):
        cases = []
        missing = valid_sbom()
        missing.pop("linux/arm64")
        cases.append(missing)
        wrong_identity = valid_sbom()
        wrong_identity["linux/amd64"]["SPDX"]["spdxVersion"] = "SPDX-9.9"
        cases.append(wrong_identity)
        empty = valid_sbom()
        empty["linux/arm/v7"]["SPDX"]["packages"] = []
        cases.append(empty)
        for index, sbom in enumerate(cases):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as directory:
                result, output, _digest = self.verify_source(
                    Path(directory), sbom=sbom
                )
                self.assertEqual(result, 1)
                self.assertFalse(output.exists())

    def test_calendar_invalid_build_time_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path, digest, provenance_path, sbom_path, image_paths = (
                self.write_evidence(root)
            )
            output = root / "github-output"
            arguments = [
                "verify-source",
                "--manifest-json",
                str(manifest_path),
                "--provenance-json",
                str(provenance_path),
                "--sbom-json",
                str(sbom_path),
            ]
            for platform, path in image_paths.items():
                arguments.extend(("--image-json", f"{platform}={path}"))
            arguments.extend(
                (
                    "--expected-digest",
                    digest,
                    "--expected-release-sha",
                    RELEASE_SHA,
                    "--expected-version",
                    VERSION,
                    "--expected-build-time",
                    "2026-99-99T10:10:06Z",
                    "--expected-run-id",
                    RUN_ID,
                    "--expected-run-attempt",
                    str(RUN_ATTEMPT),
                    "--expected-workflow-sha",
                    WORKFLOW_SHA,
                    "--expected-event-name",
                    "workflow_dispatch",
                    "--repository",
                    REPOSITORY,
                    "--owner",
                    OWNER,
                    "--github-output",
                    str(output),
                )
            )
            self.assertEqual(MODULE.main(arguments), 1)
            self.assertFalse(output.exists())

    def test_recovery_run_metadata_is_exact_and_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_path = root / "run.json"
            run_path.write_bytes(json_bytes(valid_run_metadata()))
            output = root / "github-output"
            arguments = [
                "verify-recovery-run",
                "--run-json",
                str(run_path),
                "--expected-run-id",
                RUN_ID,
                "--expected-repository",
                REPOSITORY,
                "--expected-owner",
                OWNER,
                "--github-output",
                str(output),
            ]
            self.assertEqual(MODULE.main(arguments), 0)
            values = dict(
                line.split("=", 1)
                for line in output.read_text(encoding="utf-8").splitlines()
            )
            self.assertEqual(values["source_workflow_sha"], WORKFLOW_SHA)
            self.assertEqual(values["source_run_attempt"], str(RUN_ATTEMPT))
            self.assertEqual(values["source_event_name"], "workflow_dispatch")

            for key, value in (
                ("id", int(RUN_ID) + 1),
                ("name", "Other workflow"),
                ("status", "in_progress"),
                ("conclusion", "success"),
                ("event", "push"),
                ("head_branch", "other"),
                ("path", ".github/workflows/other.yml"),
                ("head_sha", "bad"),
                ("run_attempt", 0),
            ):
                with self.subTest(key=key):
                    payload = valid_run_metadata()
                    payload[key] = value
                    run_path.write_bytes(json_bytes(payload))
                    failure_output = root / f"failure-{key}"
                    failed = MODULE.main(
                        [*arguments[:-1], str(failure_output)]
                    )
                    self.assertEqual(failed, 1)
                    self.assertFalse(failure_output.exists())

            for key in ("actor", "triggering_actor", "repository"):
                with self.subTest(key=key):
                    payload = valid_run_metadata()
                    nested_key = "full_name" if key == "repository" else "login"
                    payload[key][nested_key] = "untrusted/example"
                    run_path.write_bytes(json_bytes(payload))
                    failure_output = root / f"failure-{key}"
                    failed = MODULE.main([*arguments[:-1], str(failure_output)])
                    self.assertEqual(failed, 1)
                    self.assertFalse(failure_output.exists())

    def test_json_duplicate_keys_nonfinite_values_and_file_bounds_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "platforms.env"
            for index, raw in enumerate(
                (
                    b'{"schemaVersion":2,"schemaVersion":2}',
                    b'{"value":NaN}',
                    b"x" * (MODULE.MAX_MANIFEST_BYTES + 1),
                )
            ):
                path = root / f"invalid-{index}.json"
                path.write_bytes(raw)
                result = MODULE.main(
                    [
                        "extract-manifest",
                        "--manifest-json",
                        str(path),
                        "--expected-digest",
                        f"sha256:{hashlib.sha256(raw).hexdigest()}",
                        "--output-env",
                        str(output),
                    ]
                )
                self.assertEqual(result, 1)


if __name__ == "__main__":
    unittest.main()
