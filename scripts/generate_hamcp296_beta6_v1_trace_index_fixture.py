#!/usr/bin/env python3
"""Generate an exact shipped v2.4.2-beta.6 trace snapshot store for HAMCP-296.

The store is written by the exact beta.6 ``TraceSnapshotService`` through that
revision's own HAMCP-225 test harness: one manual capture and the shipped
maximum of ten watches.  The persisted ``trace-snapshots-v1`` bytes are copied
verbatim; they are never reconstructed or edited.

The generator also records three verdicts of the exact shipped store code,
which define the downgrade boundary for the HAMCP-296 watch lifecycle:

* the shipped ``validate_index`` accepts the ten-watch index it wrote;
* it rejects the same index with an eleventh watch (``store_invalid``);
* the shipped ``load_index`` and ``list`` ignore an unknown file
  (``watch-lifecycle.json``) in the namespace root.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any


SOURCE_TAG = "v2.4.2-beta.6"
SOURCE_COMMIT = "af5ec8375401276f910804843c2ddb9180533c77"
FIXTURE_DIR = "hamcp296_beta6_v1_trace_index"
PROVENANCE_NAME = "hamcp296_beta6_v1_trace_index_provenance.json"
NAMESPACE = "trace-snapshots-v1"
WATCHES = 10


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source_commit(source_root: Path) -> str:
    return subprocess.check_output(
        ["git", "-C", str(source_root), "rev-parse", "HEAD"],
        text=True,
    ).strip()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--generated-at", required=True)
    return parser.parse_args()


async def _write_store(harness: Any, destination: Path) -> dict[str, Any]:
    case = harness.ServiceCase()
    await case.asyncSetUp()
    try:
        case.core.add("r1")
        case.core.add("r2", "error", minute=1, error="synthetic failure")
        value, partial = await case.service.capture(automation_id="a1", note="beta6 fixture")
        if partial or not any(run["outcome"] == "captured" for run in value["runs"]):
            raise SystemExit("historical writer did not store the manual capture")
        for i in range(WATCHES):
            mode = "failures" if i % 2 == 0 else "executed"
            await case.service.watch(
                action="add", automation_id=f"fixture_watch_{i}", mode=mode,
                duration_hours=24 + i,
            )
        if any(sent["type"] not in ("trace/list", "trace/get") for sent in case.core.sent):
            raise SystemExit("historical writer sent a non-trace command")
        index = case.index()
        if len(index["watches"]) != WATCHES:
            raise SystemExit("historical writer did not hold ten watches")
        shutil.copytree(case.root / NAMESPACE, destination / NAMESPACE)
        return {
            "watch_count": len(index["watches"]),
            "snapshot_count": len(index["snapshots"]),
            "watch_ids": [w["watch_id"] for w in index["watches"]],
            "automation_ids": [w["automation_id"] for w in index["watches"]],
        }
    finally:
        await case.asyncTearDown()
        case.doCleanups()


def _verdicts(store_module: Any, service_module: Any, written: Path) -> dict[str, Any]:
    raw = json.loads((written / NAMESPACE / "index.json").read_text(encoding="utf-8"))
    store_module.validate_index(copy.deepcopy(raw))
    accepted_ten = True
    eleven = copy.deepcopy(raw)
    extra = copy.deepcopy(eleven["watches"][-1])
    extra["watch_id"] = "e" * 32
    extra["automation_id"] = "fixture_watch_extra"
    # The extra row alone is valid: swapping it for the tenth row still passes,
    # so the eleven-watch rejection below is the shipped count bound.
    swapped = copy.deepcopy(raw)
    swapped["watches"][-1] = copy.deepcopy(extra)
    store_module.validate_index(swapped)
    eleven["watches"].append(extra)
    try:
        store_module.validate_index(eleven)
    except store_module.StoreError as error:
        rejected_eleven = error.reason
    else:
        raise SystemExit("shipped validate_index accepted an eleventh watch")

    probe = written.parent / "unknown-file-probe"
    shutil.copytree(written, probe)
    try:
        (probe / NAMESPACE / "watch-lifecycle.json").write_text("{\"model\": \"unknown\"}")
        loaded = store_module.TraceSnapshotStore(probe).load_index()
        ignored_unknown = len(loaded["watches"]) == WATCHES
    finally:
        shutil.rmtree(probe)
    if not ignored_unknown:
        raise SystemExit("shipped load_index did not ignore an unknown namespace file")
    return {
        "shipped_validate_index_accepts_ten_watches": accepted_ten,
        "shipped_validate_index_rejects_eleven_watches": rejected_eleven,
        "shipped_load_index_ignores_unknown_namespace_file": ignored_unknown,
        "shipped_max_watches": service_module.c.MAX_WATCHES,
    }


def main() -> int:
    args = _parse_args()
    source_root = args.source_root.resolve()
    output_dir = args.output_dir.resolve()
    if _source_commit(source_root) != SOURCE_COMMIT:
        raise SystemExit("source root is not exact shipped beta.6")
    target = output_dir / FIXTURE_DIR
    if target.exists():
        raise SystemExit(f"refusing to overwrite {target}")

    os.chdir(source_root)
    sys.path[:0] = [str(source_root), str(source_root / "hass_mcp_engineering_beta")]
    import tests.test_hamcp225_trace_snapshots as harness  # noqa: PLC0415
    from ha_mcp_engineering.trace_snapshots import service as service_module  # noqa: PLC0415
    from ha_mcp_engineering.trace_snapshots import store as store_module  # noqa: PLC0415

    for name in (
        "tests.test_hamcp225_trace_snapshots",
        "ha_mcp_engineering.trace_snapshots.contracts",
        "ha_mcp_engineering.trace_snapshots.service",
        "ha_mcp_engineering.trace_snapshots.store",
    ):
        imported = Path(sys.modules[name].__file__).resolve()
        if not imported.is_relative_to(source_root):
            raise SystemExit(f"{name} was not imported from the source root")

    target.mkdir(parents=True)
    record = asyncio.run(_write_store(harness, target))
    verdicts = _verdicts(store_module, service_module, target)

    generator = Path(__file__).resolve()
    package = source_root / "hass_mcp_engineering_beta/ha_mcp_engineering/trace_snapshots"
    provenance = {
        "schema_version": 1,
        "compatibility_model": "hamcp296-beta6-v1-trace-index-v1",
        "generated_at": args.generated_at,
        "generator": "scripts/generate_hamcp296_beta6_v1_trace_index_fixture.py",
        "generator_command": (
            "python scripts/generate_hamcp296_beta6_v1_trace_index_fixture.py "
            "--source-root <exact-beta6-clone> --output-dir tests/fixtures "
            f"--generated-at {args.generated_at}"
        ),
        "generator_sha256": _sha256(generator),
        "source": {
            "commit": SOURCE_COMMIT,
            "tag": SOURCE_TAG,
            "writer": (
                "tests.test_hamcp225_trace_snapshots.ServiceCase: capture(a1) then "
                "ten watch(action='add') calls on the exact source revision"
            ),
            "contracts_source_sha256": _sha256(package / "contracts.py"),
            "service_source_sha256": _sha256(package / "service.py"),
            "store_source_sha256": _sha256(package / "store.py"),
        },
        "record": {
            **record,
            "files": {
                str(path.relative_to(target)): _sha256(path)
                for path in sorted(target.rglob("*"))
                if path.is_file()
            },
        },
        "verdicts": verdicts,
        "boundary": (
            "Bytes are copied verbatim from the exact beta.6 writer and must not "
            "be edited.  Current code must read this v1 store unchanged; the "
            "shipped verdicts define the HAMCP-296 downgrade boundary: index.json "
            "must never hold more than ten watches, and lifecycle state must live "
            "in a separate namespace file the shipped reader ignores."
        ),
    }
    (output_dir / PROVENANCE_NAME).write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"record": record, "verdicts": verdicts}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
