#!/usr/bin/env python3
"""Create GHCR release tags without overwriting an existing tag.

The image is first pushed only by its immutable digest. This helper reads that
exact manifest, proves that the registry enforces ``If-None-Match: *`` on the
existing digest reference, and then creates each release tag with the same
precondition. It never creates a temporary tag or issues an unconditional
manifest write.
"""

from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass
import hashlib
from http.client import HTTPException
import json
import os
import re
import sys
from typing import Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener


MAX_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_TOKEN_RESPONSE_BYTES = 16 * 1024
MAX_TOKEN_LENGTH = 8192
EXPECTED_REGISTRY = "ghcr.io"
TAG_PATTERN = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]{0,127}\Z")
DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")
_REPOSITORY_SEGMENT = r"[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*"
REPOSITORY_PATH_PATTERN = re.compile(
    rf"{_REPOSITORY_SEGMENT}(?:/{_REPOSITORY_SEGMENT})*\Z"
)
MANIFEST_MEDIA_TYPES = frozenset(
    {
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
    }
)
MANIFEST_ACCEPT = ", ".join(sorted(MANIFEST_MEDIA_TYPES))


class PublicationError(RuntimeError):
    """A bounded, operator-safe publication refusal."""

    def __init__(
        self,
        code: str,
        *,
        disposition: str = "none",
        created: tuple[str, ...] = (),
    ) -> None:
        if disposition not in {"none", "partial", "unknown"}:
            raise ValueError("invalid publication disposition")
        super().__init__(code)
        self.disposition = disposition
        self.created = created


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
        body: bytes | None,
        response_limit: int,
    ) -> HttpResponse: ...


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


class UrllibTransport:
    """HTTPS transport that never forwards authorization through redirects."""

    def __init__(self) -> None:
        self._opener = build_opener(_NoRedirectHandler())

    def request(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        response_limit: int,
    ) -> HttpResponse:
        request = Request(url, data=body, headers=dict(headers), method=method)
        try:
            response = self._opener.open(request, timeout=30)
        except HTTPError as error:
            response = error
        except (OSError, TimeoutError, URLError, HTTPException) as error:
            raise PublicationError("REGISTRY_TRANSPORT_FAILED") from error
        try:
            with response:
                content_length = response.headers.get("Content-Length")
                if content_length is not None:
                    try:
                        declared_length = int(content_length)
                    except ValueError as error:
                        raise PublicationError(
                            "REGISTRY_RESPONSE_LENGTH_INVALID"
                        ) from error
                    if declared_length < 0 or declared_length > response_limit:
                        raise PublicationError("REGISTRY_RESPONSE_BOUND_EXCEEDED")
                body_bytes = response.read(response_limit + 1)
                if len(body_bytes) > response_limit:
                    raise PublicationError("REGISTRY_RESPONSE_BOUND_EXCEEDED")
                return HttpResponse(
                    status=int(response.status),
                    headers={
                        key.lower(): value for key, value in response.headers.items()
                    },
                    body=body_bytes,
                )
        except PublicationError:
            raise
        except (OSError, TimeoutError, URLError, HTTPException) as error:
            raise PublicationError("REGISTRY_TRANSPORT_FAILED") from error


@dataclass(frozen=True)
class RegistryReference:
    host: str
    path: str

    @classmethod
    def parse(cls, value: str) -> "RegistryReference":
        if value.count("/") < 1:
            raise PublicationError("REGISTRY_REPOSITORY_INVALID")
        host, path = value.split("/", 1)
        if host != EXPECTED_REGISTRY or not REPOSITORY_PATH_PATTERN.fullmatch(path):
            raise PublicationError("REGISTRY_REPOSITORY_INVALID")
        return cls(host=host, path=path)

    def manifest_url(self, reference: str) -> str:
        return (
            f"https://{self.host}/v2/{self.path}/manifests/"
            f"{quote(reference, safe=':')}"
        )


class CreateOnlyPublisher:
    def __init__(
        self,
        *,
        repository: RegistryReference,
        username: str,
        password: str,
        transport: Transport,
    ) -> None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_\-\[\]]{0,255}", username):
            raise PublicationError("REGISTRY_USERNAME_INVALID")
        if not password or len(password) > MAX_TOKEN_LENGTH:
            raise PublicationError("REGISTRY_CREDENTIAL_UNAVAILABLE")
        self._repository = repository
        self._username = username
        self._password = password
        self._transport = transport

    def _bearer_token(self) -> str:
        credentials = base64.b64encode(
            f"{self._username}:{self._password}".encode("utf-8")
        ).decode("ascii")
        query = urlencode(
            {
                "service": self._repository.host,
                "scope": f"repository:{self._repository.path}:pull,push",
            }
        )
        response = self._transport.request(
            "GET",
            f"https://{self._repository.host}/token?{query}",
            {"Authorization": f"Basic {credentials}", "Accept": "application/json"},
            None,
            MAX_TOKEN_RESPONSE_BYTES,
        )
        if response.status != 200:
            raise PublicationError("REGISTRY_TOKEN_REQUEST_FAILED")
        try:
            payload = json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise PublicationError("REGISTRY_TOKEN_RESPONSE_INVALID") from error
        token = payload.get("token") if isinstance(payload, dict) else None
        if (
            not isinstance(token, str)
            or not token
            or len(token) > MAX_TOKEN_LENGTH
            or any(ord(char) < 33 or ord(char) > 126 for char in token)
        ):
            raise PublicationError("REGISTRY_TOKEN_RESPONSE_INVALID")
        return token

    def _manifest_request(
        self,
        *,
        token: str,
        method: str,
        reference: str,
        body: bytes | None = None,
        media_type: str | None = None,
        create_only: bool = False,
    ) -> HttpResponse:
        validate_manifest_reference(reference)
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": MANIFEST_ACCEPT,
        }
        if body is not None:
            if media_type not in MANIFEST_MEDIA_TYPES:
                raise PublicationError("SOURCE_MANIFEST_MEDIA_TYPE_INVALID")
            headers["Content-Type"] = media_type
        if create_only:
            headers["If-None-Match"] = "*"
        return self._transport.request(
            method,
            self._repository.manifest_url(reference),
            headers,
            body,
            MAX_MANIFEST_BYTES,
        )

    def publish(
        self,
        *,
        source_digest: str,
        target_tags: tuple[str, ...],
    ) -> tuple[str, ...]:
        validate_digest(source_digest)
        if not target_tags or len(target_tags) > 4:
            raise PublicationError("TARGET_TAG_COUNT_INVALID")
        if len(set(target_tags)) != len(target_tags):
            raise PublicationError("TARGET_TAG_SET_INVALID")
        for target_tag in target_tags:
            validate_tag(target_tag)

        token = self._bearer_token()
        source = self._manifest_request(
            token=token,
            method="GET",
            reference=source_digest,
        )
        if source.status != 200:
            raise PublicationError("SOURCE_MANIFEST_UNAVAILABLE")
        media_type = source.headers.get("content-type", "").split(";", 1)[0].strip()
        if media_type not in MANIFEST_MEDIA_TYPES:
            raise PublicationError("SOURCE_MANIFEST_MEDIA_TYPE_INVALID")
        actual_digest = f"sha256:{hashlib.sha256(source.body).hexdigest()}"
        header_digest = source.headers.get("docker-content-digest")
        if actual_digest != source_digest or header_digest != source_digest:
            raise PublicationError("SOURCE_MANIFEST_DIGEST_MISMATCH")

        capability = self._manifest_request(
            token=token,
            method="PUT",
            reference=source_digest,
            body=source.body,
            media_type=media_type,
            create_only=True,
        )
        if capability.status == 201:
            raise PublicationError("REGISTRY_CREATE_ONLY_UNSUPPORTED")
        if capability.status != 412:
            raise PublicationError("REGISTRY_CREATE_ONLY_CAPABILITY_AMBIGUOUS")

        created: list[str] = []
        for target_tag in target_tags:
            try:
                response = self._manifest_request(
                    token=token,
                    method="PUT",
                    reference=target_tag,
                    body=source.body,
                    media_type=media_type,
                    create_only=True,
                )
            except PublicationError as error:
                raise PublicationError(
                    f"TARGET_TAG_CREATE_UNKNOWN:{target_tag}:created={len(created)}",
                    disposition="unknown",
                    created=tuple(created),
                ) from error
            if response.status == 412:
                raise PublicationError(
                    f"TARGET_TAG_ALREADY_EXISTS:{target_tag}:created={len(created)}",
                    disposition="partial" if created else "none",
                    created=tuple(created),
                )
            if response.status != 201:
                raise PublicationError(
                    f"TARGET_TAG_CREATE_AMBIGUOUS:{target_tag}:created={len(created)}",
                    disposition="unknown",
                    created=tuple(created),
                )
            response_digest = response.headers.get("docker-content-digest")
            if response_digest != source_digest:
                raise PublicationError(
                    f"TARGET_TAG_DIGEST_UNCONFIRMED:{target_tag}:created={len(created) + 1}",
                    disposition="unknown",
                    created=tuple((*created, target_tag)),
                )
            created.append(target_tag)

        for target_tag in target_tags:
            try:
                response = self._manifest_request(
                    token=token,
                    method="GET",
                    reference=target_tag,
                )
            except PublicationError as error:
                raise PublicationError(
                    f"TARGET_TAG_POSTCONDITION_UNKNOWN:{target_tag}",
                    disposition="unknown",
                    created=tuple(created),
                ) from error
            actual = f"sha256:{hashlib.sha256(response.body).hexdigest()}"
            if (
                response.status != 200
                or actual != source_digest
                or response.headers.get("docker-content-digest") != source_digest
            ):
                raise PublicationError(
                    f"TARGET_TAG_POSTCONDITION_FAILED:{target_tag}",
                    disposition="unknown",
                    created=tuple(created),
                )
        return tuple(created)


def validate_tag(value: str) -> None:
    if not TAG_PATTERN.fullmatch(value):
        raise PublicationError("REGISTRY_TAG_INVALID")


def validate_digest(value: str) -> None:
    if not DIGEST_PATTERN.fullmatch(value):
        raise PublicationError("REGISTRY_DIGEST_INVALID")


def validate_manifest_reference(value: str) -> None:
    if TAG_PATTERN.fullmatch(value) or DIGEST_PATTERN.fullmatch(value):
        return
    raise PublicationError("REGISTRY_REFERENCE_INVALID")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create GHCR tags with an enforced no-overwrite precondition."
    )
    parser.add_argument("--repository", required=True)
    parser.add_argument("--source-digest", required=True)
    parser.add_argument("--target-tag", action="append", required=True)
    return parser.parse_args(argv)


def write_outputs(*, created: tuple[str, ...], disposition: str) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    with open(output_path, "a", encoding="utf-8", newline="") as output:
        output.write(f"publication_disposition={disposition}\n")
        output.write(f"known_created_tag_count={len(created)}\n")
        output.write(
            f"release_tags_created={'true' if disposition == 'complete' else 'false'}\n"
        )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(argv if argv is not None else sys.argv[1:]))
    try:
        repository = RegistryReference.parse(args.repository)
        publisher = CreateOnlyPublisher(
            repository=repository,
            username=os.environ.get("GHCR_USERNAME", ""),
            password=os.environ.get("GHCR_TOKEN", ""),
            transport=UrllibTransport(),
        )
        created = publisher.publish(
            source_digest=args.source_digest,
            target_tags=tuple(args.target_tag),
        )
    except PublicationError as error:
        write_outputs(created=error.created, disposition=error.disposition)
        print(f"::error::{error}", file=sys.stderr)
        return 1
    write_outputs(created=created, disposition="complete")
    print(
        f"Created {len(created)} release tag(s) with enforced create-only semantics."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
