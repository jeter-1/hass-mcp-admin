"""Compatibility facade for the canonical shipped F3 adapter contract.

The authoritative definitions live in :mod:`ha_mcp_engineering.f3.contracts`.
This repository-root module exists only for historical specification and test
imports; every exported object is the canonical shipped object.
"""

from ha_mcp_engineering.f3.contracts import *  # noqa: F403
from ha_mcp_engineering.f3.contracts import __all__
