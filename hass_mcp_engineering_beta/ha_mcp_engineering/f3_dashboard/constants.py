"""Reviewed F3-B models and evidence-backed defensive limits."""

from __future__ import annotations

import re


RAW_EVIDENCE_MODEL = "f3-dashboard-raw-evidence-v1"
PATCH_MODEL = "f3-dashboard-json-pointer-patch-v1"
SEMANTIC_DIFF_MODEL = "f3-dashboard-semantic-diff-v1"
RISK_MODEL = "f3-dashboard-action-risk-v1"
PROPOSAL_MODEL = "f3-dashboard-update-proposal-v1"
ARTIFACT_SCHEMA = "f3-dashboard-write-artifact-v1"
VERIFICATION_MODEL = "f3-dashboard-exact-reread-v1"
ATOMICITY_MODEL = "f3-dashboard-atomicity-gate-v1"
OBSERVABILITY_MODEL = "f3-dashboard-observability-v1"

CANONICAL_URL_PATH = re.compile(r"^[a-z0-9_-]{1,256}$")
CANONICAL_OPERATION_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
CANONICAL_PLAN_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{7,127}$")
UPSTREAM_CONFIG_HASH = re.compile(r"^[0-9a-f]{16}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")

PROTOCOL_VERSION = "2025-03-26"
SUPPORTED_UPSTREAM_VERSIONS = frozenset(
    {"7.14.2", "8.0.0", "8.1.1", "8.2.0", "8.4.1"}
)

# The current Engineering dashboard provider defaults to a 60,000-byte MCP
# response and reserves 16,000 bytes for its envelope.  Forty thousand bytes
# leaves an additional 4,000-byte margin for exact raw read metadata.
MAX_RAW_CONFIG_BYTES = 40_000
MAX_RESULT_CONFIG_BYTES = 40_000

# Existing bounded structured-provider evidence uses 16 KiB.  F3-B applies the
# same ceiling independently to patch growth, the canonical patch, and the
# complete reviewer diff instead of inventing an unbounded new payload class.
MAX_CONFIG_GROWTH_BYTES = 16_384
MAX_PATCH_BYTES = 16_384
MAX_SEMANTIC_DIFF_BYTES = 16_384
MAX_INDIVIDUAL_VALUE_BYTES = 8_192

# The authenticated approval surface contains every declared before/after
# operation value, not the complete dashboard.  This matches the existing
# reviewed per-operation configuration-projection ceiling and stays below the
# immutable dashboard artifact bound.
MAX_DASHBOARD_APPROVAL_PROJECTION_BYTES = 131_072

# The existing signed-registry reader accepts at most 256 KiB.  One immutable
# dashboard artifact must remain below that already-tested repository storage
# and CI handling envelope.
MAX_ARTIFACT_BYTES = 262_144

MIN_PATCH_OPERATIONS = 1
MAX_PATCH_OPERATIONS = 16
MAX_SEMANTIC_LEAF_CHANGES = 16
MAX_POINTER_CHARS = 1_024
MAX_POINTER_DEPTH = 32
MAX_JSON_DEPTH = 48
MAX_JSON_NODES = 10_000
MAX_DIFF_PREVIEW_CHARS = 192
MAX_DIFF_MISMATCH_PATHS = 32
MAX_RISK_FINDINGS = 64
MAX_OBSERVABILITY_EVENTS = 128
MAX_EVENT_CODES = 16
MAX_TITLE_CHARS = 160
MAX_DESCRIPTION_CHARS = 2_000
MIN_EXPIRATION_MINUTES = 5
MAX_EXPIRATION_MINUTES = 1_440
ARTIFACT_RETENTION_DAYS = 30
