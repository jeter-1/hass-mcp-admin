"""Compiled 9.1.0 projections for ledger rows 13, 14 and 17-24 (HAMCP-319 section 7).

Each entry in ``PROJECTIONS`` maps an upstream tool name to
``(project(payload, arguments) -> payload, completeness(payload) -> (partial, warnings))``.
``project`` raises ``ValueError`` on an unrecognized shape (fail closed).
Warnings are fixed Engineering strings, never upstream text.
"""
from __future__ import annotations

PROJECTIONS: dict = {}
