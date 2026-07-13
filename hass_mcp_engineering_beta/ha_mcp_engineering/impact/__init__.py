"""Single-entity change-impact analysis boundary."""

from .models import ImpactAnalysisOutput
from .runtime import CHANGE_IMPACT_ANALYSIS

__all__ = ["CHANGE_IMPACT_ANALYSIS", "ImpactAnalysisOutput"]
