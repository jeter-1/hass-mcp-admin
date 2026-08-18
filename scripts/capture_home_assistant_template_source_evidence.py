#!/usr/bin/env python3
"""Capture immutable Home Assistant template source evidence.

This is an explicitly network-using capture tool.  It is never executed by
tests, by the registry generator, or by CI.  It reads the official
``home-assistant/core`` tags through the GitHub Git Data API, recomputes the
git blob SHA-1 from the returned blob bytes, and writes the deterministic
evidence file that
``scripts/generate_template_semantic_registry.py`` verifies against offline.

The generator must never verify a declared path/blob pair against another
copy of the same declaration.  This file is the independent witness: each
record is derived from bytes served by the official repository, and each blob
SHA-1 is recomputed locally from those bytes before it is written.

Usage:
    gh auth status
    python scripts/capture_home_assistant_template_source_evidence.py \
        --output docs/evidence/home-assistant-template-source-blobs.json
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any


REPOSITORY = "home-assistant/core"
EVIDENCE_MODEL = "home-assistant-template-source-evidence-v1"


def _api(endpoint: str) -> Any:
    completed = subprocess.run(
        ["gh", "api", f"repos/{REPOSITORY}/{endpoint}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise SystemExit(
            f"github read failed for {endpoint}: "
            f"{completed.stderr.strip()[:300]}"
        )
    return json.loads(completed.stdout)


def _resolve_tag(tag: str) -> str:
    reference = _api(f"git/ref/tags/{tag}")
    obj = reference["object"]
    if obj["type"] == "tag":
        return _api(f"git/tags/{obj['sha']}")["object"]["sha"]
    return obj["sha"]


def _tree_entries(sha: str, cache: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if sha not in cache:
        cache[sha] = {
            entry["path"]: entry
            for entry in _api(f"git/trees/{sha}")["tree"]
        }
    return cache[sha]


def _lookup(root: str, path: str, cache: dict[str, dict[str, Any]]):
    current = root
    entry = None
    parts = path.split("/")
    for index, part in enumerate(parts):
        entry = _tree_entries(current, cache).get(part)
        if entry is None:
            return None
        if index < len(parts) - 1:
            if entry["type"] != "tree":
                return None
            current = entry["sha"]
    return entry if entry is not None and entry["type"] == "blob" else None


def _blob_record(sha: str, blobs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if sha not in blobs:
        payload = _api(f"git/blobs/{sha}")
        raw = base64.b64decode(payload["content"])
        recomputed = hashlib.sha1(
            b"blob %d\0" % len(raw) + raw
        ).hexdigest()
        if recomputed != sha:
            raise SystemExit(f"blob {sha} failed local sha-1 verification")
        blobs[sha] = {
            "blob": sha,
            "size": len(raw),
            "content_sha256": hashlib.sha256(raw).hexdigest(),
        }
    return blobs[sha]


def capture(tags: list[str], paths: list[str]) -> dict[str, Any]:
    trees: dict[str, dict[str, Any]] = {}
    blobs: dict[str, dict[str, Any]] = {}
    versions = []
    for tag in tags:
        commit = _resolve_tag(tag)
        root = _api(f"git/commits/{commit}")["tree"]["sha"]
        present: dict[str, Any] = {}
        absent: list[str] = []
        for path in paths:
            entry = _lookup(root, path, trees)
            if entry is None:
                absent.append(path)
                continue
            present[path] = _blob_record(entry["sha"], blobs)
        versions.append(
            {
                "tag": tag,
                "commit": commit,
                "paths": dict(sorted(present.items())),
                "absent_paths": sorted(absent),
            }
        )
    return {
        "model": EVIDENCE_MODEL,
        "capture": {
            "repository": REPOSITORY,
            "method": "github_git_data_api_tree_and_blob_read",
            "tool": (
                "scripts/capture_home_assistant_template_source_evidence.py"
            ),
            "verification": (
                "git_blob_sha1_recomputed_locally_from_returned_blob_bytes"
            ),
        },
        "versions": versions,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tag", action="append", required=True)
    parser.add_argument("--path", action="append", required=True)
    args = parser.parse_args()
    value = capture(list(args.tag), list(args.path))
    args.output.write_bytes(
        (
            json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True)
            + "\n"
        ).encode("utf-8")
    )
    print(hashlib.sha256(args.output.read_bytes()).hexdigest())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
