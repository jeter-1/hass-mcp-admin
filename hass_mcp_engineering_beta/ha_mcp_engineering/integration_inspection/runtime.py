"""Explicit no-I/O application composition for the native inspector."""

from .provider import AlarmoProvider
from .service import IntegrationInspectionService
from .models import InspectionError


class IntegrationInspectionRuntime:
    def __init__(self):
        self.service = None

    def configure(self, client, core_runtime, settings):
        self.service = IntegrationInspectionService(
            AlarmoProvider(client, core_runtime, known_secrets=(settings.access_secret, settings.ha_token)),
            response_limit=settings.response_size_limit,
        )

    def require(self):
        if self.service is None:
            raise InspectionError("authority_unavailable")
        return self.service


INTEGRATION_INSPECTION = IntegrationInspectionRuntime()
