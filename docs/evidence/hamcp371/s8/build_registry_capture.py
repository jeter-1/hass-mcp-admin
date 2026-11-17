"""HAMCP-371 S8: build the 9.2.0 registry capture from committed evidence.

Run from the repository root with ``python -I``. Writes
``docs/evidence/upstream-read-compatibility/ha-mcp-9.2.0.json`` (``--check``
compares instead of writing).

The capture has two sources, each checked against its own ``SHA256SUMS``:

- Catalog: the S4 ``capture`` of the exact 9.2.0 image against the
  repository's synthetic fixture (``s4/ha-mcp-9.2.0/capture.json``; two
  byte-identical captures). Everything except ``error_shapes`` is taken as it
  is, and the catalog fingerprint and tool count are recomputed and compared.
- Error shapes: the four review probes of S8 ``probes-1`` on 9.2.0 against a
  disposable Core 2026.11.0 with no seeded object (N31 ruling (a)), each
  computed with the review script's own ``error_evidence``. The S4 lane's
  fixture refused the WebSocket login, so its shapes are not used (S4 README,
  "Finding").

No value is written by hand. This is the shape ``import-runtime-capture``
produces (catalog from one retained capture, error shapes from another).
"""

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path.cwd()
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))
import review_upstream_read_release as review  # noqa: E402

S4 = ROOT / "docs/evidence/hamcp371/s4"
PROBES = ROOT / "docs/evidence/hamcp371/s8/probes-1"
OUTPUT = ROOT / "docs/evidence/upstream-read-compatibility/ha-mcp-9.2.0.json"
VERSION = "9.2.0"
CATALOG = "ha-mcp-9.2.0/capture.json"


class _Result:
    def __init__(self, value):
        self._value = value

    def model_dump(self, **_):
        return self._value


def _verified(base, relative):
    sums = {}
    for line in (base / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        digest, name = line.split("  ", 1)
        sums[name] = digest
    raw = (base / relative).read_bytes()
    if hashlib.sha256(raw).hexdigest() != sums[relative]:
        raise SystemExit(f"sha256 mismatch: {base / relative}")
    return raw


def build():
    catalog = json.loads(_verified(S4, CATALOG))
    if catalog["server_version"] != VERSION:
        raise SystemExit("S4 capture is not 9.2.0")
    if catalog["tool_count"] != len(catalog["tools"]):
        raise SystemExit("S4 tool count does not match its tools")
    if catalog["catalog_fingerprint"] != review.catalog_fingerprint(catalog["tools"]):
        raise SystemExit("S4 catalog fingerprint does not match its tools")
    shapes = {}
    for probe in review.ERROR_PROBES:
        relative = f"out/{VERSION}/review-probes/e-{probe.replace('_', '-')}.json"
        exchange = json.loads(_verified(PROBES, relative))["exchange"]
        request = exchange["request"]["json"]["params"]
        if (request["name"], request["arguments"]) != tuple(review.ERROR_PROBES[probe]):
            raise SystemExit(f"probe call differs from ERROR_PROBES: {probe}")
        shapes[probe] = review.error_evidence(_Result(exchange["response"]["result"]))
    value = dict(catalog)
    value["error_shapes"] = shapes
    return value


def main():
    value = build()
    encoded = review.canonical_json(value) + b"\n"
    if sys.argv[1:] == ["--check"]:
        same = OUTPUT.read_bytes() == encoded
        print("capture up to date" if same else "capture differs")
        return 0 if same else 1
    OUTPUT.write_bytes(encoded)
    print(f"wrote {OUTPUT.relative_to(ROOT)} sha256 {hashlib.sha256(encoded).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
