"""Execute the reviewed ha-mcp custom-component worker teardown functions.

The immutable standalone/add-on images do not contain Home Assistant custom
component source. CI therefore downloads this one file from the exact reviewed
source commit, verifies its reviewed SHA-256 before parsing it, and executes
only the two worker-loop teardown functions in an isolated namespace.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
from collections.abc import AsyncIterator
from functools import partial
import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Callable


MAX_EVIDENCE_BYTES = 32_768
EXPECTED_FUNCTIONS = {"_cancel_pending_tasks", "_teardown_worker_loop"}


class CustomComponentShutdownFailure(RuntimeError):
    """One reviewed custom-component teardown assertion failed."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise CustomComponentShutdownFailure(message)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_reviewed_teardown(
    source_path: Path,
    *,
    expected_sha256: str,
) -> tuple[Callable[[asyncio.AbstractEventLoop], None], dict[str, Any]]:
    data = source_path.read_bytes()
    observed_sha256 = _sha256(data)
    require(
        observed_sha256 == expected_sha256,
        "custom-component source did not match reviewed SHA-256",
    )
    tree = ast.parse(data, filename="reviewed-embedded-server.py")
    timeout_nodes = [
        node
        for node in tree.body
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        and any(
            isinstance(target, ast.Name)
            and target.id == "_TEARDOWN_TIMEOUT_SECONDS"
            for target in (
                node.targets
                if isinstance(node, ast.Assign)
                else [node.target]
            )
        )
    ]
    function_nodes = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in EXPECTED_FUNCTIONS
    ]
    require(len(timeout_nodes) == 1, "reviewed teardown timeout declaration changed")
    require(
        {node.name for node in function_nodes} == EXPECTED_FUNCTIONS,
        "reviewed worker teardown functions changed",
    )
    selected = ast.Module(
        body=[*timeout_nodes, *function_nodes],
        type_ignores=[],
    )
    ast.fix_missing_locations(selected)
    namespace: dict[str, Any] = {
        "asyncio": asyncio,
        "partial": partial,
        "_LOGGER": logging.getLogger("reviewed-embedded-worker-teardown"),
    }
    exec(compile(selected, "reviewed-embedded-server.py", "exec"), namespace)
    timeout = namespace.get("_TEARDOWN_TIMEOUT_SECONDS")
    require(
        isinstance(timeout, float) and timeout == 2.0,
        "reviewed worker teardown timeout changed",
    )
    teardown = namespace.get("_teardown_worker_loop")
    require(callable(teardown), "reviewed worker teardown was not callable")
    return teardown, {
        "source_sha256": observed_sha256,
        "teardown_timeout_seconds": timeout,
        "executed_functions": sorted(EXPECTED_FUNCTIONS),
    }


def exercise_worker_teardown(
    teardown: Callable[[asyncio.AbstractEventLoop], None],
) -> dict[str, Any]:
    loop = asyncio.new_event_loop()
    events: list[str] = []
    loop_errors: list[str] = []
    loop.set_exception_handler(
        lambda _loop, context: loop_errors.append(
            str(context.get("message", "unknown"))[:96]
        )
    )

    async def stream() -> AsyncIterator[None]:
        try:
            while True:
                await asyncio.sleep(3600)
                yield None
        finally:
            events.append("generator_finalized")

    async def reader() -> None:
        async for _ in stream():
            pass

    asyncio.set_event_loop(loop)
    reader_task = loop.create_task(reader(), name="embedded-reader")
    watcher_task = loop.create_task(
        asyncio.sleep(3600), name="embedded-shutdown-watcher"
    )
    try:
        loop.run_until_complete(asyncio.sleep(0))
        loop.run_until_complete(asyncio.sleep(0))
        pending_before = len(asyncio.all_tasks(loop))
        require(pending_before == 2, "worker probe did not create two pending tasks")
        teardown(loop)
    finally:
        asyncio.set_event_loop(None)
        if not loop.is_closed():
            for task in asyncio.all_tasks(loop):
                task.cancel()
            loop.run_until_complete(asyncio.gather(*asyncio.all_tasks(loop), return_exceptions=True))
            loop.close()

    require(reader_task.cancelled(), "embedded reader task was not cancelled")
    require(watcher_task.cancelled(), "embedded watcher task was not cancelled")
    require(events == ["generator_finalized"], "embedded async generator was not finalized")
    require(loop.is_closed(), "embedded worker loop was not closed")
    require(not loop_errors, "embedded worker loop reported teardown errors")
    return {
        "pending_before": pending_before,
        "reader_cancelled": True,
        "watcher_cancelled": True,
        "generator_finalized": True,
        "loop_closed": True,
        "loop_error_count": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--expected-source-sha256", required=True)
    parser.add_argument("--expected-source-commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    failed = False
    try:
        require(
            len(args.expected_source_sha256) == 64,
            "expected source SHA-256 was malformed",
        )
        require(
            len(args.expected_source_commit) == 40,
            "expected source commit was malformed",
        )
        teardown, source_evidence = load_reviewed_teardown(
            args.source,
            expected_sha256=args.expected_source_sha256,
        )
        result = {
            "result": "PASS",
            "model": "ha-mcp-reviewed-custom-component-shutdown-v1",
            "source_commit": args.expected_source_commit,
            **source_evidence,
            **exercise_worker_teardown(teardown),
        }
    except Exception as exc:
        failed = True
        result = {
            "result": "FAIL",
            "model": "ha-mcp-reviewed-custom-component-shutdown-v1",
            "failure_type": type(exc).__name__[:96],
        }
    encoded = json.dumps(result, indent=2, sort_keys=True) + "\n"
    require(len(encoded.encode("utf-8")) <= MAX_EVIDENCE_BYTES, "evidence too large")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(encoded, encoding="utf-8")
    if failed:
        raise SystemExit(
            "custom-component shutdown acceptance failed; see bounded evidence"
        )


if __name__ == "__main__":
    main()
