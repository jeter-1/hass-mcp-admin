"""Core's entity-ID slug for the name heuristic, on a proven subset only.

Core 2026.10.1 ``homeassistant/util/__init__.py`` ``slugify`` calls
python-slugify 9.1.2 (``package_constraints.txt``) with ``separator="_"``
and the default legacy pipeline (``slugify/_legacy.py``), and returns
``"unknown"`` for an empty slug. The port below covers only text that is
ASCII and has no ``&``: there the pipeline's transliteration, Unicode
normalization and HTML-entity decoding are identities, and the remaining
steps are the regular expressions below. Any other text is not slugified
(``None``) and so never yields a derived entity ID; a match is never
guessed from a slug this module cannot reproduce.
"""

from __future__ import annotations

import re

SLUGIFY_PIN = "python-slugify 9.1.2 legacy pipeline (Core 2026.10.1 package_constraints.txt)"

_QUOTE = re.compile(r"'+")
_NUMBERS = re.compile(r"(?<=\d),(?=\d)")
_DISALLOWED = re.compile(r"[^-a-z0-9]+")
_DUPLICATE_DASH = re.compile(r"-{2,}")


def core_slugify(text: str) -> str | None:
    """Core's slug of ``text``, or ``None`` outside the proven subset."""

    if type(text) is not str or not text.isascii() or "&" in text:
        return None
    if text == "":
        return ""
    text = _QUOTE.sub("-", text).lower()
    text = _NUMBERS.sub("", text)
    text = _DISALLOWED.sub("-", text)
    text = _DUPLICATE_DASH.sub("-", text).strip("-")
    text = text.replace("-", "_")
    return "unknown" if text == "" else text
