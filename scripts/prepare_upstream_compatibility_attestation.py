"""Compatibility wrapper for the registry lifecycle operator CLI.

New automation should invoke ``manage_upstream_trust_registry.py`` directly.
This fixed-path wrapper preserves the prior exact-version interface without
duplicating inspection, evidence, signing, or sequence logic.
"""

from __future__ import annotations

import argparse

from manage_upstream_trust_registry import (
    DEFAULT_PATHS,
    RegistryOperationError,
    canonical_json,
    load_json,
    mutate_registry,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    try:
        if DEFAULT_PATHS.registry.exists():
            current = load_json(DEFAULT_PATHS.registry)
            sequence = current.get("sequence")
            if isinstance(sequence, bool) or not isinstance(sequence, int):
                raise RegistryOperationError("existing registry sequence is invalid")
            operation = "add"
        else:
            sequence = 0
            operation = "bootstrap"
        result = mutate_registry(
            operation=operation,
            upstream_version=args.version,
            expected_current_sequence=sequence,
        )
    except (RegistryOperationError, OSError, ValueError) as exc:
        message = str(exc)
        if len(message) > 240:
            message = "registry lifecycle operation failed"
        print(message)
        return 2
    print(canonical_json(result).decode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
