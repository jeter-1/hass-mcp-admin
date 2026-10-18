"""Build and verify the GitHub Release body for an exact image publication.

The publishing job runs this standard-library script from the workflow
authority commit instead of inline interpreter code (HAMCP-204).

``notes`` appends the immutable publication identity to the canonical release
notes.  ``verify`` checks the published GitHub Release against the exact tag,
title, URL, body and prerelease state and exits non-zero on any mismatch.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


def release_body(notes, version, source_sha, release_sha, build_time, digest, platforms):
    identity = f"""## Immutable publication identity

- Source main commit: `{source_sha}`
- Release commit: `{release_sha}`
- Version image: `ghcr.io/jeter-1/hass-mcp-engineering-beta:{version}`
- Commit image: `ghcr.io/jeter-1/hass-mcp-engineering-beta:sha-{release_sha}`
- OCI manifest digest: `{digest}`
- Build timestamp: `{build_time}`
- Architectures: `{platforms}`
- SLSA provenance and SBOM attestations: verified
"""
    return f"{notes.rstrip()}\n\n{identity}"


def release_failures(payload, expected_body, expected_tag, expected_title, expected_url,
                     expected_prerelease):
    failures = []
    if payload.get("tag_name") != expected_tag:
        failures.append("tag")
    if payload.get("name") != expected_title:
        failures.append("title")
    if payload.get("html_url") != expected_url:
        failures.append("url")
    if payload.get("body", "").rstrip() != expected_body.rstrip():
        failures.append("body")
    if payload.get("draft") is not False:
        failures.append("draft")
    if payload.get("prerelease") is not expected_prerelease:
        failures.append("prerelease")
    if not isinstance(payload.get("id"), int) or not payload.get("published_at"):
        failures.append("publication_identity")
    return failures


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="operation", required=True)
    notes = commands.add_parser("notes")
    for name in ("notes_path", "output_path", "version", "source_sha", "release_sha",
                 "build_time", "digest", "platforms"):
        notes.add_argument(name)
    verify = commands.add_parser("verify")
    for name in ("release_json", "release_body", "tag", "title", "url"):
        verify.add_argument(name)
    verify.add_argument("prerelease", choices=("true", "false"))
    args = parser.parse_args(argv)
    if args.operation == "notes":
        body = release_body(
            Path(args.notes_path).read_text(encoding="utf-8"), args.version,
            args.source_sha, args.release_sha, args.build_time, args.digest, args.platforms,
        )
        Path(args.output_path).write_text(body, encoding="utf-8", newline="")
        return 0
    payload = json.loads(Path(args.release_json).read_text(encoding="utf-8"))
    failures = release_failures(
        payload if isinstance(payload, dict) else {},
        Path(args.release_body).read_text(encoding="utf-8"),
        args.tag, args.title, args.url, args.prerelease == "true",
    )
    if failures:
        print("GitHub Release verification failed: " + ", ".join(failures), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
