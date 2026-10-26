"""Levels of container journal entries, parsed from their known text formats (HAMCP-234).

A container's journald log driver records stdout at PRIORITY 6 and stderr at
PRIORITY 3, so the journal priority of a ``supervisor`` or ``addon:<slug>``
entry says which stream the line was written to, not its level: Python
logging writes every record to stderr, which made every line ``error``. The
level is instead read from the start of the screened, sanitized first line,
in one of these pinned formats, and is a decision only, never output text:

- **Supervisor** 2026.09.1 (``40e3ee7640a3c44abe67f1a1397f39c3cd949806``)
  ``supervisor/bootstrap.py:302-306``:
  ``%(asctime)s.%(msecs)03d %(levelname)s (%(threadName)s) [%(name)s] %(message)s``
  with ``%Y-%m-%d %H:%M:%S``; 2026.10.4 (``d92eb0e373a304c1e6a66dcef66585854a2002d1``)
  ``supervisor/bootstrap.py:306-324`` (``isoformat`` at 317) writes the
  same with a ``+HH:MM`` offset.
  A thread name may hold one level of parentheses (``Thread-3 (_worker)``).
- **matter.js** (the Matter Server add-on 9.2.0, ``home-assistant/addons``
  ``b518b7c1a09f14bd956113da5d0c6b21aa730c04`` ``matter_server/build.yaml:3-4``,
  is ``matterjs-server`` 1.4.0 on ``@matter/main`` 0.17.9): the console
  destination uses the ansi format (``packages/general/src/log/LogDestination.ts:88``),
  ``LogFormat.ts:252`` ``<time> <LEVEL padded to 6> <facility>``, and the plain
  format ``LogFormat.ts:135-145`` ``<time> <LEVEL> <facility>``; ``<time>`` is
  ``YYYY-MM-DD HH:MM:SS.mmm`` (``LogFormat.ts:692-703``) and the levels are
  ``LogLevel.ts:46-51``. The ansi colour codes are already removed by the
  screen (``host_journal.screen_text``).
- **Python logging** CPython 3.13.5: ``BASIC_FORMAT``
  ``%(levelname)s:%(name)s:%(message)s`` (``Lib/logging/__init__.py:546``) and
  the documented ``%(asctime)s - %(name)s - %(levelname)s - %(message)s``
  (``Doc/howto/logging.rst:625``).
- **bashio** v0.17.1 (``0d0a2f71``) ``lib/const.sh:14,16,34-43``:
  ``[%T] LEVEL: message``.

Every pattern is anchored at the start of the line and needs the format's
whole prefix, so a level word elsewhere in a message is never read.
"""

from __future__ import annotations

import re

BASIS = "parsed_text"
# The journal priority of a container entry whose text matched no format.
PRIORITY_BASIS = "journal_priority"
# A withheld container message: its text was never read, so its level is the
# journal priority, which is not the level (HAMCP-234).
UNVERIFIED_BASIS = "priority_unverified"

_HEAD = 256
_DATE = r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}"
_PYTHON = r"(DEBUG|INFO|WARNING|ERROR|CRITICAL)"
_FORMATS = (
    # Supervisor.
    re.compile(_DATE + r"\.\d{3}(?:[+-]\d{2}:\d{2})? " + _PYTHON
               + r" \((?:[^()\n]|\([^()\n]*\))*\) \[[^\]\n]+\] "),
    # matter.js ansi (level padded to six) and plain.
    re.compile(_DATE + r"\.\d{3} (DEBUG|INFO|NOTICE|WARN|ERROR|FATAL) {1,3}\S"),
    # Python BASIC_FORMAT.
    re.compile(_PYTHON + r":[^:\s]+:"),
    # Python's documented asctime - name - level format.
    re.compile(_DATE + r",\d{3} - [^\n]{1,128}? - " + _PYTHON + r" - "),
    # bashio.
    re.compile(r"\[\d{2}:\d{2}:\d{2}\] (TRACE|DEBUG|INFO|NOTICE|WARNING|ERROR|FATAL): "),
)
_LEVELS = {
    "TRACE": "debug", "DEBUG": "debug", "INFO": "info", "NOTICE": "info",
    "WARN": "warning", "WARNING": "warning", "ERROR": "error",
    "CRITICAL": "critical", "FATAL": "critical",
}


def container_source(key: str) -> bool:
    """Whether ``key`` is a host journal source written by a container."""

    return key == "supervisor" or key.startswith("addon:")


def parse_level(message: str) -> str | None:
    """The level of a screened, sanitized message in a known format, else ``None``."""

    head = message.split("\n", 1)[0][:_HEAD]
    for form in _FORMATS:
        match = form.match(head)
        if match:
            return _LEVELS[match.group(1)]
    return None
