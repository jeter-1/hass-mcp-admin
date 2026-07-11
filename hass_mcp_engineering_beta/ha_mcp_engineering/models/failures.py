"""Compatibility export for the structured beta failure contract."""

from .responses import FailureResponse

ErrorModel = FailureResponse

__all__ = ["ErrorModel", "FailureResponse"]
