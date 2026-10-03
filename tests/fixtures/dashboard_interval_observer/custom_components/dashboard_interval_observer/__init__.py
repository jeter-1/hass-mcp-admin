"""Test-only observation of exact Core 2026.9.4; never a shipped component.

Hooks observe commands, flow entry/continuation/abort, services and Store save,
queue/write/remove. They never retain command arguments or store contents.
This is an interval observation, not a proof about future arbitrary tasks or
writes that bypass Core's Store API. Fixed persisted-store hashes supplement it.
"""

import asyncio
from contextvars import ContextVar
from functools import wraps
import hashlib
from pathlib import Path
from threading import Lock
import time
from uuid import uuid4

DOMAIN = "dashboard_interval_observer"
MODEL = "dashboard-core-interval-v1"
MAX_EVENTS = 128
MAX_SECONDS = 45
COMMANDS = frozenset({"lovelace/config", "manifest/get", "config_entries/get", "config/entity_registry/get_entries",
    "auth/current_user", "config/device_registry/list", "config/entity_registry/list", "ping",
    "alarmo/config", "alarmo/areas", "alarmo/sensors", "alarmo/entities", "alarmo/sensor_groups"})
CONTROL_COMMANDS = frozenset({DOMAIN + "/ready", DOMAIN + "/start", DOMAIN + "/finish"})
STORE_KEYS = frozenset({"alarmo.storage", "core.config_entries", "core.entity_registry",
                        "core.device_registry", "auth", "core.restore_state"})
HASH_KEYS = tuple(sorted(STORE_KEYS - {"auth", "core.restore_state"}))
REQUEST = ContextVar("alarmo_observer_request", default=None)


class Ledger:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = Lock()
        self.active = None
        self.stores = {}

    def record(self, kind, *, command=None, store=None):
        with self.lock:
            if store is not None and store.key in HASH_KEYS:
                self.stores[store.key] = store
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
            current["events"].append(event)

    def pending_setup_storage(self):
        # Only readiness bits; never inspect the pending data or callbacks.
        with self.lock:
            return any(store._data is not None or store._write_lock.locked()
                       for store in self.stores.values())

    def start(self, kind, hashes):
        with self.lock:
            if self.active is not None or kind not in {"inspection", "control"}:
                raise ValueError("observer interval unavailable")
            self.active = {"interval_id": uuid4().hex, "kind": kind, "started": self.clock(),
                           "events": [], "overflow": False, "expired": False,
                           "baseline_store_hashes": hashes}
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


async def async_setup(hass, _config):
    import voluptuous as vol
    from homeassistant.const import __version__, EVENT_HOMEASSISTANT_STOP
    from homeassistant.core import CoreState, ServiceRegistry, callback
    from homeassistant.config_entries import ConfigEntriesFlowManager
    from homeassistant.data_entry_flow import FlowManager
    from homeassistant.helpers.storage import Store
    from homeassistant.components import websocket_api
    from homeassistant.components.websocket_api.connection import ActiveConnection

    if __version__ != "2026.9.4" or DOMAIN in hass.data:
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
        # Setup only: fixed booleans, no forced save and no stored values.
        connection.send_result(message["id"], {
            "core_running": hass.state is CoreState.running,
            "storage_pending": ledger.pending_setup_storage(),
            "hooks_intact": all(coverage().values()),
        })

    @websocket_api.websocket_command({vol.Required("type"): DOMAIN + "/start",
                                     vol.Required("kind"): vol.In(["inspection", "control"])})
    @websocket_api.require_admin
    @websocket_api.async_response
    async def start(_hass, connection, message):
        try:
            deadline = time.monotonic() + 20
            while ledger.pending_setup_storage():
                if time.monotonic() >= deadline:
                    raise ValueError("disposable setup storage is not settled")
                await asyncio.sleep(0.1)
            if hass.state is not CoreState.running or not all(coverage().values()):
                raise ValueError("observer hooks changed")
            hashes = await hass.async_add_executor_job(store_hashes, hass.config.config_dir)
            identity = ledger.start(message["kind"], hashes)
            connection.send_result(message["id"], {"model": MODEL, "interval_id": identity})
        except ValueError:
            connection.send_error(message["id"], "observer_refused", "Observer refused interval")

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
