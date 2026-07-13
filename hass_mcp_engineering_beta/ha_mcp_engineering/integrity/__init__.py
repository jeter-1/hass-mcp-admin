"""Global configuration-integrity analysis boundary."""

from .models import IntegrityAnalysisOutput
from .runtime import CONFIGURATION_INTEGRITY_ANALYSIS

__all__ = ["CONFIGURATION_INTEGRITY_ANALYSIS", "IntegrityAnalysisOutput"]
