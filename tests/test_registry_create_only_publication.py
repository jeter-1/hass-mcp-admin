import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys
import tempfile
from unittest import mock
from urllib.parse import unquote, urlsplit
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "publish_registry_tags_create_only.py"
SPEC = importlib.util.spec_from_file_location(
    "publish_registry_tags_create_only", SCRIPT_PATH
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


MANIFEST = json.dumps(
    {
        "manifests": [],
        "mediaType": "application/vnd.oci.image.index.v1+json",
        "schemaVersion": 2,
    },
    sort_keys=True,
    separators=(",", ":"),
).encode("utf-8")
DIGEST = f"sha256:{hashlib.sha256(MANIFEST).hexdigest()}"
COMPETING_MANIFEST = json.dumps(
    {
        "manifests": [{"digest": f"sha256:{'a' * 64}"}],
        "mediaType": "application/vnd.oci.image.index.v1+json",
        "schemaVersion": 2,
    },
    sort_keys=True,
    separators=(",", ":"),
).encode("utf-8")
MEDIA_TYPE = "application/vnd.oci.image.index.v1+json"
COMMIT_TAG = f"sha-{'b' * 40}"
VERSION_TAG = "2.2.0-beta.53"


class FakeRegistryTransport:
    def __init__(
        self,
        *,
        conditional_supported=True,
        race_target=None,
        ambiguous_target=None,
        applied_then_error_target=None,
    ):
        self.conditional_supported = conditional_supported
        self.race_target = race_target
        self.ambiguous_target = ambiguous_target
        self.applied_then_error_target = applied_then_error_target
        self.tags = {DIGEST: MANIFEST}
        self.requests = []

    @staticmethod
    def _response(status, body=b"", digest=None, content_type=None):
        headers = {}
        if digest is not None:
            headers["docker-content-digest"] = digest
        if content_type is not None:
            headers["content-type"] = content_type
        return MODULE.HttpResponse(status=status, headers=headers, body=body)

    @staticmethod
    def _tag(url):
        path = urlsplit(url).path
        return unquote(path.rsplit("/", 1)[-1])

    def request(self, method, url, headers, body, response_limit):
        self.requests.append(
            {
                "method": method,
                "url": url,
                "headers": {
                    key: value
                    for key, value in headers.items()
                    if key.lower() != "authorization"
                },
                "body": body,
                "response_limit": response_limit,
            }
        )
        if url.startswith("https://ghcr.io/token?"):
            return self._response(200, b'{"token":"synthetic-registry-token"}')

        tag = self._tag(url)
        if method == "GET":
            manifest = self.tags.get(tag)
            if manifest is None:
                return self._response(404)
            digest = f"sha256:{hashlib.sha256(manifest).hexdigest()}"
            return self._response(
                200,
                manifest,
                digest=digest,
                content_type=MEDIA_TYPE,
            )

        if method != "PUT":
            raise AssertionError(f"unexpected method: {method}")
        if headers.get("If-None-Match") != "*":
            raise AssertionError("manifest PUT was not create-only")
        if tag == self.race_target and tag not in self.tags:
            self.tags[tag] = COMPETING_MANIFEST
        if tag == self.ambiguous_target:
            return self._response(503)
        if tag in self.tags and self.conditional_supported:
            return self._response(412)
        assert body is not None
        self.tags[tag] = body
        if tag == self.applied_then_error_target:
            raise MODULE.PublicationError("REGISTRY_TRANSPORT_FAILED")
        digest = f"sha256:{hashlib.sha256(body).hexdigest()}"
        return self._response(201, digest=digest)


class CreateOnlyRegistryPublicationTests(unittest.TestCase):
    def publisher(self, transport):
        return MODULE.CreateOnlyPublisher(
            repository=MODULE.RegistryReference.parse(
                "ghcr.io/jeter-1/hass-mcp-engineering-beta"
            ),
            username="jeter-1",
            password="synthetic-registry-password",
            transport=transport,
        )

    def test_two_release_tags_are_created_from_exact_digest_manifest(self):
        transport = FakeRegistryTransport()
        targets = (COMMIT_TAG, VERSION_TAG)

        created = self.publisher(transport).publish(
            source_digest=DIGEST,
            target_tags=targets,
        )

        self.assertEqual(created, targets)
        for tag in targets:
            self.assertEqual(transport.tags[tag], MANIFEST)
        manifest_puts = [
            request for request in transport.requests if request["method"] == "PUT"
        ]
        self.assertEqual(len(manifest_puts), 3)
        self.assertTrue(
            transport.requests[1]["url"].endswith(f"/manifests/{DIGEST}")
        )
        self.assertNotIn(
            "publication-staging",
            "\n".join(request["url"] for request in transport.requests),
        )
        self.assertTrue(
            all(request["headers"].get("If-None-Match") == "*" for request in manifest_puts)
        )
        self.assertNotIn(
            "Authorization",
            "\n".join(repr(request) for request in transport.requests),
        )

    def test_late_competing_tag_is_never_overwritten(self):
        targets = (COMMIT_TAG, VERSION_TAG)
        for target_index, target in enumerate(targets):
            with self.subTest(target=target):
                transport = FakeRegistryTransport(race_target=target)

                with self.assertRaisesRegex(
                    MODULE.PublicationError,
                    rf"TARGET_TAG_ALREADY_EXISTS:{re.escape(target)}:created={target_index}",
                ) as raised:
                    self.publisher(transport).publish(
                        source_digest=DIGEST,
                        target_tags=targets,
                    )

                expected_disposition = "partial" if target_index else "none"
                self.assertEqual(raised.exception.disposition, expected_disposition)
                self.assertEqual(transport.tags[target], COMPETING_MANIFEST)
                for prior in targets[:target_index]:
                    self.assertEqual(transport.tags[prior], MANIFEST)
                for later in targets[target_index + 1 :]:
                    self.assertNotIn(later, transport.tags)

    def test_unsupported_create_only_semantics_stop_before_release_tags(self):
        transport = FakeRegistryTransport(conditional_supported=False)

        with self.assertRaisesRegex(
            MODULE.PublicationError, "REGISTRY_CREATE_ONLY_UNSUPPORTED"
        ):
            self.publisher(transport).publish(
                source_digest=DIGEST,
                target_tags=("2.2.0-beta.53",),
            )

        self.assertEqual(set(transport.tags), {DIGEST})

    def test_ambiguous_target_response_stops_without_claiming_that_tag(self):
        target = COMMIT_TAG
        transport = FakeRegistryTransport(ambiguous_target=target)

        with self.assertRaisesRegex(
            MODULE.PublicationError,
            rf"TARGET_TAG_CREATE_AMBIGUOUS:{re.escape(target)}:created=0",
        ) as raised:
            self.publisher(transport).publish(
                source_digest=DIGEST,
                target_tags=(target,),
            )

        self.assertEqual(raised.exception.disposition, "unknown")
        self.assertNotIn(target, transport.tags)

    def test_applied_then_lost_response_is_unknown_and_version_is_last(self):
        targets = (COMMIT_TAG, VERSION_TAG)
        for target_index, target in enumerate(targets):
            with self.subTest(target=target):
                transport = FakeRegistryTransport(
                    applied_then_error_target=target
                )

                with self.assertRaisesRegex(
                    MODULE.PublicationError,
                    rf"TARGET_TAG_CREATE_UNKNOWN:{re.escape(target)}:created={target_index}",
                ) as raised:
                    self.publisher(transport).publish(
                        source_digest=DIGEST,
                        target_tags=targets,
                    )

                self.assertEqual(raised.exception.disposition, "unknown")
                self.assertEqual(transport.tags[target], MANIFEST)
                if target == COMMIT_TAG:
                    self.assertNotIn(VERSION_TAG, transport.tags)
                else:
                    self.assertEqual(transport.tags[COMMIT_TAG], MANIFEST)

    def test_failure_outputs_preserve_partial_and_unknown_dispositions(self):
        cases = (
            ("partial", (COMMIT_TAG,)),
            ("unknown", (COMMIT_TAG,)),
            ("none", ()),
            ("complete", (COMMIT_TAG, VERSION_TAG)),
        )
        for disposition, created in cases:
            with (
                self.subTest(disposition=disposition),
                tempfile.TemporaryDirectory() as directory,
            ):
                output_path = Path(directory) / "github-output"
                with mock.patch.dict(
                    "os.environ", {"GITHUB_OUTPUT": str(output_path)}
                ):
                    MODULE.write_outputs(
                        created=created,
                        disposition=disposition,
                    )
                outputs = dict(
                    line.split("=", 1)
                    for line in output_path.read_text(encoding="utf-8").splitlines()
                )
                self.assertEqual(outputs["publication_disposition"], disposition)
                self.assertEqual(
                    outputs["known_created_tag_count"], str(len(created))
                )
                self.assertEqual(
                    outputs["release_tags_created"],
                    "true" if disposition == "complete" else "false",
                )

    def test_source_digest_drift_stops_before_any_manifest_write(self):
        transport = FakeRegistryTransport()
        transport.tags[DIGEST] = COMPETING_MANIFEST

        with self.assertRaisesRegex(
            MODULE.PublicationError, "SOURCE_MANIFEST_DIGEST_MISMATCH"
        ):
            self.publisher(transport).publish(
                source_digest=DIGEST,
                target_tags=("2.2.0-beta.53",),
            )

        self.assertFalse(
            any(request["method"] == "PUT" for request in transport.requests)
        )

    def test_inputs_are_bounded_and_exact(self):
        publisher = self.publisher(FakeRegistryTransport())
        invalid_cases = (
            {
                "source_digest": "bad/tag",
                "target_tags": ("2.2.0-beta.53",),
            },
            {
                "source_digest": DIGEST,
                "target_tags": ("same", "same"),
            },
            {
                "source_digest": "sha256:not-a-digest",
                "target_tags": ("2.2.0-beta.53",),
            },
            {
                "source_digest": DIGEST,
                "target_tags": tuple(f"tag-{index}" for index in range(5)),
            },
        )
        for values in invalid_cases:
            with self.subTest(values=values), self.assertRaises(MODULE.PublicationError):
                publisher.publish(**values)

        with self.assertRaisesRegex(
            MODULE.PublicationError, "REGISTRY_REPOSITORY_INVALID"
        ):
            MODULE.RegistryReference.parse("registry.example/repository")
        with self.assertRaisesRegex(
            MODULE.PublicationError, "REGISTRY_USERNAME_INVALID"
        ):
            MODULE.CreateOnlyPublisher(
                repository=MODULE.RegistryReference.parse(
                    "ghcr.io/jeter-1/hass-mcp-engineering-beta"
                ),
                username="actor:injected",
                password="synthetic-registry-password",
                transport=FakeRegistryTransport(),
            )


if __name__ == "__main__":
    unittest.main()
