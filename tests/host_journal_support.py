"""Offline host journal fixtures for HAMCP-224 (no Docker, no live host).

The journal files are the real-format fixtures that
``tests/fixtures/hamcp224_journal/generate.py`` produced with systemd v257.13
(see ``provenance.json``); they are decompressed into a disposable container
root laid out as the Supervisor mounts them. The ``mountinfo-*`` files are
synthetic ``/proc/self/mountinfo`` tables.
"""

from __future__ import annotations

import gzip
from pathlib import Path
import shutil
import tempfile

from ha_mcp_engineering.incident_logs.host_journal import HostJournalReader


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "hamcp224_journal"
MACHINE_ID = "5e7e7e7e000000000000000000000224"
# 2026-10-20T10:00:00Z, the fixtures' first entry, in microseconds.
T0_US = 1_792_490_400_000_000
T0_MS = T0_US // 1000


def journald_off_reader() -> HostJournalReader:
    """A reader whose mount table has no journal mount (``journald: false``)."""

    return HostJournalReader(root=str(FIXTURES / "no-such-root"), mountinfo=str(FIXTURES / "mountinfo-off"))


def journal_root(variant: str = "uncompressed", *, persistent: bool = True, runtime: bool = True) -> str:
    """A disposable container root holding the fixture journals; the caller removes it."""

    root = Path(tempfile.mkdtemp(prefix="hamcp224-root-"))
    for kind, relative, wanted in (("persistent", "var/log/journal", persistent),
                                   ("runtime", "run/log/journal", runtime)):
        directory = root / relative / MACHINE_ID
        directory.mkdir(parents=True)
        if wanted:
            with gzip.open(FIXTURES / f"{kind}-{variant}.journal.gz") as source, \
                    open(directory / f"{kind}.journal", "wb") as target:
                shutil.copyfileobj(source, target)
    return str(root)


def reader(root: str, mountinfo: str = "mountinfo-on", **kwargs) -> HostJournalReader:
    return HostJournalReader(root=root, mountinfo=str(FIXTURES / mountinfo), **kwargs)
