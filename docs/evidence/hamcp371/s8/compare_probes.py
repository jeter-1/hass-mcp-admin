"""HAMCP-371 S8 (N31): compare the review error probes with the bound 9.1.2 shapes.

Run from the repository root with ``python -I``. It reads only committed
files. For each probe file of ``probes-1`` (no seeded object) it computes the
error evidence with the review script's own ``error_evidence`` and compares it
with ``error_shapes`` of the committed 9.1.2 capture, then compares each
version's aggregate (``schema_fingerprint`` of the four, as at review time).
It then computes ``missing_automation`` from the S6 seeded runs, where Core
held one automation, to show that this probe's shape depends on Core content.
Exit 0 only if every probe-1 shape and both aggregates match.
"""

import json
import sys
from pathlib import Path

ROOT = Path.cwd()
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))
import review_upstream_read_release as review  # noqa: E402

S8 = ROOT / "docs/evidence/hamcp371/s8/probes-1"
S6 = ROOT / "docs/evidence/hamcp371/s6"
BOUND = ROOT / "docs/evidence/upstream-read-compatibility/ha-mcp-9.1.2.json"
VERSIONS = ("9.1.2", "9.2.0")
SEEDED = (
    ("run-a-1", "9.1.2"),
    ("run-a-1", "9.2.0"),
    ("run-b-9.1.2-1", "9.1.2"),
    ("run-b-9.2.0-2", "9.2.0"),
)


class _Result:
    """The stored JSON result, in the shape ``error_evidence`` reads."""

    def __init__(self, value):
        self._value = value

    def model_dump(self, **_):
        return self._value


def _evidence(path):
    exchange = json.loads(path.read_text(encoding="utf-8"))["exchange"]
    return review.error_evidence(_Result(exchange["response"]["result"]))


def main():
    bound = json.loads(BOUND.read_text(encoding="utf-8"))["error_shapes"]
    bound_aggregate = review.schema_fingerprint(bound)
    print(f"bound 9.1.2 aggregate {bound_aggregate}")
    ok = True
    for version in VERSIONS:
        shapes = {}
        for probe in sorted(bound):
            name = "e-" + probe.replace("_", "-") + ".json"
            shapes[probe] = _evidence(S8 / "out" / version / "review-probes" / name)
            match = shapes[probe] == bound[probe]
            ok &= match
            print(
                f"probes-1 {version} {probe} {shapes[probe]['structured_code']} "
                f"{shapes[probe]['shape_fingerprint']} "
                f"{'match' if match else 'DIFFERS'}"
            )
        aggregate = review.schema_fingerprint(shapes)
        ok &= aggregate == bound_aggregate
        print(
            f"probes-1 {version} aggregate {aggregate} "
            f"{'match' if aggregate == bound_aggregate else 'DIFFERS'}"
        )
    for run, version in SEEDED:
        path = S6 / run / "out" / version / "calls" / "p4-absent-automation.json"
        evidence = _evidence(path)
        same = evidence == bound["missing_automation"]
        print(
            f"seeded {run} {version} missing_automation "
            f"{evidence['structured_code']} {evidence['shape_fingerprint']} "
            f"{'equals bound' if same else 'differs from bound'}"
        )
    print("RESULT " + ("all probes-1 shapes match" if ok else "MISMATCH"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
