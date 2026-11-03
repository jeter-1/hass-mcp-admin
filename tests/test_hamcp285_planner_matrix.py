"""HAMCP-285 R1-M1: the options and availability planners over the real index.

Each case builds a ``DependencyIndex`` over a synthetic installation (minimal,
or with the installation-shaped noise automations), hands the snapshot it
produces to the real planner through the harness snapshot reader, and asserts
the classification, risk and disclosed dependency evidence.  No causal
finding, profile or gap is inserted by hand.

The matrix records what each reference form attributes today:

* literal, ``expand()`` and label targets attribute the consumer; a lock
  action makes the plan ``safety_critical`` (``feeds_protected_automation``);
* area and domain iteration and an opaque template are not attributed; they
  are disclosed as ``unresolved_dynamic_consumer`` and label ``unknown``;
* a plain group reference (a state trigger on a group that contains the
  target) is NOT attributed by either planner and discloses nothing: the
  availability plan stays ``standard_admin`` / ``none``, and the options plan
  keeps only its unconditional floor.  Precise group attribution is HAMCP-287;
  the target-scoped helper hold (HAMCP-285 R1-H1) does not reach these
  planners;
* unrelated template-heavy automations and a notify-only consumer add no
  dependency reason;
* one malformed unrelated label record makes every ``label_entities()``
  consumer unresolvable, so the availability plan is elevated ``unknown``
  rather than attributed or standard (fail-closed, recorded separately; the
  noise matrix uses the well-formed registry).

Retained floors: every options plan is elevated through the always-present
helper-chain gap (and its base risk is MEDIUM when not elevated); an
availability disable of a protected object stays ``safety_critical`` with
``disable_protected_object``; a plain availability disable stays
``standard_admin`` with LOW risk.

Synthetic and offline: every request outside the reviewed dependency reads
fails the test.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys
from typing import Any
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.dependency.index import DependencyIndex  # noqa: E402
from ha_mcp_engineering.dependency.provider import (  # noqa: E402
    DirectHaDependencyProvider,
)
from ha_mcp_engineering.governance import (  # noqa: E402
    helper_options_planning as options_planning,
)

from tests.hamcp069b_fixtures import (  # noqa: E402
    AvailabilityExposureServiceTestCase,
)
from tests.hamcp232_helper_options_fixtures import (  # noqa: E402
    HelperOptionsServiceTestCase,
)
from tests.hamcp285_fixtures import (  # noqa: E402
    SUPPORTED_HA_VERSION,
    consumer,
    installation_configs,
    installation_registry,
    template,
)


# Literal expectations, not values imported from the code under test.
FEEDS = "feeds_protected_automation:consumer"
UNRESOLVED = "dependency_evidence_incomplete:unresolved_dynamic_consumer"
HELPER_CHAIN_GAP = (
    "dependency_evidence_incomplete:config_entry_helper_chain_coverage_unavailable"
)
STANDARD = ("standard_admin", "low", "none")
SAFETY = ("elevated_admin", "high", "safety_critical")
UNKNOWN = ("elevated_admin", "high", "unknown")
LOCK_ACTION = [{"action": "lock.unlock", "target": {"entity_id": "lock.front"}}]
GROUP = "group.matrix"
AREA = "hall"
LABEL = "matrix"


def forms(target: str) -> dict[str, dict[str, Any]]:
    """One consumer per reference form; each acts on a lock unless noted."""

    domain = target.split(".")[0]

    def condition(value: str) -> dict[str, Any]:
        return consumer("Consumer", conditions=template(value), actions=LOCK_ACTION)

    return {
        "none": {},
        "literal": consumer(
            "Consumer", triggers=[{"trigger": "state", "entity_id": target}],
            actions=LOCK_ACTION,
        ),
        "literal_notify_only": consumer(
            "Consumer", triggers=[{"trigger": "state", "entity_id": target}],
        ),
        "expand": condition(
            "{{ expand('%s') | selectattr('state', 'eq', 'on') | list "
            "| count > 0 }}" % GROUP
        ),
        "plain_group": consumer(
            "Consumer", triggers=[{"trigger": "state", "entity_id": GROUP}],
            actions=LOCK_ACTION,
        ),
        "area": condition(
            "{{ area_entities('%s') | select('is_state', 'on') | list "
            "| count > 0 }}" % AREA
        ),
        "label": condition(
            "{{ label_entities('%s') | select('is_state', 'on') | list "
            "| count > 0 }}" % LABEL
        ),
        "domain": condition(
            "{{ states.%s | selectattr('state', 'eq', 'on') | list "
            "| count > 0 }}" % domain
        ),
        "opaque": condition(
            "{{ is_state('%s.' ~ states('sensor.which'), 'on') }}" % domain
        ),
        "unrelated": consumer(
            "Consumer",
            triggers=[{"trigger": "state", "entity_id": "sensor.room_1"}],
            conditions=template("{{ states('sensor.room_2') | int(0) > 3 }}"),
            actions=LOCK_ACTION,
        ),
    }


# form -> (availability decision, dependency reasons, attributed consumers)
MATRIX: dict[str, tuple[tuple[str, str, str], list[str], list[tuple[str, str]]]] = {
    "none": (STANDARD, [], []),
    "literal": (SAFETY, [FEEDS], [("consumer", "trigger")]),
    "literal_notify_only": (STANDARD, [], [("consumer", "trigger")]),
    "expand": (SAFETY, [FEEDS], [("consumer", "condition")]),
    # Not attributed: precise group attribution is HAMCP-287.
    "plain_group": (STANDARD, [], []),
    "area": (UNKNOWN, [UNRESOLVED], []),
    "label": (SAFETY, [FEEDS], [("consumer", "condition")]),
    "domain": (UNKNOWN, [UNRESOLVED], []),
    "opaque": (UNKNOWN, [UNRESOLVED], []),
    "unrelated": (STANDARD, [], []),
}


class ScriptReader:
    """A reviewed script reader over an installation with no scripts."""

    provenance = (
        "ha-mcp 8.5.0; synthetic reviewed contract; identity inventory "
        "direct_ha_api; no fallback"
    )

    def current(self) -> bool:
        return True

    async def read(self, entity: str) -> Any:
        raise AssertionError(f"no script exists: {entity}")


class MatrixRest:
    def __init__(self, configs: dict[str, Any], states: list[dict[str, Any]]):
        self.configs, self.states = configs, states

    async def request(self, method: str, path: str, **_: Any) -> Any:
        if method != "GET":
            raise AssertionError(f"unreviewed dependency request: {method} {path}")
        if path == "/config":
            return {"version": SUPPORTED_HA_VERSION}
        if path == "/states":
            return deepcopy(self.states) + [
                {"entity_id": f"automation.{key}", "state": "on",
                 "attributes": {"id": key, "friendly_name": value["alias"]}}
                for key, value in self.configs.items()
            ]
        prefix = "/config/automation/config/"
        if path.startswith(prefix):
            return deepcopy(self.configs[path.removeprefix(prefix)])
        raise AssertionError(f"unreviewed dependency request: {path}")


class MatrixWebSocket:
    def __init__(self, registry: list[Any], labels: list[dict[str, Any]]):
        self.registry, self.labels = registry, labels

    async def command(self, payload: dict[str, Any]) -> Any:
        if payload == {"type": "config/entity_registry/list"}:
            return deepcopy(self.registry)
        if payload == {"type": "config/label_registry/list"}:
            return deepcopy(self.labels)
        raise AssertionError(f"unreviewed dependency command: {payload}")


def wellformed_noise_registry() -> list[Any]:
    """The installation registry without its one malformed record."""

    return [row for row in installation_registry()
            if isinstance(row.get("labels"), list)]


async def index_snapshot(target: str, form: str, *, noise: bool,
                         noise_registry: list[Any] | None = None) -> Any:
    """The snapshot a real ``DependencyIndex`` builds for one matrix case."""

    configs = installation_configs() if noise else {}
    automation = forms(target)[form]
    if automation:
        configs["consumer"] = automation
    states = [
        {"entity_id": target, "state": "off", "attributes": {}},
        {"entity_id": GROUP, "state": "off", "attributes": {"entity_id": [target]}},
        {"entity_id": "sensor.which", "state": "unrelated", "attributes": {}},
        {"entity_id": "lock.front", "state": "locked", "attributes": {}},
    ]
    registry = [{"entity_id": target, "platform": "template",
                 "labels": [LABEL], "area_id": AREA}]
    labels = [{"label_id": LABEL, "name": "Matrix"}]
    if noise:
        registry += (wellformed_noise_registry() if noise_registry is None
                     else noise_registry)
        labels.append({"label_id": "kitchen", "name": "Kitchen"})
    index = DependencyIndex(DirectHaDependencyProvider(
        MatrixRest(configs, states), MatrixWebSocket(registry, labels),
        concurrency=4, script_reader_factory=ScriptReader,
    ))
    snapshot, _, _ = await index.get(refresh=True)
    return snapshot


def consumer_rows(disclosure: dict[str, Any]) -> list[tuple[str, str]]:
    consumers = disclosure["consumers"]
    items = consumers["items"] if isinstance(consumers, dict) else consumers
    return sorted((item["source_id"], item["relation"]) for item in items)


def decision(created: dict[str, Any]) -> tuple[str, str, str]:
    policy = created["policy_decision"]
    return (policy["policy_class"], policy["risk_delta"],
            policy["physical_consequence"])


class AvailabilityMatrixTests(AvailabilityExposureServiceTestCase):
    noise = False
    target = "sensor.hall_motion"

    async def disable(self, form: str, target: str | None = None,
                      **index: Any) -> dict[str, Any]:
        target = target or self.target
        self.snapshot = await index_snapshot(target, form, noise=self.noise,
                                             **index)
        return await self.create(operation="disable", entity_ids=[target])

    @staticmethod
    def disclosure(created: dict[str, Any]) -> dict[str, Any]:
        return created["dry_run_results"]["operations"][0]["dry_run_results"][
            "entity_availability_exposure"
        ]

    async def test_matrix(self):
        self.add_entity(self.target)
        for form, (expected, reasons, consumers) in MATRIX.items():
            with self.subTest(form=form):
                created = await self.disable(form)
                disclosure = self.disclosure(created)
                self.assertEqual(decision(created), expected)
                self.assertEqual(created["risk"]["level"], expected[1])
                self.assertTrue(created["approval_actionable"])
                self.assertEqual(disclosure["protection"]["dependency_reasons"],
                                 reasons)
                self.assertEqual(consumer_rows(disclosure), consumers)
                self.assertEqual(disclosure["consumers"]["evidence_complete"],
                                 UNRESOLVED not in reasons)
        self.assertEqual(self.ws.writes(), [])

    async def test_a_plain_group_member_is_neither_attributed_nor_disclosed(self):
        # Recorded limitation: the hold is target-scoped to the helper planners.
        self.add_entity(self.target)
        created = await self.disable("plain_group")
        disclosure = self.disclosure(created)
        self.assertEqual(decision(created), STANDARD)
        self.assertEqual(disclosure["consumers"]["count"], 0)
        self.assertNotIn("group", " ".join(disclosure["protection"]["dependency_reasons"]))

    async def test_a_protected_object_keeps_its_floor_over_real_evidence(self):
        target = "binary_sensor.smoke"
        self.add_entity(target, original_device_class="smoke")
        for form in ("none", "unrelated", "plain_group"):
            with self.subTest(form=form):
                created = await self.disable(form, target)
                self.assertEqual(created["policy_decision"]["policy_class"],
                                 "elevated_admin")
                self.assertEqual(created["policy_decision"]["physical_consequence"],
                                 "safety_critical")
                self.assertIn("disable_protected_object",
                              created["policy_decision"]["reason_codes"])

    async def test_a_plain_disable_keeps_low_standard_risk(self):
        self.add_entity(self.target)
        created = await self.disable("none")
        self.assertEqual(decision(created), STANDARD)
        self.assertEqual(created["risk"]["level"], "low")
        self.assertEqual(self.disclosure(created)["consumers"]["evidence_gaps"], [])


class AvailabilityMatrixNoiseTests(AvailabilityMatrixTests):
    noise = True

    async def test_a_malformed_unrelated_label_record_is_disclosed_not_ignored(self):
        # One unrelated registry row whose labels are not a list leaves label
        # membership incomplete, so the noise ``label_entities('kitchen')``
        # consumers can no longer be excluded and the label consumer can no
        # longer be attributed: every disable is elevated ``unknown``.
        self.add_entity(self.target)
        for form in ("none", "label", "plain_group"):
            with self.subTest(form=form):
                created = await self.disable(
                    form, noise_registry=installation_registry()
                )
                disclosure = self.disclosure(created)
                self.assertEqual(decision(created), UNKNOWN)
                self.assertEqual(disclosure["protection"]["dependency_reasons"],
                                 [UNRESOLVED])
                self.assertEqual(consumer_rows(disclosure), [])


class HelperOptionsMatrixTests(HelperOptionsServiceTestCase):
    noise = False

    async def options(self, form: str) -> dict[str, Any]:
        entity_id = self.add_template_sensor()
        self.snapshot = await index_snapshot(entity_id, form, noise=self.noise)
        return await self.create_options(
            {"entity_id": entity_id, "set": {"state": "{{ 3 }}"}}
        )

    async def test_matrix(self):
        for form, (availability, reasons, consumers) in MATRIX.items():
            with self.subTest(form=form):
                created = await self.options(form)
                disclosure = self.options_disclosure(created)
                expected = SAFETY if availability == SAFETY else UNKNOWN
                self.assertEqual(decision(created), expected)
                self.assertEqual(created["risk"]["level"], "high")
                self.assertTrue(created["approval_actionable"])
                self.assertEqual(disclosure["protection"]["dependency_reasons"],
                                 sorted([HELPER_CHAIN_GAP, *reasons]))
                self.assertEqual(consumer_rows(disclosure), consumers)

    async def test_the_floor_is_elevated_with_a_medium_base_risk(self):
        created = await self.options("none")
        self.assertEqual(decision(created), UNKNOWN)
        protection = self.options_disclosure(created)["protection"]
        self.assertEqual(protection["basis"], ["options_protected_dependency"])
        self.assertEqual(protection["dependency_reasons"], [HELPER_CHAIN_GAP])
        standard = options_planning.classify((), [], [], [])
        self.assertEqual(
            options_planning.risk_assessment("op", standard).level.value, "medium"
        )

    async def test_a_plain_group_member_keeps_only_the_floor(self):
        created = await self.options("plain_group")
        self.assertEqual(decision(created), UNKNOWN)
        self.assertEqual(
            self.options_disclosure(created)["protection"]["dependency_reasons"],
            [HELPER_CHAIN_GAP],
        )


class HelperOptionsMatrixNoiseTests(HelperOptionsMatrixTests):
    noise = True


if __name__ == "__main__":
    unittest.main()
