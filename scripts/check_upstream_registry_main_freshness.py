"""Fail closed when origin/main or its committed registry sequence has moved."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.upstream_registry_signing_core import (  # noqa: E402
    SigningCoreError,
    check_origin_main_freshness,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--workflow-base-sha", required=True)
    parser.add_argument("--expected-current-sequence", type=int, required=True)
    parser.add_argument("--phase", choices=("signing", "publication"), required=True)
    parser.add_argument("--signed-base-sha")
    args = parser.parse_args()
    try:
        result = check_origin_main_freshness(
            repository=args.repository,
            workflow_base_sha=args.workflow_base_sha,
            expected_current_sequence=args.expected_current_sequence,
            phase=args.phase,
            signed_base_sha=args.signed_base_sha,
            environment=os.environ,
        )
    except (OSError, SigningCoreError, ValueError) as exc:
        print(getattr(exc, "category", "workflow_freshness_failed"), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
