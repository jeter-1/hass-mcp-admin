"""Generate the HAMCP-511 parity goldens with the exact delegated history-slices service.

Run with ``python -I`` (so this script's directory is not on the path):

    git clone --shared <repo> <dir> && git -C <dir> checkout --detach f2275ab1...
    python -I tests/generate_hamcp511_parity_goldens.py --source <dir> --output <file>

The source checkout must be clean at ``SOURCE_COMMIT``. Its unmodified
``HistorySliceService`` and its ``tests/history_slices_sim.py`` (the ha-mcp
9.2.0 ``_fetch_history`` reader over the recorder model) produce every page;
only ``hamcp511_parity`` (the synthetic rows and the read cases) comes from
this tree. No network; no bytes are edited after generation.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
from pathlib import Path
import socket
import subprocess
import sys

SOURCE_COMMIT = "f2275ab19ef5acd94ea2acf3737fa7e6a1b2ed56"
SOURCE_FILES = (
    "hass_mcp_engineering_beta/ha_mcp_engineering/history_slices/service.py",
    "hass_mcp_engineering_beta/ha_mcp_engineering/history_slices/contracts.py",
    "tests/history_slices_sim.py",
)
HERE = Path(__file__).resolve().parent


def git(source: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(source), *args], text=True).strip()


def refuse(*args, **kwargs):
    raise AssertionError("parity generation cannot use a network")


def load_parity():
    spec = importlib.util.spec_from_file_location("hamcp511_parity", HERE / "hamcp511_parity.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def generate(parity) -> dict:
    from history_slices_sim import FakeReader, Recorder
    from ha_mcp_engineering.history_slices.service import HistorySliceService
    from ha_mcp_engineering.incident_logs.cursor import CursorCodec
    from ha_mcp_engineering.request_context import begin_request, end_request

    cases = {}
    for case in parity.CASES:
        recorder = Recorder(entity_id=parity.ENTITY)
        parity.seed(recorder)
        reader = FakeReader(recorder)
        codec = CursorCodec("history_slices", key=parity.CODEC_KEY, clock=lambda: parity.NOW_MS / 1000)
        service = HistorySliceService(lambda: reader, codec=codec, clock=lambda: parity.NOW_MS)
        pages, calls = [], []
        cursor = None
        while True:
            arguments = parity.arguments(case)
            if cursor:
                arguments["cursor"] = cursor
            telemetry, token = begin_request()
            telemetry.caller_id = "synthetic_parity_caller"
            before = len(recorder.calls)
            try:
                page = await service.read(arguments)
            finally:
                end_request(token)
            calls.append(len(recorder.calls) - before)
            pages.append(parity.normalize(page))
            cursor = page["next_cursor"]
            if not cursor:
                break
            if len(pages) > 400:
                raise AssertionError("cursor chain did not end")
        cases[case] = {"pages": pages, "delegated_upstream_calls": calls}
    return cases


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    source = options.source.resolve()
    if git(source, "rev-parse", "HEAD") != SOURCE_COMMIT:
        raise SystemExit(f"source is not at {SOURCE_COMMIT}")
    if git(source, "status", "--porcelain", "--untracked-files=no"):
        raise SystemExit("source checkout has tracked modifications")
    if options.output.exists():
        raise SystemExit("refusing to overwrite an existing output")
    sys.path[:0] = [str(source / "hass_mcp_engineering_beta"), str(source / "tests")]
    socket.socket.connect = refuse
    socket.create_connection = refuse
    parity = load_parity()
    cases = asyncio.run(generate(parity))
    import ha_mcp_engineering
    package = Path(ha_mcp_engineering.__file__).resolve().parent
    if not package.is_relative_to(source):
        raise SystemExit("the service did not load from the source checkout")
    document = {
        "provenance": {
            "purpose": "HAMCP-511 parity: delegated history-slices pages over the synthetic recorder",
            "source_commit": SOURCE_COMMIT,
            "source_files_sha256": {
                name: hashlib.sha256((source / name).read_bytes()).hexdigest() for name in SOURCE_FILES
            },
            "fixture_sha256": hashlib.sha256((HERE / "hamcp511_parity.py").read_bytes()).hexdigest(),
            "generator": "tests/generate_hamcp511_parity_goldens.py",
            "python": sys.version.split()[0],
            "compared": "every reply key except " + ", ".join(sorted(parity.DIFFERING_KEYS))
                        + "; next_cursor as has_cursor",
        },
        "cases": cases,
    }
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(json.dumps(document, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
