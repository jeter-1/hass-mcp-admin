"""Typed error boundaries reserved for v2 response governance."""


class EngineeringServerError(RuntimeError):
    """Base exception for beta application failures."""


class ConfigurationError(EngineeringServerError):
    """Raised when beta configuration is invalid."""


class HomeAssistantError(EngineeringServerError):
    """Raised when Home Assistant rejects or cannot complete a request."""


class AuthenticationError(EngineeringServerError):
    """Raised for authenticated gateway failures."""
