"""HAMCP-371 S8 Q9: recheck the profiles-2 run from committed files only.

Run from the repository root with ``python -I``. Checks each committed copy
against the run's ``SHA256SUMS`` and the HAMCP-351 responses against their own
``SHA256SUMS``. For both 9.1.2 controls it recomputes the review script's
fingerprints (``catalog_fingerprint`` and the order-preserving
``schema_fingerprint({"tools": ...})``) and full result equality against the
committed HAMCP-351 ``tools/list`` response. It checks that the App and
standalone 9.2.0 filesystem-on captures have the same sha256 (the receipt's
values) and that the committed standalone capture is that file. Exit 0 only if
every check holds.
"""

import hashlib
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[4]
HAMCP351 = ROOT / "docs/evidence/hamcp351"
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))
from ha_mcp_engineering.upstream_tool_policy import (  # noqa: E402
    catalog_fingerprint, schema_fingerprint)

CONTROLS = {"control-standalone-9.1.2-fs-off": "standalone-fs-off-tools-response.json",
            "control-standalone-9.1.2-fs-on": "standalone-fs-on-tools-response.json"}
# The committed name of a run file, where it differs.
RENAMED = {"out/standalone-9.2.0-fs-on/capture.json": "out/standalone-9.2.0-fs-on/capture-1.json"}


def sums(path, strip=""):
    found = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        digest, name = line.split("  ", 1)
        found[name.removeprefix(strip)] = digest
    return found


def verified(path, digest):
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != digest:
        raise SystemExit(f"sha256 mismatch: {path.relative_to(ROOT)}")
    return raw


def fingerprints(result):
    tools = result["tools"]
    return {"tool_count": len(tools), "raw": catalog_fingerprint(tools),
            "strict": schema_fingerprint({"tools": tools})}


def main():
    run = sums(BASE / "SHA256SUMS")
    reference = sums(HAMCP351 / "SHA256SUMS", strip="./")
    committed = sorted(str(path.relative_to(BASE)) for path in BASE.rglob("*")
                       if path.is_file() and path.name not in (
                           "SHA256SUMS", "check_profiles2.py", "check-output.txt"))
    for name in committed:
        verified(BASE / name, run[RENAMED.get(name, name)])
    receipt = json.loads((BASE / "receipt.json").read_bytes())
    failures = []
    print(f"run {receipt['result']} harness {receipt['harness_commit']}"
          f" {receipt['started']} to {receipt['ended']}")
    for profile, response in CONTROLS.items():
        observation = json.loads((BASE / "out" / profile / "observation.json").read_bytes())
        pages = observation["tools_list_pages"]
        assert len(pages) == 1 and len(pages[0]["messages"]) == 1
        found = pages[0]["messages"][0]["result"]
        expected = json.loads(verified(HAMCP351 / "catalogs" / response,
                                       reference["catalogs/" + response]))["result"]
        same = fingerprints(found) == fingerprints(expected) and found == expected
        print(f"{profile}: found {fingerprints(found)}")
        print(f"{profile}: HAMCP-351 {response} {fingerprints(expected)}")
        print(f"{profile}: reproduces HAMCP-351 (fingerprints and result) {same}")
        if not same:
            failures.append(profile)
    app = receipt["profiles"]["app-9.2.0-fs-on"]
    standalone = receipt["profiles"]["standalone-9.2.0-fs-on"]
    capture = "sha256:" + hashlib.sha256(
        (BASE / "out/standalone-9.2.0-fs-on/capture.json").read_bytes()).hexdigest()
    for profile, entry in (("standalone-9.2.0-fs-on", standalone), ("app-9.2.0-fs-on", app)):
        print(f"{profile}: tools {entry['capture']['tool_count']} capture {entry['capture_sha256']}"
              f" two captures identical {entry['captures_byte_identical']}")
    same = app["capture_sha256"] == standalone["capture_sha256"] == capture
    print(f"App fs-on capture equals standalone fs-on capture (committed file) {same}")
    if not same:
        failures.append("app-9.2.0-fs-on")
    print("tool differences", json.dumps(receipt["tool_differences"], sort_keys=True))
    print("profiles-2 recheck", "FAIL " + ",".join(failures) if failures else "PASS")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
