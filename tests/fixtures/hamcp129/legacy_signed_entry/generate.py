"""Regenerate the base-generated signed 8.4.4 entry from exact shipped code.

Usage: python generate.py <checkout> <output-dir>

<checkout> must be a clean checkout of BASE_COMMIT, the last main commit
before HAMCP-129 changed the compiled 8.4.3 policy. The entry is produced by
that checkout's own ``_signed_entry_for`` against that checkout's own compiled
8.4.3 release, exactly as the PR #247 delta review's old-binding probe did.
It is an unsigned synthetic entry payload: tests sign it with an ephemeral
synthetic envelope. Nothing is hand-edited after generation.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys

BASE_COMMIT = "5ea311895e1fcb52758235298ac21c670dc8c756"
ENTRY = "base_5ea3118_signed_entry_8_4_4.json"


def main() -> None:
    checkout, output = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
    head = subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "-C", str(checkout), "status", "--porcelain"],
        check=True, capture_output=True, text=True,
    ).stdout
    if head != BASE_COMMIT or dirty:
        raise SystemExit(f"checkout must be clean {BASE_COMMIT}, got {head}")
    sys.path[:0] = [str(checkout / "hass_mcp_engineering_beta"), str(checkout / "tests")]
    from ha_mcp_engineering.upstream_tool_policy import (
        load_reviewed_upstream_release_registry,
    )
    from test_ha_mcp_production_readmission import _signed_entry_for

    module = sys.modules["ha_mcp_engineering.upstream_tool_policy"]
    if not Path(module.__file__).resolve().is_relative_to(checkout):
        raise SystemExit("imported code is not from the base checkout")
    release = load_reviewed_upstream_release_registry().by_version["8.4.3"]
    body = json.dumps(_signed_entry_for(release, version="8.4.4"), indent=2)
    (output / ENTRY).write_text(body, encoding="utf-8")
    generator = Path(__file__).resolve()
    provenance = {
        "task": "HAMCP-129",
        "source_commit": BASE_COMMIT,
        "generator": {
            "path": "tests/fixtures/hamcp129/legacy_signed_entry/generate.py",
            "sha256": hashlib.sha256(generator.read_bytes()).hexdigest(),
        },
        "process": (
            "Clean detached checkout of source_commit; that checkout's "
            "_signed_entry_for(load_reviewed_upstream_release_registry()"
            ".by_version['8.4.3'], version='8.4.4') written with "
            "json.dumps(indent=2). Not hand-edited."
        ),
        "entry": {
            "path": f"tests/fixtures/hamcp129/legacy_signed_entry/{ENTRY}",
            "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            "policy_resource": release.policy_resource,
            "policy_sha256": release.policy_sha256,
        },
        "statement": (
            "Synthetic entry derived only from committed compiled release "
            "data; no production registry, signing key, live Home Assistant "
            "or deployed endpoint was used."
        ),
    }
    (output / "provenance.json").write_text(
        json.dumps(provenance, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
