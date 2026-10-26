"""Explicit baseline composition, with no import-time collection or persistence."""

from pathlib import Path

from ..clients.audit_baseline import BaselineReadClient
from .capture_contracts import CaptureError
from .capture_provider import BaselineCaptureProvider
from .capture_service import BaselineCaptureService
from .store import BaselineStore


class BaselineCaptureRuntime:
    def __init__(self):
        self.service = None

    def configure(self, settings, core_runtime):
        self.service = BaselineCaptureService(BaselineCaptureProvider(
            BaselineReadClient(settings), core_runtime,
            known_secrets=(settings.access_secret, settings.ha_token)),
            # HAMCP-228: under the governance disk boundary, like HAMCP-225 snapshots.
            BaselineStore(Path(settings.governance_path)),
            response_limit=settings.response_size_limit)

    def require(self):
        if self.service is None:
            raise CaptureError("authority_unavailable")
        return self.service


AUTOMATION_BASELINE_CAPTURE = BaselineCaptureRuntime()
