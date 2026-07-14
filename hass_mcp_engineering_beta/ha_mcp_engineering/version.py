"""Immutable beta server identity and build metadata."""

import os

SERVER_NAME = "HA MCP Engineering Server Beta"
SERVER_ID = "hass-mcp-engineering-beta"
SERVER_VERSION = "2.0.0-beta.26"
SCHEMA_VERSION = "1"
BUILD_SHA = os.environ.get("HAMCP_BUILD_SHA", "unknown")
BUILD_TIME = os.environ.get("HAMCP_BUILD_TIME", "unknown")
