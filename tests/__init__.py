"""Unit-test package (HAMCP-363).

A dotted run such as ``python -m unittest tests.test_x`` imports this first, so
``tests.support`` installs the offline network guard before the test module
loads. Discovery (``-s tests``) does not import this file; it imports
``tests/support`` directly, which installs the same guard.
"""

from . import support  # noqa: F401
