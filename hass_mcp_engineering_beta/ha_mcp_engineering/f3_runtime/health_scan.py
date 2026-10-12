"""Private per-call health evidence, never an execution or recovery authority."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Callable

from ..errors import ErrorCode, GovernanceError

# Bounded diagnostic labels for the deep-health fence, in source order.
HEALTH_FENCE_CATEGORIES = ("plans", "tasks", "f3_children", "f3_locks")


class HealthScanFence:
    """Optimistic writer fences plus a cooperative check of inspected files.

    Supported writers replace records atomically and advance their namespace
    directory token. Per-file stamps also detect in-place edits during a scan.
    This does not claim an atomic snapshot against arbitrary external writers.
    No repository lock is retained by this object.
    """

    def __init__(
        self,
        sources: tuple[tuple[ErrorCode, Callable[[], Any]], ...],
        *,
        categories: tuple[str, ...] | None = None,
        roots: tuple[tuple[Path, str], ...] = (),
        acquire: bool = True,
    ):
        self.sources = sources
        # Bounded diagnostic labels only: which fenced source moved or failed.
        self.categories = categories or ("unknown",) * len(sources)
        if len(self.categories) != len(sources):
            raise ValueError("fence categories must match its sources")
        self.roots = tuple(
            sorted(roots, key=lambda item: len(item[0].parts), reverse=True))
        self.moved_category: str | None = None
        self.failed_category: str | None = None
        self.tokens: tuple[Any, ...] | None = None
        self.files: dict[Path, tuple[int, int, int, int] | None] = {}
        self.file_codes: dict[Path, ErrorCode] = {}
        self.file_categories: dict[Path, str] = {}
        if acquire:
            self.acquire()

    def acquire(self) -> None:
        """Read the initial tokens.

        A caller that constructs with ``acquire=False`` holds the fence before
        this read, so an initial read failure keeps its ``failed_category``.
        """
        self.tokens = tuple(
            self._read_source(code, read, category)
            for (code, read), category in zip(self.sources, self.categories, strict=True))

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

    def _read_source(self, code: ErrorCode, read: Callable[[], Any], category: str) -> Any:
        try:
            return read()
        except GovernanceError:
            self.failed_category = category
            raise
        except Exception as exc:
            # Unavailable evidence is a storage failure, never supersession.
            # Preserve the source's category without exposing exception text.
            self.failed_category = category
            raise GovernanceError(code) from exc

    def check(self) -> None:
        for (code, read), token, category in zip(
                self.sources, self.tokens, self.categories, strict=True):
            if self._read_source(code, read, category) != token:
                self.moved_category = category
                self.superseded(code)

    def category_for(self, path: Path) -> str:
        for root, category in self.roots:
            if path.is_relative_to(root):
                return category
        return "unknown"

    def check_after_failure(self) -> None:
        """Only proven writer movement can supersede an original read failure.

        If the fence itself is unavailable, the caller must still attribute
        and propagate its original storage failure. Cancellation is untouched.
        """
        if self.tokens is None:
            # No initial tokens were read, so no movement can be proven.
            return
        # A swallowed re-read failure must not claim the original's category.
        original_category = self.failed_category
        try:
            self.check()
        except GovernanceError as exc:
            if exc.details.get("reason") == "health_snapshot_superseded":
                raise
            self.failed_category = original_category
        except Exception:
            self.failed_category = original_category

    def watch(
        self, path: Path, *, code: ErrorCode = ErrorCode.EXECUTION_TASK_STORAGE_ERROR
    ) -> None:
        self.files.setdefault(path, self.stamp(path))
        self.file_codes.setdefault(path, code)
        self.file_categories.setdefault(path, self.category_for(path))

    async def verify_files(self) -> None:
        for path, stamp in self.files.items():
            await asyncio.sleep(0)
            self.check()
            if self.stamp(path) != stamp:
                self.moved_category = self.file_categories.get(path, "unknown")
                self.superseded(self.file_codes[path])
        self.check()
