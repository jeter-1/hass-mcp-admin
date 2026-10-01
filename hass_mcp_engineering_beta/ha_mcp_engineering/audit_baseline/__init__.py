"""Offline automation audit-baseline validation and comparison."""

from .comparison import compare_baselines, comparison_report_dict, render_bounded_report
from .models import (
    BASELINE_SCHEMA,
    COMPARISON_SCHEMA,
    FINGERPRINT_MODEL,
    LEGACY_UNRESOLVED_FINGERPRINT_MODEL,
    Classification,
)
from .validation import (
    BaselineValidationError,
    canonical_configuration_digest,
    load_baseline,
    normalize_legacy_baseline,
)

__all__ = [
    "BASELINE_SCHEMA",
    "COMPARISON_SCHEMA",
    "FINGERPRINT_MODEL",
    "LEGACY_UNRESOLVED_FINGERPRINT_MODEL",
    "BaselineValidationError",
    "Classification",
    "canonical_configuration_digest",
    "compare_baselines",
    "comparison_report_dict",
    "load_baseline",
    "normalize_legacy_baseline",
    "render_bounded_report",
]
