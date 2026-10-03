"""One process-owned analysis service; no collection at import or configuration."""

from ..clients.dashboard_analysis import DashboardAnalysisClient
from .contracts import AnalysisError
from .provider import DashboardAnalysisProvider
from .service import DashboardAnalysisService


class DashboardAnalysisRuntime:
    def __init__(self):
        self.service = None

    def configure(self, settings, core_runtime, upstream):
        secrets = tuple(s for s in (settings.access_secret, settings.ha_token,
                                    *upstream._known_secrets) if s)
        self.service = DashboardAnalysisService(DashboardAnalysisProvider(
            DashboardAnalysisClient(settings), core_runtime, upstream, known_secrets=secrets),
            response_limit=settings.response_size_limit, known_secrets=secrets)

    def require(self):
        if self.service is None:
            raise AnalysisError("authority_unavailable")
        return self.service


DASHBOARD_ANALYSIS = DashboardAnalysisRuntime()
