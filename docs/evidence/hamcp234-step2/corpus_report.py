"""Write the HAMCP-234 step 2 corpus report (offline, deterministic).

Run from the repository root: ``python docs/evidence/hamcp234-step2/corpus_report.py``.
The corpus and the computation live in ``tests/hamcp234_step2_corpus.py``;
``tests/test_hamcp234_step2_journal_relaxation.py`` keeps the committed
report equal to it.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tests"))

import hamcp234_step2_corpus as corpus  # noqa: E402


def main() -> None:
    corpus.REPORT_PATH.write_text(json.dumps(corpus.report(), indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
