"""Offline test-support packages that are excluded from production artifacts."""

# HAMCP-363: discovery imports this package before any test module ("support"
# sorts before "test_"), so importing the guard here installs it for the whole
# run; tests/__init__ imports this package for dotted runs.
from . import offline_network  # noqa: F401
