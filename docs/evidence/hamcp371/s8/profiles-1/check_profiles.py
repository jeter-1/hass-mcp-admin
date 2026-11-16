"""HAMCP-371 S8 Q7-b: what run profiles-1 captured, from committed files only.

Run from the repository root with ``python -I``. Checks the committed receipt
and the control observation against ``SHA256SUMS``, then prints each profile's
tool count and capture sha256, and compares the 9.1.2 control's ``tools/list``
result with the two HAMCP-351 standalone captures (filesystem flag off and
on). Exit 0 always; this records a WITHHELD run, it is not a gate.
"""

import hashlib
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent
HAMCP351 = Path.cwd() / "docs/evidence/hamcp351"
CONTROL = "out/control-standalone-9.1.2-fs-on/observation.json"


def _verified(base, relative):
    sums = {}
    for line in (base / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        digest, name = line.split("  ", 1)
        sums[name.removeprefix("./")] = digest
    raw = (base / relative).read_bytes()
    if hashlib.sha256(raw).hexdigest() != sums[relative]:
        raise SystemExit(f"sha256 mismatch: {base / relative}")
    return raw


def main():
    receipt = json.loads(_verified(BASE, "receipt.json"))
    print(f"result {receipt['result']} failure {receipt['failure']}")
    for profile, value in sorted(receipt["profiles"].items()):
        print(f"{profile}: tools {value['capture']['tool_count']}"
              f" capture {value['capture_sha256']}"
              f" byte-identical {value['captures_byte_identical']}")
    for name, value in sorted(receipt["comparisons"].items()):
        print(f"comparison {name} {value}")
    observation = json.loads(_verified(BASE, CONTROL))
    [page] = observation["tools_list_pages"]
    [message] = page["messages"]
    control = message["result"]
    for label in ("off", "on"):
        relative = f"catalogs/standalone-fs-{label}-tools-response.json"
        reference = json.loads(_verified(HAMCP351, relative))["result"]
        print(f"9.1.2 control ({len(control['tools'])} tools) equals HAMCP-351 fs-{label}"
              f" ({len(reference['tools'])} tools): {control == reference}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
