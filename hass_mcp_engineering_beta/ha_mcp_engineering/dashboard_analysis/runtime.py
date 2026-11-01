"""One process-owned analysis service; no collection at import or configuration."""

from ..clients.dashboard_analysis import DashboardAnalysisClient
from .contracts import AnalysisError
from .provider import DashboardAnalysisProvider
from .service import DashboardAnalysisService


class DashboardAnalysisRuntime:
    def __init__(self):
        self.service = None
        self._dependency = None

    def configure(self, settings, core_runtime, upstream):
        secrets = tuple(s for s in (settings.access_secret, settings.ha_token,
                                    *upstream._known_secrets) if s)
        self.service = DashboardAnalysisService(DashboardAnalysisProvider(
            DashboardAnalysisClient(settings), core_runtime, upstream, known_secrets=secrets),
            response_limit=settings.response_size_limit, known_secrets=secrets)
        self._dependency = (core_runtime, upstream, secrets)

    def dependency_reader(self, *, audit=None):
        """HAMCP-227: a fresh read-only reader for one dependency build."""
        from .dependency_reader import DashboardDependencyReader

        if self._dependency is None:
            return None
        core_runtime, upstream, secrets = self._dependency
        return DashboardDependencyReader(core_runtime, upstream, audit=audit, known_secrets=secrets)

    def require(self):
        if self.service is None:
            raise AnalysisError("authority_unavailable")
        return self.service


DASHBOARD_ANALYSIS = DashboardAnalysisRuntime()
