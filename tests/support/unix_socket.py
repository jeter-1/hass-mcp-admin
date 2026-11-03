"""Short, test-owned pathname sockets independent of deep shard scratch."""

import os
from pathlib import Path
import shutil
import tempfile


# Linux sun_path has 108 bytes including the trailing NUL.
MAX_SOCKET_PATH_BYTES = 107


def assert_socket_path_length(path):
    size = len(os.fsencode(path))
    if size > MAX_SOCKET_PATH_BYTES:
        raise AssertionError(
            f"AF_UNIX socket path is {size} bytes; maximum is "
            f"{MAX_SOCKET_PATH_BYTES} bytes (excluding NUL): {path}"
        )


def socket_path(case):
    """Register cleanup before validation; keep mkdtemp visible to the shard guard."""
    directory = Path(tempfile.mkdtemp(prefix="hamcp-s-", dir="/tmp"))
    case.addCleanup(shutil.rmtree, directory)
    path = directory / "s"
    assert_socket_path_length(path)
    return str(path)
