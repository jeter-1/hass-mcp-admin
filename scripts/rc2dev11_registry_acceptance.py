"""Run the disposable RC2dev11 signed-registry acceptance harness.

The selected tests use temporary data directories, synthetic Ed25519 keys, an
injected fetch transport, and an injectable clock.  They never contact the
fixed production registry or Home Assistant.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    suite = unittest.defaultTestLoader.discover(
        str(ROOT / "tests"), pattern="test_rc2dev11_registry_operations.py"
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
