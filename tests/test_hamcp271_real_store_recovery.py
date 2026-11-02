"""HAMCP-271 R1-M2: stranded-child recovery per family over real stores.

Every driver composes the real ``F3RuntimeIntegration``: its durable file
lock store and execution-child store, the real governance service, the real
provider gateway and the real Core-authority admission, in front of a
synthetic Home Assistant transport (REST and WebSocket fakes that refuse any
call outside a Core-authority window). Nothing stubs the lock-conflict path:
the stranded child's expired locks block a new unrelated operation through
the real lock store, and recovery is the only thing that unblocks it.

Each family proves, from the deployed shape (a dispatched child left
``observing`` under a parent the pre-HAMCP-263 sweep made terminal): one
post-dispatch readback, a truthful terminal outcome for readable, drifted and
unreadable evidence, every lock released by its exact terminal owner, the
terminal parent record byte-for-byte unchanged, one provider write in total,
and a new unrelated F3 operation that then succeeds. A stop mid-cleanup
resumes across a restart.
"""

from __future__ import annotations

import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))
sys.path.insert(0, str(Path(__file__).parent))

from ha_mcp_engineering.errors import HomeAssistantUnavailableError  # noqa: E402
from ha_mcp_engineering.f3_runtime.runtime import (  # noqa: E402
    F3RuntimeIntegration,
    TERMINAL_PARENT_CLEANUP_PENDING,
)
from ha_mcp_engineering.governance.configuration_delete import (  # noqa: E402
    CAPABILITY_IDENTITIES as DELETE_IDENTITIES,
)
from ha_mcp_engineering.governance.helper_options import (  # noqa: E402
    CAPABILITY_IDENTITY as OPTIONS_IDENTITY,
)
from ha_mcp_engineering.governance.helper_state import (  # noqa: E402
    HelperStateGateway,
)
from tests import test_hamcp271_stranded_children as stranded  # noqa: E402
from tests.core_authority_fakes import require_core_authority  # noqa: E402
from tests.hamcp069a_entity_fixtures import _provider_identity  # noqa: E402
from tests.hamcp069b_fixtures import (  # noqa: E402
    AvailabilityExposureServiceTestCase,
)
from tests.hamcp071_fixtures import (  # noqa: E402
    CONFIG_PREFIX as SCENE_PREFIX,
    LEAVING,
    LOUNGE,
    SceneServiceTestCase,
)
from tests.hamcp155_fixtures import (  # noqa: E402
    CONFIG_PREFIX,
    ConfigurationDeleteServiceTestCase,
    automation_config,
    script_config,
)
from tests.hamcp165_fixtures import (  # noqa: E402
    LABEL_LIST,
    LABEL_UPDATE,
    LabelServiceTestCase,
)
from tests.hamcp232_helper_options_fixtures import (  # noqa: E402
    OPEN_ROUTE,
    HelperOptionsServiceTestCase,
)
from tests.test_beta37_exact_helper_state import (  # noqa: E402
    FakeDependencyRiskReader,
)

_Harness = stranded._StrandedFamilyHarness
_Matrix = stranded._StrandedFamilyMatrix
UNRELATED = "sensor.porch"


class _RealStoreRecovery:
    """Per-family recovery proofs; a concrete class supplies the driver.

    Driver: ``_dispatch_observing(failed_reads=...)``, ``_reads_unavailable``,
    ``_post_write_reads``, ``_provider_writes`` and ``_drift``; optionally
    ``_assert_family_invariants``. ``_compose`` rebuilds the runtime over the
    same durable storage (a process restart).
    """

    _pass = _Harness._pass
    _strand = _Harness._strand
    _assert_blocked = _Harness._assert_blocked
    _parent_bytes = _Matrix._parent_bytes
    _marker = _Matrix._marker
    _f3_status = _Matrix._f3_status
    _live_shape = _Matrix._live_shape
    _assert_settled = _Matrix._assert_settled
    _stop_during_cleanup = _Matrix._stop_during_cleanup
    _restart_and_assert_settled = _Matrix._restart_and_assert_settled

    def _compose(self):
        self.runtime = F3RuntimeIntegration(
            service=self.service,
            storage_root=str(self.root / "plans"),
            configuration_gateway=self.resource_gateway,
            backup_gateway=None,
            lifecycle_gateway=None,
            provider_identity_reader=_provider_identity,
            retention_days=90,
            core_runtime=self.core,
            **self._runtime_extras(),
        )
        self.service.f3_runtime = self.runtime
        self._after_compose()

    def _runtime_extras(self):
        return {}

    def _after_compose(self):
        """Bounded polls never really sleep (as the family fixtures do)."""

        async def no_sleep(_seconds):
            return None

        for identity in self._sleeping_identities():
            self.runtime.registry.adapter(identity)._sleep = no_sleep

    def _sleeping_identities(self):
        return ()

    def _seed_unrelated(self):
        self.add_entity(UNRELATED, icon="mdi:old")

    async def _unrelated_apply(self):
        """A new entity-metadata apply: a different family and target."""

        created = await self.service.create_entity_metadata_plan(
            {"entity_id": UNRELATED, "name": f"Porch {self._unrelated_count}"}
        )
        self._unrelated_count += 1
        await self.approve(created)
        before = len(self.ws.updates())
        result = await self.service.apply(
            created["plan_id"], created["plan_hash"]
        )
        return result, len(self.ws.updates()) - before

    async def _install(self):
        self._unrelated_count = 0
        self._seed_unrelated()
        self._compose()
        await self.runtime.recover_once("startup")

    # -- proofs --------------------------------------------------------------

    async def _settle(self, *, outcome, prepare=None):
        task_id, child_id, parent = await self._live_shape()
        before = self._parent_bytes(task_id)
        # The real lock store refuses the unrelated operation.
        blocked, writes = await self._unrelated_apply()
        self._assert_blocked(blocked, writes)
        self.assertEqual("recovering", await self._f3_status())
        if prepare is not None:
            prepare()
        reads = self._post_write_reads()

        await self.runtime.recover_once("periodic")

        self._assert_settled(task_id, child_id, before, reads, outcome=outcome)
        self.assertEqual(parent, self.service.task_repository.get(task_id))
        self.assertNotEqual("recovering", await self._f3_status())
        settled = self.runtime.children.get(child_id)
        settled_runtime = self.runtime.children.runtime(child_id)
        for _ in range(2):
            await self._pass()
        self.assertEqual(settled, self.runtime.children.get(child_id))
        self.assertEqual(
            settled_runtime, self.runtime.children.runtime(child_id)
        )
        self.assertEqual(1, self._post_write_reads() - reads)
        applied, writes = await self._unrelated_apply()
        self.assertEqual("succeeded_verified", applied["task_state"], applied)
        self.assertEqual(1, writes)
        self.assertEqual(before, self._parent_bytes(task_id))
        self.assertEqual(1, self._provider_writes())
        self._assert_family_invariants()
        return self.runtime.children.get(child_id)

    def _assert_family_invariants(self):
        """A family's own transport invariants after recovery (default none)."""

    def _diagnostics_unavailable(self):
        """Diagnostic codes of the final unreadable observation."""

        return [
            "post_dispatch_evidence_deadline_expired",
            "readback_unavailable",
        ]

    async def test_readable_evidence_settles_verified(self):
        await self._settle(outcome="succeeded_verified")

    async def test_drifted_evidence_is_a_verification_mismatch(self):
        await self._settle(outcome="verification_mismatch", prepare=self._drift)

    async def test_unreadable_evidence_is_manual_review_with_a_reason(self):
        child = await self._settle(
            outcome="manual_review_required",
            prepare=lambda: self._reads_unavailable(100),
        )
        self.assertEqual(
            {
                "manual_review_reason_code": (
                    "adapter_recovery_requires_manual_review"
                )
            },
            child.evidence,
        )
        observed = [
            event["diagnostic_codes"]
            for event in child.events
            if event["event_type"] == "observation_recorded"
        ]
        self.assertEqual(self._diagnostics_unavailable(), observed[-1])

    async def test_stop_mid_cleanup_resumes_after_restart(self):
        stopped = await self._stop_during_cleanup(
            lambda: patch.object(
                type(self.runtime.locks),
                "release",
                side_effect=stranded._Crash(),
            )
        )
        self.assertEqual(stopped[4], len(self.runtime.locks.records()))
        self.assertEqual(
            TERMINAL_PARENT_CLEANUP_PENDING, self._marker(stopped[1])
        )
        await self._restart_and_assert_settled(*stopped[:4])
        self._assert_family_invariants()


class _ConfigurationRestDriver(_RealStoreRecovery):
    """Automation or script update through ``ConfigurationResourceGateway``.

    Post-write readbacks are exact ``GET /config/<type>/config/<id>`` reads
    on the synthetic REST store; only reads after the provider POST count.
    """

    RESOURCE = ""
    TARGET = ""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.add(self.RESOURCE, self.TARGET)
        self.path = CONFIG_PREFIX[self.RESOURCE] + self.TARGET
        self.failing_reads = 0
        self.reads_after_write = 0
        original = self.config_rest.request

        async def request(method, path, **kwargs):
            if (
                method == "GET"
                and str(path) == self.path
                and self._provider_writes()
            ):
                require_core_authority(self.config_rest, method, str(path))
                self.reads_after_write += 1
                if self.failing_reads > 0:
                    self.failing_reads -= 1
                    raise HomeAssistantUnavailableError()
            return await original(method, path, **kwargs)

        self.config_rest.request = request
        await self._install()

    def _sleeping_identities(self):
        return tuple(DELETE_IDENTITIES.values())

    def _proposed(self):
        if self.RESOURCE == "automation":
            config = automation_config(self.TARGET)
        else:
            config = script_config(self.TARGET)
        config["alias"] = "Governed HAMCP-271 alias"
        return config

    async def _dispatch_observing(self, *, failed_reads):
        created = await self.service.create_configuration_plan(
            title="HAMCP-271 real-store configuration fixture",
            description="Stranded configuration child over real stores",
            operations=[
                {
                    "operation_id": f"update_{self.RESOURCE}",
                    "resource_type": self.RESOURCE,
                    "action": "update",
                    "target_id": self.TARGET,
                    "depends_on": [],
                    "proposed_config": self._proposed(),
                }
            ],
        )
        await self.approve(created)
        self.failing_reads = failed_reads
        result = await self.service.apply(
            created["plan_id"], created["plan_hash"]
        )
        self.assertEqual("observing", result["task_state"], result)
        self.assertEqual(1, self._provider_writes())
        return result["task_id"]

    def _reads_unavailable(self, count):
        self.failing_reads = count

    def _post_write_reads(self):
        return self.reads_after_write

    def _provider_writes(self):
        return sum(
            1
            for item in self.config_rest.requests
            if item == ("POST", self.path)
        )

    def _drift(self):
        self.config_rest.configs[self.RESOURCE][self.TARGET]["alias"] = (
            "Drifted by another writer"
        )


class AutomationRealStoreTests(
    _ConfigurationRestDriver, ConfigurationDeleteServiceTestCase
):
    RESOURCE = "automation"
    TARGET = "kitchen_motion"


class ScriptRealStoreTests(
    _ConfigurationRestDriver, ConfigurationDeleteServiceTestCase
):
    RESOURCE = "script"
    TARGET = "evening_scene"


class HelperConfigurationRealStoreTests(
    _RealStoreRecovery, ConfigurationDeleteServiceTestCase
):
    """``input_boolean`` helper update through the storage-collection API.

    The synthetic WebSocket answers ``input_boolean/list`` and
    ``input_boolean/update`` as Core's storage collection does; the
    post-write readback is the exact ``list`` lookup by object ID.
    """

    TARGET = "input_boolean.night_mode"
    OBJECT_ID = "night_mode"

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.helpers = {
            self.OBJECT_ID: {"id": self.OBJECT_ID, "name": "Night mode"}
        }
        self.helper_updates = []
        self.failing_reads = 0
        self.reads_after_write = 0
        original = self.ws.command

        async def command(message):
            kind = message.get("type")
            if kind not in {"input_boolean/list", "input_boolean/update"}:
                return await original(message)
            require_core_authority(self.ws, "WS", kind)
            if kind == "input_boolean/update":
                self.helper_updates.append(copy.deepcopy(message))
                item = self.helpers[message["input_boolean_id"]]
                for field, value in message.items():
                    if field not in {"type", "input_boolean_id", "id"}:
                        item[field] = copy.deepcopy(value)
                return copy.deepcopy(item)
            if self.helper_updates:
                self.reads_after_write += 1
                if self.failing_reads > 0:
                    self.failing_reads -= 1
                    raise HomeAssistantUnavailableError()
            return copy.deepcopy(list(self.helpers.values()))

        self.ws.command = command
        self.config_rest.states.add(self.TARGET)
        await self._install()

    def _sleeping_identities(self):
        return tuple(DELETE_IDENTITIES.values())

    async def _dispatch_observing(self, *, failed_reads):
        created = await self.service.create_configuration_plan(
            title="HAMCP-271 real-store helper fixture",
            description="Stranded helper configuration child over real stores",
            operations=[
                {
                    "operation_id": "update_night_mode",
                    "resource_type": "helper",
                    "helper_type": "input_boolean",
                    "action": "update",
                    "target_id": self.TARGET,
                    "depends_on": [],
                    "proposed_config": {"name": "Night mode governed"},
                }
            ],
        )
        await self.approve(created)
        self.failing_reads = failed_reads
        result = await self.service.apply(
            created["plan_id"], created["plan_hash"]
        )
        self.assertEqual("observing", result["task_state"], result)
        self.assertEqual(1, self._provider_writes())
        return result["task_id"]

    def _reads_unavailable(self, count):
        self.failing_reads = count

    def _post_write_reads(self):
        return self.reads_after_write

    def _provider_writes(self):
        return len(self.helper_updates)

    def _drift(self):
        self.helpers[self.OBJECT_ID]["name"] = "Drifted by another writer"


class _WebSocketReadDriver(_RealStoreRecovery):
    """Wrap ``ws.command``: after the family write, count and fail reads."""


    def _wrap_websocket_reads(self):
        self.failing_reads = 0
        self.reads_after_write = 0
        original = self.ws.command

        async def command(message):
            if self._is_readback(message) and self._provider_writes():
                require_core_authority(self.ws, "WS", str(message.get("type")))
                self.reads_after_write += 1
                if self.failing_reads > 0:
                    self.failing_reads -= 1
                    raise HomeAssistantUnavailableError()
            return await original(message)

        self.ws.command = command

    async def _dispatch_observing(self, *, failed_reads):
        created = await self._family_plan()
        await self.approve(created)
        self.failing_reads = failed_reads
        result = await self.service.apply(
            created["plan_id"], created["plan_hash"]
        )
        self.assertEqual("observing", result["task_state"], result)
        self.assertEqual(1, self._provider_writes())
        return result["task_id"]

    def _reads_unavailable(self, count):
        self.failing_reads = count

    def _post_write_reads(self):
        return self.reads_after_write


class EntityMetadataRealStoreTests(
    _WebSocketReadDriver, AvailabilityExposureServiceTestCase
):
    """Entity registry metadata: one ``config/entity_registry/update``."""

    TARGET = "sensor.kitchen"

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.add_entity(self.TARGET, icon="mdi:old")
        self._wrap_websocket_reads()
        await self._install()

    def _family_plan(self):
        return self.service.create_entity_metadata_plan(
            {"entity_id": self.TARGET, "name": "Kitchen governed"}
        )

    def _is_readback(self, message):
        return (
            message.get("type") == "config/entity_registry/get"
            and message.get("entity_id") == self.TARGET
        )

    def _provider_writes(self):
        return sum(
            1
            for item in self.ws.updates()
            if item.get("entity_id") == self.TARGET
        )

    def _drift(self):
        self.ws.set_external(self.TARGET, name="Drifted by another writer")


class LabelRealStoreTests(_WebSocketReadDriver, LabelServiceTestCase):
    """Label registry: one ``config/label_registry/update``."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.ws.add_label("outdoor", "Outdoor", color="green")
        self._wrap_websocket_reads()
        await self._install()

    def _family_plan(self):
        return self.create(
            {"operation": "update", "label_id": "outdoor", "color": "red"}
        )

    def _is_readback(self, message):
        return message.get("type") == LABEL_LIST

    def _provider_writes(self):
        return sum(
            1 for item in self.ws.label_writes()
            if item.get("type") == LABEL_UPDATE
        )

    def _drift(self):
        self.ws.set_external_label("outdoor", color="blue")


class SceneRealStoreTests(_RealStoreRecovery, SceneServiceTestCase):
    """Scene configuration edit: one ``POST /config/scene/config/<id>``.

    The unrelated operation edits a different scene (the fixture binds the
    registry gateways to its own WebSocket), through the same real stores.
    """


    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.failing_reads = 0
        self.reads_after_write = 0
        self.path = SCENE_PREFIX + LOUNGE
        original = self.scene_rest.request

        async def request(method, path, **kwargs):
            if (
                method == "GET"
                and str(path) == self.path
                and self._provider_writes()
            ):
                require_core_authority(self.scene_rest, method, str(path))
                if self.failing_reads > 0:
                    self.failing_reads -= 1
                    raise HomeAssistantUnavailableError()
            return await original(method, path, **kwargs)

        self.scene_rest.request = request
        # One readback is one ``read_state`` (config, registry, config
        # re-read for stability); count those, after the provider POST.
        scene = self.resource_gateway.scene_configuration
        read_state = scene.read_state

        async def counted(config_id):
            if config_id == LOUNGE and self._provider_writes():
                self.reads_after_write += 1
            return await read_state(config_id)

        scene.read_state = counted
        await self._install()

    def _seed_unrelated(self):
        return None

    async def _unrelated_apply(self):
        created = await self.create(
            config_id=LEAVING, name=f"Synthetic Leaving {self._unrelated_count}"
        )
        self._unrelated_count += 1
        await self.approve(created)
        before = self._posts(LEAVING)
        result = await self.service.apply(
            created["plan_id"], created["plan_hash"]
        )
        return result, self._posts(LEAVING) - before

    def _posts(self, config_id):
        return sum(1 for item, _ in self.scene_rest.posts if item == config_id)

    async def _dispatch_observing(self, *, failed_reads):
        created = await self.create(
            config_id=LOUNGE, name="Synthetic Lounge governed"
        )
        await self.approve(created)
        self.failing_reads = failed_reads
        result = await self.service.apply(
            created["plan_id"], created["plan_hash"]
        )
        self.assertEqual("observing", result["task_state"], result)
        self.assertEqual(1, self._provider_writes())
        return result["task_id"]

    def _reads_unavailable(self, count):
        self.failing_reads = count

    def _post_write_reads(self):
        return self.reads_after_write

    def _provider_writes(self):
        return self._posts(LOUNGE)

    def _drift(self):
        self.scene_rest.set_external(LOUNGE, name="Drifted by another writer")


class ConfigurationDeleteRealStoreTests(
    _RealStoreRecovery, ConfigurationDeleteServiceTestCase
):
    """Governed delete: its own absence verifier over the real gateway.

    The delete adapter overrides ``_observe`` with the config, registry and
    state absence reads; the readback here counts exact config GETs after
    the one provider DELETE.
    """

    RESOURCE = "automation"
    TARGET = "kitchen_motion"

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.add(self.RESOURCE, self.TARGET)
        self.path = CONFIG_PREFIX[self.RESOURCE] + self.TARGET
        self.failing_reads = 0
        self.reads_after_write = 0
        original = self.config_rest.request

        async def request(method, path, **kwargs):
            if (
                method == "GET"
                and str(path) == self.path
                and self._provider_writes()
            ):
                require_core_authority(self.config_rest, method, str(path))
                self.reads_after_write += 1
                if self.failing_reads > 0:
                    self.failing_reads -= 1
                    raise HomeAssistantUnavailableError()
            return await original(method, path, **kwargs)

        self.config_rest.request = request
        await self._install()

    def _sleeping_identities(self):
        return tuple(DELETE_IDENTITIES.values())

    async def _dispatch_observing(self, *, failed_reads):
        created = await self.create_one(self.RESOURCE, self.TARGET)
        await self.approve(created)
        self.failing_reads = failed_reads
        result = await self.service.apply(
            created["plan_id"], created["plan_hash"]
        )
        self.assertEqual("observing", result["task_state"], result)
        self.assertEqual(1, self._provider_writes())
        return result["task_id"]

    def _reads_unavailable(self, count):
        self.failing_reads = count

    def _post_write_reads(self):
        return self.reads_after_write

    def _provider_writes(self):
        return len(self.config_rest.deletes())

    def _drift(self):
        # Another writer recreated the configuration after the delete.
        self.config_rest.configs[self.RESOURCE][self.TARGET] = (
            automation_config(self.TARGET)
        )


class HelperOptionsRealStoreTests(
    _RealStoreRecovery, HelperOptionsServiceTestCase
):
    """Helper options (HAMCP-232): its own options-flow verifier.

    A readback opens an options flow, reads the form and aborts it; it never
    submits. Recovery therefore proves, beyond the shared proofs: the one
    submit is the dispatch, every readback flow is aborted, and Core holds
    no open flow afterwards.
    """

    ENTRY = "filterentry01"

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.entity_id = self.add_filter(self.ENTRY)
        self.failing_reads = 0
        self.reads_after_write = 0
        self.failed_opens = 0
        original = self.options_rest.request

        async def request(method, path, *args, **kwargs):
            if (
                method == "POST"
                and str(path) == OPEN_ROUTE
                and self._provider_writes()
            ):
                require_core_authority(self.options_rest, method, str(path))
                self.reads_after_write += 1
                if self.failing_reads > 0:
                    self.failing_reads -= 1
                    self.failed_opens += 1
                    raise HomeAssistantUnavailableError()
            return await original(method, path, *args, **kwargs)

        self.options_rest.request = request
        await self._install()

    def _sleeping_identities(self):
        return (*DELETE_IDENTITIES.values(), OPTIONS_IDENTITY)

    async def _dispatch_observing(self, *, failed_reads):
        created = await self.create_options(
            {"entity_id": self.entity_id, "set": {"time_constant": 20}}
        )
        await self.approve(created)
        self.failing_reads = failed_reads
        result = await self.service.apply(
            created["plan_id"], created["plan_hash"]
        )
        self.assertEqual("observing", result["task_state"], result)
        self.assertEqual(1, self._provider_writes())
        return result["task_id"]

    def _reads_unavailable(self, count):
        self.failing_reads = count

    def _post_write_reads(self):
        return self.reads_after_write

    def _provider_writes(self):
        return len(self.options_rest.submits())

    def _drift(self):
        self.options_rest.set_external(self.ENTRY, time_constant=15)

    def _assert_family_invariants(self):
        # No submit during recovery: the only submit is the dispatch, and
        # its body is the approved change.
        self.assertEqual(1, len(self.options_rest.submits()))
        # Every flow was closed: the dispatch flow by its submit, every other
        # flow (plan and readback) by an abort. None is left open.
        self.assertEqual({}, self.options_rest.flows)
        self.assertEqual(
            len(self.options_rest.opens()),
            len(self.options_rest.aborts()) + len(self.options_rest.submits()),
        )


class _SyntheticInputBooleanCore:
    """Synthetic Core for one ``input_boolean``: exact REST state reads and
    exact ``turn_on``/``turn_off`` WebSocket service calls, nothing else.

    Both sides refuse outside a Core-authority window exactly as the real
    client does.
    """

    def __init__(self, entity_id: str) -> None:
        self.entity_id = entity_id
        self.state = {
            "entity_id": entity_id,
            "state": "off",
            "last_changed": "2026-10-22T12:00:00+00:00",
        }
        self.clock = 0
        self.commands: list[dict] = []
        self.reads_after_send = 0
        self.failing_reads = 0
        self.authority_refusals: list[tuple[str, str]] = []

    def set_external(self, state: str) -> None:
        self.clock += 1
        self.state["state"] = state
        self.state["last_changed"] = f"2026-10-22T12:01:{self.clock:02d}+00:00"

    async def request(self, method, path, **_):
        require_core_authority(self, method, str(path))
        if method != "GET" or str(path) != f"/states/{self.entity_id}":
            raise AssertionError(f"unreviewed REST request: {method} {path}")
        if self.commands:
            self.reads_after_send += 1
            if self.failing_reads > 0:
                self.failing_reads -= 1
                raise HomeAssistantUnavailableError()
        return copy.deepcopy(self.state)

    async def command(self, payload):
        require_core_authority(self, "WS", str(payload.get("type")))
        if payload != {
            "type": "call_service",
            "domain": "input_boolean",
            "service": payload.get("service"),
            "target": {"entity_id": self.entity_id},
        } or payload["service"] not in {"turn_on", "turn_off"}:
            raise AssertionError(f"unreviewed WebSocket command: {payload}")
        self.commands.append(copy.deepcopy(payload))
        self.set_external("on" if payload["service"] == "turn_on" else "off")
        return None


class HelperStateRealStoreTests(
    _RealStoreRecovery, AvailabilityExposureServiceTestCase
):
    """Operational ``set_input_boolean_state`` through the REAL
    ``HelperStateGateway`` over a synthetic Core transport.

    ``set_input_select_option`` has the same real-store, real-gateway proof
    in ``test_hamcp271_stranded_children.InputSelectFamilyTests`` (real
    ``InputSelectGateway``, synthetic transport, real stores, Core runtime).
    """

    TARGET = "input_boolean.night_mode"

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.boolean = _SyntheticInputBooleanCore(self.TARGET)
        self.helper_gateway = HelperStateGateway(self.boolean, self.boolean)
        self.service.helper_state_gateway = self.helper_gateway
        self.service.helper_dependency_risk_reader = (
            FakeDependencyRiskReader(self.TARGET)
        )
        await self._install()

    def _runtime_extras(self):
        return {"helper_state_gateway": self.helper_gateway}

    def _diagnostics_unavailable(self):
        return ["evidence_deadline_expired"]

    async def _dispatch_observing(self, *, failed_reads):
        created = await self.service.create_helper_state_plan(
            entity_id=self.TARGET, desired_state="on"
        )
        plan = created["plan"]
        await self.approve(plan)
        self.boolean.failing_reads = failed_reads
        result = await self.service.apply(plan["plan_id"], plan["plan_hash"])
        self.assertEqual("observing", result["task_state"], result)
        self.assertEqual(1, self._provider_writes())
        return result["task_id"]

    def _reads_unavailable(self, count):
        self.boolean.failing_reads = count

    def _post_write_reads(self):
        return self.boolean.reads_after_send

    def _provider_writes(self):
        return len(self.boolean.commands)

    def _drift(self):
        self.boolean.set_external("off")

    def _assert_family_invariants(self):
        # Recovery sent no service call (the one command is the dispatch)
        # and every read ran inside a Core-authority window.
        self.assertEqual(1, len(self.boolean.commands))
        self.assertEqual([], self.boolean.authority_refusals)


if __name__ == "__main__":
    unittest.main()
