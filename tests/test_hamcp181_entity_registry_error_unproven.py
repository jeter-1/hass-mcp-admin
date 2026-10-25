"""HAMCP-181 R3-1: an entity-registry error result is never read as no-mutation.

This is the required regression for the withdrawn P2.  Home Assistant Core can
assign the new entity-registry entry and then answer
``config/entity_registry/update`` with an error result.  Engineering must
never report such a result as "nothing changed".

What the module proves, and how:

1. **Engineering end to end.**  A loopback WebSocket peer answers the update
   command with the exact message produced by the Core model of item 2: the
   synthetic registry entry is changed first, then the error result is sent.
   The command crosses the real ``HomeAssistantWebSocketClient``, the real
   entity-metadata gateway, the configuration adapter, the F3 executor and
   the shared dispatch-facts projection.  Every client surface (the apply
   result, its ``execution_task`` and nested ``plan.execution_task``,
   ``get_execution_task``, the ``list_execution_tasks`` row,
   ``get_change_plan`` and the ``list_change_plans`` row) reports a send,
   ``changed_by_engineering`` null, no ``provider_rejected_mutation``
   outcome and no not-sent proof.

   **Divergence from the design row:** the row expects
   ``dispatch_phase:"sent_failed_mutation_unproven"`` for a live error result.
   Current source records the result as HAMCP-183 (OD-15) specifies it: the
   uncertainty codes ``provider_error_after_send`` and
   ``provider_response_indeterminate`` are recorded, and the projection then
   reports the more uncertain ``intent_recorded_send_uncertain`` (c).  In
   phase (c) ``changed_by_engineering`` is omitted rather than published as
   null.  The tests pin that phase as ``LIVE_ERROR_RESULT_PHASE``.  They also
   assert the invariant the row exists for: the phase is never less uncertain
   than (r-unproven), and no field claims non-mutation.  Phase
   ``sent_failed_mutation_unproven`` itself is exercised by the beta.8 record
   and by the projection controls.

2. **Offline Core reproduction on 2026.10.0 and 2026.10.1.**  Hash-pinned
   excerpts of Core's ``_async_update_entity``, ``async_update_entity``,
   ``websocket_update_entity``, ``ActiveConnection.async_handle``,
   ``async_handle_exception``, ``require_admin`` and the ``ERR_*`` constants
   are checked against ``provenance.json`` before any use.  They are then:

   * asserted structurally with ``ast``:
     - the assignment comes before save scheduling;
     - save scheduling comes before the update event;
     - ``_async_update_entity`` contains no ``try``;
     - the handler catches only ``ValueError``;
     - the dispatcher maps any other exception to ``unknown_error`` by default;
     - ``require_admin`` raises before the handler runs.
   * executed against small stubs.  This is an executed model, not Core.
     The dedented excerpt functions run unmodified.  Every name they import
     from Home Assistant is replaced by a stub defined in this module:
     - ``attr.evolve``;
     - the ``RegistryEntry`` type;
     - the store, the bus and the config-entry, device and label registries;
     - the probatio schema;
     - ``messages.error_message``;
     - the ``Unauthorized`` and ``HomeAssistantError`` types;
     - ``async_redact_data``.

     No ``homeassistant`` module is imported.

   Proven: with Core's own control flow, a fault in save scheduling, in the
   update event, or after the registry update returned (result serialization)
   leaves the registry changed and answers ``unknown_error``.  A non-admin
   caller gets ``unauthorized`` before the handler runs and leaves the
   registry unchanged.

   Not proven: that the real ``Store.async_delay_save``, the real event bus or
   the real ``extended_dict`` can raise in a deployed Core.  Nor any behavior
   of the stubbed dependencies.  The model shows only what Core's code does
   *if* they raise.

3. **Controls.**
   * Every fault class above, and a generic ``unknown_error`` with no fault
     model, is not-disproven on every surface.
   * The P3 ``unauthorized`` result stays (r-proven): phase
     ``sent_rejected_no_mutation`` with ``changed_by_engineering`` false.
   * A historical beta.8 entity-registry error-result record projects
     (r-unproven).  It carries ``provider_rejected_mutation`` without the
     permission-denied reason.  It was written by the exact beta.8 writer
     (``tests/fixtures/hamcp181_beta8_entity_registry_error``) and is never
     rewritten.

No test infers non-mutation from an error result.  The registry state used
for that question always comes from the synthetic registry or from the
executed model.  Everything is synthetic and offline.  The only network use
is a ``127.0.0.1`` loopback peer.
"""

from __future__ import annotations

import __future__
import ast
from copy import deepcopy
import dataclasses
from datetime import datetime, timezone
import functools
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import textwrap
from types import SimpleNamespace
from typing import Any, Final
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT)]

from ha_mcp_engineering.clients.websocket import (  # noqa: E402
    HomeAssistantWebSocketClient,
)
from ha_mcp_engineering.f3_runtime import dispatch_facts as df  # noqa: E402

import tests.test_hamcp181_dispatch_facts as dispatch_facts_tests  # noqa: E402
from tests.test_hamcp181_dispatch_facts import FakeRecord  # noqa: E402
from tests.test_hamcp181_proven_no_call_producers import (  # noqa: E402
    _loopback_settings,
    _ProofAssertions,
    all_codes,
)
from tests.test_hamcp183_entity_registry_error_truth import (  # noqa: E402
    ERROR_DIAGNOSTIC,
    REGISTRY_GET,
    UPDATE,
    CoreShapedRegistryPeer,
    LoopbackRegistryTestCase,
)


EXCERPTS = ROOT / "tests" / "fixtures" / "hamcp181_core_entity_registry_excerpts"
BETA8_REGISTRY_ERROR = ROOT / "tests" / "fixtures" / "hamcp181_beta8_entity_registry_error"
BETA8_SHAPE = "r_unproven_entity_registry_error_after_assignment"
BETA8_SOURCE = "7370ad25eb98e03347d73e152f38881895477d74"

CORE_REVISIONS: dict[str, tuple[str, str]] = {
    "core_2026_10_0": ("2026.10.0", "6a811d3359c7b2076dc9e1cf900843a129c044af"),
    "core_2026_10_1": ("2026.10.1", "abe1fdd7cd6b4de1715c87b6fd87d92699514ef9"),
}
PRIVATE_UPDATE = "entity_registry_async_update_entity_private.txt"
PUBLIC_UPDATE = "entity_registry_async_update_entity_public.txt"
HANDLER = "config_entity_registry_websocket_update_entity.txt"
DISPATCH = "websocket_api_connection_async_handle.txt"
HANDLE_EXCEPTION = "websocket_api_connection_async_handle_exception.txt"
REQUIRE_ADMIN = "websocket_api_decorators_require_admin.txt"
CONST = "websocket_api_const_error_codes.txt"
EXCERPT_NAMES = (
    PRIVATE_UPDATE, PUBLIC_UPDATE, HANDLER, DISPATCH, HANDLE_EXCEPTION, REQUIRE_ADMIN, CONST,
)

# Fault points in Core's update path (item 2).  Each one raises after the
# registry entry was assigned.
SAVE_SCHEDULING = "save_scheduling"
UPDATE_EVENT = "update_event"
POST_ASSIGNMENT_RESULT = "post_assignment_result_serialization"
POST_ASSIGNMENT_FAULTS = (SAVE_SCHEDULING, UPDATE_EVENT, POST_ASSIGNMENT_RESULT)

# What the projection reports today for a live error result after send
# (HAMCP-183 OD-15 codes, phase (c)).  The design row's expectation was
# ``sent_failed_mutation_unproven``; see the module docstring.
LIVE_ERROR_RESULT_PHASE = df.PHASE_SEND_UNCERTAIN
NON_MUTATION_TEXTS = {df.REJECTED_NO_MUTATION_TEXT, df.NOT_SENT_TEXT, df.ALREADY_PRESENT_TEXT}
NON_MUTATION_CODES = {
    df.PROVIDER_REJECTED_MUTATION, df.PROVIDER_PERMISSION_DENIED,
    df.PROVIDER_CALL_NOT_SENT, df.SETTER_CALL_NOT_SENT, "provider_confirmed_no_mutation",
}


# -- hash-pinned Core excerpts -----------------------------------------------------


class ExcerptHashMismatch(AssertionError):
    """A Core excerpt does not match its pinned SHA-256; it is never used."""


def load_excerpts(revision: str, root: Path = EXCERPTS) -> dict[str, str]:
    """Return the revision's excerpts after verifying every pinned hash."""

    provenance = json.loads((root / "provenance.json").read_text(encoding="utf-8"))
    entry = provenance["revisions"][revision]
    if set(entry["excerpts"]) != set(EXCERPT_NAMES):
        raise ExcerptHashMismatch(f"{revision}: unexpected excerpt set")
    texts = {}
    for name, meta in entry["excerpts"].items():
        data = (root / revision / name).read_bytes()
        if hashlib.sha256(data).hexdigest() != meta["sha256"]:
            raise ExcerptHashMismatch(f"{revision}/{name}: SHA-256 mismatch")
        texts[name] = data.decode("utf-8")
    return texts


def parse_function(text: str) -> ast.FunctionDef:
    (node,) = ast.parse(textwrap.dedent(text)).body
    assert isinstance(node, ast.FunctionDef), type(node)
    return node


def _exec_excerpt(text: str, name: str, namespace: dict[str, Any]) -> None:
    # Annotations stay unevaluated, so Core's type names need no stub.
    code = compile(
        textwrap.dedent(text), f"<core excerpt {name}>", "exec",
        flags=__future__.annotations.compiler_flag, dont_inherit=True,
    )
    exec(code, namespace)  # noqa: S102 - pinned, hash-verified Core excerpt


# -- executed model of Core's update path ---------------------------------------


_REGISTRY_FIELDS = (
    "aliases", "area_id", "categories", "capabilities", "config_entry_id",
    "config_subentry_id", "device_class", "device_id", "disabled_by",
    "entity_category", "hidden_by", "icon", "has_entity_name", "labels", "name",
    "object_id_base", "options", "original_device_class", "original_icon",
    "original_name", "original_name_unprefixed", "platform", "suggested_object_id",
    "supported_features", "translation_key", "unit_of_measurement",
)


class CoreUpdateModel:
    """Core's ``config/entity_registry/update`` path, run from the excerpts.

    The excerpt functions execute unmodified.  The registry, store, bus,
    schema, message builder and exception types are stubs (see the module
    docstring for what that does and does not prove).  ``faults`` raises
    ``exception`` at the named point; ``admin`` selects the caller's role.
    ``area_without_name`` makes Core's own pre-assignment area/name check
    raise its ``ValueError``.
    """

    def __init__(
        self,
        revision: str,
        entry: dict[str, Any],
        *,
        faults: tuple[str, ...] = (),
        exception: Exception | None = None,
        admin: bool = True,
        area_without_name: bool = False,
        known_labels: tuple[str, ...] = (),
    ) -> None:
        texts = load_excerpts(revision)
        model = self
        self.revision = revision
        self.faults = set(faults)
        self.exception = exception or RuntimeError("synthetic fault after assignment")
        self.saves_attempted = 0
        self.events_fired: list[tuple[str, dict[str, Any]]] = []
        self.handler_entered = False
        self.sent: list[dict[str, Any]] = []
        self.logged: list[str] = []

        class HomeAssistantError(Exception):
            translation_domain = None
            translation_key = None
            translation_placeholders = None

        class Unauthorized(HomeAssistantError):
            pass

        class Invalid(Exception):
            pass

        self.HomeAssistantError = HomeAssistantError
        self.Unauthorized = Unauthorized

        def fault(point: str) -> None:
            if point in model.faults:
                raise model.exception

        @dataclasses.dataclass
        class RegistryEntry:
            entity_id: str
            unique_id: str
            previous_unique_id: str | None = None
            modified_at: Any = None
            aliases: Any = None
            area_id: Any = None
            categories: Any = None
            capabilities: Any = None
            config_entry_id: Any = None
            config_subentry_id: Any = None
            device_class: Any = None
            device_id: Any = None
            disabled_by: Any = None
            entity_category: Any = None
            hidden_by: Any = None
            icon: Any = None
            has_entity_name: Any = False
            labels: Any = None
            name: Any = None
            object_id_base: Any = None
            options: Any = None
            original_device_class: Any = None
            original_icon: Any = None
            original_name: Any = None
            original_name_unprefixed: Any = None
            platform: Any = "synthetic"
            suggested_object_id: Any = None
            supported_features: Any = 0
            translation_key: Any = None
            unit_of_measurement: Any = None

            @property
            def domain(self) -> str:
                return self.entity_id.split(".", 1)[0]

            @property
            def extended_dict(self) -> dict[str, Any]:
                fault(POST_ASSIGNMENT_RESULT)
                value = {name: getattr(self, name) for name in ("entity_id", *_REGISTRY_FIELDS)}
                value["labels"] = sorted(value["labels"] or ())
                return value

        values = {name: deepcopy(entry.get(name)) for name in _REGISTRY_FIELDS if name in entry}
        values["labels"] = set(entry.get("labels") or ())
        values["categories"] = dict(entry.get("categories") or {})
        self.original = RegistryEntry(
            entity_id=entry["entity_id"], unique_id=entry.get("id", entry["entity_id"]), **values)

        class Bus:
            def async_fire_internal(self, event_type: str, data: dict[str, Any]) -> None:
                model.events_fired.append((event_type, deepcopy(data)))
                fault(UPDATE_EVENT)

        hass = SimpleNamespace(
            bus=Bus(),
            verify_event_loop_thread=lambda _what: None,
            config_entries=SimpleNamespace(async_get_entry=lambda _entry_id: None),
        )

        class Registry:
            def __init__(self) -> None:
                self.hass = hass
                self.entities = {model.original.entity_id: model.original}

            def async_get(self, entity_id: str) -> Any:
                return self.entities.get(entity_id)

            def _ignore_composite_device_id(self, _platform: Any, device_id: Any) -> Any:
                return device_id

            def _entity_id_available(self, entity_id: str) -> bool:
                return entity_id not in self.entities

            def async_get_entity_id(self, *_args: Any) -> None:
                return None

            def async_schedule_save(self) -> None:
                model.saves_attempted += 1
                fault(SAVE_SCHEDULING)

            def async_update_entity_options(self, *_args: Any) -> Any:
                raise AssertionError("Engineering never sends options_domain")

        def error_message(iden, code, message, translation_domain=None,
                          translation_key=None, translation_placeholders=None):
            return {"id": iden, "type": "result", "success": False,
                    "error": {"code": code, "message": message}}

        const_namespace: dict[str, Any] = {"Final": Final}
        _exec_excerpt(texts[CONST], CONST, const_namespace)
        const = SimpleNamespace(**{key: value for key, value in const_namespace.items()
                                   if key.startswith("ERR_")})
        self.const = const
        stub_enum = SimpleNamespace(
            USER=SimpleNamespace(value="user"), CONFIG_ENTRY="config_entry",
            INTEGRATION="integration")
        registry = Registry()
        self.registry = registry
        namespace: dict[str, Any] = {
            "__name__": f"core_excerpt_model_{revision}",
            "callback": lambda function: function,
            "wraps": functools.wraps,
            "TYPE_CHECKING": False,
            "UNDEFINED": object(),
            "attr": SimpleNamespace(evolve=lambda inst, **changes: dataclasses.replace(inst, **changes)),
            "utcnow": lambda: datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc),
            "_validate_item": lambda *_args, **_kwargs: None,
            "_unprefix_original_name": lambda _hass, name, *_args: name,
            "_has_own_area_without_own_name": lambda _hass, _new: area_without_name,
            "valid_entity_id": lambda entity_id: "." in entity_id,
            "split_entity_id": lambda entity_id: entity_id.split(".", 1),
            "RegistryEntryDisabler": stub_enum,
            "EVENT_ENTITY_REGISTRY_UPDATED": "entity_registry_updated",
            "er": SimpleNamespace(
                async_get=lambda _hass: registry, RegistryEntryDisabler=stub_enum,
                RegistryEntryHider=stub_enum, COMPUTED_NAME=object()),
            "lr": SimpleNamespace(
                async_get_missing_label_ids=lambda _hass, labels: set(labels) - set(known_labels)),
            "dr": SimpleNamespace(async_get=lambda _hass: SimpleNamespace(async_get=lambda _id: None)),
            "config_entries": SimpleNamespace(RELOAD_AFTER_UPDATE_DELAY=30),
            "cv": SimpleNamespace(entity_id=str, schema_with_slug_keys=lambda value: value),
            "probatio": SimpleNamespace(
                Required=lambda key: key, Optional=lambda key: key,
                Inclusive=lambda key, _group: key, Any=lambda *values: values,
                All=lambda *values: values, Coerce=lambda kind: kind, Invalid=Invalid),
            "websocket_api": SimpleNamespace(
                websocket_command=lambda _schema: (lambda function: function),
                error_message=error_message),
            "ERR_NOT_FOUND": const.ERR_NOT_FOUND,
            "messages": SimpleNamespace(error_message=error_message),
            "const": const,
            "Unauthorized": Unauthorized,
            "HomeAssistantError": HomeAssistantError,
            "humanize_error": lambda _msg, err: str(err),
            "async_redact_data": lambda msg, _keys: msg,
            "REDACT_KEYS": frozenset(),
            "current_request": SimpleNamespace(get=lambda: None),
        }
        for name in (REQUIRE_ADMIN, PRIVATE_UPDATE, PUBLIC_UPDATE, DISPATCH, HANDLE_EXCEPTION):
            _exec_excerpt(texts[name], name, namespace)
        Registry._async_update_entity = namespace["_async_update_entity"]
        Registry.async_update_entity = namespace["async_update_entity"]
        _exec_excerpt(texts[HANDLER], HANDLER, namespace)
        core_handler = namespace["websocket_update_entity"]
        self.handler_wrapped_by_require_admin = core_handler.__wrapped__ is not core_handler

        class Connection:
            def __init__(self) -> None:
                self.hass = hass
                self.user = SimpleNamespace(is_admin=admin)
                self.last_id = 0
                self.handlers = {UPDATE: (core_handler, lambda msg: msg)}
                self.logger = SimpleNamespace(
                    error=lambda *args: model.logged.append(str(args[0])),
                    info=lambda *args: model.logged.append(str(args[0])),
                    exception=lambda *args: model.logged.append(str(args[0])),
                )

            def send_message(self, message: dict[str, Any]) -> None:
                model.sent.append(message)

            def send_result(self, msg_id: int, result: Any = None) -> None:
                model.sent.append({"id": msg_id, "type": "result", "success": True,
                                   "result": result})

            def get_description(self, _request: Any) -> str:
                return "synthetic model connection"

        Connection.async_handle = namespace["async_handle"]
        Connection.async_handle_exception = namespace["async_handle_exception"]
        self.connection = Connection()

        original_update = Registry.async_update_entity

        def tracking_update(registry_self, *args, **kwargs):
            model.handler_entered = True
            return original_update(registry_self, *args, **kwargs)

        Registry.async_update_entity = tracking_update

    def handle(self, command: dict[str, Any]) -> dict[str, Any]:
        """Dispatch one command as Core's connection does; return its one answer."""

        before = len(self.sent)
        self.connection.async_handle({"id": 1, **deepcopy(command)})
        (answer,) = self.sent[before:]
        return answer

    @property
    def stored(self) -> Any:
        return self.registry.entities[self.original.entity_id]

    @property
    def assigned(self) -> bool:
        return self.stored is not self.original


def mirror_into(entry: dict[str, Any], model: CoreUpdateModel, command: dict[str, Any]) -> None:
    """Copy the fields the model actually stored into the synthetic registry."""

    stored = model.stored
    for field_name in command:
        if field_name in {"type", "entity_id"}:
            continue
        value = getattr(stored, field_name)
        entry[field_name] = sorted(value) if isinstance(value, set) else deepcopy(value)
    entry["modified_at"] = float(entry["modified_at"]) + 1


def porch_entry(**changes: Any) -> dict[str, Any]:
    value = {"entity_id": "sensor.porch", "id": "synthetic-registry-id-porch",
             "name": None, "icon": "mdi:old", "labels": ["night"], "categories": {},
             "area_id": None, "modified_at": 1.0}
    value.update(changes)
    return value


PORCH_COMMAND = {"type": UPDATE, "entity_id": "sensor.porch",
                 "name": "Porch sensor", "icon": "mdi:new"}


# -- (2) the Core excerpts themselves ----------------------------------------------


class CoreExcerptProvenanceTests(unittest.TestCase):
    """Each excerpt matches its pinned hash and its recorded source."""

    def test_every_excerpt_matches_its_pinned_hash_and_source(self):
        provenance = json.loads((EXCERPTS / "provenance.json").read_text(encoding="utf-8"))
        self.assertEqual(provenance["retrieved"], "2026-10-25")
        self.assertEqual(set(provenance["revisions"]), set(CORE_REVISIONS))
        for revision, (version, commit) in CORE_REVISIONS.items():
            with self.subTest(revision=revision):
                entry = provenance["revisions"][revision]
                self.assertEqual((entry["core_version"], entry["commit"]), (version, commit))
                texts = load_excerpts(revision)
                for name, meta in entry["excerpts"].items():
                    self.assertIn(f"/home-assistant/core/{commit}/{meta['path']}",
                                  meta["source_url"])
                    lines = texts[name].splitlines()
                    self.assertEqual(len(lines), meta["last_line"] - meta["first_line"] + 1)
                    self.assertTrue(texts[name].endswith("\n"))
                    self.assertIn(meta["path"], entry["full_file_sha256"])

    def test_a_tampered_excerpt_is_refused_before_use(self):
        with tempfile.TemporaryDirectory() as temp:
            copy = Path(temp) / "excerpts"
            shutil.copytree(EXCERPTS, copy)
            target = copy / "core_2026_10_0" / PRIVATE_UPDATE
            # Simulate a Core change that wraps the save in a try block.
            target.write_text(target.read_text(encoding="utf-8").replace(
                "self.async_schedule_save()", "pass"), encoding="utf-8")
            with self.assertRaises(ExcerptHashMismatch):
                load_excerpts("core_2026_10_0", copy)
            load_excerpts("core_2026_10_1", copy)


class _CoreRevisionCases:
    """Structural and executed-model assertions for one pinned Core revision."""

    REVISION: str

    def excerpt(self, name: str) -> ast.FunctionDef:
        return parse_function(load_excerpts(self.REVISION)[name])

    @staticmethod
    def _top_level_index(function: ast.FunctionDef, predicate) -> int:
        matches = [index for index, node in enumerate(function.body) if predicate(node)]
        assert len(matches) == 1, matches
        return matches[0]

    # -- structural (ast) ------------------------------------------------------

    def test_registry_assigns_before_save_and_event_without_any_try(self):
        function = self.excerpt(PRIVATE_UPDATE)
        self.assertEqual(function.name, "_async_update_entity")
        self.assertFalse([node for node in ast.walk(function) if isinstance(node, ast.Try)])
        assign = self._top_level_index(function, lambda node: (
            isinstance(node, ast.Assign)
            and ast.unparse(node.targets[0]) == "self.entities[entity_id]"))
        save = self._top_level_index(function, lambda node: (
            isinstance(node, ast.Expr) and ast.unparse(node) == "self.async_schedule_save()"))
        event = self._top_level_index(function, lambda node: (
            isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
            and ast.unparse(node.value.func) == "self.hass.bus.async_fire_internal"))
        returned = self._top_level_index(function, lambda node: (
            isinstance(node, ast.Return) and ast.unparse(node.value) == "new"))
        self.assertLess(assign, save)
        self.assertLess(save, event)
        self.assertLess(event, returned)
        # Every validation raise precedes the assignment (it cannot follow it).
        assign_line = function.body[assign].lineno
        raises = [node for node in ast.walk(function) if isinstance(node, ast.Raise)]
        self.assertTrue(raises)
        self.assertTrue(all(node.lineno < assign_line for node in raises))

    def test_public_update_delegates_to_the_private_update(self):
        function = self.excerpt(PUBLIC_UPDATE)
        self.assertEqual(function.name, "async_update_entity")
        (statement,) = [node for node in function.body if isinstance(node, ast.Return)]
        self.assertEqual(ast.unparse(statement.value.func), "self._async_update_entity")

    def test_handler_is_admin_gated_and_catches_only_value_error(self):
        function = self.excerpt(HANDLER)
        self.assertEqual(function.name, "websocket_update_entity")
        self.assertEqual(ast.unparse(function.decorator_list[0]), "require_admin")
        tries = [node for node in function.body if isinstance(node, ast.Try)]
        # Both guarded registry calls (the update and the options update)
        # catch only ValueError; nothing else in the handler is guarded.
        self.assertEqual(len(tries), 2)
        for node in tries:
            self.assertEqual([ast.unparse(handler.type) for handler in node.handlers],
                             ["ValueError"])
        self.assertFalse([node for node in ast.walk(function) if isinstance(node, ast.Try)
                          and node not in tries])
        (guarded,) = [node for node in tries
                      if "registry.async_update_entity(entity_id" in ast.unparse(node.body[0])]
        # Result serialization happens after, and outside, the guarded update.
        serialized = self._top_level_index(function, lambda node: (
            isinstance(node, ast.AnnAssign)
            and "entity_entry.extended_dict" in ast.unparse(node.value)))
        self.assertGreater(serialized, function.body.index(guarded))
        self.assertEqual(ast.unparse(guarded.handlers[0].body[0].value.args[0].args[1]),
                         "'invalid_info'")
        sent = self._top_level_index(function, lambda node: (
            isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
            and ast.unparse(node.value.func) == "connection.send_result"))
        self.assertGreater(sent, function.body.index(guarded))

    def test_dispatcher_routes_every_handler_exception_to_unknown_error_by_default(self):
        dispatch = self.excerpt(DISPATCH)
        (guarded,) = [node for node in ast.walk(dispatch) if isinstance(node, ast.Try)]
        self.assertIn("handler(self.hass, self", ast.unparse(guarded))
        self.assertEqual([ast.unparse(handler.type) for handler in guarded.handlers],
                         ["Exception"])
        self.assertEqual(ast.unparse(guarded.handlers[0].body[0]),
                         "self.async_handle_exception(msg, err)")
        handle = self.excerpt(HANDLE_EXCEPTION)
        first_code = next(node for node in handle.body if isinstance(node, ast.Assign)
                          and ast.unparse(node.targets[0]) == "code")
        self.assertEqual(ast.unparse(first_code.value), "const.ERR_UNKNOWN_ERROR")
        overridden = {ast.unparse(node.test.args[1]) for node in ast.walk(handle)
                      if isinstance(node, ast.If) and isinstance(node.test, ast.Call)
                      and ast.unparse(node.test.func) == "isinstance"}
        self.assertEqual(overridden, {"Unauthorized", "probatio.Invalid", "TimeoutError",
                                      "HomeAssistantError"})
        constants = {}
        for node in ast.parse(load_excerpts(self.REVISION)[CONST]).body:
            constants[node.target.id] = ast.literal_eval(node.value)
        self.assertEqual(constants["ERR_UNKNOWN_ERROR"], "unknown_error")
        self.assertEqual(constants["ERR_UNAUTHORIZED"], "unauthorized")

    def test_require_admin_raises_before_the_handler_runs(self):
        function = self.excerpt(REQUIRE_ADMIN)
        (inner,) = [node for node in function.body if isinstance(node, ast.FunctionDef)]
        guard = self._top_level_index(inner, lambda node: (
            isinstance(node, ast.If) and ast.unparse(node.body[0]) == "raise Unauthorized"))
        call = self._top_level_index(inner, lambda node: (
            isinstance(node, ast.Expr) and ast.unparse(node) == "func(hass, connection, msg)"))
        self.assertLess(guard, call)

    # -- executed model ----------------------------------------------------------

    def model(self, **kwargs: Any) -> CoreUpdateModel:
        return CoreUpdateModel(self.REVISION, porch_entry(), **kwargs)

    def test_model_success_path_is_core_shaped(self):
        model = self.model()
        self.assertTrue(model.handler_wrapped_by_require_admin)
        answer = model.handle(PORCH_COMMAND)
        self.assertIs(answer["success"], True)
        self.assertEqual(answer["result"]["entity_entry"]["name"], "Porch sensor")
        self.assertTrue(model.assigned)
        self.assertEqual(model.saves_attempted, 1)
        self.assertEqual(len(model.events_fired), 1)

    def test_fault_after_assignment_changes_registry_and_answers_unknown_error(self):
        answers = {}
        for point in POST_ASSIGNMENT_FAULTS:
            with self.subTest(fault=point):
                model = self.model(faults=(point,))
                answer = model.handle(PORCH_COMMAND)
                self.assertIs(answer["success"], False)
                self.assertEqual(answer["error"]["code"], "unknown_error")
                self.assertEqual(answer["error"]["code"], model.const.ERR_UNKNOWN_ERROR)
                # The registry entry was assigned: an error result is no proof
                # that nothing changed.
                self.assertTrue(model.assigned)
                self.assertEqual((model.stored.name, model.stored.icon),
                                 ("Porch sensor", "mdi:new"))
                self.assertEqual(model.saves_attempted, 1)
                self.assertEqual(len(model.events_fired),
                                 0 if point == SAVE_SCHEDULING else 1)
                answers[point] = answer
        # All three faults put the same answer on the wire: Engineering cannot
        # tell them apart, so it must treat every one the same way.
        self.assertEqual(len({json.dumps(answer, sort_keys=True)
                              for answer in answers.values()}), 1)

    def test_other_exception_types_after_assignment_are_also_error_results(self):
        for exception, code in ((ValueError("synthetic"), "invalid_info"),
                                (TimeoutError(), "timeout")):
            with self.subTest(code=code):
                model = self.model(faults=(SAVE_SCHEDULING,), exception=exception)
                answer = model.handle(PORCH_COMMAND)
                self.assertEqual(answer["error"]["code"], code)
                self.assertTrue(model.assigned)
        model = self.model(faults=(UPDATE_EVENT,))
        model.exception = model.HomeAssistantError("synthetic")
        self.assertEqual(model.handle(PORCH_COMMAND)["error"]["code"], "home_assistant_error")
        self.assertTrue(model.assigned)

    def test_pre_assignment_validation_answers_invalid_info_without_change(self):
        model = self.model(area_without_name=True)
        answer = model.handle({**PORCH_COMMAND, "area_id": "porch", "name": None})
        self.assertEqual(answer["error"]["code"], "invalid_info")
        self.assertFalse(model.assigned)
        self.assertEqual((model.saves_attempted, model.events_fired), (0, []))

    def test_non_admin_is_unauthorized_before_the_handler_and_registry_is_unchanged(self):
        model = self.model(admin=False, faults=POST_ASSIGNMENT_FAULTS)
        answer = model.handle(PORCH_COMMAND)
        self.assertEqual(answer["error"]["code"], "unauthorized")
        self.assertFalse(model.handler_entered)
        self.assertFalse(model.assigned)
        self.assertEqual((model.saves_attempted, model.events_fired), (0, []))


class Core2026100ExcerptTests(_CoreRevisionCases, unittest.TestCase):
    REVISION = "core_2026_10_0"


class Core2026101ExcerptTests(_CoreRevisionCases, unittest.TestCase):
    REVISION = "core_2026_10_1"


# -- (1) and (3) Engineering end to end ----------------------------------------------


class CoreModelPeer(CoreShapedRegistryPeer):
    """The loopback peer sends the Core model's exact answer to an update."""

    def __init__(self, backend: Any) -> None:
        super().__init__(backend)
        self.core_answer = None

    async def _answer(self, command: dict[str, Any]) -> dict[str, Any]:
        if command.get("type") == UPDATE and self.core_answer is not None:
            answer = self.core_answer(command)
            return {key: value for key, value in answer.items() if key not in {"id", "type"}}
        return await super()._answer(command)


class EntityRegistryErrorEndToEndTests(_ProofAssertions, LoopbackRegistryTestCase):
    """Real client, gateway, adapter, F3 executor and projection; Core-model answers."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self.peer.stop()
        self.peer = CoreModelPeer(self.ws)
        await self.peer.start()
        self.resource_gateway.entity_metadata.websocket_client = HomeAssistantWebSocketClient(
            _loopback_settings(self.peer.url))
        self.models: list[CoreUpdateModel] = []

    def answer_with_core(self, revision: str, **model_kwargs: Any) -> None:
        def answer(command: dict[str, Any]) -> dict[str, Any]:
            self.ws.commands.append(deepcopy(command))
            entry = self.ws.entries[command["entity_id"]]
            model = CoreUpdateModel(revision, entry, known_labels=("night", "outdoor"),
                                    **model_kwargs)
            message = model.handle(command)
            if model.assigned:
                mirror_into(entry, model, command)
            self.models.append(model)
            return message

        self.peer.core_answer = answer

    async def planned(self, entity_id: str) -> dict:
        self.add_entity(entity_id, labels=["night"], icon="mdi:old")
        created = await self.create(entity_id=entity_id, name="Porch sensor", icon="mdi:new")
        await self.approve(created)
        return created

    def surfaces(self, created: dict, result: dict) -> dict[str, dict]:
        plan_id, task_id = created["plan_id"], result["task_id"]
        plan = self.service.get_plan(plan_id)
        plan = plan.get("plan", plan)
        [listed_plan] = [item for item in self.service.list_plans(None, 50)["plans"]
                         if item["plan_id"] == plan_id]
        [row] = [item for item in self.service.list_execution_tasks()["tasks"]
                 if item["task_id"] == task_id]
        return {
            "apply": result,
            "apply.execution_task": result["execution_task"],
            "apply.plan.execution_task": result["plan"]["execution_task"],
            "get_execution_task": self.service.get_execution_task(task_id),
            "list_execution_tasks": row,
            "get_change_plan.execution_task": plan["execution_task"],
            "list_change_plans.execution_task": listed_plan["execution_task"],
        }

    def assert_mutation_not_disproven(self, created: dict, result: dict) -> None:
        """The error result is reported as sent and never as no-mutation."""

        task_id = result["task_id"]
        for label, surface in self.surfaces(created, result).items():
            with self.subTest(surface=label):
                phase = surface["dispatch_phase"]
                self.assertEqual(phase, LIVE_ERROR_RESULT_PHASE, surface)
                self.assertLessEqual(
                    df.PHASE_RANK.index(phase),
                    df.PHASE_RANK.index(df.PHASE_SENT_FAILED_MUTATION_UNPROVEN))
                # null or omitted, never false.
                self.assertIsNone(surface.get("changed_by_engineering"), surface)
                self.assertIs(surface["provider_dispatch_occurred"], True)
                self.assertNotEqual(surface.get("outcome_code"), df.PROVIDER_REJECTED_MUTATION)
                self.assertNotIn(surface.get("result_text"), NON_MUTATION_TEXTS)
                self.assertNotEqual(surface.get("dispatch_performed"), False)
                self.assertNotIn(df.PROVIDER_CALL_NOT_SENT, surface)
                self.assertNotIn(df.SETTER_CALL_NOT_SENT, surface)
        observed = self.service.get_plan_observability(created["plan_id"])
        self.assertIs(observed["execution_task"]["provider_dispatch_occurred"], True)
        task = self.service.task_repository.get(task_id)
        facts = self.runtime.dispatch_facts_for_task(task)
        self.assertIsNone(facts.changed_by_engineering)
        self.assertNotEqual(facts.outcome_code, df.PROVIDER_REJECTED_MUTATION)
        record = self.record(task_id)
        self.assertEqual(record.dispatch_count, 1)
        self.assertIs(record.provider_response_received, True)
        self.assertEqual(self.event_codes(task_id, df.DISPATCH_RESULT_EVENT),
                         {ERROR_DIAGNOSTIC, "provider_response_indeterminate"})
        self.assertFalse(all_codes(record.events) & NON_MUTATION_CODES)
        self.assert_no_proof(self.service.get_execution_task(task_id), record.events)

    async def run_fault(self, revision: str, point: str | None) -> tuple[dict, dict]:
        entity_id = f"sensor.porch_{revision}_{point or 'generic'}"
        created = await self.planned(entity_id)
        if point is None:
            self.assign_then_fail("unknown_error")
        else:
            self.answer_with_core(revision, faults=(point,))
        result = await self.apply(created)
        entry = self.ws.entries[entity_id]
        # The registry really changed before the error result arrived.
        self.assertEqual((entry["name"], entry["icon"]), ("Porch sensor", "mdi:new"))
        return created, result

    async def test_every_post_assignment_fault_is_not_disproven_on_every_surface(self):
        for revision in CORE_REVISIONS:
            for point in POST_ASSIGNMENT_FAULTS:
                with self.subTest(revision=revision, fault=point):
                    created, result = await self.run_fault(revision, point)
                    model = self.models[-1]
                    self.assertTrue(model.assigned)
                    self.assertEqual(model.sent[-1]["error"]["code"], "unknown_error")
                    self.assert_mutation_not_disproven(created, result)
        counts = self.service.health_summary()["execution_tasks"]["dispatch_outcome_counts"]
        self.assertEqual(counts["sent_rejected_no_mutation"], 0)
        self.assertEqual(counts["failed_after_intent_not_dispatched"], 0)
        self.assertEqual(counts["already_present_not_dispatched"], 0)

    async def test_generic_unknown_error_after_assignment_is_not_disproven(self):
        created, result = await self.run_fault("core_2026_10_1", None)
        self.assert_mutation_not_disproven(created, result)

    async def test_error_without_change_is_still_not_read_as_no_mutation(self):
        # Even when readback later finds the prior state, the error result is
        # not the evidence: the dispatch facts never claim non-mutation.
        created = await self.planned("sensor.porch_unchanged")
        self.fail_without_change("unknown_error")
        result = await self.apply(created)
        self.assertEqual(self.ws.entries["sensor.porch_unchanged"]["icon"], "mdi:old")
        self.assert_mutation_not_disproven(created, result)

    async def test_p3_unauthorized_from_core_stays_r_proven(self):
        for revision in CORE_REVISIONS:
            with self.subTest(revision=revision):
                entity_id = f"sensor.porch_{revision}_unauthorized"
                created = await self.planned(entity_id)
                self.answer_with_core(revision, admin=False, faults=POST_ASSIGNMENT_FAULTS)
                result = await self.apply(created)
                model = self.models[-1]
                self.assertEqual(model.sent[-1]["error"]["code"], "unauthorized")
                self.assertFalse(model.assigned)
                self.assertFalse(model.handler_entered)
                entry = self.ws.entries[entity_id]
                self.assertEqual((entry["name"], entry["icon"]), (None, "mdi:old"))
                for label, surface in self.surfaces(created, result).items():
                    with self.subTest(surface=label):
                        self.assertEqual(surface["dispatch_phase"],
                                         df.PHASE_SENT_REJECTED_NO_MUTATION, surface)
                        self.assertIs(surface["changed_by_engineering"], False)
                        self.assertIs(surface["provider_dispatch_occurred"], True)
                        if label != "list_change_plans.execution_task":
                            self.assertEqual(surface["outcome_code"],
                                             df.PROVIDER_REJECTED_MUTATION)
                            self.assertEqual(surface["result_text"],
                                             df.REJECTED_NO_MUTATION_TEXT)
                record = self.record(result["task_id"])
                codes = all_codes(record.events)
                self.assertTrue({df.PROVIDER_REJECTED_MUTATION,
                                 df.PROVIDER_PERMISSION_DENIED} <= codes)
                self.assertNotIn(ERROR_DIAGNOSTIC, codes)
                self.assert_no_proof(self.service.get_execution_task(result["task_id"]),
                                     record.events)
        counts = self.service.health_summary()["execution_tasks"]["dispatch_outcome_counts"]
        self.assertEqual(counts["sent_rejected_no_mutation"], len(CORE_REVISIONS))

    async def test_readback_follows_the_error_result(self):
        created, result = await self.run_fault("core_2026_10_0", SAVE_SCHEDULING)
        update_index = max(i for i, c in enumerate(self.ws.commands) if c.get("type") == UPDATE)
        self.assertTrue(any(c.get("type") == REGISTRY_GET
                            for c in self.ws.commands[update_index + 1:]))
        self.assertEqual(len(self.ws.updates()), 1)


# -- (3) projection controls -----------------------------------------------------------


def _registry_record(codes: tuple[str, ...], *, outcome="dispatch_failed_confirmed",
                     response=True) -> FakeRecord:
    return FakeRecord(operation="update_entity_registry_metadata_configuration",
                      target_type="entity_registry_metadata", response=response,
                      outcome=outcome, codes=codes)


def _parent(record: FakeRecord) -> df.DispatchFacts:
    return df.dispatch_facts(task=SimpleNamespace(dispatched_at=None),
                             children=[df.child_facts(record)])


class EntityRegistryProjectionControlTests(unittest.TestCase):
    """Record shapes an entity-registry error result can leave behind."""

    def test_confirmed_failure_without_permission_denied_is_r_unproven(self):
        for codes in ((df.PROVIDER_REJECTED_MUTATION,),
                      (df.PROVIDER_REJECTED_MUTATION, ERROR_DIAGNOSTIC),
                      ("provider_rejection_confirmed",), ()):
            with self.subTest(codes=codes):
                facts = _parent(_registry_record(codes))
                fields = facts.public_fields()
                self.assertEqual(fields["dispatch_phase"],
                                 df.PHASE_SENT_FAILED_MUTATION_UNPROVEN)
                self.assertIn("changed_by_engineering", fields)
                self.assertIsNone(fields["changed_by_engineering"])
                self.assertIs(fields["provider_dispatch_occurred"], True)
                self.assertIs(fields["dispatch_performed"], True)
                self.assertNotIn("outcome_code", fields)
                self.assertEqual(fields["result_text"], df.FAILED_MUTATION_UNPROVEN_TEXT)
                self.assertNotIn(df.PROVIDER_CALL_NOT_SENT, fields)
                counts = df.outcome_counts([facts])
                self.assertEqual(counts["sent_failed_mutation_unproven"], 1)
                self.assertEqual(counts["sent_rejected_no_mutation"], 0)

    def test_only_permission_denied_proves_the_rejection(self):
        facts = _parent(_registry_record(
            (df.PROVIDER_REJECTED_MUTATION, df.PROVIDER_PERMISSION_DENIED)))
        self.assertEqual(facts.dispatch_phase, df.PHASE_SENT_REJECTED_NO_MUTATION)
        self.assertIs(facts.changed_by_engineering, False)
        self.assertEqual(facts.outcome_code, df.PROVIDER_REJECTED_MUTATION)
        # A conflicting not-sent code withdraws the proof.
        conflicted = _parent(_registry_record(
            (df.PROVIDER_REJECTED_MUTATION, df.PROVIDER_PERMISSION_DENIED,
             df.PROVIDER_CALL_NOT_SENT)))
        self.assertNotEqual(conflicted.dispatch_phase, df.PHASE_SENT_REJECTED_NO_MUTATION)
        self.assertIsNone(conflicted.changed_by_engineering)
        # Permission denied without the rejection code proves nothing either.
        bare = _parent(_registry_record((df.PROVIDER_PERMISSION_DENIED,)))
        self.assertEqual(bare.dispatch_phase, df.PHASE_SENT_FAILED_MUTATION_UNPROVEN)

    def test_od15_error_result_codes_stay_uncertain_whatever_the_readback(self):
        codes = (ERROR_DIAGNOSTIC, "provider_response_indeterminate")
        for outcome in ("succeeded_verified", "verification_mismatch",
                        "manual_review_required", "dispatch_indeterminate"):
            with self.subTest(outcome=outcome):
                facts = _parent(_registry_record(codes, outcome=outcome))
                self.assertEqual(facts.dispatch_phase, df.PHASE_SEND_UNCERTAIN)
                self.assertIsNone(facts.changed_by_engineering)
                self.assertIsNone(facts.outcome_code)
                self.assertNotIn("changed_by_engineering", facts.public_fields())


# -- (3) the historical beta.8 record ------------------------------------------------


def _beta8_hashes() -> dict[str, str]:
    provenance = json.loads((BETA8_REGISTRY_ERROR / "provenance.json").read_text(encoding="utf-8"))
    assert provenance["source"] == BETA8_SOURCE, provenance["source"]
    generator = (BETA8_REGISTRY_ERROR / "generate.py").read_bytes()
    assert hashlib.sha256(generator).hexdigest() == provenance["generator_sha256"]
    return provenance


class Beta8EntityRegistryErrorRecordTests(dispatch_facts_tests.Beta8HistoricalRecordTests):
    """beta.8 wrote the error result as a confirmed rejection; it projects (r-unproven)."""

    async def open_registry_error(self):
        provenance = _beta8_hashes()
        summary = provenance["shapes"][BETA8_SHAPE]
        with patch.object(dispatch_facts_tests, "FIXTURES", BETA8_REGISTRY_ERROR):
            opened = await self.open(BETA8_SHAPE)
        return (*opened, summary)

    async def test_beta8_registry_error_record_projects_r_unproven_and_is_not_rewritten(self):
        service, runtime, stored, unchanged, summary = await self.open_registry_error()
        # What the beta.8 writer recorded (provenance shape summary): the
        # registry changed, yet the child was stored as a confirmed rejection
        # without the permission-denied reason.
        self.assertEqual(summary["registry_after"], {"name": "Porch sensor", "icon": "mdi:new"})
        self.assertEqual(summary["normalized_outcome"], "dispatch_failed_confirmed")
        self.assertIn(df.PROVIDER_REJECTED_MUTATION, summary["codes"])
        self.assertNotIn(df.PROVIDER_PERMISSION_DENIED, summary["codes"])
        task = service.task_repository.get(stored["task_id"])
        facts = runtime.dispatch_facts_for_task(task)
        self.assertEqual(facts.dispatch_phase, df.PHASE_SENT_FAILED_MUTATION_UNPROVEN)
        self.assertEqual([child.phase for child in facts.children],
                         [df.PHASE_SENT_FAILED_MUTATION_UNPROVEN])
        self.assertIsNone(facts.changed_by_engineering)
        self.assertIs(facts.provider_dispatch_occurred, True)
        view = service.get_execution_task(stored["task_id"])
        [row] = [item for item in service.list_execution_tasks()["tasks"]
                 if item["task_id"] == stored["task_id"]]
        plan = service.get_plan(stored["plan_id"])
        plan = plan.get("plan", plan)
        [listed] = [item for item in service.list_plans(None, 10)["plans"]
                    if item["plan_id"] == stored["plan_id"]]
        for label, surface in (("get_execution_task", view), ("list_execution_tasks", row),
                               ("get_change_plan.execution_task", plan["execution_task"]),
                               ("list_change_plans.execution_task", listed["execution_task"])):
            with self.subTest(surface=label):
                self.assertEqual(surface["dispatch_phase"],
                                 df.PHASE_SENT_FAILED_MUTATION_UNPROVEN, surface)
                self.assertIn("changed_by_engineering", surface)
                self.assertIsNone(surface["changed_by_engineering"])
                self.assertIs(surface["provider_dispatch_occurred"], True)
                self.assertNotEqual(surface.get("outcome_code"), df.PROVIDER_REJECTED_MUTATION)
                self.assertNotIn(surface.get("result_text"), NON_MUTATION_TEXTS)
                self.assertNotIn(df.PROVIDER_CALL_NOT_SENT, surface)
        self.assertEqual(view["result_text"], df.FAILED_MUTATION_UNPROVEN_TEXT)
        # Stored values the projection must not alter.
        self.assertEqual(view["state"], stored["state"])
        self.assertEqual(view["terminal_outcome"], stored["terminal_outcome"])
        counts = service.health_summary()["execution_tasks"]["dispatch_outcome_counts"]
        self.assertEqual(counts["sent_failed_mutation_unproven"], 1)
        self.assertEqual(counts["sent_rejected_no_mutation"], 0)
        unchanged()


# The harness bases carry their own tests, which run in their own modules.
for _cls in (EntityRegistryErrorEndToEndTests, Beta8EntityRegistryErrorRecordTests):
    for _base in _cls.__mro__[1:]:
        for _name in vars(_base):
            if _name.startswith("test_") and _name not in vars(_cls):
                setattr(_cls, _name, None)


if __name__ == "__main__":
    unittest.main()
