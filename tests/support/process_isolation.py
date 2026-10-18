"""Process-wide state isolation for the unittest suite (HAMCP-206).

Every run of the suite (``python -m unittest discover -s tests``, the
``check.ps1 -Parallel`` workers and CI's shards) performs full discovery, which
imports ``tests/test_process_isolation.py``; that import calls ``install()``.
The hook therefore lives in unittest's own suite machinery, not in each module:

* Shared reset: around every test the known process-wide state (``PROBES``) is
  captured before each test is called and restored after it, cleanups included.
  A test can never hand its state to a later test, whatever the shard order.
* Leak guard: state set by class or module fixtures outlives single tests. At
  every module boundary the state is compared with the run's baseline, taken
  before the first module started. A difference is reported as a
  ``tearDownModule (<module>)`` error naming the module and the leaked entries,
  then restored so the failure stays with the module that caused it.

Only the result object of the outermost run is guarded. Suites that tests run
internally (synthetic shard fixtures) are left alone unless a test binds its
own ``Guard`` to that inner result. Environment variable values are never
reported, only their names.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import os
import sys
import unittest
from unittest import case as _case, suite as _suite
import warnings

CATALOG_MODULE = "ha_mcp_engineering.capabilities"
CATALOG_ATTRIBUTES = ("_DYNAMIC_UPSTREAM_CAPABILITIES", "_UPSTREAM_READ_GATEWAY_SUMMARY")
STATE_ATTRIBUTE = "_hamcp_process_isolation"
MAX_LISTED = 10
_MISSING = object()


class ProcessStateLeak(AssertionError):
    """A test module left process-wide state different from the run's baseline."""


def _listed(items):
    items = sorted(str(item) for item in items)
    shown = ", ".join(items[:MAX_LISTED])
    return shown + (f", ... and {len(items) - MAX_LISTED} more" if len(items) > MAX_LISTED else "")


class ModuleAttributeProbe:
    """A runtime module global, compared by identity and by value."""

    def __init__(self, module, attribute, describe=None):
        self.module, self.attribute, self.describe = module, attribute, describe
        self.name = f"{module}.{attribute}"

    def capture(self):
        module = sys.modules.get(self.module)
        if module is None:
            return _MISSING
        value = getattr(module, self.attribute, _MISSING)
        return value, copy.deepcopy(value)

    def changes(self, saved):
        module = sys.modules.get(self.module)
        if saved is _MISSING or module is None:
            return []
        current = getattr(module, self.attribute, _MISSING)
        if current is saved[0] and current == saved[1]:
            return []
        detail = self.describe(saved[1], current) if self.describe else "changed"
        return [f"{self.name}: {detail}"]

    def restore(self, saved):
        module = sys.modules.get(self.module)
        if saved is _MISSING or module is None:
            return
        original, value = saved
        if isinstance(original, dict):
            original.clear()
            original.update(copy.deepcopy(value))
        elif isinstance(original, list):
            original[:] = copy.deepcopy(value)
        setattr(module, self.attribute, original)


def _catalog_entries(before, after):
    def names(value):
        try:
            return {str(item.get("name")) for item in value}
        except (TypeError, AttributeError):
            return set()
    added, removed = names(after) - names(before), names(before) - names(after)
    text = f"{len(before) if isinstance(before, tuple) else '?'} -> {len(after) if isinstance(after, tuple) else '?'} entries"
    if added:
        text += f"; added {_listed(added)}"
    if removed:
        text += f"; removed {_listed(removed)}"
    return text


def _summary_keys(before, after):
    if not isinstance(before, dict) or not isinstance(after, dict):
        return "replaced"
    keys = {k for k in set(before) | set(after) if before.get(k, _MISSING) != after.get(k, _MISSING)}
    return f"keys changed: {_listed(keys)}" if keys else "replaced by an equal object"


class EnvironmentProbe:
    name = "os.environ"

    def capture(self):
        return dict(os.environ)

    def changes(self, saved):
        current = dict(os.environ)
        if current == saved:
            return []
        added = set(current) - set(saved)
        removed = set(saved) - set(current)
        changed = {k for k in set(current) & set(saved) if current[k] != saved[k]}
        parts = [f"{label} {_listed(keys)}" for label, keys in
                 (("added", added), ("removed", removed), ("changed", changed)) if keys]
        return [f"os.environ: {'; '.join(parts)} (values not shown)"]

    def restore(self, saved):
        if dict(os.environ) != saved:
            os.environ.clear()
            os.environ.update(saved)


class WorkingDirectoryProbe:
    name = "cwd"

    def capture(self):
        return os.getcwd()

    def changes(self, saved):
        current = os.getcwd()
        return [] if current == saved else [f"cwd: {saved} -> {current}"]

    def restore(self, saved):
        if os.getcwd() != saved:
            os.chdir(saved)


class SysPathProbe:
    name = "sys.path"

    def capture(self):
        return list(sys.path)

    def changes(self, saved):
        if sys.path == saved:
            return []
        added = [p for p in sys.path if p not in saved]
        removed = [p for p in saved if p not in sys.path]
        return [f"sys.path: added {added or '[]'}, removed {removed or '[]'}"
                + ("" if added or removed else ", reordered")]

    def restore(self, saved):
        if sys.path != saved:
            sys.path[:] = saved


class SysModulesProbe:
    """Modules present at capture must keep their identity; new imports are allowed."""

    name = "sys.modules"

    def capture(self):
        return dict(sys.modules)

    def changes(self, saved):
        modules = sys.modules
        removed = [n for n, m in saved.items() if n not in modules]
        replaced = [n for n, m in saved.items() if n in modules and modules[n] is not m]
        parts = [f"{label} {_listed(names)}" for label, names in
                 (("removed", removed), ("replaced", replaced)) if names]
        return [f"sys.modules: {'; '.join(parts)}"] if parts else []

    def restore(self, saved):
        modules = sys.modules
        for name, module in saved.items():
            if modules.get(name, _MISSING) is not module:
                modules[name] = module


class WarningsFilterProbe:
    name = "warnings.filters"

    def capture(self):
        return list(warnings.filters)

    def changes(self, saved):
        return [] if warnings.filters == saved else [
            f"warnings.filters: {len(saved)} -> {len(warnings.filters)} filters"]

    def restore(self, saved):
        if warnings.filters != saved:
            warnings.filters[:] = saved
            mutated = getattr(warnings, "_filters_mutated", None)
            if mutated is not None:
                mutated()


def _logger_state(logger):
    return (logger.level, logger.disabled, getattr(logger, "propagate", True),
            tuple(logger.handlers), tuple(logger.filters))


_DEFAULT_LOGGER_STATE = (logging.NOTSET, False, True, (), ())


class LoggingProbe:
    """Level, disabled, propagate, handlers and filters of every logger."""

    name = "logging"

    def capture(self):
        loggers = {"": logging.getLogger()}
        loggers.update((n, l) for n, l in list(logging.Logger.manager.loggerDict.items())
                       if isinstance(l, logging.Logger))
        return logging.root.manager.disable, {n: (l, _logger_state(l)) for n, l in loggers.items()}

    def _current(self):
        return self.capture()[1]

    def changes(self, saved):
        disable, loggers = saved
        found = []
        if logging.root.manager.disable != disable:
            found.append(f"logging.disable: {disable} -> {logging.root.manager.disable}")
        for name, (logger, state) in sorted(self._current().items()):
            before = loggers.get(name, (logger, _DEFAULT_LOGGER_STATE))[1]
            if state == before:
                continue
            fields = [label for label, a, b in zip(
                ("level", "disabled", "propagate", "handlers", "filters"), before, state) if a != b]
            found.append(f"logger {name or 'root'!r}: {', '.join(fields)} changed")
        return found

    def restore(self, saved):
        disable, loggers = saved
        if logging.root.manager.disable != disable:
            logging.disable(disable)
        for name, (logger, state) in self._current().items():
            original = loggers.get(name, (logger, _DEFAULT_LOGGER_STATE))[1]
            if state == original:
                continue
            level, disabled, propagate, handlers, filters = original
            logger.setLevel(level)
            logger.disabled, logger.propagate = disabled, propagate
            logger.handlers[:] = handlers
            logger.filters[:] = filters


class EventLoopProbe:
    """The event loop policy and the loop set for the main thread."""

    name = "asyncio"

    @staticmethod
    def _loop(policy):
        local = getattr(policy, "_local", None)
        return getattr(local, "_loop", None)

    def capture(self):
        policy = asyncio.get_event_loop_policy()
        return policy, self._loop(policy)

    def changes(self, saved):
        policy, loop = saved
        found = []
        current = asyncio.get_event_loop_policy()
        if current is not policy:
            found.append(f"asyncio event loop policy: {type(policy).__name__} -> {type(current).__name__}")
        elif self._loop(current) is not loop:
            found.append(f"asyncio main-thread event loop: {loop!r} -> {self._loop(current)!r}")
        return found

    def restore(self, saved):
        policy, loop = saved
        if asyncio.get_event_loop_policy() is not policy:
            asyncio.set_event_loop_policy(policy)
        local = getattr(policy, "_local", None)
        if local is not None and getattr(local, "_loop", None) is not loop:
            local._loop = loop


PROBES = (
    ModuleAttributeProbe(CATALOG_MODULE, CATALOG_ATTRIBUTES[0], _catalog_entries),
    ModuleAttributeProbe(CATALOG_MODULE, CATALOG_ATTRIBUTES[1], _summary_keys),
    EnvironmentProbe(),
    WorkingDirectoryProbe(),
    SysPathProbe(),
    SysModulesProbe(),
    WarningsFilterProbe(),
    LoggingProbe(),
    EventLoopProbe(),
)


class Guard:
    """Snapshot, compare and restore ``probes`` for one bound test result."""

    def __init__(self, probes=PROBES, observer=None):
        self.probes = tuple(probes)
        self.observer = observer
        self.baseline = None
        self.modules_checked = 0
        self.tests_reset = 0
        self.leaks = []

    def capture(self):
        return [(probe, probe.capture()) for probe in self.probes]

    @staticmethod
    def changes(snapshot):
        return [change for probe, saved in snapshot for change in probe.changes(saved)]

    @staticmethod
    def restore(snapshot):
        for probe, saved in reversed(snapshot):
            probe.restore(saved)

    def around_test(self, test, run):
        snapshot = self.capture()
        try:
            return run()
        finally:
            changes = self.changes(snapshot)
            if changes:
                self.restore(snapshot)
                self.tests_reset += 1
                if self.observer:
                    self.observer("test_reset", test.id(), changes)

    def module_boundary(self, module):
        """Return the leak message for ``module`` (or None) and restore the baseline."""

        if self.baseline is None:
            self.baseline = self.capture()
            return None
        if module is None:
            return None
        self.modules_checked += 1
        changes = self.changes(self.baseline)
        if not changes:
            return None
        self.restore(self.baseline)
        self.leaks.append((module, changes))
        if self.observer:
            self.observer("module_leak", module, changes)
        return (f"test module {module!r} leaked process-wide state past its own tests "
                f"(class or module fixtures); restored for later modules: " + "; ".join(changes))


class _State:
    def __init__(self):
        self.guards = {}
        self.process_guard = Guard()
        self.process_result = None


def _state():
    return getattr(unittest.TestCase, STATE_ATTRIBUTE, None)


def _eligible(result):
    return isinstance(result, unittest.TestResult) and not isinstance(result, _suite._DebugResult)


def _guard_for(result, *, bind):
    state = _state()
    if state is None or result is None:
        return None
    guard = state.guards.get(id(result))
    if guard is None and bind and state.process_result is None and _eligible(result):
        state.process_result = result
        state.guards[id(result)] = guard = state.process_guard
    return guard


def bind(guard, result):
    """Guard an inner run explicitly (used by the guard's own tests)."""

    install()
    _state().guards[id(result)] = guard


def unbind(result):
    _state().guards.pop(id(result), None)


def process_guard():
    state = _state()
    return None if state is None else state.process_guard


def install():
    """Install the hook once per process; returns the process guard."""

    state = _state()
    if state is not None:
        return state.process_guard
    state = _State()
    original_call = unittest.TestCase.__call__
    original_fixture = _suite.TestSuite._handleModuleFixture
    original_teardown = _suite.TestSuite._handleModuleTearDown

    # __call__ rather than run: IsolatedAsyncioTestCase.run sets up and closes
    # its event loop around TestCase.run, so wrapping the call keeps that pair
    # inside one snapshot.
    def call(self, *args, **kwds):
        result = args[0] if args else kwds.get("result")
        guard = _guard_for(result, bind=False)
        if guard is None:
            return original_call(self, *args, **kwds)
        return guard.around_test(self, lambda: original_call(self, *args, **kwds))

    def handle_module_fixture(self, test, result):
        _guard_for(result, bind=True)
        return original_fixture(self, test, result)

    def handle_module_teardown(self, result):
        guard = _guard_for(result, bind=False)
        module = self._get_previous_module(result)
        original_teardown(self, result)
        if guard is None:
            return
        message = guard.module_boundary(module)
        if message is not None:
            try:
                raise ProcessStateLeak(message)
            except ProcessStateLeak as exc:
                self._createClassOrModuleLevelException(result, exc, "tearDownModule", module)

    call.__wrapped__ = original_call
    handle_module_fixture.__wrapped__ = original_fixture
    handle_module_teardown.__wrapped__ = original_teardown
    unittest.TestCase.__call__ = call
    _suite.TestSuite._handleModuleFixture = handle_module_fixture
    _suite.TestSuite._handleModuleTearDown = handle_module_teardown
    setattr(unittest.TestCase, STATE_ATTRIBUTE, state)
    return state.process_guard


def uninstall():
    """Remove the hook (only the guard's own mutation tests use this)."""

    state = _state()
    if state is None:
        return
    unittest.TestCase.__call__ = unittest.TestCase.__call__.__wrapped__
    _suite.TestSuite._handleModuleFixture = _suite.TestSuite._handleModuleFixture.__wrapped__
    _suite.TestSuite._handleModuleTearDown = _suite.TestSuite._handleModuleTearDown.__wrapped__
    delattr(unittest.TestCase, STATE_ATTRIBUTE)
