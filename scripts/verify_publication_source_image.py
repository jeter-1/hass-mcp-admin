#!/usr/bin/env python3
"""Verify a digest-only Engineering publication source without Docker pulls."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from http.client import HTTPException
import json
from pathlib import Path
import re
import sys
from typing import Any, Iterable, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")
SHA_PATTERN = re.compile(r"[0-9a-f]{40}\Z")
RUN_ID_PATTERN = re.compile(r"[1-9][0-9]{0,19}\Z")
VERSION_PATTERN = re.compile(r"[0-9A-Za-z][0-9A-Za-z.+-]{0,63}\Z")
BUILD_TIME_PATTERN = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z"
)

INDEX_MEDIA_TYPE = "application/vnd.oci.image.index.v1+json"
IMAGE_MEDIA_TYPE = "application/vnd.oci.image.manifest.v1+json"
ATTESTATION_TYPE = "attestation-manifest"
ATTESTATION_ARTIFACT_TYPE = "application/vnd.docker.attestation.manifest.v1+json"
IN_TOTO_MEDIA_TYPE = "application/vnd.in-toto+json"
IN_TOTO_STATEMENT_TYPE = "https://in-toto.io/Statement/v1"
SLSA_PREDICATE_TYPE = "https://slsa.dev/provenance/v1"
SPDX_PREDICATE_TYPE = "https://spdx.dev/Document"
EMPTY_CONFIG_MEDIA_TYPE = "application/vnd.oci.empty.v1+json"
EMPTY_CONFIG_DIGEST = (
    "sha256:44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a"
)
BUILD_TYPE = (
    "https://github.com/moby/buildkit/blob/master/docs/attestations/"
    "slsa-definitions.md"
)
WORKFLOW_NAME = "Publish reviewed Engineering release"
WORKFLOW_PATH = ".github/workflows/publish-rc-image.yml"
SOURCE_DIRECTORY = "hass_mcp_engineering_beta"

REQUIRED_PLATFORMS = {
    "linux/amd64": ("linux", "amd64", None, "SOURCE_AMD64_DIGEST"),
    "linux/arm64": ("linux", "arm64", None, "SOURCE_ARM64_DIGEST"),
    "linux/arm/v7": ("linux", "arm", "v7", "SOURCE_ARMV7_DIGEST"),
}

MAX_MANIFEST_BYTES = 1_048_576
MAX_IMAGE_METADATA_BYTES = 4_194_304
MAX_PROVENANCE_BYTES = 16_777_216
MAX_SBOM_BYTES = 16_777_216
MAX_RUN_METADATA_BYTES = 1_048_576
MAX_TOKEN_RESPONSE_BYTES = 16_384
MAX_TOKEN_LENGTH = 8_192
MAX_REDIRECT_URL_LENGTH = 4_096
GHCR_HOST = "ghcr.io"
_REPOSITORY_SEGMENT = r"[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*"
REGISTRY_REPOSITORY_PATTERN = re.compile(
    rf"{_REPOSITORY_SEGMENT}(?:/{_REPOSITORY_SEGMENT})*\Z"
)


class VerificationError(RuntimeError):
    """Raised when publication recovery evidence is not exact."""


def _fail(reason: str) -> None:
    raise VerificationError(reason)


def _strict_object(pairs: Iterable[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("JSON_DUPLICATE_KEY")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    _fail("JSON_NONFINITE_NUMBER")


def _decode_json(raw: bytes, reason: str) -> Any:
    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise VerificationError(reason) from exc


def _read_bytes(path: Path, limit: int) -> bytes:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise VerificationError("EVIDENCE_FILE_UNAVAILABLE") from exc
    if size <= 0 or size > limit:
        _fail("EVIDENCE_FILE_BOUND_INVALID")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise VerificationError("EVIDENCE_FILE_UNAVAILABLE") from exc


def _read_json(path: Path, limit: int) -> Any:
    return _decode_json(_read_bytes(path, limit), "EVIDENCE_JSON_INVALID")


def _mapping(value: Any, reason: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail(reason)
    return value


def _sequence(value: Any, reason: str) -> list[Any]:
    if not isinstance(value, list):
        _fail(reason)
    return value


def _exact_pattern(value: str, pattern: re.Pattern[str], reason: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        _fail(reason)
    return value


def _exact_build_time(value: str) -> str:
    value = _exact_pattern(value, BUILD_TIME_PATTERN, "EXPECTED_BUILD_TIME_INVALID")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError as exc:
        raise VerificationError("EXPECTED_BUILD_TIME_INVALID") from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        _fail("EXPECTED_BUILD_TIME_INVALID")
    return value


@dataclass(frozen=True)
class Descriptor:
    digest: str
    size: int


@dataclass(frozen=True)
class ManifestEvidence:
    platforms: dict[str, Descriptor]
    attestations: dict[str, Descriptor]


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


class Transport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        response_limit: int,
    ) -> HttpResponse: ...


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


class UrllibTransport:
    """Bounded HTTPS transport that never forwards headers through redirects."""

    def __init__(self) -> None:
        self._opener = build_opener(_NoRedirectHandler())

    def request(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        response_limit: int,
    ) -> HttpResponse:
        request = Request(url, headers=dict(headers), method=method)
        try:
            response = self._opener.open(request, timeout=30)
        except HTTPError as error:
            response = error
        except (OSError, TimeoutError, URLError, HTTPException) as error:
            raise VerificationError("REGISTRY_TRANSPORT_FAILED") from error
        try:
            with response:
                content_length = response.headers.get("Content-Length")
                if content_length is not None:
                    try:
                        declared_length = int(content_length)
                    except ValueError as error:
                        raise VerificationError(
                            "REGISTRY_RESPONSE_LENGTH_INVALID"
                        ) from error
                    if declared_length < 0 or declared_length > response_limit:
                        _fail("REGISTRY_RESPONSE_BOUND_EXCEEDED")
                body = response.read(response_limit + 1)
                if len(body) > response_limit:
                    _fail("REGISTRY_RESPONSE_BOUND_EXCEEDED")
                return HttpResponse(
                    status=int(response.status),
                    headers={
                        key.lower(): value for key, value in response.headers.items()
                    },
                    body=body,
                )
        except VerificationError:
            raise
        except (OSError, TimeoutError, URLError, HTTPException) as error:
            raise VerificationError("REGISTRY_TRANSPORT_FAILED") from error


@dataclass(frozen=True)
class RegistryReference:
    host: str
    path: str

    @classmethod
    def parse(cls, value: str) -> "RegistryReference":
        if value.count("/") < 1:
            _fail("REGISTRY_REPOSITORY_INVALID")
        host, path = value.split("/", 1)
        if host != GHCR_HOST or not REGISTRY_REPOSITORY_PATTERN.fullmatch(path):
            _fail("REGISTRY_REPOSITORY_INVALID")
        return cls(host=host, path=path)

    def manifest_url(self, digest: str) -> str:
        return (
            f"https://{self.host}/v2/{self.path}/manifests/"
            f"{quote(digest, safe=':')}"
        )

    def blob_url(self, digest: str) -> str:
        return (
            f"https://{self.host}/v2/{self.path}/blobs/"
            f"{quote(digest, safe=':')}"
        )


class AnonymousRegistryReader:
    def __init__(
        self,
        *,
        repository: RegistryReference,
        transport: Transport,
    ) -> None:
        self._repository = repository
        self._transport = transport
        self._token = self._read_token()

    def _read_token(self) -> str:
        query = urlencode(
            {
                "service": self._repository.host,
                "scope": f"repository:{self._repository.path}:pull",
            }
        )
        response = self._transport.request(
            "GET",
            f"https://{self._repository.host}/token?{query}",
            {"Accept": "application/json"},
            MAX_TOKEN_RESPONSE_BYTES,
        )
        if response.status != 200:
            _fail("REGISTRY_TOKEN_REQUEST_FAILED")
        payload = _mapping(
            _decode_json(response.body, "REGISTRY_TOKEN_RESPONSE_INVALID"),
            "REGISTRY_TOKEN_RESPONSE_INVALID",
        )
        token = payload.get("token")
        if (
            not isinstance(token, str)
            or not token
            or len(token) > MAX_TOKEN_LENGTH
            or any(ord(character) < 33 or ord(character) > 126 for character in token)
        ):
            _fail("REGISTRY_TOKEN_RESPONSE_INVALID")
        return token

    @staticmethod
    def _validate_redirect(value: str | None) -> str:
        if not isinstance(value, str) or not value or len(value) > MAX_REDIRECT_URL_LENGTH:
            _fail("REGISTRY_REDIRECT_INVALID")
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
        ):
            _fail("REGISTRY_REDIRECT_INVALID")
        return value

    def _read(
        self,
        *,
        url: str,
        expected_digest: str,
        expected_size: int,
        response_limit: int,
        accept: str,
        allow_blob_redirect: bool,
        require_digest_header: bool,
    ) -> bytes:
        response = self._transport.request(
            "GET",
            url,
            {
                "Accept": accept,
                "Authorization": f"Bearer {self._token}",
            },
            response_limit,
        )
        if response.status in {301, 302, 303, 307, 308}:
            if not allow_blob_redirect:
                _fail("REGISTRY_REDIRECT_FORBIDDEN")
            redirect = self._validate_redirect(response.headers.get("location"))
            response = self._transport.request(
                "GET",
                redirect,
                {"Accept": accept},
                response_limit,
            )
        if response.status != 200:
            _fail("REGISTRY_EVIDENCE_UNAVAILABLE")
        if (
            require_digest_header
            and response.headers.get("docker-content-digest") != expected_digest
        ):
            _fail("REGISTRY_EVIDENCE_HEADER_DIGEST_MISMATCH")
        if len(response.body) != expected_size:
            _fail("REGISTRY_EVIDENCE_SIZE_MISMATCH")
        actual_digest = f"sha256:{hashlib.sha256(response.body).hexdigest()}"
        if actual_digest != expected_digest:
            _fail("REGISTRY_EVIDENCE_DIGEST_MISMATCH")
        return response.body

    def manifest(self, descriptor: Descriptor) -> bytes:
        raw = self._read(
            url=self._repository.manifest_url(descriptor.digest),
            expected_digest=descriptor.digest,
            expected_size=descriptor.size,
            response_limit=MAX_MANIFEST_BYTES,
            accept=IMAGE_MEDIA_TYPE,
            allow_blob_redirect=False,
            require_digest_header=True,
        )
        return raw

    def blob(self, descriptor: Descriptor, response_limit: int) -> bytes:
        return self._read(
            url=self._repository.blob_url(descriptor.digest),
            expected_digest=descriptor.digest,
            expected_size=descriptor.size,
            response_limit=response_limit,
            accept=IN_TOTO_MEDIA_TYPE,
            allow_blob_redirect=True,
            require_digest_header=False,
        )


def _descriptor(value: dict[str, Any], reason: str) -> Descriptor:
    digest = _exact_pattern(value.get("digest"), DIGEST_PATTERN, reason)
    size = value.get("size")
    if type(size) is not int or size <= 0 or size > MAX_SBOM_BYTES:
        _fail(reason)
    return Descriptor(digest=digest, size=size)


def _manifest_evidence(
    manifest_path: Path,
    expected_digest: str,
) -> ManifestEvidence:
    _exact_pattern(expected_digest, DIGEST_PATTERN, "SOURCE_DIGEST_INVALID")
    raw = _read_bytes(manifest_path, MAX_MANIFEST_BYTES)
    actual_digest = f"sha256:{hashlib.sha256(raw).hexdigest()}"
    if actual_digest != expected_digest:
        _fail("SOURCE_MANIFEST_DIGEST_MISMATCH")
    try:
        manifest = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise VerificationError("SOURCE_MANIFEST_JSON_INVALID") from exc
    root = _mapping(manifest, "SOURCE_MANIFEST_INVALID")
    if root.get("schemaVersion") != 2 or root.get("mediaType") != INDEX_MEDIA_TYPE:
        _fail("SOURCE_MANIFEST_TYPE_INVALID")
    entries = _sequence(root.get("manifests"), "SOURCE_MANIFEST_ENTRIES_INVALID")
    if len(entries) != len(REQUIRED_PLATFORMS) * 2:
        _fail("SOURCE_MANIFEST_CARDINALITY_INVALID")

    platform_descriptors: dict[str, Descriptor] = {}
    attestation_descriptors: list[tuple[str, Descriptor]] = []
    for raw_entry in entries:
        entry = _mapping(raw_entry, "SOURCE_MANIFEST_ENTRY_INVALID")
        descriptor = _descriptor(entry, "SOURCE_CHILD_DESCRIPTOR_INVALID")
        if entry.get("mediaType") != IMAGE_MEDIA_TYPE:
            _fail("SOURCE_CHILD_MEDIA_TYPE_INVALID")
        platform = _mapping(
            entry.get("platform"), "SOURCE_CHILD_PLATFORM_INVALID"
        )
        annotations = entry.get("annotations")
        if annotations is not None:
            annotation_map = _mapping(
                annotations, "SOURCE_ATTESTATION_ANNOTATIONS_INVALID"
            )
            if annotation_map.get("vnd.docker.reference.type") != ATTESTATION_TYPE:
                _fail("SOURCE_ATTESTATION_TYPE_INVALID")
            subject = _exact_pattern(
                annotation_map.get("vnd.docker.reference.digest"),
                DIGEST_PATTERN,
                "SOURCE_ATTESTATION_SUBJECT_INVALID",
            )
            if platform != {"architecture": "unknown", "os": "unknown"}:
                _fail("SOURCE_ATTESTATION_PLATFORM_INVALID")
            attestation_descriptors.append((subject, descriptor))
            continue

        key = None
        for candidate, (os_name, architecture, variant, _env_name) in (
            REQUIRED_PLATFORMS.items()
        ):
            expected_platform = {"os": os_name, "architecture": architecture}
            if variant is not None:
                expected_platform["variant"] = variant
            if platform == expected_platform:
                key = candidate
                break
        if key is None or key in platform_descriptors:
            _fail("SOURCE_PLATFORM_SET_INVALID")
        platform_descriptors[key] = descriptor

    if set(platform_descriptors) != set(REQUIRED_PLATFORMS):
        _fail("SOURCE_PLATFORM_SET_INVALID")
    subject_platforms = {
        descriptor.digest: platform
        for platform, descriptor in platform_descriptors.items()
    }
    attestations: dict[str, Descriptor] = {}
    for subject, descriptor in attestation_descriptors:
        platform = subject_platforms.get(subject)
        if platform is None or platform in attestations:
            _fail("SOURCE_ATTESTATION_SET_INVALID")
        attestations[platform] = descriptor
    if set(attestations) != set(REQUIRED_PLATFORMS):
        _fail("SOURCE_ATTESTATION_SET_INVALID")
    return ManifestEvidence(
        platforms=platform_descriptors,
        attestations=attestations,
    )


def _bounded_subject(
    statement: dict[str, Any],
    *,
    platform: str,
    expected_digest: str,
) -> None:
    subjects = _sequence(statement.get("subject"), "SOURCE_STATEMENT_SUBJECT_INVALID")
    if len(subjects) != 1:
        _fail("SOURCE_STATEMENT_SUBJECT_INVALID")
    subject = _mapping(subjects[0], "SOURCE_STATEMENT_SUBJECT_INVALID")
    name = subject.get("name")
    if (
        not isinstance(name, str)
        or not name
        or len(name) > 1_024
        or any(ord(character) < 32 or ord(character) == 127 for character in name)
        or f"platform={quote(platform, safe='')}" not in name
    ):
        _fail("SOURCE_STATEMENT_SUBJECT_INVALID")
    if subject.get("digest") != {"sha256": expected_digest.removeprefix("sha256:")}:
        _fail("SOURCE_STATEMENT_SUBJECT_DIGEST_MISMATCH")


def _attestation_statements(
    image_repository: str,
    manifest: ManifestEvidence,
    *,
    transport: Transport | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    reader = AnonymousRegistryReader(
        repository=RegistryReference.parse(image_repository),
        transport=transport or UrllibTransport(),
    )
    provenance: dict[str, Any] = {}
    sbom: dict[str, Any] = {}
    total_provenance_bytes = 0
    total_sbom_bytes = 0

    for platform in REQUIRED_PLATFORMS:
        platform_descriptor = manifest.platforms[platform]
        attestation_descriptor = manifest.attestations[platform]
        raw_attestation = reader.manifest(attestation_descriptor)
        attestation = _mapping(
            _decode_json(raw_attestation, "SOURCE_ATTESTATION_MANIFEST_JSON_INVALID"),
            "SOURCE_ATTESTATION_MANIFEST_INVALID",
        )
        if (
            attestation.get("schemaVersion") != 2
            or attestation.get("mediaType") != IMAGE_MEDIA_TYPE
            or attestation.get("artifactType") != ATTESTATION_ARTIFACT_TYPE
        ):
            _fail("SOURCE_ATTESTATION_MANIFEST_TYPE_INVALID")
        config = _mapping(
            attestation.get("config"), "SOURCE_ATTESTATION_CONFIG_INVALID"
        )
        if config != {
            "mediaType": EMPTY_CONFIG_MEDIA_TYPE,
            "digest": EMPTY_CONFIG_DIGEST,
            "size": 2,
            "data": "e30=",
        }:
            _fail("SOURCE_ATTESTATION_CONFIG_INVALID")
        subject = _mapping(
            attestation.get("subject"), "SOURCE_ATTESTATION_SUBJECT_INVALID"
        )
        if subject != {
            "mediaType": IMAGE_MEDIA_TYPE,
            "digest": platform_descriptor.digest,
            "size": platform_descriptor.size,
        }:
            _fail("SOURCE_ATTESTATION_SUBJECT_MISMATCH")

        layers = _sequence(
            attestation.get("layers"), "SOURCE_ATTESTATION_LAYERS_INVALID"
        )
        if len(layers) != 2:
            _fail("SOURCE_ATTESTATION_LAYERS_INVALID")
        predicates: dict[str, dict[str, Any]] = {}
        for raw_layer in layers:
            layer = _mapping(raw_layer, "SOURCE_ATTESTATION_LAYER_INVALID")
            if layer.get("mediaType") != IN_TOTO_MEDIA_TYPE:
                _fail("SOURCE_ATTESTATION_LAYER_MEDIA_TYPE_INVALID")
            annotations = _mapping(
                layer.get("annotations"),
                "SOURCE_ATTESTATION_LAYER_ANNOTATIONS_INVALID",
            )
            if set(annotations) != {"in-toto.io/predicate-type"}:
                _fail("SOURCE_ATTESTATION_LAYER_ANNOTATIONS_INVALID")
            predicate_type = annotations.get("in-toto.io/predicate-type")
            if predicate_type not in {SLSA_PREDICATE_TYPE, SPDX_PREDICATE_TYPE}:
                _fail("SOURCE_ATTESTATION_PREDICATE_TYPE_INVALID")
            if predicate_type in predicates:
                _fail("SOURCE_ATTESTATION_PREDICATE_SET_INVALID")
            layer_descriptor = _descriptor(
                layer, "SOURCE_ATTESTATION_LAYER_DESCRIPTOR_INVALID"
            )
            limit = (
                MAX_PROVENANCE_BYTES
                if predicate_type == SLSA_PREDICATE_TYPE
                else MAX_SBOM_BYTES
            )
            if layer_descriptor.size > limit:
                _fail("SOURCE_ATTESTATION_LAYER_BOUND_EXCEEDED")
            statement = _mapping(
                _decode_json(
                    reader.blob(layer_descriptor, limit),
                    "SOURCE_ATTESTATION_STATEMENT_JSON_INVALID",
                ),
                "SOURCE_ATTESTATION_STATEMENT_INVALID",
            )
            if statement.get("_type") != IN_TOTO_STATEMENT_TYPE:
                _fail("SOURCE_ATTESTATION_STATEMENT_TYPE_INVALID")
            if statement.get("predicateType") != predicate_type:
                _fail("SOURCE_ATTESTATION_PREDICATE_TYPE_MISMATCH")
            _bounded_subject(
                statement,
                platform=platform,
                expected_digest=platform_descriptor.digest,
            )
            predicate = _mapping(
                statement.get("predicate"), "SOURCE_ATTESTATION_PREDICATE_INVALID"
            )
            predicates[predicate_type] = predicate
            if predicate_type == SLSA_PREDICATE_TYPE:
                total_provenance_bytes += layer_descriptor.size
            else:
                total_sbom_bytes += layer_descriptor.size

        if set(predicates) != {SLSA_PREDICATE_TYPE, SPDX_PREDICATE_TYPE}:
            _fail("SOURCE_ATTESTATION_PREDICATE_SET_INVALID")
        provenance[platform] = {"SLSA": predicates[SLSA_PREDICATE_TYPE]}
        sbom[platform] = {"SPDX": predicates[SPDX_PREDICATE_TYPE]}

    if total_provenance_bytes > MAX_PROVENANCE_BYTES:
        _fail("SOURCE_PROVENANCE_BOUND_EXCEEDED")
    if total_sbom_bytes > MAX_SBOM_BYTES:
        _fail("SOURCE_SBOM_BOUND_EXCEEDED")
    return provenance, sbom


def _write_lines(path: Path, lines: Iterable[str]) -> None:
    value = "\n".join(lines) + "\n"
    try:
        path.write_text(value, encoding="utf-8", newline="")
    except OSError as exc:
        raise VerificationError("OUTPUT_FILE_UNAVAILABLE") from exc


def extract_manifest(args: argparse.Namespace) -> None:
    evidence = _manifest_evidence(args.manifest_json, args.expected_digest)
    _write_lines(
        args.output_env,
        (
            f"{REQUIRED_PLATFORMS[platform][3]}={evidence.platforms[platform].digest}"
            for platform in REQUIRED_PLATFORMS
        ),
    )


def verify_recovery_run(args: argparse.Namespace) -> None:
    expected_run_id = _exact_pattern(
        args.expected_run_id, RUN_ID_PATTERN, "RECOVERY_RUN_ID_INVALID"
    )
    run = _mapping(
        _read_json(args.run_json, MAX_RUN_METADATA_BYTES),
        "RECOVERY_RUN_METADATA_INVALID",
    )
    expected = {
        "id": int(expected_run_id),
        "name": WORKFLOW_NAME,
        "status": "completed",
        "conclusion": "failure",
        "event": "workflow_dispatch",
        "head_branch": "main",
        "path": WORKFLOW_PATH,
    }
    for key, value in expected.items():
        if run.get(key) != value:
            _fail(f"RECOVERY_RUN_{key.upper()}_MISMATCH")
    head_sha = _exact_pattern(
        run.get("head_sha"), SHA_PATTERN, "RECOVERY_WORKFLOW_SHA_INVALID"
    )
    run_attempt = run.get("run_attempt")
    if type(run_attempt) is not int or run_attempt < 1 or run_attempt > 100:
        _fail("RECOVERY_RUN_ATTEMPT_INVALID")
    actor = _mapping(run.get("actor"), "RECOVERY_RUN_ACTOR_INVALID")
    triggering_actor = _mapping(
        run.get("triggering_actor"), "RECOVERY_RUN_TRIGGERING_ACTOR_INVALID"
    )
    repository = _mapping(
        run.get("repository"), "RECOVERY_RUN_REPOSITORY_INVALID"
    )
    if actor.get("login") != args.expected_owner:
        _fail("RECOVERY_RUN_ACTOR_MISMATCH")
    if triggering_actor.get("login") != args.expected_owner:
        _fail("RECOVERY_RUN_TRIGGERING_ACTOR_MISMATCH")
    if repository.get("full_name") != args.expected_repository:
        _fail("RECOVERY_RUN_REPOSITORY_MISMATCH")
    _write_lines(
        args.github_output,
        (
            f"source_workflow_sha={head_sha}",
            f"source_run_attempt={run_attempt}",
            "source_event_name=workflow_dispatch",
        ),
    )


def _parse_image_arguments(values: list[str]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        platform, separator, path = value.partition("=")
        if not separator or platform not in REQUIRED_PLATFORMS or platform in result:
            _fail("SOURCE_IMAGE_ARGUMENT_INVALID")
        result[platform] = Path(path)
    if set(result) != set(REQUIRED_PLATFORMS):
        _fail("SOURCE_IMAGE_ARGUMENT_SET_INVALID")
    return result


def _required_labels(
    release_sha: str,
    version: str,
    build_time: str,
    source_url: str,
) -> dict[str, str]:
    return {
        "io.hass-mcp.build.dirty": "false",
        "org.opencontainers.image.created": build_time,
        "org.opencontainers.image.revision": release_sha,
        "org.opencontainers.image.source": source_url,
        "org.opencontainers.image.version": version,
    }


def _required_build_args(
    release_sha: str,
    version: str,
    build_time: str,
    source_url: str,
) -> dict[str, str]:
    labels = _required_labels(release_sha, version, build_time, source_url)
    return {
        "build-arg:BUILD_VERSION": version,
        "build-arg:HAMCP_BUILD_DIRTY": "false",
        "build-arg:HAMCP_BUILD_SHA": release_sha,
        "build-arg:HAMCP_BUILD_TIME": build_time,
        **{f"label:{key}": value for key, value in labels.items()},
    }


def _require_items(container: dict[str, Any], expected: dict[str, Any], reason: str) -> None:
    for key, value in expected.items():
        if container.get(key) != value:
            _fail(reason)


def verify_source(args: argparse.Namespace) -> None:
    release_sha = _exact_pattern(
        args.expected_release_sha, SHA_PATTERN, "EXPECTED_RELEASE_SHA_INVALID"
    )
    version = _exact_pattern(
        args.expected_version, VERSION_PATTERN, "EXPECTED_VERSION_INVALID"
    )
    build_time = _exact_build_time(args.expected_build_time)
    run_id = _exact_pattern(
        args.expected_run_id, RUN_ID_PATTERN, "EXPECTED_RUN_ID_INVALID"
    )
    workflow_sha = _exact_pattern(
        args.expected_workflow_sha, SHA_PATTERN, "EXPECTED_WORKFLOW_SHA_INVALID"
    )
    if args.expected_run_attempt < 1 or args.expected_run_attempt > 100:
        _fail("EXPECTED_RUN_ATTEMPT_INVALID")
    if args.expected_event_name not in {"push", "workflow_dispatch"}:
        _fail("EXPECTED_EVENT_NAME_INVALID")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repository):
        _fail("EXPECTED_REPOSITORY_INVALID")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", args.owner):
        _fail("EXPECTED_OWNER_INVALID")

    manifest_evidence = _manifest_evidence(
        args.manifest_json, args.expected_digest
    )
    platform_digests = {
        platform: descriptor.digest
        for platform, descriptor in manifest_evidence.platforms.items()
    }
    image_paths = _parse_image_arguments(args.image_json)
    source_url = f"https://github.com/{args.repository}"
    labels = _required_labels(release_sha, version, build_time, source_url)

    for platform, path in image_paths.items():
        image = _mapping(
            _read_json(path, MAX_IMAGE_METADATA_BYTES),
            "SOURCE_IMAGE_METADATA_INVALID",
        )
        os_name, architecture, variant, _env_name = REQUIRED_PLATFORMS[platform]
        expected_platform = {"os": os_name, "architecture": architecture}
        if variant is not None:
            expected_platform["variant"] = variant
        _require_items(image, expected_platform, "SOURCE_IMAGE_PLATFORM_MISMATCH")
        if variant is None and image.get("variant") not in {None, ""}:
            _fail("SOURCE_IMAGE_PLATFORM_MISMATCH")
        config = _mapping(image.get("config"), "SOURCE_IMAGE_CONFIG_INVALID")
        actual_labels = _mapping(
            config.get("Labels"), "SOURCE_IMAGE_LABELS_INVALID"
        )
        _require_items(actual_labels, labels, "SOURCE_IMAGE_LABEL_MISMATCH")

    provenance, sbom = _attestation_statements(
        args.image_repository,
        manifest_evidence,
    )
    if set(provenance) != set(REQUIRED_PLATFORMS):
        _fail("SOURCE_PROVENANCE_PLATFORM_SET_INVALID")
    required_args = _required_build_args(
        release_sha, version, build_time, source_url
    )
    expected_builder = (
        f"https://github.com/{args.repository}/actions/runs/{run_id}/attempts/"
        f"{args.expected_run_attempt}"
    )
    expected_workflow_ref = (
        f"{args.repository}/{WORKFLOW_PATH}@refs/heads/main"
    )
    expected_internal = {
        "github_actor": args.owner,
        "github_event_name": args.expected_event_name,
        "github_job": "promote",
        "github_ref": "refs/heads/main",
        "github_ref_name": "main",
        "github_ref_protected": "true",
        "github_ref_type": "branch",
        "github_repository": args.repository,
        "github_repository_owner": args.owner,
        "github_run_attempt": str(args.expected_run_attempt),
        "github_run_id": run_id,
        "github_server_url": "https://github.com",
        "github_triggering_actor": args.owner,
        "github_workflow": WORKFLOW_NAME,
        "github_workflow_ref": expected_workflow_ref,
        "github_workflow_sha": workflow_sha,
    }
    expected_vcs = {
        "localdir:context": SOURCE_DIRECTORY,
        "localdir:dockerfile": SOURCE_DIRECTORY,
        "revision": release_sha,
        "source": source_url,
    }

    if set(sbom) != set(REQUIRED_PLATFORMS):
        _fail("SOURCE_SBOM_PLATFORM_SET_INVALID")
    for platform in REQUIRED_PLATFORMS:
        envelope = _mapping(sbom.get(platform), "SOURCE_SBOM_ENVELOPE_INVALID")
        spdx = _mapping(envelope.get("SPDX"), "SOURCE_SBOM_SPDX_INVALID")
        _require_items(
            spdx,
            {
                "SPDXID": "SPDXRef-DOCUMENT",
                "dataLicense": "CC0-1.0",
                "spdxVersion": "SPDX-2.3",
            },
            "SOURCE_SBOM_IDENTITY_MISMATCH",
        )
        packages = _sequence(spdx.get("packages"), "SOURCE_SBOM_PACKAGES_INVALID")
        if not packages:
            _fail("SOURCE_SBOM_PACKAGES_EMPTY")

    for platform in REQUIRED_PLATFORMS:
        envelope = _mapping(
            provenance.get(platform), "SOURCE_PROVENANCE_ENVELOPE_INVALID"
        )
        slsa = _mapping(envelope.get("SLSA"), "SOURCE_SLSA_INVALID")
        definition = _mapping(
            slsa.get("buildDefinition"), "SOURCE_SLSA_DEFINITION_INVALID"
        )
        if definition.get("buildType") != BUILD_TYPE:
            _fail("SOURCE_SLSA_BUILD_TYPE_MISMATCH")
        external = _mapping(
            definition.get("externalParameters"),
            "SOURCE_SLSA_EXTERNAL_PARAMETERS_INVALID",
        )
        request = _mapping(
            external.get("request"), "SOURCE_SLSA_REQUEST_INVALID"
        )
        request_args = _mapping(
            request.get("args"), "SOURCE_SLSA_BUILD_ARGS_INVALID"
        )
        _require_items(
            request_args, required_args, "SOURCE_SLSA_BUILD_ARGS_MISMATCH"
        )
        root = _mapping(request.get("root"), "SOURCE_SLSA_ROOT_INVALID")
        root_request = _mapping(
            root.get("request"), "SOURCE_SLSA_ROOT_REQUEST_INVALID"
        )
        root_args = _mapping(
            root_request.get("args"), "SOURCE_SLSA_ROOT_ARGS_INVALID"
        )
        _require_items(root_args, required_args, "SOURCE_SLSA_ROOT_ARGS_MISMATCH")
        _require_items(
            root_args,
            {
                "vcs:localdir:context": SOURCE_DIRECTORY,
                "vcs:localdir:dockerfile": SOURCE_DIRECTORY,
                "vcs:revision": release_sha,
                "vcs:source": source_url,
            },
            "SOURCE_SLSA_ROOT_VCS_MISMATCH",
        )

        internal = _mapping(
            definition.get("internalParameters"),
            "SOURCE_SLSA_INTERNAL_PARAMETERS_INVALID",
        )
        _require_items(
            internal, expected_internal, "SOURCE_SLSA_GITHUB_IDENTITY_MISMATCH"
        )
        event_payload = _mapping(
            internal.get("github_event_payload"),
            "SOURCE_SLSA_EVENT_PAYLOAD_INVALID",
        )
        if event_payload.get("ref") != "refs/heads/main":
            _fail("SOURCE_SLSA_EVENT_REF_MISMATCH")
        payload_repository = _mapping(
            event_payload.get("repository"),
            "SOURCE_SLSA_EVENT_REPOSITORY_INVALID",
        )
        if payload_repository.get("full_name") != args.repository:
            _fail("SOURCE_SLSA_EVENT_REPOSITORY_MISMATCH")
        if args.expected_event_name == "workflow_dispatch":
            inputs = _mapping(
                event_payload.get("inputs"), "SOURCE_SLSA_EVENT_INPUTS_INVALID"
            )
            _require_items(
                inputs,
                {"release_sha": release_sha, "expected_version": version},
                "SOURCE_SLSA_EVENT_INPUTS_MISMATCH",
            )

        run_details = _mapping(
            slsa.get("runDetails"), "SOURCE_SLSA_RUN_DETAILS_INVALID"
        )
        builder = _mapping(
            run_details.get("builder"), "SOURCE_SLSA_BUILDER_INVALID"
        )
        if builder.get("id") != expected_builder:
            _fail("SOURCE_SLSA_BUILDER_MISMATCH")
        metadata = _mapping(
            run_details.get("metadata"), "SOURCE_SLSA_METADATA_INVALID"
        )
        completeness = _mapping(
            metadata.get("buildkit_completeness"),
            "SOURCE_SLSA_COMPLETENESS_INVALID",
        )
        if completeness.get("request") is not True:
            _fail("SOURCE_SLSA_REQUEST_INCOMPLETE")
        buildkit_metadata = _mapping(
            metadata.get("buildkit_metadata"),
            "SOURCE_BUILDKIT_METADATA_INVALID",
        )
        vcs = _mapping(
            buildkit_metadata.get("vcs"), "SOURCE_BUILDKIT_VCS_INVALID"
        )
        _require_items(vcs, expected_vcs, "SOURCE_BUILDKIT_VCS_MISMATCH")

    _write_lines(
        args.github_output,
        (
            "source_image_verified=true",
            f"manifest_digest={args.expected_digest}",
            "sbom_status=present",
            *(
                f"{REQUIRED_PLATFORMS[platform][3]}={platform_digests[platform]}"
                for platform in REQUIRED_PLATFORMS
            ),
        ),
    )


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    commands = root.add_subparsers(dest="command", required=True)

    extract = commands.add_parser("extract-manifest")
    extract.add_argument("--manifest-json", type=Path, required=True)
    extract.add_argument("--expected-digest", required=True)
    extract.add_argument("--output-env", type=Path, required=True)
    extract.set_defaults(handler=extract_manifest)

    run = commands.add_parser("verify-recovery-run")
    run.add_argument("--run-json", type=Path, required=True)
    run.add_argument("--expected-run-id", required=True)
    run.add_argument("--expected-repository", required=True)
    run.add_argument("--expected-owner", required=True)
    run.add_argument("--github-output", type=Path, required=True)
    run.set_defaults(handler=verify_recovery_run)

    source = commands.add_parser("verify-source")
    source.add_argument("--manifest-json", type=Path, required=True)
    source.add_argument("--image-json", action="append", required=True)
    source.add_argument("--image-repository", required=True)
    source.add_argument("--expected-digest", required=True)
    source.add_argument("--expected-release-sha", required=True)
    source.add_argument("--expected-version", required=True)
    source.add_argument("--expected-build-time", required=True)
    source.add_argument("--expected-run-id", required=True)
    source.add_argument("--expected-run-attempt", type=int, required=True)
    source.add_argument("--expected-workflow-sha", required=True)
    source.add_argument("--expected-event-name", required=True)
    source.add_argument("--repository", required=True)
    source.add_argument("--owner", required=True)
    source.add_argument("--github-output", type=Path, required=True)
    source.set_defaults(handler=verify_source)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        args.handler(args)
    except VerificationError as exc:
        print(f"publication source verification failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
