"""Failure-only local lane diagnostics. Raw text never enters a receipt.

Instrumentation re-raises the original exception and is inert outside a local
lane scope. Unknown messages are entirely redacted, rather than trusting a
pattern blacklist to recognize every possible credential.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import inspect
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import traceback

ROOT = Path(__file__).resolve().parents[1]
RAW_DIRECTORY = Path.home() / '.local/state/hamcp-lane-failures'
MAX_CHAIN, MAX_FRAMES, MAX_MESSAGE = 4, 8, 160
# Explicitly integrated local lanes; any other name is recorded as 'unknown'.
LANES = frozenset(('helper_value', 'marketplace_read', 'security_metadata', 'device_configuration_entity'))
STAGES = frozenset(('image_verify', 'container_start', 'core_boot', 'auth',
                    'helper_setup', 'operation', 'readback', 'cleanup', 'admission'))
# Code-owned identifiers only: callers cannot echo arbitrary exception metadata.
CHECKS = frozenset(('preflight', 'journal_inputs', 'cached_image_digest', 'host_resources',
    'network_create', 'core_start', 'port_mapping', 'bootstrap', 'onboarding_ready', 'bootstrap_http',
    'runtime_ready', 'upstream_start', 'running_identity', 'helper_contract',
    'authority_setup', 'provider_setup', 'operation_dispatch', 'duplicate_suppression',
    'state_readback', 'value_assertions', 'recovery', 'negative_controls',
    'service_close', 'container_remove', 'network_remove', 'configuration_remove',
    'cleanup_absence', 'install_fixture', 'redaction_canary'))
SAFE_MESSAGES = frozenset((
    'Local container loopback port mapping is missing or unsafe',
    'Local lane requires a dedicated non-internal bridge',
    'Disposable Home Assistant did not become ready within 120 seconds',
    'Disposable Home Assistant did not finish required integration setup',
    'Disposable Home Assistant configuration was not fresh',
    'Disposable Home Assistant did not return an onboarding authorization code',
    'Disposable Home Assistant did not issue an access token',
    'A bounded disposable Docker operation timed out',
    'A bounded disposable Docker operation failed',
    'Exact upstream liveness did not become ready',
    # marketplace_read install fixture reasons (FIXTURE_REASONS); code-owned, no upstream text.
    'github_account_connected', 'disposable_core_not_fresh', 'install_candidate_missing',
    'warning_accept_failed', 'install_failed', 'installed_record_missing', 'more_than_one_installed',
    'installed_record_duplicated', 'installed_record_unprojected', 'installed_record_identity_changed',
    'installed_record_flags_changed', 'installed_record_not_default_catalog', 'installed_version_missing',
    'installed_record_unbounded', 'installed_record_text_changed', 'secret_in_page',
    'redaction_canary_unavailable', 'redaction_canary_leaked', 'redaction_canary_undisclosed',
    'redaction_canary_changed_records',
))
SAFE_TYPES = frozenset(('Exception', 'BaseException', 'RuntimeError', 'AssertionError',
    'ValueError', 'TypeError', 'KeyError', 'OSError', 'PermissionError', 'FileNotFoundError',
    'TimeoutError', 'TimeoutExpired', 'CancelledError', 'Refusal', 'HelperRefusal',
    'ClientError', 'ClientConnectorError', 'ClientResponseError', 'ConnectionRefusedError',
    'FixtureError'))
_active = ContextVar('local_lane_diagnostics', default=None)


@contextmanager
def lane(name):
    # Only explicitly integrated lanes are supported.
    token = _active.set(name if name in LANES else 'unknown')
    try:
        yield
    finally:
        _active.reset(token)


@contextmanager
def check(stage, identifier):
    try:
        yield
    except BaseException as exc:
        if _active.get() and not hasattr(exc, '_local_lane_check'):
            exc._local_lane_check = (
                stage if stage in STAGES else 'unknown',
                identifier if identifier in CHECKS else 'unknown')
        raise


def checked(stage, identifier):
    def decorate(function):
        if inspect.iscoroutinefunction(function):
            @wraps(function)
            async def asynchronous(*args, **kwargs):
                with check(stage, identifier):
                    return await function(*args, **kwargs)
            return asynchronous
        @wraps(function)
        def synchronous(*args, **kwargs):
            with check(stage, identifier):
                return function(*args, **kwargs)
        return synchronous
    return decorate


def _frames(exc, tracked):
    result = []
    for frame in traceback.extract_tb(exc.__traceback__):
        try:
            relative = Path(frame.filename).resolve().relative_to(ROOT).as_posix()
        except ValueError:
            continue
        if relative in tracked and relative != 'scripts/lane_failure_diagnostics.py':
            result.append({'file': relative, 'line': frame.lineno})
    return result[-MAX_FRAMES:]


def _raw_log(exc, extra):
    if os.environ.get('GITHUB_ACTIONS') == 'true':
        return False  # Never retain raw material in Actions/upload territory.
    try:
        directory = RAW_DIRECTORY
        if directory.resolve() != directory.absolute() or directory.resolve().is_relative_to(ROOT):
            return False
        # Refuse any repository, including a different task checkout.
        if any((p / '.git').exists() for p in (directory, *directory.parents)):
            return False
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = directory.stat()
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            return False
        fd, name = tempfile.mkstemp(prefix='failure-', suffix='.log', dir=directory)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            os.fchmod(stream.fileno(), 0o600)
            traceback.print_exception(exc, file=stream)  # no locals; raw text stays here
            if extra is not None:
                try:
                    extra(stream)
                except Exception:
                    stream.write('\nAdditional local diagnostics unavailable.\n')
        return Path(name).is_file()
    except Exception:
        return False  # Diagnostic failure cannot replace the original result.


def failure(exc, *, extra=None):
    origin, visited = exc, set()
    while origin is not None and id(origin) not in visited and len(visited) < MAX_CHAIN:
        visited.add(id(origin))
        if hasattr(origin, '_local_lane_check'):
            break
        origin = origin.__cause__ or (None if origin.__suppress_context__ else origin.__context__)
    stage, identifier = getattr(origin, '_local_lane_check', ('admission', 'preflight'))
    stage = stage if stage in STAGES else 'unknown'
    identifier = identifier if identifier in CHECKS else 'unknown'
    try:
        tracked = set(subprocess.check_output(
            ['git', 'ls-files', '-z', '--', '*.py'], cwd=ROOT, timeout=5
        ).decode().split('\0'))
    except Exception:
        tracked = set()
    chain, seen, current = [], set(), exc
    while current is not None and id(current) not in seen and len(chain) < MAX_CHAIN:
        seen.add(id(current))
        kind = type(current).__name__
        # Exact whole-message allowlist. Do not export a matching prefix/suffix.
        try:
            message = str(current)
        except Exception:
            message = "[redacted]"
        chain.append({'type': kind if kind in SAFE_TYPES else 'Exception',
                      'message': (message if message in SAFE_MESSAGES else '[redacted]')[:MAX_MESSAGE],
                      'frames': _frames(current, tracked)})
        current = current.__cause__ or (None if current.__suppress_context__ else current.__context__)
    return {'lane': _active.get() or 'helper_value', 'stage': stage, 'check': identifier,
            'exception_chain': chain, 'chain_truncated': current is not None,
            'raw_log_retained': _raw_log(exc, extra)}
