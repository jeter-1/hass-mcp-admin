"""Regenerate the beta.43-written baseline store from exact shipped code.

Usage: python generate.py <checkout> <output-dir>

<checkout> must be a clean checkout of BASE_COMMIT (2.4.1-beta.43, the PR #322
base). Its own capture service, provider and store write two captures into
``<output-dir>/store`` with its own synthetic test fixture and fake client:

- ``anchorless``: two configuration reads time out and the final fence's
  principal read times out, so beta.43 stores an unestablished capture with no
  first-fence installation anchor (the HAMCP-235 review 1 R1-M1 case);
- ``established``: one configuration read times out and the final fence
  completes, so beta.43 stores an established installation id.

Nothing is hand-edited after generation; ``provenance.json`` records each file's
sha256. The store's empty transaction lock file is not kept.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys

BASE_COMMIT = "0e0624297d32608bd4dfda36d90879b3462334fa"
CORE = "2026.10.3"


def main() -> None:
    checkout, output = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
    head = subprocess.run(["git", "-C", str(checkout), "rev-parse", "HEAD"],
                          check=True, capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "-C", str(checkout), "status", "--porcelain"],
                           check=True, capture_output=True, text=True).stdout
    if head != BASE_COMMIT or dirty:
        raise SystemExit(f"checkout must be clean {BASE_COMMIT}, got {head}")
    sys.path[:0] = [str(checkout / "hass_mcp_engineering_beta"), str(checkout / "tests")]
    from test_automation_baseline_capture import fixture
    from test_hamcp228_baseline_store import OWNER, Harness, capture_id
    from ha_mcp_engineering.audit_baseline import capture_contracts as c

    store = output / "store"
    if store.exists():
        shutil.rmtree(store)
    captures = {}

    async def write():
        harness = Harness()
        harness.setUp()
        harness.governance = store
        harness.core.current_observation.version = CORE
        try:
            for label, timeouts, fail_final in (("anchorless", ("1", "2"), True), ("established", ("1",), False)):
                data = fixture(4)
                for key in timeouts:
                    data["configs"][key] = c.CaptureError("timeout")
                service, client = harness.service(data)
                principal = []

                async def hook(kind, principal=principal, fail_final=fail_final):
                    if kind == "principal":
                        principal.append(kind)
                        if fail_final and len(principal) == 2:
                            raise c.CaptureError("timeout")
                client.hook = hook
                page = await harness.capture(service)
                captures[label] = {"capture_id": capture_id(page),
                                   "installation": page["baseline_header"]["installation"],
                                   "final_fence": page["diagnostics"]["final_fence"],
                                   "first_fence_anchor_recorded": "first_fence_installation_id" in page["diagnostics"],
                                   "record_outcomes": page["record_outcomes"]}
            return harness.now, OWNER
        finally:
            harness.tearDown()
            harness.doCleanups()

    now, caller = asyncio.run(write())
    (store / "audit-baselines-v1" / ".transaction.lock").unlink(missing_ok=True)
    files = {path.relative_to(output).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
             for path in sorted(store.rglob("*")) if path.is_file()}
    provenance = {
        "writer_commit": BASE_COMMIT, "writer_version": "2.4.1-beta.43",
        "generator": "tests/fixtures/hamcp235_beta43_resume/generate.py",
        "python": platform.python_version(), "core_version": CORE, "caller": caller,
        "now_ms": int(now * 1000), "captures": captures, "files": files,
    }
    (output / "provenance.json").write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n")
    print(json.dumps(captures, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
