"""Private per-call health evidence, never an execution or recovery authority."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Callable

from ..errors import ErrorCode, GovernanceError


class HealthScanFence:
    """Optimistic writer fences plus a cooperative check of inspected files.

    Supported writers replace records atomically and advance their namespace
    directory token. Per-file stamps also detect in-place edits during a scan.
    This does not claim an atomic snapshot against arbitrary external writers.
    No repository lock is retained by this object.
    """

    def __init__(self, sources: tuple[tuple[ErrorCode, Callable[[], Any]], ...]):
        self.sources = sources
        self.tokens = tuple(read() for _, read in sources)
        self.files: dict[Path, tuple[int, int, int, int] | None] = {}
        self.file_codes: dict[Path, ErrorCode] = {}

    @staticmethod
    def stamp(path: Path) -> tuple[int, int, int, int] | None:
        try:
            stat = path.stat()
        except FileNotFoundError:
            return None
        return stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns

    @staticmethod
    def superseded(code: ErrorCode = ErrorCode.EXECUTION_TASK_STORAGE_ERROR) -> None:
        raise GovernanceError(code, details={"reason": "health_snapshot_superseded"})

    def check(self) -> None:
        for (code, read), token in zip(self.sources, self.tokens, strict=True):
            if read() != token:
                self.superseded(code)

    def check_after_failure(self) -> None:
        """Only proven writer movement can supersede an original read failure.

        If the fence itself is unavailable, the caller must still attribute
        and propagate its original storage failure. Cancellation is untouched.
        """
        try:
            self.check()
        except GovernanceError as exc:
            if exc.details.get("reason") == "health_snapshot_superseded":
                raise
        except Exception:
            pass

    def watch(
        self, path: Path, *, code: ErrorCode = ErrorCode.EXECUTION_TASK_STORAGE_ERROR
    ) -> None:
        self.files.setdefault(path, self.stamp(path))
        self.file_codes.setdefault(path, code)

    async def verify_files(self) -> None:
        for path, stamp in self.files.items():
            await asyncio.sleep(0)
            self.check()
            if self.stamp(path) != stamp:
                self.superseded(self.file_codes[path])
        self.check()
