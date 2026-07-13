"""Single-automation reliability analysis package.

Runtime exports are loaded lazily so the shared trace timestamp normalizer can
be imported independently by other read-only Engineering providers.
"""

__all__ = ["AutomationReliabilityAnalysisService", "RELIABILITY_ANALYSIS"]


def __getattr__(name):
    if name == "RELIABILITY_ANALYSIS":
        from .runtime import RELIABILITY_ANALYSIS

        return RELIABILITY_ANALYSIS
    if name == "AutomationReliabilityAnalysisService":
        from .service import AutomationReliabilityAnalysisService

        return AutomationReliabilityAnalysisService
    raise AttributeError(name)
