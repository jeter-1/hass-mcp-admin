"""Test-only observation of one exact disposable Core; never a shipped component.

CI binds the observer to the support-window Core slot under test by writing
``alarmo_interval_observer: {core_version: <exact version>}``; any other Core
or a missing, partial or extra configuration refuses setup (HAMCP-159).

Hooks observe commands, flow entry/continuation/abort, services and Store save,
queue/write/remove. They never retain command arguments or store contents.
This is an interval observation, not a proof about future arbitrary tasks or
writes that bypass Core's Store API. Fixed persisted-store hashes supplement it.

Setup readiness covers every Core Store instance, not only the hashed ones
(HAMCP-180): Stores alive when the hooks install and every Store a hook later
sees. Only the stores whose background bookkeeping intervals already tolerate
are excluded. Unnamed stores are reported by a class from a fixed
vocabulary (a known integration domain, a known ``core.<name>``, ``core`` or
``unclassified``), never the full key, an identifier suffix, a path or contents.
A refused ``/start`` names one fixed cause and keeps it for ``/ready`` with
the client's ``attempt_id``, so the client attributes only its own attempt.
"""

import asyncio
from contextvars import ContextVar
from functools import wraps
import hashlib
from pathlib import Path
import re
from threading import Lock
import time
from uuid import uuid4
import weakref

DOMAIN = "alarmo_interval_observer"
MODEL = "alarmo-core-interval-v2"
MAX_EVENTS = 128
MAX_SECONDS = 45
COMMANDS = frozenset({"manifest/get", "config_entries/get", "config/entity_registry/get_entries",
    "auth/current_user", "config/device_registry/list", "config/entity_registry/list", "ping",
    "alarmo/config", "alarmo/areas", "alarmo/sensors", "alarmo/entities", "alarmo/sensor_groups"})
CONTROL_COMMANDS = frozenset({DOMAIN + "/ready", DOMAIN + "/start", DOMAIN + "/finish"})
STORE_KEYS = frozenset({"alarmo.storage", "core.config_entries", "core.entity_registry",
                        "core.device_registry", "auth", "core.restore_state", "repairs.issue_registry"})
# Background bookkeeping on Core's own timers that intervals report, not refuse.
# ``repairs.issue_registry`` (HAMCP-219): Core's issue registry Store
# (helpers/issue_registry.py:26, :100-128, identical at the current-slot commit
# abe1fdd7 and the rollback-slot commit 6a811d33) saves through ``async_delay_save`` on a 10 s
# timer (helpers/registry.py:14, :84-89) whenever an issue is created or
# deleted, e.g. by homeassistant_alerts' background update task.
TOLERATED_KEYS = frozenset({"auth", "core.restore_state", "repairs.issue_registry"})
HASH_KEYS = tuple(sorted(STORE_KEYS - TOLERATED_KEYS))
QUIET_SECONDS = 2
MAX_STORE_CLASSES = 16
# The fixed store-class vocabulary (HAMCP-180). Names are the ``core.<name>``
# Store keys and the integration domains that create Stores in the pinned Core
# source (commit abe1fdd7cd6b4de1715c87b6fd87d92699514ef9), plus this lane's Alarmo. A key is
# classified only by an exact vocabulary match, never by its shape.
CORE_STORE_NAMES = frozenset({
    "analytics", "area_registry", "category_registry", "config", "config_entries", "device_registry",
    "entity_registry", "floor_registry", "label_registry", "labs", "logger", "network", "restore_state", "uuid"})
STORE_DOMAINS = frozenset({
    "ai_task", "alarmo", "alexa", "analytics", "application_credentials", "assist_pipeline", "auth_module",
    "auth_provider", "backup", "bluetooth", "broadlink", "camera", "cloud", "counter", "emulated_hue", "energy",
    "esphome", "evohome", "feedreader", "freebox", "frontend", "google", "google_assistant", "hassio",
    "homeassistant", "homeassistant_hardware", "homekit", "homekit_controller", "http", "icloud", "image",
    "image_upload", "input_boolean", "input_button", "input_datetime", "input_number", "input_select",
    "input_text", "knx", "labs", "logger", "lovelace", "mobile_app", "nest", "network", "onboarding", "person",
    "reolink", "repairs", "risco", "roborock", "schedule", "tag", "template", "thread", "timer", "trace", "unifi",
    "vizio", "voip", "zone"})
# Longest first, so ``homekit_controller-entity-map`` is never ``homekit``.
DOMAIN_ORDER = tuple(sorted(STORE_DOMAINS, key=lambda name: (-len(name), name)))
STORE_CLASSES = frozenset({"unclassified", "core", *STORE_KEYS, *("core." + name for name in CORE_STORE_NAMES),
                           *STORE_DOMAINS})
# Each refused /start names exactly one of these; no interval opens.
START_REFUSALS = ("setup_not_settled_late_start", "core_not_running", "hooks_not_intact",
                  "store_hash_unavailable", "interval_unavailable", "start_internal_error")
# A client-generated identifier for one /start attempt; random, never derived from Core.
ATTEMPT_ID = r"[0-9a-f]{32}"
REQUEST = ContextVar("alarmo_observer_request", default=None)


def store_class(key):
    """A Store key's class from the fixed vocabulary; never the key's own text.

    ``core.<name>`` keeps a known name, else ``core``. A known domain counts only
    as the whole key or before ``.``, ``_``, ``-`` or ``/``; its suffix is dropped.
    Anything else, however domain-shaped, is ``unclassified``.
    """
    if type(key) is not str:
        return "unclassified"
    if key in STORE_KEYS:
        return key
    head, dot, rest = key.partition(".")
    if head == "core" and dot:
        return "core." + rest if rest in CORE_STORE_NAMES else "core"
    for domain in DOMAIN_ORDER:
        if key == domain or (key.startswith(domain) and key[len(domain)] in "._-/"):
            return domain
    return "unclassified"


class Refusal(ValueError):
    """A /start refusal with one fixed cause from ``START_REFUSALS``."""

    def __init__(self, cause):
        super().__init__(cause)
        self.cause = cause


def store_pending(store):
    """Readiness bits only: a scheduled or in-progress Core write, never its data."""
    return store._write_lock.locked() or (store._data is not None and (
        store._delay_handle is not None or store._unsub_final_write_listener is not None))


class Ledger:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = Lock()
        self.active = None
        # Every Store seen, keyed by identity; weak so retired Stores drop out.
        self.stores = weakref.WeakValueDictionary()
        self.last_activity = None
        # The most recent /start refusal with its attempt, cleared by a started interval.
        self.last_start_refusal = None

    def track(self, store):
        with self.lock:
            self.stores[id(store)] = store

    def record(self, kind, *, command=None, store=None):
        with self.lock:
            if store is not None:
                self.stores[id(store)] = store
                if store.key not in TOLERATED_KEYS:
                    self.last_activity = (self.clock(), store_class(store.key))
            current = self.active
            if current is None:
                return
            if self.clock() - current["started"] > MAX_SECONDS:
                current["expired"] = True
            if len(current["events"]) >= MAX_EVENTS:
                current["overflow"] = True
                return
            event = {"kind": kind, "origin": "command" if REQUEST.get() else "background"}
            if command is not None:
                event["command"] = command if command in COMMANDS else "other"
            if store is not None:
                event["store"] = store.key if store.key in STORE_KEYS else "other"
                if event["store"] == "other":
                    event["store_class"] = store_class(store.key)
            current["events"].append(event)

    def pending_store_classes(self):
        """Sorted classes of untolerated stores with a pending or recent write."""
        with self.lock:
            classes = {store_class(store.key) for store in list(self.stores.values())
                       if store.key not in TOLERATED_KEYS and store_pending(store)}
            if self.last_activity is not None and self.clock() - self.last_activity[0] < QUIET_SECONDS:
                classes.add(self.last_activity[1])
        return sorted(classes)

    def pending_setup_storage(self):
        return bool(self.pending_store_classes())

    def refuse_start(self, cause, classes, attempt_id):
        refusal = {"attempt_id": attempt_id if type(attempt_id) is str and re.fullmatch(ATTEMPT_ID, attempt_id)
                   else "0" * 32,
                   "cause": cause if cause in START_REFUSALS else "start_internal_error",
                   "pending_store_classes": list(classes)[:MAX_STORE_CLASSES]}
        with self.lock:
            self.last_start_refusal = refusal
        return refusal

    def start_refusal(self):
        with self.lock:
            value = self.last_start_refusal
        return None if value is None else {**value, "pending_store_classes": list(value["pending_store_classes"])}

    def start(self, kind, hashes):
        with self.lock:
            if self.active is not None or kind not in {"inspection", "control"}:
                raise ValueError("observer interval unavailable")
            self.active = {"interval_id": uuid4().hex, "kind": kind, "started": self.clock(),
                           "events": [], "overflow": False, "expired": False,
                           "baseline_store_hashes": hashes}
            self.last_start_refusal = None
            return self.active["interval_id"]

    def finish(self, interval_id, hashes, coverage):
        with self.lock:
            if self.active is None or self.active["interval_id"] != interval_id:
                raise ValueError("observer interval mismatch")
            value, self.active = self.active, None
            elapsed = self.clock() - value.pop("started")
            value.update(model=MODEL, elapsed_seconds=elapsed,
                         expired=value["expired"] or elapsed > MAX_SECONDS,
                         final_store_hashes=hashes, coverage=coverage)
            return value


def observe_sync(ledger, kind, original):
    @wraps(original)
    def wrapped(obj, *args, **kwargs):
        if kind == "command":
            message = args[0] if args else None
            name = message.get("type") if isinstance(message, dict) else None
            if isinstance(name, str) and name in CONTROL_COMMANDS:
                return original(obj, *args, **kwargs)
            safe = name if isinstance(name, str) and name in COMMANDS else "other"
            ledger.record("command", command=safe)
            token = REQUEST.set(safe)
            try:
                return original(obj, *args, **kwargs)
            finally:
                REQUEST.reset(token)
        ledger.record(kind, store=obj if kind.startswith("storage_") else None)
        return original(obj, *args, **kwargs)
    return wrapped


def observe_async(ledger, kind, original):
    @wraps(original)
    async def wrapped(obj, *args, **kwargs):
        ledger.record(kind, store=obj if kind.startswith("storage_") else None)
        return await original(obj, *args, **kwargs)
    return wrapped


def store_hashes(config_directory):
    result = {}
    for key in HASH_KEYS:
        path = Path(config_directory) / ".storage" / key
        if path.is_symlink():
            raise ValueError("observer store symlink refused")
        if not path.exists():
            result[key] = None
            continue
        if path.stat().st_size > 2 * 1024 * 1024:
            raise ValueError("observer store exceeds bound")
        with path.open("rb") as stream:
            raw = stream.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError("observer store exceeds bound")
        result[key] = hashlib.sha256(raw).hexdigest()
    return result


async def async_setup(hass, config):
    import gc
    import voluptuous as vol
    from homeassistant.const import __version__, EVENT_HOMEASSISTANT_STOP
    from homeassistant.core import CoreState, ServiceRegistry, callback
    from homeassistant.config_entries import ConfigEntriesFlowManager
    from homeassistant.data_entry_flow import FlowManager
    from homeassistant.helpers.storage import Store
    from homeassistant.components import websocket_api
    from homeassistant.components.websocket_api.connection import ActiveConnection

    if config.get(DOMAIN) != {"core_version": __version__} or DOMAIN in hass.data:
        raise ValueError("observer requires its exact disposable Core revision")
    ledger = Ledger()
    hass.data[DOMAIN] = ledger
    specifications = (
        (ActiveConnection, "async_handle", "command", False),
        (ConfigEntriesFlowManager, "async_init", "config_flow_init", True),
        (FlowManager, "async_init", "flow_init", True),
        (FlowManager, "async_configure", "flow_configure", True),
        (FlowManager, "async_abort", "flow_abort", False),
        (ServiceRegistry, "async_call", "service", True),
        (Store, "async_save", "storage_save", True),
        (Store, "async_delay_save", "storage_delay_save", False),
        (Store, "_async_write_data", "storage_write", True),
        (Store, "async_remove", "storage_remove", True),
    )
    hooks = []
    for cls, name, kind, asynchronous in specifications:
        original = getattr(cls, name)
        wrapper = (observe_async if asynchronous else observe_sync)(ledger, kind, original)
        setattr(cls, name, wrapper)
        hooks.append((cls, name, original, wrapper))
    # Stores created before the hooks may already hold a scheduled write.
    for candidate in gc.get_objects():
        if issubclass(type(candidate), Store):
            ledger.track(candidate)

    def coverage():
        return {cls.__name__ + "." + name: getattr(cls, name) is wrapper
                for cls, name, _original, wrapper in hooks}

    @callback
    def restore(_event):
        for cls, name, original, wrapper in reversed(hooks):
            if getattr(cls, name) is wrapper:
                setattr(cls, name, original)
    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, restore)

    @websocket_api.websocket_command({vol.Required("type"): DOMAIN + "/ready"})
    @websocket_api.require_admin
    @callback
    def ready(_hass, connection, message):
        # Setup only: fixed booleans and bounded store classes, no forced save
        # and no stored values.
        pending = ledger.pending_store_classes()
        connection.send_result(message["id"], {
            "core_running": hass.state is CoreState.running,
            "storage_pending": bool(pending),
            "pending_store_classes": pending[:MAX_STORE_CLASSES],
            "hooks_intact": all(coverage().values()),
            "last_start_refusal": ledger.start_refusal(),
        })

    @websocket_api.websocket_command({vol.Required("type"): DOMAIN + "/start",
                                     vol.Required("kind"): vol.In(["inspection", "control"]),
                                     vol.Required("attempt_id"): vol.Match("^" + ATTEMPT_ID + r"\Z")})
    @websocket_api.require_admin
    @websocket_api.async_response
    async def start(_hass, connection, message):
        # Every refusal is one fixed cause, sent as the error code and kept for
        # /ready with the classes pending at that moment and this request's
        # attempt_id (HAMCP-180).
        classes = []
        try:
            deadline = time.monotonic() + 20
            while classes := ledger.pending_store_classes():
                if time.monotonic() >= deadline:
                    raise Refusal("setup_not_settled_late_start")
                await asyncio.sleep(0.1)
            if hass.state is not CoreState.running:
                raise Refusal("core_not_running")
            if not all(coverage().values()):
                raise Refusal("hooks_not_intact")
            try:
                hashes = await hass.async_add_executor_job(store_hashes, hass.config.config_dir)
            except ValueError:
                raise Refusal("store_hash_unavailable") from None
            try:
                identity = ledger.start(message["kind"], hashes)
            except ValueError:
                raise Refusal("interval_unavailable") from None
        except Refusal as refusal:
            cause = refusal.cause
        except Exception:  # cancellation is not an Exception and propagates
            cause = "start_internal_error"
        else:
            connection.send_result(message["id"], {"model": MODEL, "interval_id": identity})
            return
        refused = ledger.refuse_start(cause, classes, message.get("attempt_id"))
        connection.send_error(message["id"], refused["cause"], "Observer refused interval")

    @websocket_api.websocket_command({vol.Required("type"): DOMAIN + "/finish",
                                     vol.Required("interval_id"): vol.Match(r"^[0-9a-f]{32}$")})
    @websocket_api.require_admin
    @websocket_api.async_response
    async def finish(_hass, connection, message):
        try:
            hashes = await hass.async_add_executor_job(store_hashes, hass.config.config_dir)
            result = ledger.finish(message["interval_id"], hashes, coverage())
            result.update(core_version=__version__, observer_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
            connection.send_result(message["id"], result)
        except ValueError:
            connection.send_error(message["id"], "observer_refused", "Observer refused interval")

    websocket_api.async_register_command(hass, ready)
    websocket_api.async_register_command(hass, start)
    websocket_api.async_register_command(hass, finish)
    return True
