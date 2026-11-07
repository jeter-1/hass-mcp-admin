"""Compiled 9.1.0 ha_get_history projection: rule 3 state rows and the section 6.1 statistics projection (ledger row 16).

Each entry in ``PROJECTIONS`` maps an upstream tool name to
``(project(payload, arguments) -> payload, completeness(payload) -> (partial, warnings))``.
``project`` raises ``ValueError`` on an unrecognized shape (fail closed).
Warnings are fixed Engineering strings, never upstream text.
"""
from __future__ import annotations

PROJECTIONS: dict = {}
