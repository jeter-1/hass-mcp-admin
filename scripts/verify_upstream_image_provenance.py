#!/usr/bin/env python3
"""Verify official ha-mcp image provenance from its attestation payloads.

The upstream index is fetched anonymously by exact digest.  Every platform
manifest must have exactly one BuildKit attestation manifest whose in-toto
SLSA statement names that platform digest in every subject and binds the
build to the exact upstream source commit, repository, protected workflow and
GitHub-hosted builder.  The statement's resolved dependencies (materials) must
be well formed, platform-consistent and agree with that source: every Git
material must name the official repository at the exact commit, and when no
Git material is present the source must be the attested local build context.
Index annotations are used only to locate attestation manifests; image labels
are never read here and remain informational consistency checks in the
calling workflow.

Upstream currently publishes unsigned BuildKit in-toto statements.  There is
no DSSE envelope, Sigstore bundle or signer certificate to verify, so the
result records ``unsigned_buildkit_in_toto`` instead of claiming a verified
signature.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any
from urllib.parse import parse_qsl, quote, unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))

import verify_publication_source_image as registry  # noqa: E402
from verify_publication_source_image import (  # noqa: E402
    DIGEST_PATTERN,
    IMAGE_MEDIA_TYPE,
    INDEX_MEDIA_TYPE,
    IN_TOTO_MEDIA_TYPE,
    SHA_PATTERN,
    SLSA_PREDICATE_TYPE,
    SPDX_PREDICATE_TYPE,
    Descriptor,
    VerificationError,
    _decode_json,
    _descriptor,
    _exact_pattern,
    _fail,
    _mapping,
    _sequence,
)

IMAGE_REPOSITORY = "ghcr.io/homeassistant-ai/ha-mcp"
GITHUB_REPOSITORY = "homeassistant-ai/ha-mcp"
GITHUB_REPOSITORY_ID = "1056618941"
GITHUB_REPOSITORY_OWNER_ID = "232192432"
GITHUB_SERVER_URL = "https://github.com"
SOURCE_URL = "https://github.com/homeassistant-ai/ha-mcp"
WORKFLOW_REF = (
    "homeassistant-ai/ha-mcp/.github/workflows/release-publish.yml"
    "@refs/heads/master"
)
SOURCE_REF = "refs/heads/master"
BUILD_TYPE = registry.BUILD_TYPE
ATTESTATION_ARTIFACT_TYPE = registry.ATTESTATION_ARTIFACT_TYPE
EMPTY_CONFIG = {
    "mediaType": registry.EMPTY_CONFIG_MEDIA_TYPE,
    "digest": registry.EMPTY_CONFIG_DIGEST,
    "size": 2,
    "data": "e30=",
}
LEGACY_CONFIG_MEDIA_TYPE = "application/vnd.oci.image.config.v1+json"
STATEMENT_TYPES = {
    "https://in-toto.io/Statement/v0.1",
    "https://in-toto.io/Statement/v1",
}
SUBJECT_PREFIX = f"pkg:docker/{IMAGE_REPOSITORY}@"
REQUIRED_PLATFORMS = frozenset({"linux/amd64", "linux/arm64"})
# Same selection as the calling workflows: linux amd64, arm64 and arm (with
# any variant).  Every platform manifest is attested regardless of selection.
SELECTED_PLATFORM_PATTERN = re.compile(r"linux/(amd64|arm64|arm)(/[a-z0-9]+)?\Z")
ATTESTATION_REFERENCE_TYPE = "attestation-manifest"
UNKNOWN_PLATFORM = {"architecture": "unknown", "os": "unknown"}
BUILDER_PATTERN = re.compile(
    r"https://github\.com/homeassistant-ai/ha-mcp/actions/runs/"
    r"([1-9][0-9]{0,19})/attempts/([1-9][0-9]{0,4})\Z"
)
VERSION_PATTERN = re.compile(
    r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z"
)
CREATED_PATTERN = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,9})?Z\Z"
)
MAX_INDEX_ENTRIES = 32
MAX_SUBJECTS = 64
MAX_SUBJECT_NAME_LENGTH = 1_024
MAX_TOTAL_STATEMENT_BYTES = 67_108_864
ATTESTATION_MANIFEST_KEYS = frozenset(
    {"schemaVersion", "mediaType", "artifactType", "config", "layers", "subject"}
)
# SLSA v1 predicate shape.  A v0.2-style ``materials`` list, or any other
# alternative location for dependency evidence, is refused rather than ignored.
PREDICATE_KEYS = frozenset({"buildDefinition", "runDetails"})
BUILD_DEFINITION_KEYS = frozenset(
    {"buildType", "externalParameters", "internalParameters", "resolvedDependencies"}
)
MAX_MATERIALS = 64
MAX_MATERIAL_URI_LENGTH = 1_024
IMAGE_MATERIAL_PREFIX = "pkg:docker/"
IMAGE_MATERIAL_QUERY_KEYS = frozenset({"digest", "platform"})
SHA256_HEX_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
# BuildKit records the requested source as local when the build context is a
# checked-out directory; the commit is then attested only by VCS metadata.
LOCAL_CONTEXT_KEYS = ("localdir:context", "localdir:dockerfile")
RESULT_SCHEMA = "hamcp.upstream_image_provenance.v1"


class UpstreamRegistryReader(registry.AnonymousRegistryReader):
    """Anonymous reader that also fetches one image index by exact digest."""

    def index(self, digest: str) -> bytes:
        digest = _exact_pattern(digest, DIGEST_PATTERN, "UPSTREAM_INDEX_DIGEST_INVALID")
        response = self._transport.request(
            "GET",
            self._repository.manifest_url(digest),
            {
                "Accept": INDEX_MEDIA_TYPE,
                "Authorization": f"Bearer {self._token}",
            },
            registry.MAX_MANIFEST_BYTES,
        )
        if response.status != 200:
            _fail("UPSTREAM_INDEX_UNAVAILABLE")
        if response.headers.get("docker-content-digest") != digest:
            _fail("UPSTREAM_INDEX_HEADER_DIGEST_MISMATCH")
        if f"sha256:{hashlib.sha256(response.body).hexdigest()}" != digest:
            _fail("UPSTREAM_INDEX_DIGEST_MISMATCH")
        return response.body


def _platform_key(value: Any) -> str:
    platform = _mapping(value, "UPSTREAM_INDEX_PLATFORM_INVALID")
    operating_system = platform.get("os")
    architecture = platform.get("architecture")
    variant = platform.get("variant")
    if not isinstance(operating_system, str) or not isinstance(architecture, str):
        _fail("UPSTREAM_INDEX_PLATFORM_INVALID")
    if variant is not None and not isinstance(variant, str):
        _fail("UPSTREAM_INDEX_PLATFORM_INVALID")
    key = f"{operating_system}/{architecture}" + (f"/{variant}" if variant else "")
    if len(key) > 64 or re.fullmatch(r"[a-z0-9]+(/[a-z0-9]+){1,2}", key) is None:
        _fail("UPSTREAM_INDEX_PLATFORM_INVALID")
    return key


def _index_entries(
    raw_index: bytes,
) -> tuple[dict[str, Descriptor], dict[str, Descriptor]]:
    index = _mapping(
        _decode_json(raw_index, "UPSTREAM_INDEX_JSON_INVALID"),
        "UPSTREAM_INDEX_INVALID",
    )
    if index.get("schemaVersion") != 2 or index.get("mediaType") != INDEX_MEDIA_TYPE:
        _fail("UPSTREAM_INDEX_TYPE_INVALID")
    manifests = _sequence(index.get("manifests"), "UPSTREAM_INDEX_MANIFESTS_INVALID")
    if not manifests or len(manifests) > MAX_INDEX_ENTRIES:
        _fail("UPSTREAM_INDEX_MANIFESTS_INVALID")

    images: dict[str, Descriptor] = {}
    references: dict[str, Descriptor] = {}
    for raw_entry in manifests:
        entry = _mapping(raw_entry, "UPSTREAM_INDEX_ENTRY_INVALID")
        if entry.get("mediaType") != IMAGE_MEDIA_TYPE:
            _fail("UPSTREAM_INDEX_ENTRY_MEDIA_TYPE_INVALID")
        descriptor = _descriptor(entry, "UPSTREAM_INDEX_ENTRY_DESCRIPTOR_INVALID")
        annotations = entry.get("annotations")
        if annotations is None:
            key = _platform_key(entry.get("platform"))
            if key in images:
                _fail("UPSTREAM_INDEX_PLATFORM_AMBIGUOUS")
            images[key] = descriptor
            continue
        annotations = _mapping(annotations, "UPSTREAM_INDEX_ANNOTATIONS_INVALID")
        if annotations.get("vnd.docker.reference.type") != ATTESTATION_REFERENCE_TYPE:
            _fail("UPSTREAM_INDEX_ANNOTATIONS_INVALID")
        if set(annotations) != {
            "vnd.docker.reference.type",
            "vnd.docker.reference.digest",
        }:
            _fail("UPSTREAM_INDEX_ANNOTATIONS_INVALID")
        if entry.get("platform") != UNKNOWN_PLATFORM:
            _fail("UPSTREAM_ATTESTATION_PLATFORM_INVALID")
        referenced = _exact_pattern(
            annotations.get("vnd.docker.reference.digest"),
            DIGEST_PATTERN,
            "UPSTREAM_ATTESTATION_REFERENCE_INVALID",
        )
        if referenced in references:
            _fail("UPSTREAM_ATTESTATION_AMBIGUOUS")
        references[referenced] = descriptor

    if not REQUIRED_PLATFORMS.issubset(images):
        _fail("UPSTREAM_REQUIRED_PLATFORM_MISSING")
    image_digests = [descriptor.digest for descriptor in images.values()]
    if len(set(image_digests)) != len(image_digests):
        _fail("UPSTREAM_INDEX_PLATFORM_AMBIGUOUS")
    if set(references) != set(image_digests):
        # A platform manifest without an attestation payload, or an
        # attestation that references no platform in this index, is rejected.
        _fail("UPSTREAM_ATTESTATION_MISSING_OR_UNBOUND")
    attestations = {
        platform: references[descriptor.digest]
        for platform, descriptor in images.items()
    }
    return images, attestations


def _attestation_layers(
    raw_manifest: bytes, platform_descriptor: Descriptor
) -> list[tuple[str, Descriptor]]:
    manifest = _mapping(
        _decode_json(raw_manifest, "UPSTREAM_ATTESTATION_MANIFEST_JSON_INVALID"),
        "UPSTREAM_ATTESTATION_MANIFEST_INVALID",
    )
    if not set(manifest).issubset(ATTESTATION_MANIFEST_KEYS):
        _fail("UPSTREAM_ATTESTATION_MANIFEST_INVALID")
    if manifest.get("schemaVersion") != 2 or manifest.get("mediaType") != IMAGE_MEDIA_TYPE:
        _fail("UPSTREAM_ATTESTATION_MANIFEST_TYPE_INVALID")
    config = _mapping(manifest.get("config"), "UPSTREAM_ATTESTATION_CONFIG_INVALID")
    if "artifactType" in manifest or "subject" in manifest:
        # Current BuildKit form: typed artifact with an exact OCI subject.
        if manifest.get("artifactType") != ATTESTATION_ARTIFACT_TYPE:
            _fail("UPSTREAM_ATTESTATION_MANIFEST_TYPE_INVALID")
        if config != EMPTY_CONFIG:
            _fail("UPSTREAM_ATTESTATION_CONFIG_INVALID")
        if manifest.get("subject") != {
            "mediaType": IMAGE_MEDIA_TYPE,
            "digest": platform_descriptor.digest,
            "size": platform_descriptor.size,
        }:
            _fail("UPSTREAM_ATTESTATION_SUBJECT_MISMATCH")
    else:
        # Earlier BuildKit form: untyped manifest bound only by the index
        # reference; the statement subjects below still bind every platform.
        if config.get("mediaType") != LEGACY_CONFIG_MEDIA_TYPE:
            _fail("UPSTREAM_ATTESTATION_CONFIG_INVALID")
        _descriptor(config, "UPSTREAM_ATTESTATION_CONFIG_INVALID")

    layers = _sequence(manifest.get("layers"), "UPSTREAM_ATTESTATION_LAYERS_INVALID")
    if not 1 <= len(layers) <= 4:
        _fail("UPSTREAM_ATTESTATION_LAYERS_INVALID")
    result: list[tuple[str, Descriptor]] = []
    seen: set[str] = set()
    for raw_layer in layers:
        layer = _mapping(raw_layer, "UPSTREAM_ATTESTATION_LAYER_INVALID")
        if layer.get("mediaType") != IN_TOTO_MEDIA_TYPE:
            _fail("UPSTREAM_ATTESTATION_LAYER_MEDIA_TYPE_INVALID")
        annotations = _mapping(
            layer.get("annotations"), "UPSTREAM_ATTESTATION_LAYER_ANNOTATIONS_INVALID"
        )
        if set(annotations) != {"in-toto.io/predicate-type"}:
            _fail("UPSTREAM_ATTESTATION_LAYER_ANNOTATIONS_INVALID")
        predicate_type = annotations["in-toto.io/predicate-type"]
        if predicate_type not in {SLSA_PREDICATE_TYPE, SPDX_PREDICATE_TYPE}:
            _fail("UPSTREAM_ATTESTATION_PREDICATE_TYPE_INVALID")
        if predicate_type in seen:
            _fail("UPSTREAM_ATTESTATION_PREDICATE_SET_INVALID")
        seen.add(predicate_type)
        result.append(
            (
                predicate_type,
                _descriptor(layer, "UPSTREAM_ATTESTATION_LAYER_DESCRIPTOR_INVALID"),
            )
        )
    if SLSA_PREDICATE_TYPE not in seen:
        _fail("UPSTREAM_SLSA_PROVENANCE_MISSING")
    return result


def _verify_subjects(
    statement: dict[str, Any], *, platform: str, digest: str, version: str
) -> None:
    subjects = _sequence(statement.get("subject"), "UPSTREAM_STATEMENT_SUBJECT_INVALID")
    if not 1 <= len(subjects) <= MAX_SUBJECTS:
        _fail("UPSTREAM_STATEMENT_SUBJECT_INVALID")
    suffix = f"?platform={quote(platform, safe='')}"
    exact_version = f"{SUBJECT_PREFIX}{version}{suffix}"
    names: set[str] = set()
    for raw_subject in subjects:
        subject = _mapping(raw_subject, "UPSTREAM_STATEMENT_SUBJECT_INVALID")
        if set(subject) != {"name", "digest"}:
            _fail("UPSTREAM_STATEMENT_SUBJECT_INVALID")
        name = subject["name"]
        if (
            not isinstance(name, str)
            or len(name) > MAX_SUBJECT_NAME_LENGTH
            or not name.startswith(SUBJECT_PREFIX)
            or not name.endswith(suffix)
            or any(ord(character) < 33 or ord(character) == 127 for character in name)
        ):
            _fail("UPSTREAM_STATEMENT_SUBJECT_INVALID")
        if subject["digest"] != {"sha256": digest.removeprefix("sha256:")}:
            _fail("UPSTREAM_STATEMENT_SUBJECT_DIGEST_MISMATCH")
        names.add(name)
    if exact_version not in names:
        _fail("UPSTREAM_STATEMENT_VERSION_SUBJECT_MISSING")


def _string(container: dict[str, Any], key: str, reason: str) -> str:
    value = container.get(key)
    if not isinstance(value, str):
        _fail(reason)
    return value


def _verify_request_arguments(
    arguments: dict[str, Any],
    *,
    version: str,
    source_commit: str,
    workflow_sha: str,
) -> dict[str, str]:
    for key, value in arguments.items():
        if not isinstance(key, str) or not isinstance(value, str):
            _fail("UPSTREAM_PROVENANCE_REQUEST_INVALID")
    expected = {
        "vcs:revision": source_commit,
        "vcs:source": SOURCE_URL,
        "build-arg:BUILD_VERSION": version,
        "label:org.opencontainers.image.version": version,
        "label:org.opencontainers.image.revision": workflow_sha,
        "label:org.opencontainers.image.source": SOURCE_URL,
    }
    for key, value in expected.items():
        if key in arguments and arguments[key] != value:
            _fail("UPSTREAM_PROVENANCE_REQUEST_MISMATCH")
    attested: dict[str, str] = {}
    for key in ("version", "revision", "source", "created"):
        value = arguments.get(f"label:org.opencontainers.image.{key}")
        if value is not None:
            attested[key] = value
    if "created" in attested and CREATED_PATTERN.fullmatch(attested["created"]) is None:
        _fail("UPSTREAM_PROVENANCE_REQUEST_INVALID")
    return attested


def _material_uri(material: dict[str, Any]) -> str:
    uri = material.get("uri")
    if (
        not isinstance(uri, str)
        or not 1 <= len(uri) <= MAX_MATERIAL_URI_LENGTH
        or any(ord(character) < 33 or ord(character) == 127 for character in uri)
    ):
        _fail("UPSTREAM_PROVENANCE_MATERIAL_INVALID")
    return uri


def _git_material_repository(uri: str) -> str | None:
    """Return the repository URL named by a Git or URL source material.

    Accepts BuildKit (``https://host/repo.git#ref``) and purl-style
    (``git+https://host/repo@ref``) forms.  Returns ``None`` for a URI that
    is not a remote source at all.
    """

    if uri.startswith("git+"):
        remainder = uri.removeprefix("git+")
    elif uri.startswith(("https://", "http://", "git://", "ssh://", "git@")):
        remainder = uri
    else:
        return None
    parsed = urlsplit(remainder)
    if parsed.scheme != "https" or parsed.query:
        # Non-HTTPS transports, credentials, ports and queries are never the
        # official repository.
        _fail("UPSTREAM_PROVENANCE_MATERIAL_REPOSITORY_MISMATCH")
    path = parsed.path.split("@", 1)[0].removesuffix(".git")
    return f"https://{parsed.netloc}{path}"


def _image_material(
    uri: str, digest: dict[str, Any], *, platform: str
) -> tuple[str, str]:
    if set(digest) != {"sha256"} or not isinstance(digest["sha256"], str):
        _fail("UPSTREAM_PROVENANCE_MATERIAL_DIGEST_INVALID")
    sha256 = digest["sha256"]
    if SHA256_HEX_PATTERN.fullmatch(sha256) is None:
        _fail("UPSTREAM_PROVENANCE_MATERIAL_DIGEST_INVALID")
    name, separator, query = uri.partition("?")
    if len(name) <= len(IMAGE_MATERIAL_PREFIX) or "#" in uri:
        _fail("UPSTREAM_PROVENANCE_MATERIAL_INVALID")
    try:
        pairs = parse_qsl(query, keep_blank_values=True, strict_parsing=bool(separator))
    except ValueError:
        _fail("UPSTREAM_PROVENANCE_MATERIAL_INVALID")
    keys = [key for key, _value in pairs]
    if len(keys) != len(set(keys)) or not set(keys).issubset(IMAGE_MATERIAL_QUERY_KEYS):
        _fail("UPSTREAM_PROVENANCE_MATERIAL_INVALID")
    parameters = dict(pairs)
    if "digest" in parameters and parameters["digest"] != f"sha256:{sha256}":
        _fail("UPSTREAM_PROVENANCE_MATERIAL_DIGEST_MISMATCH")
    if "platform" in parameters and parameters["platform"] != platform:
        _fail("UPSTREAM_PROVENANCE_MATERIAL_PLATFORM_MISMATCH")
    # Normalized without the platform qualifier so platforms can be compared.
    return unquote(name), sha256


def _verify_materials(
    definition: dict[str, Any],
    metadata: dict[str, Any],
    vcs: dict[str, Any],
    *,
    platform: str,
    source_commit: str,
) -> dict[str, Any]:
    """Validate resolved dependencies against the expected source.

    Image materials must be digest-consistent and name only this platform.
    Every Git material must be the official repository at the exact commit.
    Absent any Git material, BuildKit must attest a local build context, whose
    commit is the VCS revision already bound to ``source_commit``.  Anything
    else (missing, empty, malformed, unsupported or conflicting materials)
    fails closed.
    """

    if "resolvedDependencies" not in definition:
        _fail("UPSTREAM_PROVENANCE_MATERIALS_MISSING")
    materials = _sequence(
        definition["resolvedDependencies"], "UPSTREAM_PROVENANCE_MATERIALS_INVALID"
    )
    if not materials:
        _fail("UPSTREAM_PROVENANCE_MATERIALS_MISSING")
    if len(materials) > MAX_MATERIALS:
        _fail("UPSTREAM_PROVENANCE_MATERIALS_INVALID")
    images: set[tuple[str, str]] = set()
    image_names: set[str] = set()
    git_commits: set[str] = set()
    for raw_material in materials:
        material = _mapping(raw_material, "UPSTREAM_PROVENANCE_MATERIAL_INVALID")
        if set(material) != {"uri", "digest"}:
            _fail("UPSTREAM_PROVENANCE_MATERIAL_INVALID")
        uri = _material_uri(material)
        digest = _mapping(material["digest"], "UPSTREAM_PROVENANCE_MATERIAL_DIGEST_INVALID")
        if uri.startswith(IMAGE_MATERIAL_PREFIX):
            name, sha256 = _image_material(uri, digest, platform=platform)
            if name in image_names:
                _fail("UPSTREAM_PROVENANCE_MATERIAL_AMBIGUOUS")
            image_names.add(name)
            images.add((name, sha256))
            continue
        repository = _git_material_repository(uri)
        if repository is None:
            _fail("UPSTREAM_PROVENANCE_MATERIAL_UNSUPPORTED")
        if repository != SOURCE_URL:
            _fail("UPSTREAM_PROVENANCE_MATERIAL_REPOSITORY_MISMATCH")
        if set(digest) != {"sha1"} or not isinstance(digest["sha1"], str):
            _fail("UPSTREAM_PROVENANCE_MATERIAL_DIGEST_INVALID")
        if digest["sha1"] != source_commit:
            _fail("UPSTREAM_PROVENANCE_MATERIAL_COMMIT_MISMATCH")
        git_commits.add(digest["sha1"])

    completeness = metadata.get("buildkit_completeness")
    declared_complete: bool | None = None
    if completeness is not None:
        completeness = _mapping(completeness, "UPSTREAM_PROVENANCE_INVALID")
        declared_complete = completeness.get("resolvedDependencies")
        if not isinstance(declared_complete, bool):
            _fail("UPSTREAM_PROVENANCE_INVALID")
    if git_commits:
        source_material = "official_git_commit"
    else:
        for key in LOCAL_CONTEXT_KEYS:
            if not isinstance(vcs.get(key), str) or not vcs[key]:
                _fail("UPSTREAM_PROVENANCE_SOURCE_MATERIAL_MISSING")
        source_material = "local_build_context"
    return {
        "source_material": source_material,
        "materials_declared_complete": declared_complete,
        "image_materials": [
            {"uri": name, "sha256": sha256} for name, sha256 in sorted(images)
        ],
    }


def _verify_slsa_predicate(
    statement: dict[str, Any], *, platform: str, version: str, source_commit: str
) -> dict[str, Any]:
    predicate = _mapping(statement.get("predicate"), "UPSTREAM_PROVENANCE_INVALID")
    if set(predicate) != PREDICATE_KEYS:
        _fail("UPSTREAM_PROVENANCE_INVALID")
    definition = _mapping(
        predicate.get("buildDefinition"), "UPSTREAM_PROVENANCE_INVALID"
    )
    if not set(definition).issubset(BUILD_DEFINITION_KEYS):
        _fail("UPSTREAM_PROVENANCE_INVALID")
    if definition.get("buildType") != BUILD_TYPE:
        _fail("UPSTREAM_PROVENANCE_BUILD_TYPE_INVALID")
    run_details = _mapping(predicate.get("runDetails"), "UPSTREAM_PROVENANCE_INVALID")
    builder = _mapping(run_details.get("builder"), "UPSTREAM_PROVENANCE_BUILDER_INVALID")
    builder_id = _string(builder, "id", "UPSTREAM_PROVENANCE_BUILDER_INVALID")
    builder_match = BUILDER_PATTERN.fullmatch(builder_id)
    if builder_match is None:
        _fail("UPSTREAM_PROVENANCE_BUILDER_INVALID")

    metadata = _mapping(run_details.get("metadata"), "UPSTREAM_PROVENANCE_INVALID")
    buildkit = _mapping(
        metadata.get("buildkit_metadata"), "UPSTREAM_PROVENANCE_VCS_INVALID"
    )
    vcs = _mapping(buildkit.get("vcs"), "UPSTREAM_PROVENANCE_VCS_INVALID")
    if vcs.get("source") != SOURCE_URL:
        _fail("UPSTREAM_PROVENANCE_SOURCE_MISMATCH")
    if vcs.get("revision") != source_commit:
        _fail("UPSTREAM_PROVENANCE_COMMIT_MISMATCH")
    materials = _verify_materials(
        definition, metadata, vcs, platform=platform, source_commit=source_commit
    )

    internal = _mapping(
        definition.get("internalParameters"), "UPSTREAM_PROVENANCE_IDENTITY_INVALID"
    )
    identity = {
        "github_repository": GITHUB_REPOSITORY,
        "github_repository_id": GITHUB_REPOSITORY_ID,
        "github_repository_owner_id": GITHUB_REPOSITORY_OWNER_ID,
        "github_server_url": GITHUB_SERVER_URL,
        "github_workflow_ref": WORKFLOW_REF,
        "github_ref": SOURCE_REF,
        "github_ref_protected": "true",
        "github_runner_environment": "github-hosted",
        "github_run_id": builder_match.group(1),
        "github_run_attempt": builder_match.group(2),
    }
    for key, value in identity.items():
        if internal.get(key) != value:
            _fail("UPSTREAM_PROVENANCE_IDENTITY_MISMATCH")
    workflow_sha = _exact_pattern(
        internal.get("github_workflow_sha"),
        SHA_PATTERN,
        "UPSTREAM_PROVENANCE_IDENTITY_INVALID",
    )

    external = _mapping(
        definition.get("externalParameters"), "UPSTREAM_PROVENANCE_REQUEST_INVALID"
    )
    request = _mapping(external.get("request"), "UPSTREAM_PROVENANCE_REQUEST_INVALID")
    arguments = _mapping(request.get("args"), "UPSTREAM_PROVENANCE_REQUEST_INVALID")
    attested = _verify_request_arguments(
        arguments,
        version=version,
        source_commit=source_commit,
        workflow_sha=workflow_sha,
    )
    if "root" in request:
        root = _mapping(request["root"], "UPSTREAM_PROVENANCE_REQUEST_INVALID")
        root_request = _mapping(root.get("request"), "UPSTREAM_PROVENANCE_REQUEST_INVALID")
        root_arguments = _mapping(
            root_request.get("args"), "UPSTREAM_PROVENANCE_REQUEST_INVALID"
        )
        root_attested = _verify_request_arguments(
            root_arguments,
            version=version,
            source_commit=source_commit,
            workflow_sha=workflow_sha,
        )
        for key, value in root_attested.items():
            if attested.setdefault(key, value) != value:
                _fail("UPSTREAM_PROVENANCE_REQUEST_MISMATCH")
    if attested.get("version") != version or attested.get("revision") != workflow_sha:
        _fail("UPSTREAM_PROVENANCE_REQUEST_MISMATCH")
    return {
        "builder_id": builder_id,
        "workflow_sha": workflow_sha,
        "attested_labels": dict(sorted(attested.items())),
        **materials,
    }


def verify(
    *,
    index_digest: str,
    version: str,
    source_commit: str,
    transport: registry.Transport,
) -> dict[str, Any]:
    index_digest = _exact_pattern(
        index_digest, DIGEST_PATTERN, "UPSTREAM_INDEX_DIGEST_INVALID"
    )
    version = _exact_pattern(version, VERSION_PATTERN, "UPSTREAM_VERSION_INVALID")
    source_commit = _exact_pattern(
        source_commit, SHA_PATTERN, "UPSTREAM_SOURCE_COMMIT_INVALID"
    )
    reader = UpstreamRegistryReader(
        repository=registry.RegistryReference.parse(IMAGE_REPOSITORY),
        transport=transport,
    )
    images, attestations = _index_entries(reader.index(index_digest))

    total_bytes = 0
    bindings: list[dict[str, Any]] = []
    sbom_platforms: set[str] = set()
    for platform in sorted(images):
        platform_descriptor = images[platform]
        layers = _attestation_layers(
            reader.manifest(attestations[platform]), platform_descriptor
        )
        for predicate_type, layer in layers:
            limit = (
                registry.MAX_PROVENANCE_BYTES
                if predicate_type == SLSA_PREDICATE_TYPE
                else registry.MAX_SBOM_BYTES
            )
            if layer.size > limit:
                _fail("UPSTREAM_ATTESTATION_LAYER_BOUND_EXCEEDED")
            total_bytes += layer.size
            if total_bytes > MAX_TOTAL_STATEMENT_BYTES:
                _fail("UPSTREAM_ATTESTATION_BOUND_EXCEEDED")
            statement = _mapping(
                _decode_json(
                    reader.blob(layer, limit),
                    "UPSTREAM_STATEMENT_JSON_INVALID",
                ),
                "UPSTREAM_STATEMENT_INVALID",
            )
            if statement.get("_type") not in STATEMENT_TYPES:
                _fail("UPSTREAM_STATEMENT_TYPE_INVALID")
            if statement.get("predicateType") != predicate_type:
                _fail("UPSTREAM_STATEMENT_PREDICATE_TYPE_MISMATCH")
            _verify_subjects(
                statement,
                platform=platform,
                digest=platform_descriptor.digest,
                version=version,
            )
            if predicate_type == SLSA_PREDICATE_TYPE:
                bindings.append(
                    _verify_slsa_predicate(
                        statement,
                        platform=platform,
                        version=version,
                        source_commit=source_commit,
                    )
                )
            else:
                sbom_platforms.add(platform)

    if any(binding != bindings[0] for binding in bindings):
        _fail("UPSTREAM_PROVENANCE_PLATFORM_INCONSISTENT")
    return {
        "schema": RESULT_SCHEMA,
        "image_repository": IMAGE_REPOSITORY,
        "image_index_digest": index_digest,
        "version": version,
        "source_commit": source_commit,
        "source_repository": GITHUB_REPOSITORY,
        "workflow_ref": WORKFLOW_REF,
        "workflow_sha": bindings[0]["workflow_sha"],
        "builder_id": bindings[0]["builder_id"],
        "attested_labels": bindings[0]["attested_labels"],
        "source_material": bindings[0]["source_material"],
        "materials_declared_complete": bindings[0]["materials_declared_complete"],
        "image_materials": bindings[0]["image_materials"],
        "platform_digests": {
            platform: images[platform].digest
            for platform in sorted(images)
            if SELECTED_PLATFORM_PATTERN.fullmatch(platform)
        },
        "attested_platforms": sorted(images),
        "slsa_provenance": "present_per_platform",
        "sbom": "present" if sbom_platforms == set(images) else "absent",
        "statement_signature": "unsigned_buildkit_in_toto",
        "label_evidence": "informational_only",
    }


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    root.add_argument("--index-digest", required=True)
    root.add_argument("--version", required=True)
    root.add_argument("--source-commit", required=True)
    root.add_argument("--output", type=Path, required=True)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        result = verify(
            index_digest=args.index_digest,
            version=args.version,
            source_commit=args.source_commit,
            transport=registry.UrllibTransport(),
        )
        try:
            args.output.write_text(
                json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            raise VerificationError("OUTPUT_FILE_UNAVAILABLE") from exc
    except VerificationError as exc:
        print(f"upstream provenance verification failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
