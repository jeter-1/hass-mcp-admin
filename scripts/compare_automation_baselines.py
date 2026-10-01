#!/usr/bin/env python3
"""Compare two explicit local automation-baseline JSON files offline."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
BETA = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(BETA))

from ha_mcp_engineering.audit_baseline import (  # noqa: E402
    BaselineValidationError,
    compare_baselines,
    load_baseline,
    render_bounded_report,
)
from ha_mcp_engineering.audit_baseline.comparison import (  # noqa: E402
    MAX_OUTPUT_BYTES,
    MAX_OUTPUT_DETAILS,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate and compare two retained Home Assistant automation baselines. "
            "The command is local-only and performs no provider or network access."
        )
    )
    parser.add_argument("earlier", help="Earlier local baseline JSON file")
    parser.add_argument("later", help="Later local baseline JSON file")
    parser.add_argument("--output", help="Write compact JSON report to this local file")
    parser.add_argument(
        "--max-output-bytes",
        type=int,
        default=MAX_OUTPUT_BYTES,
        help=f"Bound report bytes (1024..{MAX_OUTPUT_BYTES})",
    )
    parser.add_argument(
        "--max-details",
        type=int,
        default=MAX_OUTPUT_DETAILS,
        help=f"Bound retained record details (0..{MAX_OUTPUT_DETAILS})",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        earlier = load_baseline(args.earlier)
        later = load_baseline(args.later)
        report = compare_baselines(earlier, later)
        rendered = render_bounded_report(
            report,
            max_bytes=args.max_output_bytes,
            max_details=args.max_details,
        )
        if args.output:
            Path(args.output).write_bytes(rendered + b"\n")
        else:
            sys.stdout.buffer.write(rendered + b"\n")
        return 0
    except BaselineValidationError as exc:
        # Fixed codes only: never echo arbitrary input, provider payloads or paths.
        sys.stderr.write(f"error:{exc.code}\n")
        return 2
    except OSError:
        sys.stderr.write("error:output_unavailable\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
