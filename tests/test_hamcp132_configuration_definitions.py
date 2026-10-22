"""HAMCP-132 slice 2: read-only configuration definitions (``get_configuration_definitions``).

Fixtures model the pinned Home Assistant Core 2026.10.1 surfaces
(``schemas.py`` names each source line), the reviewed ``ha_config_get_yaml``
envelope through the gateway's ``configuration_definitions`` purpose, and
Core's own ``config_entries/get`` and ``manifest/get`` reads. Everything is
synthetic; no live Home Assistant. Credential-shaped values are assembled at
test time from fragments.
"""

from __future__ import annotations

import ast
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT / "tests"), str(ROOT)]

import httpx  # noqa: E402

import hamcp162_synthetic as syn  # noqa: E402
from ha_mcp_engineering.audit import AuditLogger  # noqa: E402
from ha_mcp_engineering.capabilities import (  # noqa: E402
    BETA_NATIVE_CAPABILITIES,
    CAPABILITY_PROVIDER_MATRIX,
)
from ha_mcp_engineering.configuration import Settings  # noqa: E402
from ha_mcp_engineering.configuration_definitions import contracts as c  # noqa: E402
from ha_mcp_engineering.configuration_definitions import diagnosis  # noqa: E402
from ha_mcp_engineering.configuration_definitions import schemas as s  # noqa: E402
from ha_mcp_engineering.configuration_definitions import service as service_module  # noqa: E402
from ha_mcp_engineering.configuration_definitions.naming import core_slugify  # noqa: E402
from ha_mcp_engineering.configuration_definitions.projection import (  # noqa: E402
    DEFINITION_PATHS,
    Definitions,
    paths_for,
)
from ha_mcp_engineering.configuration_definitions.service import (  # noqa: E402
    CONFIGURATION_DEFINITIONS,
    ConfigurationDefinitionsRuntime,
    ConfigurationDefinitionsService,
)
from ha_mcp_engineering.errors import (  # noqa: E402
    AuthorizationError,
    HomeAssistantApiError,
    HomeAssistantTimeoutError,
    HomeAssistantUnavailableError,
)
from ha_mcp_engineering.ha_core_readmission.routes import STATIC_TOOL_CORE_REQUIREMENTS  # noqa: E402
from ha_mcp_engineering.providers.upstream_read_gateway import (  # noqa: E402
    DEFINITION_YAML_PATHS,
    YAML_CONFIG_PATHS,
    YAML_READER_PURPOSES,
    UpstreamReadGateway,
)
from ha_mcp_engineering.recorder_settings.projection import gateway_envelope  # noqa: E402
from ha_mcp_engineering.request_context import begin_request, current_telemetry, end_request  # noqa: E402
from ha_mcp_engineering.routing import AuthenticatedMcpGateway  # noqa: E402
from ha_mcp_engineering.tools import configuration_definitions as public  # noqa: E402
from ha_mcp_engineering.tools import get_registered_server, registered_tools  # noqa: E402

PACKAGE = ROOT / "hass_mcp_engineering_beta/ha_mcp_engineering/configuration_definitions"
TOOL = ROOT / "hass_mcp_engineering_beta/ha_mcp_engineering/tools/configuration_definitions.py"
NOW = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)
HA_TOKEN = syn.needle("hatoken")
ACCESS_SECRET = syn.needle("accesssecret")
DB_USER = syn.needle("sqldbuser")
DB_PASSWORD = syn.needle("sqldbpassword")
DB_HOST = syn.needle("sqldbhost").lower()
SECRET_NAME = syn.needle("definitionsecretname")
ENV_NAME = syn.needle("definitionenvname")
NESTED_PASSWORD = syn.needle("nestedpassword")
API_KEY = syn.needle("nestedapikey")
REASON_TEXT = syn.needle("entryreasontext")
UNKNOWN_KEY = syn.needle("unknownkey")
UNKNOWN_VALUE = syn.needle("unknownvalue")
EXCEPTION_TEXT = syn.needle("exceptiontext")
NEEDLES = (HA_TOKEN, ACCESS_SECRET, DB_USER, DB_PASSWORD, DB_HOST, SECRET_NAME, ENV_NAME, NESTED_PASSWORD,
           API_KEY, REASON_TEXT, UNKNOWN_KEY, UNKNOWN_VALUE, EXCEPTION_TEXT)
MISSING = object()
UI_ENTRY = "01JSYNTHETICTEMPLATEENTRY01"
COMPONENT_ENTRY = "01JSYNTHETICCOMPONENTENTRY1"
COMPONENTS = ["template", "template.sensor", "sql", "sql.sensor", "filter", "filter.sensor", "derivative",
              "derivative.sensor", "group"]


def db_url(scheme="postgresql"):
    """A database URL with userinfo, assembled at test time."""

    return syn.url(scheme, DB_USER, DB_PASSWORD, DB_HOST + ".invalid:5432/homeassistant")


def envelope(path, value=MISSING, *, completeness="complete", limitations=()):
    """One reviewed ``ha_config_get_yaml`` gateway envelope for a fixed path."""

    found = value is not MISSING
    return {
        "success": True,
        "metadata": {"provider": "upstream_read_gateway", "fallback_occurred": False,
                     "completeness": completeness},
        "data": {"file": "configuration.yaml", "yaml_path": path, "source": "disk", "found": found,
                 "value": value if found else None, "completeness": completeness,
                 "limitations": list(limitations), "redaction": {"redacted_field_count": 0, "categories": []}},
    }


# The owner's six YAML definitions (HAMCP-132 acceptance), anonymized: two
# SQL sensors with a database URL (free space and database size), a filter,
# two derivatives and an old-style group. Names, IDs and queries are synthetic.
def owner_sql():
    return [
        {"name": "Database Free Space", "db_url": db_url(), "unique_id": "sql_db_free_space",
         "query": "SELECT free_mb FROM synthetic_space;", "column": "free_mb", "unit_of_measurement": "MB"},
        {"name": "Database Size", "db_url": db_url("mysql"), "unique_id": "sql_db_size",
         "query": "SELECT size_mb FROM synthetic_size;", "column": "size_mb", "unit_of_measurement": "MB",
         "state_class": "measurement"},
    ]


def owner_sensors():
    return [
        {"platform": "filter", "name": "Outdoor Temperature Smoothed", "entity_id": "sensor.outdoor_temperature",
         "unique_id": "filter_outdoor_temperature",
         "filters": [{"filter": "outlier", "window_size": 4, "radius": 2.0},
                     {"filter": "lowpass", "time_constant": 10, "precision": 2}]},
        {"platform": "derivative", "name": "Energy Rate", "source": "sensor.energy_total", "round": 2,
         "unit_time": "h"},
        {"platform": "derivative", "source": "sensor.water_total", "unique_id": "derivative_water",
         "time_window": "00:05:00"},
    ]


def owner_group():
    return {"downstairs_lights": {"name": "Downstairs Lights", "entities": ["light.kitchen", "light.hall"]}}


def owner_template():
    return [{"sensor": [{"name": "Outdoor Feels Like", "unique_id": "feels_like",
                         "state": "{{ states('sensor.outdoor_temperature') }}", "unit_of_measurement": "C"}]}]


OWNER_ENTITIES = ("sensor.database_free_space", "sensor.database_size", "sensor.outdoor_temperature_smoothed",
                  "sensor.energy_rate", "sensor.water_rate", "group.downstairs_lights")


def yaml(*, template=MISSING, sql=MISSING, group=MISSING, utility_meter=MISSING, sensor=MISSING,
         binary_sensor=MISSING, packages=MISSING, core=MISSING, kwargs=None):
    values = {"template": template, "sql": sql, "group": group, "utility_meter": utility_meter,
              "sensor": sensor, "binary_sensor": binary_sensor, "homeassistant.packages": packages,
              "homeassistant": {"name": "Synthetic home"} if core is MISSING else core}
    return {path: envelope(path, value, **(kwargs or {}).get(path, {})) for path, value in values.items()}


def owner_yaml(**overrides):
    return yaml(**{"template": owner_template(), "sql": owner_sql(), "group": owner_group(),
                   "sensor": owner_sensors(), **overrides})


def state(entity_id, value="1"):
    return {"entity_id": entity_id, "state": value, "attributes": {"friendly_name": entity_id}}


def registry_row(entity_id, platform, unique_id, *, entry=None, disabled_by=None):
    return {"entity_id": entity_id, "platform": platform, "unique_id": unique_id,
            "config_entry_id": entry, "disabled_by": disabled_by}


def owner_core():
    return {
        "states": [state(entity_id) for entity_id in (
            "sensor.database_free_space", "sensor.database_size", "sensor.outdoor_temperature_smoothed",
            "sensor.water_rate", "group.downstairs_lights", "sensor.outdoor_feels_like",
            "sensor.outdoor_temperature", "sensor.water_total", "light.kitchen", "light.hall")]
            + [state("sensor.energy_rate", "unavailable"), state("sensor.energy_total", "unknown")],
        "config/entity_registry/list": [
            registry_row("sensor.database_free_space", "sql", "sql_db_free_space"),
            registry_row("sensor.database_size", "sql", "sql_db_size"),
            registry_row("sensor.outdoor_temperature_smoothed", "filter", "filter_outdoor_temperature"),
            registry_row("sensor.water_rate", "derivative", "derivative_water"),
            registry_row("sensor.outdoor_feels_like", "template", "feels_like"),
            registry_row("sensor.ui_template", "template", UI_ENTRY, entry=UI_ENTRY),
            registry_row("light.kitchen", "hue", "hue-1"),
        ],
        "entity/source": {
            "sensor.database_free_space": {"domain": "sql"}, "sensor.database_size": {"domain": "sql"},
            "sensor.outdoor_temperature_smoothed": {"domain": "filter"},
            "sensor.energy_rate": {"domain": "derivative"}, "sensor.water_rate": {"domain": "derivative"},
            "group.downstairs_lights": {"domain": "group"}, "sensor.outdoor_feels_like": {"domain": "template"},
            "sensor.ui_template": {"domain": "template"}, "light.kitchen": {"domain": "hue"},
        },
        "config": {"components": list(COMPONENTS), "location_name": UNKNOWN_VALUE},
        "config_entries/get": {
            "template": [{"entry_id": UI_ENTRY, "domain": "template", "title": "UI template helper",
                          "state": "loaded", "disabled_by": None, "reason": None,
                          "error_reason_translation_key": None}],
            c.COMPONENT_DOMAIN: [],
        },
        "manifest/get": HomeAssistantApiError(details={"status": 404}),
    }


class SyntheticCore:
    def __init__(self):
        self.available, self.generation = True, 1
        self.current_observation = SimpleNamespace(version="2026.10.1")

    def route_status(self, requirements):
        assert requirements == c.CORE_REQUIREMENTS
        return {"available": self.available, "generation": self.generation}


class SyntheticReader:
    """The admitted fixed-path reader; records every path it is asked for."""

    provenance = "synthetic ha-mcp reader; configuration.yaml only"

    def __init__(self, values, calls):
        self.values, self.calls, self.paths, self.hook = values, calls, [], None

    async def read(self, path):
        self.paths.append(path)
        self.calls.append(f"yaml:{path}")
        if self.hook:
            self.hook(f"yaml:{path}")
        value = self.values[path]
        if isinstance(value, Exception):
            raise value
        return copy.deepcopy(value)


class SyntheticRest:
    """Answers only ``GET /states`` and ``GET /config``."""

    def __init__(self, values, calls):
        self.values, self.calls, self.hook = values, calls, None

    async def request(self, method, path, **kwargs):
        assert method == "GET" and path in ("/states", "/config") and kwargs == {}, (method, path, kwargs)
        kind = path.lstrip("/")
        self.calls.append(kind)
        if self.hook:
            self.hook(kind)
        value = self.values[kind]
        if isinstance(value, Exception):
            raise value
        return copy.deepcopy(value)


def allowed_payload(payload):
    return payload in ({"type": "config/entity_registry/list"}, {"type": "entity/source"},
                       {"type": "manifest/get", "integration": c.COMPONENT_DOMAIN}) or (
        set(payload) == {"type", "domain"} and payload["type"] == "config_entries/get"
        and payload["domain"] in (*c.INTEGRATIONS, c.COMPONENT_DOMAIN))


class SyntheticWebSocket:
    def __init__(self, values, calls):
        self.values, self.calls, self.hook = values, calls, None
        self.payloads = []

    async def command(self, payload):
        assert allowed_payload(payload), payload
        self.payloads.append(copy.deepcopy(payload))
        kind = payload["type"]
        label = f"{kind}:{payload['domain']}" if kind == "config_entries/get" else kind
        self.calls.append(label)
        if self.hook:
            self.hook(label)
        value = self.values[kind]
        if kind == "config_entries/get":
            value = value.get(payload["domain"], []) if type(value) is dict else value
        if isinstance(value, Exception):
            raise value
        return copy.deepcopy(value)


def setup(yaml_values=None, core_values=None, *, admitted=True, known_secrets=(HA_TOKEN, ACCESS_SECRET),
          caller="synthetic_authenticated_caller"):
    calls = []
    reader = SyntheticReader(yaml_values if yaml_values is not None else owner_yaml(), calls)
    values = core_values if core_values is not None else owner_core()
    rest = SyntheticRest(values, calls)
    socket = SyntheticWebSocket(values, calls)
    core = SyntheticCore()
    service = ConfigurationDefinitionsService(
        rest, socket, core, yaml_reader_factory=(lambda: reader) if admitted else (lambda: None),
        known_secrets=known_secrets, clock=lambda: NOW)
    telemetry, token = begin_request()
    telemetry.caller_id = caller
    telemetry.core_dispatch_authorizer = lambda: core.available
    return SimpleNamespace(service=service, reader=reader, rest=rest, socket=socket, core=core,
                           telemetry=telemetry, token=token, calls=calls)


def settings(**values):
    return Settings(**{**dict(ha_url="http://synthetic.invalid", ha_token=HA_TOKEN,
        access_secret=ACCESS_SECRET, port=8100, audit_path="unused",
        rate_limit_per_minute=1000, rate_limit_burst=1000, destructive_services=frozenset()), **values})


def source(report, name):
    return next(item for item in report["sources"] if item["source"] == name)


def project(**paths):
    """Projected definitions for raw values at the named YAML paths."""

    collector = Definitions({path: envelope(path, value)["data"] for path, value in paths.items()})
    return collector.collect(), collector


def walk_strings(value):
    """Every string in a value, keys included."""

    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from walk_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk_strings(item)
    elif isinstance(value, str):
        yield value


class Base(unittest.IsolatedAsyncioTestCase):
    async def run_report(self, yaml_values=None, core_values=None, entity_ids=(), integrations=(), **kwargs):
        self.env = setup(yaml_values, core_values, **kwargs)
        self.addCleanup(end_request, self.env.token)
        report = await self.env.service.report(entity_ids, integrations)
        text = json.dumps(report)
        for value in NEEDLES:
            self.assertNotIn(value, text)
        self.assertNotIn("db_url", json.dumps([d["fields"] for d in report["definitions"]]))
        return report

    @staticmethod
    def entities(report):
        return {row["entity_id"]: row for row in report["entities"]}

    @staticmethod
    def definitions(report):
        return {row["definition_id"]: row for row in report["definitions"]}

    def assert_partial(self, report, *omissions):
        for item in omissions:
            self.assertIn(item, report["omissions"])
        self.assertFalse(report["complete"])
        self.assertEqual(report["completeness"], "partial")


def expected(definition):
    return [{key: item[key] for key in ("entity_domain", "unique_id", "derived_entity_id", "derivation",
                                        "derivation_limit")} for item in definition["expected_entities"]]


def entity_row(domain, unique_id, derived, derivation, limit=None):
    return {"entity_domain": domain, "unique_id": unique_id, "derived_entity_id": derived,
            "derivation": derivation, "derivation_limit": limit}


class PinnedShapeTests(unittest.TestCase):
    """One pinned YAML shape per supported integration (Core 2026.10.1)."""

    def by_id(self, **paths):
        definitions, collector = project(**paths)
        self.collector = collector
        return {d["definition_id"]: d for d in definitions}

    def test_template_blocks(self):
        rows = self.by_id(template=[
            {"unique_id": "block", "sensor": [
                {"name": "Kitchen Feels", "unique_id": "one", "state": "{{ 1 }}", "device_class": "temperature"},
                {"state": "{{ 2 }}"},
                {"default_entity_id": "sensor.forced_id", "name": "Ignored", "state": "3"}],
             "binary_sensor": {"name": "Door Open", "state": "{{ true }}", "delay_on": "00:00:05"}},
            {"triggers": [{"trigger": "time_pattern", "minutes": "/5"}],
             "sensor": [{"name": "{{ 'dynamic' }}", "state": "1"}, {"name": "Fixed Trigger", "state": "2"}]},
            {"use_blueprint": {"path": "synthetic.yaml", "input": {}}},
            {"switch": [{"name": "Pump", "unique_id": "pump", "turn_on": [], "turn_off": []}]},
            "!include more_templates.yaml",
            7,
        ])
        self.assertEqual(expected(rows["template[0].sensor[0]"]),
                         [entity_row("sensor", "block-one", "sensor.kitchen_feels", "name")])
        self.assertEqual(rows["template[0].sensor[0]"]["fields"],
                         {"name": "Kitchen Feels", "unique_id": "one", "state": "{{ 1 }}",
                          "device_class": "temperature"})
        self.assertEqual(rows["template[0].sensor[0]"]["block"],
                         {"fields": {"unique_id": "block"}, "trigger_based": False})
        self.assertEqual(expected(rows["template[0].sensor[1]"]),
                         [entity_row("sensor", None, "sensor.template_sensor", "default_name")])
        self.assertEqual(expected(rows["template[0].sensor[2]"]),
                         [entity_row("sensor", None, "sensor.forced_id", "default_entity_id")])
        self.assertEqual(expected(rows["template[0].binary_sensor[0]"]),
                         [entity_row("binary_sensor", None, "binary_sensor.door_open", "name")])
        dynamic = rows["template[1].sensor[0]"]
        self.assertEqual(expected(dynamic),
                         [entity_row("sensor", None, None, None, "trigger_based_template_name")])
        self.assertTrue(dynamic["block"]["trigger_based"])
        self.assertEqual(dynamic["block"]["fields"]["triggers"], [{"trigger": "time_pattern", "minutes": "/5"}])
        self.assertEqual(expected(rows["template[1].sensor[1]"]),
                         [entity_row("sensor", None, "sensor.fixed_trigger", "name")])
        blueprint = rows["template[2]"]
        self.assertEqual((blueprint["form"], blueprint["status"], blueprint["fields"], blueprint["expected_entities"]),
                         ("blueprint_block", "blueprint_not_projected", {}, []))
        switch = rows["template[3].switch[0]"]
        self.assertEqual(switch["status_detail"], "platform_keys_not_projected")
        self.assertEqual(switch["fields"], {"name": "Pump", "unique_id": "pump"})
        self.assertEqual(switch["unrecognized_key_count"], 2)
        self.assertEqual(expected(switch), [entity_row("switch", "pump", "switch.pump", "name")])
        include = rows["template[4]"]
        self.assertEqual((include["status"], include["include_not_followed"]),
                         ("include_not_followed", ["template[4]"]))
        self.assertEqual(self.collector.unprojected, {"template:not_a_mapping": 1})
        for row in rows.values():
            self.assertEqual((row["integration"], row["yaml_path"]), ("template", "template"))

    def test_sql_integration_key(self):
        rows = self.by_id(sql=[*owner_sql(), {"name": "{{ 'templated' }}", "query": "SELECT 1;", "column": "x"},
                               {"name": "Café", "query": "SELECT 1;", "column": "x"}, "not a mapping"])
        first = rows["sql[0]"]
        self.assertEqual(first["fields"], {"name": "Database Free Space", "unique_id": "sql_db_free_space",
                                           "query": "SELECT free_mb FROM synthetic_space;", "column": "free_mb",
                                           "unit_of_measurement": "MB"})
        self.assertEqual(first["withheld"], ["sql[0].db_url"])
        self.assertEqual(expected(first),
                         [entity_row("sensor", "sql_db_free_space", "sensor.database_free_space", "name")])
        self.assertEqual(expected(rows["sql[2]"]), [entity_row("sensor", None, None, None, "name_is_template")])
        self.assertEqual(expected(rows["sql[3]"]),
                         [entity_row("sensor", None, None, None, "name_outside_slugify_port")])
        self.assertEqual(self.collector.unprojected, {"sql:not_a_mapping": 1})
        # A single mapping (not a list) is one definition.
        single = self.by_id(sql=owner_sql()[1])
        self.assertEqual(list(single), ["sql[0]"])

    def test_group_old_style(self):
        rows = self.by_id(group={**owner_group(), "porch": ["light.porch", "switch.porch_fan"],
                                 "all_doors": {"entities": "binary_sensor.front, binary_sensor.back", "all": True},
                                 "bad": 5})
        lights = rows["group.downstairs_lights"]
        self.assertEqual(lights["fields"], {"name": "Downstairs Lights", "entities": ["light.kitchen", "light.hall"]})
        self.assertEqual(lights["dependencies"], ["light.kitchen", "light.hall"])
        self.assertEqual(expected(lights), [entity_row("group", None, "group.downstairs_lights", "key")])
        self.assertEqual(rows["group.porch"]["fields"], {"entities": ["light.porch", "switch.porch_fan"]})
        self.assertEqual(rows["group.all_doors"]["dependencies"], ["binary_sensor.front", "binary_sensor.back"])
        self.assertEqual(self.collector.unprojected, {"group:entry_not_a_mapping": 1})

    def test_utility_meter(self):
        rows = self.by_id(utility_meter={
            "daily_energy": {"source": "sensor.energy_total", "cycle": "daily", "unique_id": "meter"},
            "named": {"source": "sensor.water_total", "name": "Water Today"},
            "tariffed": {"source": "sensor.energy_total", "tariffs": ["peak", "offpeak"], "unique_id": "tar"},
        })
        self.assertEqual(expected(rows["utility_meter.daily_energy"]),
                         [entity_row("sensor", "meter_single_tariff", "sensor.daily_energy", "key")])
        self.assertEqual(rows["utility_meter.daily_energy"]["dependencies"], ["sensor.energy_total"])
        self.assertEqual(expected(rows["utility_meter.named"]),
                         [entity_row("sensor", None, "sensor.water_today", "name")])
        self.assertEqual(expected(rows["utility_meter.tariffed"]), [
            entity_row("select", "tar", "select.tariffed", "key"),
            entity_row("sensor", "tar_peak", "sensor.tariffed_peak", "key_and_tariff"),
            entity_row("sensor", "tar_offpeak", "sensor.tariffed_offpeak", "key_and_tariff")])

    def test_sensor_platforms(self):
        rows = self.by_id(sensor=[
            *owner_sensors(),
            {"platform": "statistics", "entity_id": "sensor.outdoor_temperature", "state_characteristic": "mean",
             "max_age": {"hours": 24}},
            {"platform": "group", "type": "max", "entities": ["sensor.a", "sensor.b"]},
            {"platform": "group", "type": "mean", "name": "Sensor Group", "entities": ["sensor.a"]},
            {"platform": "integration", "source": "sensor.power", "method": "left"},
            {"platform": "min_max", "entity_ids": ["sensor.a", "sensor.b"]},
            {"platform": "min_max", "entity_ids": ["sensor.a"], "type": "mean", "unique_id": "minmax"},
            {"platform": "history_stats", "entity_id": "climate.house", "state": "heat", "type": "time",
             "start": "{{ today_at() }}", "end": "{{ now() }}", "name": "Heating Today"},
            {"platform": "history_stats", "entity_id": "climate.house", "state": "heat", "duration": {"hours": 1}},
            {"platform": "filter", "entity_id": "sensor.a", "unique_id": "nameless", "filters": []},
            {"platform": "filter", "entity_id": "sensor.a", "filters": []},
            {"platform": "derivative", "source": "sensor.a", "name": "Rate", "entity_namespace": "garage"},
        ])
        cases = {
            "sensor[0]": entity_row("sensor", "filter_outdoor_temperature", "sensor.outdoor_temperature_smoothed",
                                    "name"),
            "sensor[1]": entity_row("sensor", None, "sensor.energy_rate", "name"),
            "sensor[2]": entity_row("sensor", "derivative_water", "sensor.sensor_water_total_derivative",
                                    "default_name"),
            "sensor[3]": entity_row("sensor", None, "sensor.statistical_characteristic", "default_name"),
            "sensor[4]": entity_row("sensor", None, "sensor.sensor_group_max", "default_name"),
            "sensor[5]": entity_row("sensor", None, "sensor.sensor_group_mean", "default_name"),
            "sensor[6]": entity_row("sensor", None, "sensor.sensor_power_integral", "default_name"),
            "sensor[7]": entity_row("sensor", None, "sensor.max_sensor", "default_name"),
            "sensor[8]": entity_row("sensor", "minmax", "sensor.mean_sensor", "default_name"),
            "sensor[9]": entity_row("sensor", None, "sensor.heating_today", "name"),
            "sensor[10]": entity_row("sensor", None, "sensor.unnamed_statistics", "default_name"),
            "sensor[11]": entity_row("sensor", "nameless", "sensor.filter_nameless", "unique_id_fallback"),
            "sensor[12]": entity_row("sensor", None, "sensor.unnamed_device", "device_default_name"),
            "sensor[13]": entity_row("sensor", None, "sensor.garage_rate", "name"),
        }
        for definition_id, row in cases.items():
            self.assertEqual(expected(rows[definition_id]), [row], definition_id)
        integrations = {key: rows[key]["integration"] for key in cases}
        self.assertEqual(integrations["sensor[3]"], "statistics")
        self.assertEqual(integrations["sensor[6]"], "integration")
        self.assertEqual(rows["sensor[0]"]["fields"]["filters"], owner_sensors()[0]["filters"])
        self.assertEqual(rows["sensor[0]"]["dependencies"], ["sensor.outdoor_temperature"])
        self.assertEqual(rows["sensor[3]"]["fields"]["max_age"], {"hours": 24})
        self.assertEqual(rows["sensor[13]"]["fields"]["entity_namespace"], "garage")
        for row in rows.values():
            self.assertEqual((row["form"], row["yaml_path"], row["status"]), ("platform_entry", "sensor", "read"))

    def test_binary_sensor_platforms(self):
        rows = self.by_id(binary_sensor=[
            {"platform": "threshold", "entity_id": "sensor.outdoor_temperature", "upper": 30, "unique_id": "x"},
            {"platform": "trend", "sensors": {"temperature_rising": {
                "entity_id": "sensor.outdoor_temperature", "unique_id": "trend_one", "min_gradient": 0.1}}},
            {"platform": "group", "entities": ["binary_sensor.front", "binary_sensor.back"], "all": True},
            {"platform": "threshold", "entity_id": "sensor.power", "lower": 5, "name": "Low Power"},
        ])
        threshold = rows["binary_sensor[0]"]
        # threshold has no unique_id option: the key is counted, not used.
        self.assertEqual(threshold["unrecognized_key_count"], 1)
        self.assertNotIn("unique_id", threshold["fields"])
        self.assertEqual(expected(threshold), [entity_row("binary_sensor", None, "binary_sensor.threshold",
                                                          "default_name")])
        trend = rows["binary_sensor[1].sensors.temperature_rising"]
        self.assertEqual(expected(trend), [entity_row("binary_sensor", "trend_one", "binary_sensor.temperature_rising",
                                                      "key")])
        self.assertEqual(trend["dependencies"], ["sensor.outdoor_temperature"])
        self.assertEqual(expected(rows["binary_sensor[2]"]),
                         [entity_row("binary_sensor", None, "binary_sensor.binary_sensor_group", "default_name")])
        self.assertEqual(expected(rows["binary_sensor[3]"]),
                         [entity_row("binary_sensor", None, "binary_sensor.low_power", "name")])

    def test_legacy_platforms_are_flagged_and_never_expected(self):
        rows = self.by_id(sensor=[
            {"platform": "sql", "name": "Old SQL", "db_url": db_url(), "query": "SELECT 1;", "column": "x"},
            {"platform": "template", "sensors": {"old": {"value_template": "{{ 1 }}"}}},
            {"platform": "utility_meter", "source": "sensor.energy_total", "name": "Old Meter"},
        ])
        for key, integration in (("sensor[0]", "sql"), ("sensor[1]", "template"), ("sensor[2]", "utility_meter")):
            row = rows[key]
            self.assertEqual((row["form"], row["status"], row["integration"], row["expected_entities"]),
                             ("legacy_platform", "unsupported_legacy_platform", integration, []))
        self.assertEqual(rows["sensor[0]"]["withheld"], ["sensor[0].db_url"])
        self.assertEqual(rows["sensor[0]"]["fields"], {"platform": "sql", "name": "Old SQL", "query": "SELECT 1;",
                                                       "column": "x"})
        self.assertEqual(rows["sensor[1]"]["fields"], {"platform": "template"})
        self.assertEqual(rows["sensor[1]"]["unrecognized_key_count"], 1)

    def test_other_platforms_are_only_counted(self):
        rows = self.by_id(sensor=[
            {"platform": "mqtt", "name": UNKNOWN_VALUE, "state_topic": UNKNOWN_VALUE},
            {"platform": "Not A Domain!", "name": UNKNOWN_VALUE},
            {"name": UNKNOWN_VALUE}, "text", {"platform": 5},
        ], binary_sensor=[{"platform": "ping", "host": UNKNOWN_VALUE}])
        self.assertEqual(rows, {})
        self.assertEqual(self.collector.unprojected, {
            "sensor:platform:mqtt": 1, "sensor:platform:<other>": 1, "sensor:platform_not_text": 2,
            "sensor:not_a_mapping": 1, "binary_sensor:platform:ping": 1})
        self.assertNotIn(UNKNOWN_VALUE, json.dumps(self.collector.unprojected))

    def test_unrecognized_keys_are_counted_never_copied(self):
        entry = {**owner_sensors()[1], UNKNOWN_KEY: UNKNOWN_VALUE, "scan_interval": 30}
        rows = self.by_id(sensor=[entry])
        row = rows["sensor[0]"]
        self.assertEqual(row["unrecognized_key_count"], 1)
        self.assertEqual(row["fields"]["scan_interval"], 30)
        self.assertNotIn(UNKNOWN_VALUE, json.dumps(row))

    def test_paths_for_requests(self):
        self.assertEqual(paths_for(("sensor.a",), ()), DEFINITION_PATHS)
        self.assertEqual(paths_for(("sensor.a",), ("trend",)), DEFINITION_PATHS)
        self.assertEqual(paths_for((), ("sql",)), ("sql", "sensor"))
        self.assertEqual(paths_for((), ("threshold", "trend")), ("binary_sensor",))
        self.assertEqual(paths_for((), ("template", "utility_meter")),
                         ("template", "utility_meter", "sensor", "binary_sensor"))
        self.assertEqual(set(DEFINITION_PATHS) | {"homeassistant.packages", "homeassistant"}, DEFINITION_YAML_PATHS)

    def test_slugify_port_subset(self):
        self.assertEqual(core_slugify("downstairs lights"), "downstairs_lights")
        self.assertEqual(core_slugify("a's  b--c"), "a_s_b_c")
        self.assertEqual(core_slugify("1,000 watts"), "1000_watts")
        self.assertEqual(core_slugify("!!!"), "unknown")
        self.assertEqual(core_slugify(""), "")
        self.assertIsNone(core_slugify("café"))
        self.assertIsNone(core_slugify("a & b"))
        self.assertIsNone(core_slugify(5))


class OwnerShapeTests(Base):
    """The owner's six YAML definitions (anonymized) end to end."""

    async def test_six_owner_entities_are_matched_and_diagnosed(self):
        report = await self.run_report(entity_ids=OWNER_ENTITIES)
        rows = self.entities(report)
        methods = {entity_id: (row["definition"]["method"], row["definition"]["confidence"],
                               row["definition"]["definition_ids"]) for entity_id, row in rows.items()}
        self.assertEqual(methods, {
            "sensor.database_free_space": ("exact_unique_id", "exact", ["sql[0]"]),
            "sensor.database_size": ("exact_unique_id", "exact", ["sql[1]"]),
            "sensor.outdoor_temperature_smoothed": ("exact_unique_id", "exact", ["sensor[0]"]),
            "sensor.energy_rate": ("heuristic", "high", ["sensor[1]"]),
            "sensor.water_rate": ("exact_unique_id", "exact", ["sensor[2]"]),
            "group.downstairs_lights": ("heuristic", "high", ["group.downstairs_lights"]),
        })
        self.assertEqual(rows["sensor.energy_rate"]["diagnosis"], {
            "classification": "dependency_unavailable",
            "reasons": ["state_unavailable", "source_entity_unavailable:sensor.energy_total"]})
        for entity_id in set(OWNER_ENTITIES) - {"sensor.energy_rate"}:
            self.assertEqual(rows[entity_id]["diagnosis"], {"classification": "available", "reasons": []})
        self.assertEqual([d["definition_id"] for d in report["definitions"]],
                         ["sql[0]", "sql[1]", "group.downstairs_lights", "sensor[0]", "sensor[1]", "sensor[2]"])
        definitions = self.definitions(report)
        for key in ("sql[0]", "sql[1]"):
            self.assertEqual(definitions[key]["withheld"], [f"{key}.db_url"])
            self.assertNotIn("db_url", definitions[key]["fields"])
        # The renamed derivative is matched by unique_id, not by its derived ID.
        water = definitions["sensor[2]"]["expected_entities"][0]
        self.assertEqual((water["derived_entity_id"], water["match"]["entity_id"], water["match"]["method"]),
                         ("sensor.sensor_water_total_derivative", "sensor.water_rate", "exact_unique_id"))
        # R2-M1: the always-withheld db_url makes each SQL definition a withheld item.
        self.assertEqual(report["omissions"], ["withheld:definitions[0]", "withheld:definitions[1]"])
        self.assert_partial(report)
        self.assertEqual(report["packages"], "absent")
        self.assertEqual(report["schema"], c.SCHEMA)
        self.assertEqual(report["core_pin"], s.CORE_PIN)
        self.assertEqual(report["observed_at"], "2026-10-21T12:00:00Z")
        self.assertEqual(report["upstream_calls"], 8)
        self.assertEqual(report["coverage"]["definitions_found"], 7)
        self.assertEqual(report["coverage"]["definitions_listed"], 6)
        self.assertFalse(report["coverage"]["state_values_copied"])
        self.assertEqual(report["coverage"]["yaml_paths_read"], [*DEFINITION_PATHS, "homeassistant.packages",
                                                                 "homeassistant"])
        # No state value, attribute or location text is copied.
        self.assertNotIn("friendly_name", json.dumps(report))

    async def test_owner_integrations_request_lists_whole_integrations(self):
        report = await self.run_report(integrations=("sql", "derivative"))
        self.assertEqual([d["definition_id"] for d in report["definitions"]], ["sql[0]", "sql[1]", "sensor[1]",
                                                                                "sensor[2]"])
        self.assertEqual(self.env.reader.paths, ["sql", "sensor", "homeassistant.packages", "homeassistant"])
        self.assertEqual(report["entities"], [])
        self.assertEqual(report["ui_entries"], [])
        self.assertIn("config_entries/get:derivative", self.env.calls)
        self.assertIn("config_entries/get:sql", self.env.calls)


class CredentialTests(Base):
    """db_url, credentials and references are withheld in every position."""

    async def test_db_url_and_credentials_withheld_in_every_position(self):
        nested = {"db_url": db_url(), "password": NESTED_PASSWORD, "api_key": API_KEY,
                  "database_connection": db_url("mssql"), "label": "kept"}
        values = owner_yaml(
            template=[{"unique_id": "blk", "db_url": db_url(), "sensor": [
                {"name": "Attr", "state": "1", "attributes": nested, "variables": [nested]}]}],
            sql=[{**owner_sql()[0], "password": NESTED_PASSWORD}],
            group={"lights": {"entities": ["light.kitchen"], "db_url": db_url()}},
            utility_meter={"meter": {"source": "sensor.energy_total", "db_url": db_url()}},
            sensor=[{"platform": "sql", "name": "Old", "db_url": db_url("sqlite"), "query": "q", "column": "c"},
                    {**owner_sensors()[0], "filters": [{"filter": "lowpass", "db_url": db_url()}]}],
        )
        report = await self.run_report(values, integrations=("template", "sql", "group", "utility_meter", "filter"))
        rows = self.definitions(report)
        self.assertEqual(rows["template[0].sensor[0]"]["withheld"], [
            "template[0].db_url",
            "template[0].sensor[0].attributes.db_url", "template[0].sensor[0].attributes.password",
            "template[0].sensor[0].attributes.api_key", "template[0].sensor[0].attributes.database_connection",
            "template[0].sensor[0].variables[0].db_url", "template[0].sensor[0].variables[0].password",
            "template[0].sensor[0].variables[0].api_key", "template[0].sensor[0].variables[0].database_connection"])
        self.assertEqual(rows["template[0].sensor[0]"]["fields"]["attributes"], {"label": "kept"})
        self.assertEqual(rows["sql[0]"]["withheld"], ["sql[0].db_url"])
        self.assertEqual(rows["sql[0]"]["unrecognized_key_count"], 1)
        self.assertEqual(rows["group.lights"]["withheld"], ["group.lights.db_url"])
        self.assertEqual(rows["utility_meter.meter"]["withheld"], ["utility_meter.meter.db_url"])
        self.assertEqual(rows["sensor[0]"]["withheld"], ["sensor[0].db_url"])
        self.assertEqual(rows["sensor[1]"]["withheld"], ["sensor[1].filters[0].db_url"])
        self.assertEqual(rows["sensor[1]"]["fields"]["filters"], [{"filter": "lowpass"}])
        text = json.dumps(report)
        for scheme in ("postgresql", "mysql", "mssql", "sqlite"):
            self.assertNotIn(scheme + syn.SCHEME_SEPARATOR, text)

    async def test_references_markers_and_unsupported_values_are_withheld(self):
        values = owner_yaml(sql=[{"name": "Ref", "query": {"secret_ref": SECRET_NAME}, "column": {"env_var_ref": ENV_NAME},
                                  "unit_of_measurement": "postgresql" + syn.SCHEME_SEPARATOR + "[REDACTED:connection_uri]",
                                  "value_template": "[TRUNCATED:120]", "icon": object(),
                                  "availability": {7: DB_PASSWORD, "ok": True}}])
        report = await self.run_report(values, integrations=("sql",))
        row = self.definitions(report)["sql[0]"]
        self.assertEqual(row["fields"], {
            "name": "Ref", "query": {"withheld": "secret_ref"}, "column": {"withheld": "env_var_ref"},
            "unit_of_measurement": {"withheld": "reader_redaction"},
            "value_template": {"withheld": "reader_redaction"}, "icon": {"withheld": "unsupported_value"},
            "availability": {"ok": True}})
        self.assertEqual(sorted(row["withheld"]), sorted([
            "sql[0].query", "sql[0].column", "sql[0].unit_of_measurement", "sql[0].value_template", "sql[0].icon",
            "sql[0].availability.<key>"]))

    async def test_database_url_inside_allowlisted_text_is_withheld(self):
        # The reader redacts this already; the projection does not depend on it.
        values = owner_yaml(sql=[{**owner_sql()[0], "query": "SELECT 1 -- " + db_url()}],
                            template=[{"sensor": [{"name": "Uri", "state": "{{ '" + db_url("mysql") + "' }}",
                                                   "attributes": {"note": [db_url("sqlite")]}}]}])
        report = await self.run_report(values, integrations=("sql", "template"))
        rows = self.definitions(report)
        self.assertEqual(rows["sql[0]"]["fields"]["query"], {"withheld": "credential_uri"})
        self.assertEqual(rows["sql[0]"]["withheld"], ["sql[0].db_url", "sql[0].query"])
        self.assertEqual(rows["template[0].sensor[0]"]["fields"]["state"], {"withheld": "credential_uri"})
        self.assertEqual(rows["template[0].sensor[0]"]["fields"]["attributes"],
                         {"note": [{"withheld": "credential_uri"}]})
        # R2-M1: anything withheld makes the report partial, one omission per item.
        self.assertEqual(report["omissions"], ["withheld:definitions[0]", "withheld:definitions[1]"])
        self.assert_partial(report)

    async def test_text_the_projection_keeps_still_passes_the_final_sanitizer(self):
        values = owner_yaml(template=[{"sensor": [{"name": "Header", "state": "Bearer " + syn.needle("bearer")}]}])
        report = await self.run_report(values, integrations=("template",))
        self.assert_partial(report, "redacted_by_policy")
        self.assertNotIn(syn.needle("bearer"), json.dumps(report))

    async def test_known_secret_in_a_definition_is_withheld_and_partial(self):
        values = owner_yaml(template=[{"sensor": [{"name": "Leak", "state": "{{ '" + HA_TOKEN + "' }}"}]}])
        report = await self.run_report(values, integrations=("template",))
        self.assert_partial(report, "credential_text_withheld")
        self.assertEqual(len(report["definitions"]), 1)
        self.assertEqual(report["definitions"][0]["fields"]["state"], "[withheld:credential_text]")

    async def test_config_entry_reason_text_is_withheld(self):
        core = owner_core()
        core["config_entries/get"]["template"] = [
            {"entry_id": UI_ENTRY, "domain": "template", "title": "x" * 300, "state": "setup_error",
             "disabled_by": None, "reason": REASON_TEXT, "error_reason_translation_key": REASON_TEXT},
            {"entry_id": "second", "domain": "template", "title": None, "state": "invented", "disabled_by": "robot",
             "reason": "", "error_reason_translation_key": "auth_failed"},
            {"entry_id": "bad id!", "domain": "template", "state": "loaded"},
            {"entry_id": "foreign", "domain": "sql", "state": "loaded"},
        ]
        report = await self.run_report(core_values=core, integrations=("template",))
        entries = {row["entry_id"]: row for row in report["ui_entries"]}
        self.assertEqual(set(entries), {UI_ENTRY, "second"})
        # Correction batch 2: an over-length title is withheld whole, never truncated.
        self.assertEqual(entries[UI_ENTRY]["title"], "[withheld:unverifiable_text]")
        self.assert_partial(report, "unverifiable_text_withheld")
        self.assertEqual((entries[UI_ENTRY]["reason_present"], entries[UI_ENTRY]["error_reason_translation_key"]),
                         (True, None))
        self.assertEqual(entries["second"], {
            "entry_id": "second", "domain": "template", "title": None, "state": "unrecognized",
            "disabled_by": "unrecognized", "reason_present": False, "error_reason_translation_key": "auth_failed",
            "readability": "definition_not_readable_without_flow", "entity_ids": [], "entity_count": 0})
        self.assertEqual(entries[UI_ENTRY]["entity_ids"], ["sensor.ui_template"])

    async def test_provider_exception_text_never_in_public_failure(self):
        core = owner_core()
        for kind in ("states", "config/entity_registry/list", "entity/source", "config", "config_entries/get",
                     "manifest/get"):
            core[kind] = RuntimeError(EXCEPTION_TEXT)
        values = {path: RuntimeError(EXCEPTION_TEXT) for path in owner_yaml()}
        env = setup(values, core)
        self.addCleanup(end_request, env.token)
        with self.assertRaises(c.ConfigurationDefinitionsError) as raised:
            await env.service.report(OWNER_ENTITIES)
        self.assertEqual(raised.exception.reason, "sources_unavailable")
        self.assertNotIn(EXCEPTION_TEXT, repr(raised.exception.details) + str(raised.exception))


# A lowercase synthetic secret that is also a valid entity object ID, domain
# slug and entry ID, so it can reach every identifier-bearing field.
ENTITY_SECRET = syn.needle("definitionsweep").lower()


def sweep_inputs():
    secret = ENTITY_SECRET
    values = owner_yaml(
        sql=[{"name": secret, "unique_id": secret, "query": f"SELECT '{secret}';", "column": secret}],
        group={secret: {"entities": ["light." + secret]}},
        sensor=[{"platform": secret, "name": "x"},
                {"platform": "derivative", "source": "sensor." + secret, "name": "Sweep Rate"}],
        template=[{"sensor": [{"name": "Sweep", "state": "1",
                               "attributes": {secret: 1, "password_" + secret: 2}}]}],
    )
    core = owner_core()
    core["states"] += [state("sensor." + secret), state("light." + secret, "unavailable"),
                       state("sensor.sweep_rate", "unavailable")]
    core["config/entity_registry/list"] += [
        registry_row("sensor." + secret, "sql", secret),
        registry_row("sensor.ui_" + secret, "template", "uid", entry="entry_" + secret)]
    core["entity/source"]["sensor." + secret] = {"domain": "sql"}
    core["config_entries/get"]["template"] = [
        {"entry_id": "entry_" + secret, "domain": "template", "title": secret, "state": "loaded",
         "disabled_by": None, "reason": None, "error_reason_translation_key": secret}]
    core["config_entries/get"][c.COMPONENT_DOMAIN] = [
        {"entry_id": "comp_" + secret, "domain": c.COMPONENT_DOMAIN, "title": secret, "state": "loaded",
         "disabled_by": None}]
    core["manifest/get"] = {"domain": c.COMPONENT_DOMAIN, "version": "1.2.0"}
    entity_ids = ("sensor." + secret, "sensor.ui_" + secret, "group." + secret, "sensor.sweep_rate")
    return values, core, entity_ids, ("sql", "template", "group")


class PlantedSecretSweepTests(Base):
    """A planted secret is removed from EVERY output field by the one exit sanitizer."""

    FIELDS = ("schema", "observed_at", "core_pin", "scope", "entities", "definitions", "ui_entries",
              "capability", "packages", "coverage", "sources", "omissions", "complete", "completeness")

    async def sweep(self, known_secrets):
        values, core, entity_ids, integrations = sweep_inputs()
        env = setup(values, core, known_secrets=known_secrets)
        env.reader.provenance = "synthetic reader " + ENTITY_SECRET
        try:
            return await env.service.report(entity_ids, integrations)
        finally:
            end_request(env.token)

    async def test_control_reaches_every_identifier_bearing_field(self):
        control = await self.sweep(())
        self.assertEqual(set(control), {*self.FIELDS, "upstream_calls"})
        for field in ("scope", "entities", "definitions", "ui_entries", "capability", "coverage"):
            self.assertIn(ENTITY_SECRET, json.dumps(control[field]), field)
        self.assertIn(f"sensor:platform:{ENTITY_SECRET}", control["coverage"]["definitions_not_projected"])
        self.assertTrue(any(ENTITY_SECRET in item for d in control["definitions"] for item in d["withheld"]))
        self.assertEqual(control["omissions"], ["definitions_not_projected", "withheld:definitions[0]"])

    async def test_protected_run_removes_it_from_every_field(self):
        report = await self.sweep((HA_TOKEN, ACCESS_SECRET, ENTITY_SECRET))
        for field in self.FIELDS:
            self.assertFalse(any(ENTITY_SECRET in text for text in walk_strings({field: report[field]})), field)
        self.assertNotIn(ENTITY_SECRET, json.dumps(report))
        self.assertEqual(len(report["entities"]), 4)
        self.assertEqual(len(report["definitions"]), 3 + 1)
        self.assertIn("credential_text_withheld", report["omissions"])
        self.assertFalse(report["complete"])
        self.assertEqual(report["completeness"], "partial")


class MatchingTests(Base):
    """Match quality: exact, heuristic with confidence, ambiguous, unmatched, not found."""

    def match(self, report, entity_id):
        return self.entities(report)[entity_id]["definition"]

    async def test_exact_config_entry_lists_the_ui_entry(self):
        core = owner_core()
        core["states"].append(state("sensor.ui_template"))
        report = await self.run_report(core_values=core, entity_ids=("sensor.ui_template",))
        self.assertEqual(self.match(report, "sensor.ui_template"), {
            "method": "exact_config_entry", "confidence": "exact", "definition_ids": [],
            "config_entry_id": UI_ENTRY, "readability": "definition_not_readable_without_flow", "reason": None})
        self.assertEqual(report["ui_entries"], [{
            "entry_id": UI_ENTRY, "domain": "template", "title": "UI template helper", "state": "loaded",
            "disabled_by": None, "reason_present": False, "error_reason_translation_key": None,
            "readability": "definition_not_readable_without_flow", "entity_ids": ["sensor.ui_template"],
            "entity_count": 1}])
        self.assertIn("config_entries/get:template", self.env.calls)
        self.assertEqual(report["definitions"], [])

    async def test_heuristic_confidence_levels(self):
        cases = {}
        core = owner_core()
        del core["entity/source"]["sensor.energy_rate"]
        cases["platform_unconfirmed"] = (owner_yaml(), core, "sensor.energy_rate")
        cases["sources_incomplete"] = (
            owner_yaml(kwargs={"binary_sensor": {"completeness": "partial",
                                                 "limitations": ["value_truncated:3 nodes omitted"]}}),
            owner_core(), "sensor.energy_rate")
        core = owner_core()
        core["states"].append(state("sensor.energy_rate_2"))
        core["entity/source"]["sensor.energy_rate_2"] = {"domain": "derivative"}
        cases["collision_suffix_removed"] = (owner_yaml(), core, "sensor.energy_rate_2")
        core = owner_core()
        core["config/entity_registry/list"] = None
        cases["registry_unavailable"] = (owner_yaml(), core, "sensor.energy_rate")
        expected_rows = {
            "platform_unconfirmed": ("medium", "platform_unconfirmed"),
            "sources_incomplete": ("medium", "sources_incomplete"),
            "collision_suffix_removed": ("low", "collision_suffix_removed"),
            "registry_unavailable": ("medium", "sources_incomplete"),
        }
        for name, (values, core, entity_id) in cases.items():
            with self.subTest(name):
                report = await self.run_report(values, core, entity_ids=(entity_id,))
                self.assertEqual(self.match(report, entity_id), {
                    "method": "heuristic", "confidence": expected_rows[name][0], "definition_ids": ["sensor[1]"],
                    "reason": expected_rows[name][1]})
                self.assertEqual([d["definition_id"] for d in report["definitions"]], ["sensor[1]"])

    async def test_heuristic_high_requires_every_signal(self):
        report = await self.run_report(entity_ids=("sensor.energy_rate",))
        self.assertEqual(self.match(report, "sensor.energy_rate"), {
            "method": "heuristic", "confidence": "high", "definition_ids": ["sensor[1]"], "reason": None})
        definition = self.definitions(report)["sensor[1]"]["expected_entities"][0]["match"]
        self.assertEqual(definition, {"entity_id": "sensor.energy_rate", "method": "heuristic", "reason": None,
                                      "confidence": "high"})

    async def test_ambiguous_is_never_resolved(self):
        sensors = [*owner_sensors(), {"platform": "derivative", "source": "sensor.gas_total", "name": "Energy Rate"}]
        report = await self.run_report(owner_yaml(sensor=sensors), entity_ids=("sensor.energy_rate",))
        self.assertEqual(self.match(report, "sensor.energy_rate"), {
            "method": "ambiguous", "confidence": None, "definition_ids": ["sensor[1]", "sensor[3]"],
            "reason": "several_definitions_derive_this_entity_id"})
        # Neither definition's dependencies are checked for an unresolved match.
        self.assertEqual(self.entities(report)["sensor.energy_rate"]["diagnosis"],
                         {"classification": "undetermined", "reasons": ["state_unavailable", "definition_not_matched"]})
        rows = self.definitions(report)
        for key in ("sensor[1]", "sensor[3]"):
            self.assertEqual(rows[key]["expected_entities"][0]["match"],
                             {"entity_id": "sensor.energy_rate", "method": "ambiguous", "confidence": None,
                              "reason": "several_definitions_derive_this_entity_id"})
        sql = [*owner_sql(), {**owner_sql()[0], "name": "Copy"}]
        report = await self.run_report(owner_yaml(sql=sql), entity_ids=("sensor.database_free_space",))
        self.assertEqual(self.match(report, "sensor.database_free_space"), {
            "method": "ambiguous", "confidence": None, "definition_ids": ["sql[0]", "sql[2]"],
            "reason": "several_definitions_share_this_unique_id"})

    async def test_platform_filters_cross_integration_candidates(self):
        template = [{"sensor": [{"name": "Energy Rate", "state": "1"}]}]
        report = await self.run_report(owner_yaml(template=template), entity_ids=("sensor.energy_rate",))
        self.assertEqual(self.match(report, "sensor.energy_rate")["definition_ids"], ["sensor[1]"])
        self.assertEqual(self.match(report, "sensor.energy_rate")["confidence"], "high")
        core = owner_core()
        core["entity/source"]["sensor.energy_rate"] = {"domain": "mqtt"}
        report = await self.run_report(owner_yaml(template=template), core, entity_ids=("sensor.energy_rate",))
        self.assertEqual(self.match(report, "sensor.energy_rate"), {
            "method": "unmatched", "confidence": None, "definition_ids": [],
            "reason": "no_definition_derives_this_entity_id"})

    async def test_registered_entity_is_never_matched_by_name(self):
        # sensor[1] has no unique_id and derives sensor.energy_rate by name, but
        # a registered entity cannot come from a uid-less YAML definition.
        core = owner_core()
        core["config/entity_registry/list"].append(registry_row("sensor.energy_rate", "derivative", "elsewhere"))
        report = await self.run_report(core_values=core, entity_ids=("sensor.energy_rate",))
        self.assertEqual(self.match(report, "sensor.energy_rate"), {
            "method": "unmatched", "confidence": None, "definition_ids": [],
            "reason": "no_read_definition_has_this_unique_id"})
        # Only matched definitions are listed for an entity_ids request.
        self.assertEqual(report["definitions"], [])

    async def test_unmatched_not_applicable_and_not_found(self):
        core = owner_core()
        core["config/entity_registry/list"].append(registry_row("sensor.orphan_sql", "sql", "gone"))
        core["states"].append(state("sensor.orphan_sql", "unavailable"))
        report = await self.run_report(core_values=core, entity_ids=(
            "sensor.orphan_sql", "light.kitchen", "sensor.nothing_here"))
        self.assertEqual(self.match(report, "sensor.orphan_sql"), {
            "method": "unmatched", "confidence": None, "definition_ids": [],
            "reason": "no_read_definition_has_this_unique_id"})
        self.assertEqual(self.match(report, "light.kitchen"), {
            "method": "not_applicable", "confidence": None, "definition_ids": [],
            "reason": "platform_outside_supported_integrations"})
        self.assertEqual(self.match(report, "sensor.nothing_here"), {
            "method": "unmatched", "confidence": None, "definition_ids": [],
            "reason": "no_definition_derives_this_entity_id"})
        rows = self.entities(report)
        self.assertEqual(rows["sensor.nothing_here"]["diagnosis"],
                         {"classification": "not_found",
                          "reasons": ["no_state", "definition_absent", "labelled_keys_not_addressable"]})
        self.assertEqual(rows["sensor.nothing_here"]["state"], "absent")
        self.assertEqual(rows["sensor.orphan_sql"]["diagnosis"],
                         {"classification": "undetermined", "reasons": ["state_unavailable", "definition_not_matched"]})
        self.assertEqual(rows["light.kitchen"]["diagnosis"], {"classification": "available", "reasons": []})
        self.assertEqual(report["definitions"], [])

    async def test_unread_config_entry_is_not_observed(self):
        core = owner_core()
        core["config_entries/get"] = {"template": HomeAssistantTimeoutError(), c.COMPONENT_DOMAIN: []}
        report = await self.run_report(core_values=core, entity_ids=("sensor.ui_template",))
        self.assertEqual(self.match(report, "sensor.ui_template"), {
            "method": "unmatched", "confidence": None, "definition_ids": [], "config_entry_id": UI_ENTRY,
            "reason": "config_entry_not_observed"})
        self.assertEqual(self.entities(report)["sensor.ui_template"]["diagnosis"], {
            "classification": "undetermined", "reasons": ["source_failed:config_entries/get", "no_state"]})
        self.assert_partial(report, "source_unavailable:config_entries/get:template")
        self.assertEqual(report["ui_entries"], [])

    async def test_legacy_platform_is_only_a_candidate(self):
        sensors = [{"platform": "sql", "name": "Old SQL", "db_url": db_url(), "query": "SELECT 1;", "column": "x"}]
        report = await self.run_report(owner_yaml(sensor=sensors), entity_ids=("sensor.old_sql",))
        row = self.entities(report)["sensor.old_sql"]
        self.assertEqual(row["definition"]["method"], "unmatched")
        self.assertEqual(row["legacy_candidates"], ["sensor[0]"])
        self.assertEqual(row["diagnosis"], {"classification": "deployment_dependency",
                                            "reasons": ["no_state", "legacy_platform_unsupported"]})
        legacy = self.definitions(report)["sensor[0]"]
        self.assertEqual((legacy["status"], legacy["expected_entities"], legacy["withheld"]),
                         ("unsupported_legacy_platform", [], ["sensor[0].db_url"]))

    async def test_definition_side_results(self):
        sql = [*owner_sql(), {"name": "No Row", "unique_id": "never_registered", "query": "q", "column": "c"}]
        core = owner_core()
        core["states"] = [row for row in core["states"] if row["entity_id"] != "sensor.energy_rate"]
        report = await self.run_report(owner_yaml(sql=sql), core, integrations=("sql", "derivative"))
        rows = self.definitions(report)
        self.assertEqual(rows["sql[2]"]["expected_entities"][0]["match"], {
            "entity_id": None, "method": "unmatched", "reason": "no_registry_entry_with_this_unique_id"})
        self.assertEqual(rows["sensor[1]"]["expected_entities"][0]["match"], {
            "entity_id": None, "method": "unmatched", "reason": "derived_entity_id_not_in_states"})
        self.assertEqual(rows["sql[0]"]["expected_entities"][0]["match"], {
            "entity_id": "sensor.database_free_space", "method": "exact_unique_id", "reason": None,
            "confidence": "exact"})
        core = owner_core()
        core["states"] = HomeAssistantUnavailableError()
        report = await self.run_report(core_values=core, integrations=("derivative",))
        self.assertEqual(self.definitions(report)["sensor[1]"]["expected_entities"][0]["match"], {
            "entity_id": None, "method": "unmatched", "reason": "states_unavailable"})
        self.assertNotIn("entity_states_unknown", report["omissions"])


class PackagesAndIncludeTests(Base):
    """Packages and !include are disclosed, never followed."""

    async def test_package_states(self):
        cases = {
            "present": ({"packages": {"energy": "!include packages/energy.yaml"}}, "present",
                        "definitions_in_packages_not_read"),
            "include": ("!include_dir_named packages", "unreadable", "packages_not_determined"),
            "empty": ({}, "absent", None),
        }
        for name, (value, status, omission) in cases.items():
            with self.subTest(name):
                packages = value["packages"] if type(value) is dict and "packages" in value else value
                report = await self.run_report(owner_yaml(packages=packages), entity_ids=OWNER_ENTITIES[:1])
                self.assertEqual(report["packages"], status)
                self.assertNotIn("homeassistant", self.env.reader.paths)
                if omission:
                    self.assert_partial(report, omission)
                    # A definition elsewhere can still be matched exactly.
                    self.assertEqual(self.entities(report)[OWNER_ENTITIES[0]]["definition"]["method"],
                                     "exact_unique_id")
                else:
                    # Only the listed SQL definition's withheld db_url (R2-M1).
                    self.assertEqual(report["omissions"], ["withheld:definitions[0]"])
                self.assertNotIn("energy.yaml", json.dumps(report))

    async def test_homeassistant_node_decides_a_not_found_packages_key(self):
        for core, status in (({"name": "x"}, "absent"), (MISSING, "absent"),
                             ("!include core.yaml", "unreadable"), ({"packages": "!include p"}, "unreadable")):
            with self.subTest(core=core):
                values = owner_yaml()
                values["homeassistant"] = envelope("homeassistant", core)
                report = await self.run_report(values, integrations=("sql",))
                self.assertEqual(report["packages"], status)
                self.assertEqual(self.env.reader.paths[-2:], ["homeassistant.packages", "homeassistant"])
                self.assertEqual(report["coverage"]["yaml_paths_read"][-1], "homeassistant")
                self.assertNotIn("core.yaml", json.dumps(report))
        values = owner_yaml()
        values["homeassistant"] = HomeAssistantTimeoutError()
        report = await self.run_report(values, integrations=("sql",))
        self.assertEqual(report["packages"], "unreadable")
        self.assert_partial(report, "packages_not_determined",
                            "source_unavailable:configuration_yaml:homeassistant.packages")
        self.assertEqual(source(report, "configuration_yaml:homeassistant.packages")["reason"], "yaml_read_failed")

    async def test_includes_are_disclosed_at_each_level(self):
        partial = {"completeness": "partial", "limitations": ["include_not_followed:x"]}
        values = owner_yaml(
            sql="!include sql.yaml",
            group={"porch": "!include porch.yaml", **owner_group()},
            sensor=["!include sensor_one.yaml", *owner_sensors()],
            template=[{"sensor": [{"name": "Inc", "state": "!include state.yaml"}]}],
            kwargs={path: partial for path in ("sql", "group", "sensor", "template")})
        report = await self.run_report(values, integrations=("sql", "group", "filter", "template"))
        self.assert_partial(report, "include_not_followed:sql", "definition_values_include_not_followed",
                            "source_partial:configuration_yaml:sql")
        rows = self.definitions(report)
        self.assertNotIn("sql[0]", rows)
        self.assertEqual((rows["group.porch"]["status"], rows["group.porch"]["include_not_followed"]),
                         ("include_not_followed", ["group.porch"]))
        self.assertEqual(rows["sensor[0]"]["include_not_followed"], ["sensor[0]"])
        inc = rows["template[0].sensor[0]"]
        self.assertEqual((inc["status"], inc["fields"]["state"], inc["include_not_followed"]),
                         ("include_not_followed", {"include_not_followed": True}, ["template[0].sensor[0].state"]))
        self.assertEqual(source(report, "configuration_yaml:sql"),
                         {"source": "configuration_yaml:sql", "status": "partial",
                          "reason": "include_or_truncation", "observed_at": "2026-10-21T12:00:00Z"})
        for name in ("sql.yaml", "porch.yaml", "sensor_one.yaml", "state.yaml"):
            self.assertNotIn(name, json.dumps(report))
        # group also defines sensor and binary_sensor platforms; filter needs only sensor.
        self.assertEqual(self.env.reader.paths, ["template", "sql", "group", "sensor", "binary_sensor",
                                                 "homeassistant.packages", "homeassistant"])

    async def test_truncated_reader_value_marks_definitions_partial(self):
        values = owner_yaml(kwargs={"sql": {"completeness": "partial",
                                            "limitations": ["value_truncated:9 nodes omitted"]}})
        report = await self.run_report(values, entity_ids=("sensor.database_size",))
        self.assertTrue(self.definitions(report)["sql[1]"]["reader_partial"])
        self.assert_partial(report, "source_partial:configuration_yaml:sql")
        self.assertEqual(self.match(report) if False else self.entities(report)["sensor.database_size"]
                         ["definition"]["method"], "exact_unique_id")


class SourceFailureTests(Base):
    """Every unreadable source is named and makes the report partial."""

    CORE_SOURCES = {"states": "states", "config/entity_registry/list": "config/entity_registry/list",
                    "entity/source": "entity/source", "config": "config",
                    "config_entries/get": "config_entries/get:template", "manifest/get": "manifest/get:ha_mcp_tools"}

    async def test_core_failures_map_to_closed_reasons(self):
        failures = {
            "refused": (AuthorizationError(), "permission_failure"),
            "unavailable": (HomeAssistantTimeoutError(), "timeout"),
            "ha": (HomeAssistantUnavailableError(), "home_assistant_unavailable"),
            "api": (HomeAssistantApiError(details={"status": 500}), "command_failed"),
            "api404": (HomeAssistantApiError(details={"status": 404}), "not_found"),
            "other": (RuntimeError(EXCEPTION_TEXT), "source_unavailable"),
            "invalid": ("not the right shape", "invalid_response"),
        }
        for kind, label in self.CORE_SOURCES.items():
            for name, (failure, reason) in failures.items():
                if kind == "manifest/get" and name == "api404":
                    continue
                with self.subTest(source=kind, failure=name):
                    core = owner_core()
                    if kind == "config_entries/get":
                        core[kind] = {**core[kind], "template": failure}
                    else:
                        core[kind] = failure
                    if name == "invalid" and kind in ("config_entries/get",):
                        core[kind]["template"] = {"not": "a list"}
                    report = await self.run_report(core_values=core, entity_ids=("sensor.ui_template",),
                                                   integrations=("template",))
                    row = source(report, label)
                    status = "refused" if reason == "permission_failure" else "unavailable"
                    self.assertEqual((row["status"], row["reason"]), (status, reason))
                    self.assert_partial(report, f"source_{status}:{label}")
        # Manifest 404 is a complete "not installed" answer, not a failure.
        report = await self.run_report(entity_ids=("sensor.database_size",))
        self.assertEqual(source(report, "manifest/get:ha_mcp_tools")["status"], "complete")
        self.assertTrue(all(row["status"] == "complete" for row in report["sources"]))
        self.assertEqual(report["omissions"], ["withheld:definitions[0]"])

    async def test_yaml_failures(self):
        for name, result, reason in (
                ("raises", RuntimeError(EXCEPTION_TEXT), "yaml_read_failed"),
                ("parse_error", {"success": False, "error_code": "yaml_parse_error"}, "yaml_parse_error"),
                ("local_code_from_upstream", {"success": False, "error_code": "yaml_reader_not_admitted"},
                 "yaml_read_failed"),
                ("unknown_code", {"success": False, "error_code": EXCEPTION_TEXT}, "yaml_read_failed"),
                ("not_a_dict", ["sql"], "yaml_read_failed"),
                ("identity", {**envelope("sql", []), "data": {**envelope("sql", [])["data"],
                                                            "yaml_path": "sensor"}},
                 "yaml_read_response_identity_invalid"),
                ("other_file", {**envelope("sql", []), "data": {**envelope("sql", [])["data"],
                                                              "file": "secrets.yaml"}},
                 "yaml_read_response_identity_invalid"),
                ("fallback", {**envelope("sql", []), "metadata": {"provider": "upstream_read_gateway",
                                                                  "fallback_occurred": True}},
                 "yaml_read_response_identity_invalid")):
            with self.subTest(name):
                values = owner_yaml()
                values["sql"] = result
                report = await self.run_report(values, entity_ids=("sensor.database_size",))
                self.assertIn(reason, c.YAML_FAILURE_CODES)
                self.assertEqual(source(report, "configuration_yaml:sql"),
                                 {"source": "configuration_yaml:sql", "status": "unavailable", "reason": reason,
                                  "observed_at": "2026-10-21T12:00:00Z"})
                self.assertNotIn(EXCEPTION_TEXT, json.dumps(report))
                self.assert_partial(report, "source_unavailable:configuration_yaml:sql")
                # Without the YAML the registered unique_id has no definition.
                self.assertEqual(self.entities(report)["sensor.database_size"]["definition"]["reason"],
                                 "no_read_definition_has_this_unique_id")

    async def test_reader_not_admitted_dispatches_no_yaml(self):
        report = await self.run_report(entity_ids=OWNER_ENTITIES, admitted=False)
        self.assertEqual(self.env.reader.paths, [])
        self.assertEqual(report["upstream_calls"], 0)
        self.assertIsNone(report["coverage"]["yaml_reader"])
        for path in (*DEFINITION_PATHS, "homeassistant.packages"):
            self.assertEqual(source(report, f"configuration_yaml:{path}")["reason"], "yaml_reader_not_admitted")
        self.assertEqual(report["packages"], "unreadable")
        self.assert_partial(report, "packages_not_determined")
        self.assertEqual(report["definitions"], [])
        # Core reads still answer: the registry, states and diagnosis.
        self.assertEqual(self.entities(report)["sensor.energy_rate"]["diagnosis"]["classification"],
                         "undetermined")
        self.assertEqual(source(report, "states")["status"], "complete")

    async def test_every_source_failed_is_an_error_without_text(self):
        core = {kind: RuntimeError(EXCEPTION_TEXT) for kind in self.CORE_SOURCES}
        values = {path: RuntimeError(EXCEPTION_TEXT) for path in owner_yaml()}
        for entity_ids in (OWNER_ENTITIES, ()):
            with self.subTest(entity_ids=entity_ids):
                env = setup(values, core)
                try:
                    with self.assertRaises(c.ConfigurationDefinitionsError) as raised:
                        await env.service.report(entity_ids, ("sql",))
                finally:
                    end_request(env.token)
                self.assertEqual(raised.exception.reason, "sources_unavailable")
                self.assertNotIn(EXCEPTION_TEXT, str(raised.exception.details))

    async def test_states_failure_with_entities_is_disclosed(self):
        core = owner_core()
        core["states"] = HomeAssistantTimeoutError()
        report = await self.run_report(core_values=core, entity_ids=OWNER_ENTITIES)
        self.assert_partial(report, "entity_states_unknown", "source_unavailable:states")
        for row in report["entities"]:
            self.assertEqual(row["state"], None)
            self.assertEqual(row["diagnosis"], {"classification": "undetermined", "reasons": ["source_failed:states"]})
        # Matching by unique_id needs only the registry and YAML.
        self.assertEqual(self.entities(report)["sensor.database_size"]["definition"]["method"], "exact_unique_id")
        self.assertEqual(self.entities(report)["sensor.energy_rate"]["definition"]["confidence"], "high")


class DiagnosisTests(Base):
    """Each classification rests only on the signals it names."""

    async def diagnose(self, core, entity_id, values=None, **kwargs):
        report = await self.run_report(values, core, entity_ids=(entity_id,), **kwargs)
        return self.entities(report)[entity_id]["diagnosis"]

    async def test_registry_disabled(self):
        for by, reason in (("user", "registry_disabled:user"), (UNKNOWN_VALUE, "registry_disabled:unrecognized")):
            with self.subTest(by=by):
                core = owner_core()
                core["config/entity_registry/list"][1]["disabled_by"] = by
                core["states"] = [row for row in core["states"] if row["entity_id"] != "sensor.database_size"]
                self.assertEqual(await self.diagnose(core, "sensor.database_size"),
                                 {"classification": "disabled", "reasons": [reason]})

    async def test_config_entry_states(self):
        for entry, expected_row in (
                ({"state": "setup_retry"}, ("transient", ["config_entry_state:setup_retry"])),
                ({"state": "setup_in_progress"}, ("transient", ["config_entry_state:setup_in_progress"])),
                ({"state": "setup_error"}, ("deployment_dependency", ["config_entry_state:setup_error"])),
                ({"state": "not_loaded"}, ("deployment_dependency", ["config_entry_state:not_loaded"])),
                ({"state": UNKNOWN_VALUE}, ("deployment_dependency", ["config_entry_state:unrecognized"])),
                ({"disabled_by": "user", "state": "not_loaded"}, ("disabled", ["config_entry_disabled:user"])),
                ({"state": "loaded"}, ("undetermined", ["config_entry_state:loaded", "no_state"]))):
            with self.subTest(entry=entry):
                core = owner_core()
                core["config_entries/get"]["template"][0].update(entry)
                self.assertEqual(await self.diagnose(core, "sensor.ui_template"),
                                 {"classification": expected_row[0], "reasons": expected_row[1]})

    async def test_integration_and_platform_not_loaded(self):
        for components, reason in (([c for c in COMPONENTS if not c.startswith("sql")], "integration_not_loaded:sql"),
                                   ([c for c in COMPONENTS if c != "sql.sensor"], "platform_not_loaded:sql.sensor")):
            with self.subTest(reason=reason):
                core = owner_core()
                core["config"]["components"] = components
                core["states"] = [row for row in core["states"] if row["entity_id"] != "sensor.database_size"]
                self.assertEqual(await self.diagnose(core, "sensor.database_size"),
                                 {"classification": "deployment_dependency", "reasons": ["no_state", reason]})

    async def test_missing_inventories_leave_it_undetermined(self):
        core = owner_core()
        core["config"] = HomeAssistantTimeoutError()
        core["states"] = [row for row in core["states"] if row["entity_id"] != "sensor.database_size"]
        self.assertEqual(await self.diagnose(core, "sensor.database_size"),
                         {"classification": "undetermined", "reasons": ["no_state", "source_failed:config"]})
        core = owner_core()
        core["config/entity_registry/list"] = HomeAssistantTimeoutError()
        self.assertEqual(await self.diagnose(core, "sensor.nothing_here"), {
            "classification": "undetermined", "reasons": ["no_state", "source_failed:config/entity_registry/list"]})

    async def test_dependencies(self):
        core = owner_core()
        core["states"] = [row for row in core["states"] if row["entity_id"] != "sensor.outdoor_temperature"]
        core["states"].append(state("sensor.outdoor_temperature_smoothed", "unknown"))
        core["states"] = [row for row in core["states"]
                          if (row["entity_id"], row["state"]) != ("sensor.outdoor_temperature_smoothed", "1")]
        self.assertEqual(await self.diagnose(core, "sensor.outdoor_temperature_smoothed"), {
            "classification": "dependency_unavailable",
            "reasons": ["state_unknown", "source_entity_missing:sensor.outdoor_temperature"]})
        core = owner_core()
        core["states"].append(state("sensor.water_total_2", "unavailable"))
        core["states"] = [state("sensor.water_rate", "unavailable") if row["entity_id"] == "sensor.water_rate"
                          else row for row in core["states"]]
        self.assertEqual(await self.diagnose(core, "sensor.water_rate"),
                         {"classification": "entity_level", "reasons": ["state_unavailable"]})

    async def test_state_values_are_never_copied(self):
        core = owner_core()
        core["states"] = [{**row, "state": UNKNOWN_VALUE} if row["entity_id"] == "sensor.database_size" else row
                          for row in core["states"]]
        report = await self.run_report(core_values=core, entity_ids=("sensor.database_size",))
        self.assertNotIn(UNKNOWN_VALUE, json.dumps(report))
        self.assertEqual(self.entities(report)["sensor.database_size"]["state"], "available")
        self.assertFalse(report["coverage"]["state_values_copied"])


def component_entries(*states, disabled=None):
    return diagnosis.entry_rows([
        {"entry_id": f"comp{index}", "domain": c.COMPONENT_DOMAIN, "title": "Tools", "state": state_,
         "disabled_by": disabled} for index, state_ in enumerate(states)], c.COMPONENT_DOMAIN)


class CapabilityTests(unittest.TestCase):
    """The ``ha_mcp_tools`` block uses only Core's reads, never the component's."""

    def check(self, expected_pair, *args, signals=None):
        block = diagnosis.capability(*args)
        self.assertEqual((block["classification"], block["reason"]), expected_pair)
        self.assertEqual(block["component_commands"], "not_used_owner_decision")
        if signals is not None:
            self.assertEqual(block["signals"], signals)
        return block

    def test_classifications(self):
        loaded = {c.COMPONENT_DOMAIN}
        manifest = {"domain": c.COMPONENT_DOMAIN, "version": "1.2.0"}
        block = self.check(("deployment_dependency", "not_installed"), None, "not_found", [], None, set(),
                           signals=["manifest_not_found"])
        self.assertEqual((block["installed"], block["loaded"]), (False, False))
        self.check(("permission", "permission_failure"), None, "permission_failure", None, None, None)
        self.check(("permission", "permission_failure"), manifest, None, None, "permission_failure", loaded)
        self.check(("deployment_dependency", "not_configured"), manifest, None, [], None, loaded)
        self.check(("deployment_dependency", "config_entry_disabled"), manifest, None,
                   component_entries("not_loaded", disabled="user"), None, loaded)
        self.check(("transient", "config_entry_not_yet_loaded"), manifest, None,
                   component_entries("setup_retry"), None, set())
        self.check(("deployment_dependency", "config_entry_not_loaded"), manifest, None,
                   component_entries("setup_error"), None, set(),
                   signals=["config_entry_state:setup_error"])
        self.check(("upstream_feature_gap", "component_version_before_helper_listing"),
                   {"version": "1.0.3"}, None, component_entries("loaded"), None, loaded)
        block = self.check(("undetermined", "component_loaded_cause_not_observable_without_component_commands"),
                           manifest, None, component_entries("loaded", "setup_error"), None, loaded)
        self.assertEqual((block["installed"], block["version"], block["loaded"]), (True, "1.2.0", True))
        self.check(("undetermined", "component_loaded_cause_not_observable_without_component_commands"),
                   {"version": UNKNOWN_VALUE}, None, component_entries("loaded"), None, loaded,
                   signals=["manifest_version_unparsed", "config_entry_state:loaded"])
        self.check(("transient", "source_unavailable"), None, "timeout", None, "timeout", None,
                   signals=["manifest:timeout", "config_entries:timeout", "config:unavailable"])
        self.check(("undetermined", "signals_incomplete"), None, "command_failed", None, "invalid_response", loaded)
        self.check(("undetermined", "signals_incomplete"), "not a manifest", None, None, "command_failed", loaded,
                   signals=["manifest:invalid_response", "config_entries:command_failed"])

    def test_entry_rows_keep_identity_and_state_only(self):
        rows = diagnosis.entry_rows([
            {"entry_id": "ok_1", "domain": "template", "title": "T" * 300, "state": "loaded", "disabled_by": None,
             "reason": EXCEPTION_TEXT, "error_reason_translation_key": "bad key " + UNKNOWN_VALUE,
             "data": {"password": NESTED_PASSWORD}, "options": {"state": UNKNOWN_VALUE}},
            {"entry_id": "../bad", "domain": "template"}, {"entry_id": "other", "domain": "sql"}, "x"], "template")
        # Correction batch 2: an over-length title is withheld whole, never truncated.
        self.assertEqual(rows, [{"entry_id": "ok_1", "domain": "template", "title": "[withheld:unverifiable_text]",
                                 "state": "loaded", "disabled_by": None, "reason_present": True,
                                 "error_reason_translation_key": None}])
        self.assertIsNone(diagnosis.entry_rows({"entry_id": "x"}, "template"))

    def test_owner_report_capability_block(self):
        async def run():
            core = owner_core()
            core["manifest/get"] = {"domain": c.COMPONENT_DOMAIN, "version": "1.2.0"}
            core["config"]["components"] = [*COMPONENTS, c.COMPONENT_DOMAIN]
            core["config_entries/get"][c.COMPONENT_DOMAIN] = [
                {"entry_id": COMPONENT_ENTRY, "domain": c.COMPONENT_DOMAIN, "title": "Tools", "state": "loaded",
                 "disabled_by": None}]
            env = setup(None, core)
            try:
                report = await env.service.report(("sensor.database_size",), ())
            finally:
                end_request(env.token)
            return report, env
        import asyncio
        report, env = asyncio.run(run())
        self.assertEqual(report["capability"]["classification"], "undetermined")
        self.assertEqual(report["capability"]["entries"][0]["entry_id"], COMPONENT_ENTRY)
        self.assertEqual([p for p in env.socket.payloads if p["type"] != "config_entries/get"
                          and p["type"] not in ("config/entity_registry/list", "entity/source")],
                         [{"type": "manifest/get", "integration": c.COMPONENT_DOMAIN}])
        self.assertFalse(any(p["type"].startswith(c.COMPONENT_DOMAIN) for p in env.socket.payloads))


class AuthorityTests(Base):
    STAGES = ("yaml:template", "yaml:sql", "yaml:sensor", "yaml:homeassistant.packages", "yaml:homeassistant",
              "states", "config/entity_registry/list", "entity/source", "config", "config_entries/get:template",
              f"config_entries/get:{c.COMPONENT_DOMAIN}", "manifest/get")

    async def test_unavailable_core_route_dispatches_nothing(self):
        env = setup()
        self.addCleanup(end_request, env.token)
        env.core.available = False
        with self.assertRaises(c.ConfigurationDefinitionsError) as caught:
            await env.service.report(OWNER_ENTITIES, ())
        self.assertEqual(caught.exception.reason, "authority_unavailable")
        self.assertEqual(env.calls, [])

    async def test_anonymous_caller_and_missing_authorizer_refused_before_dispatch(self):
        env = setup(caller="anonymous")
        self.addCleanup(end_request, env.token)
        with self.assertRaises(c.ConfigurationDefinitionsError):
            await env.service.report(OWNER_ENTITIES, ())
        env.telemetry.caller_id = "synthetic_authenticated_caller"
        env.telemetry.core_dispatch_authorizer = None
        with self.assertRaises(c.ConfigurationDefinitionsError):
            await env.service.report(OWNER_ENTITIES, ())
        self.assertEqual(env.calls, [])

    async def test_no_request_refused(self):
        env = setup()
        end_request(env.token)
        with self.assertRaises(c.ConfigurationDefinitionsError):
            await env.service.report(OWNER_ENTITIES, ())
        self.assertEqual(env.calls, [])

    async def test_invalid_service_arguments_refused_before_dispatch(self):
        env = setup()
        self.addCleanup(end_request, env.token)
        for entity_ids, integrations in (((), ()), (("Sensor.X",), ()), ((), ("mqtt",)), ("sensor.x", ()),
                                         ((), "sql"), (("sensor.x", "sensor.x"), ())):
            with self.subTest(entity_ids=entity_ids, integrations=integrations):
                with self.assertRaises(ValueError):
                    await env.service.report(entity_ids, integrations)
        self.assertEqual(env.calls, [])

    async def test_authority_change_during_any_read_fails_closed(self):
        for stage in self.STAGES:
            for change in ("generation", "version", "retired"):
                with self.subTest(stage=stage, change=change):
                    env = setup()

                    def bump(item, stage=stage, change=change, core=env.core):
                        if item == stage:
                            if change == "generation":
                                core.generation += 1
                            elif change == "version":
                                core.current_observation = SimpleNamespace(version="2026.11.0")
                            else:
                                core.available = False
                    env.reader.hook = env.rest.hook = env.socket.hook = bump
                    try:
                        with self.assertRaises(c.ConfigurationDefinitionsError) as caught:
                            await env.service.report(("sensor.ui_template",), ("template", "sql"))
                        self.assertEqual(caught.exception.reason, "authority_unavailable")
                        self.assertEqual(env.calls[-1], stage)
                    finally:
                        end_request(env.token)

    async def test_yaml_result_read_under_changed_authority_is_not_projected(self):
        env = setup()
        self.addCleanup(end_request, env.token)
        authority = env.service.authority()
        env.reader.hook = lambda item: setattr(env.core, "generation", env.core.generation + 1)
        with self.assertRaises(c.ConfigurationDefinitionsError) as caught:
            await env.service._yaml(env.reader, "sql", authority)
        self.assertEqual(caught.exception.reason, "authority_unavailable")
        self.assertEqual(env.reader.paths, ["sql"])

    async def test_runtime(self):
        with self.assertRaises(c.ConfigurationDefinitionsError) as caught:
            ConfigurationDefinitionsRuntime().require()
        self.assertEqual(caught.exception.reason, "authority_unavailable")
        runtime = ConfigurationDefinitionsRuntime()
        factory = Mock(return_value=None)
        runtime.configure(Mock(), Mock(), SyntheticCore(), settings(), yaml_reader_factory=factory)
        service = runtime.require()
        self.assertEqual(service.known_secrets, (ACCESS_SECRET, HA_TOKEN))
        self.assertIs(service.yaml_reader_factory, factory)
        factory.assert_not_called()

    async def test_reader_is_captured_once_per_report(self):
        env = setup()
        self.addCleanup(end_request, env.token)
        factory = Mock(return_value=env.reader)
        env.service.yaml_reader_factory = factory
        await env.service.report(OWNER_ENTITIES, ())
        factory.assert_called_once_with()

    async def test_closed_error_reasons(self):
        from ha_mcp_engineering.errors import ErrorCode
        self.assertEqual(c.ConfigurationDefinitionsError(UNKNOWN_VALUE).reason, "sources_unavailable")
        self.assertNotIn(UNKNOWN_VALUE, str(c.ConfigurationDefinitionsError(UNKNOWN_VALUE).details))
        self.assertEqual(c.ConfigurationDefinitionsError("invalid_arguments").code, ErrorCode.INVALID_REQUEST)
        self.assertEqual(c.ConfigurationDefinitionsError("authority_unavailable").code, ErrorCode.PROVIDER_UNAVAILABLE)
        self.assertEqual(c.ConfigurationDefinitionsError("sources_unavailable").code, ErrorCode.ANALYSIS_UNAVAILABLE)

    async def test_telemetry_on_partial(self):
        core = owner_core()
        core["entity/source"] = HomeAssistantTimeoutError()
        env = setup(None, core)
        self.addCleanup(end_request, env.token)
        env.telemetry.error_code = None
        report = await env.service.report(OWNER_ENTITIES, ())
        self.assertFalse(report["complete"])
        self.assertIsNone(env.telemetry.error_code)
        self.assertEqual((env.telemetry.result_status, env.telemetry.completeness), ("partial", "partial"))
        context = env.telemetry.audit_context
        self.assertEqual((context["provider"], context["fallback"], context["upstream_calls"], context["entity_count"]),
                         ("engineering", "none", 8, 6))
        self.assertIn({"source": "entity/source", "status": "unavailable", "reason": "timeout"},
                      context["source_outcomes"])
        self.assertNotIn("sensor.", json.dumps(context))


class BudgetTests(Base):
    async def test_definition_limit_and_response_budget(self):
        sql = [{"name": f"Query {n}", "unique_id": f"q{n}", "query": "SELECT " + "x" * 200 + ";", "column": "x",
                "db_url": db_url()} for n in range(210)]
        report = await self.run_report(owner_yaml(sql=sql), integrations=("sql",))
        self.assert_partial(report, "definitions_not_listed_limit", "definitions_not_listed_response_bytes")
        self.assertLess(len(report["definitions"]), c.MAX_DEFINITIONS)
        self.assertEqual(report["coverage"]["definitions_found"], 210 + 3)
        self.assertLessEqual(len(json.dumps(report, separators=(",", ":"))), c.RESPONSE_BUDGET)
        # The count follows the rows actually listed, and the trim stays close
        # to the budget rather than dropping more than needed.
        self.assertEqual(report["coverage"]["definitions_listed"], len(report["definitions"]))
        self.assertGreater(len(json.dumps(report, separators=(",", ":"))), c.RESPONSE_BUDGET - 2_000)

    async def test_budget_trims_ui_entries_after_definitions(self):
        sql = [{"name": f"Query {n}", "unique_id": f"q{n}", "query": "SELECT " + "x" * 3000 + ";",
                "column": "x"} for n in range(5)]
        core = owner_core()
        core["config_entries/get"]["sql"] = [
            {"entry_id": f"entry{n}", "domain": "sql", "title": "T" * 250, "state": "loaded",
             "disabled_by": None} for n in range(50)]
        values = owner_yaml(sql=sql, template=[{"sensor": [{"name": f"Long {n}", "state": "{{ " + "1 + " * 900
                                                            + "1 }}"} for n in range(12)]}])
        report = await self.run_report(values, core, integrations=("sql", "template"))
        self.assertLessEqual(len(json.dumps(report, separators=(",", ":"))), c.RESPONSE_BUDGET)
        self.assertEqual(report["coverage"]["definitions_listed"], len(report["definitions"]))
        self.assert_partial(report, "definitions_not_listed_response_bytes")

    async def test_returned_report_is_exactly_the_sanitizer_output(self):
        """The single exit: what the caller gets is the last sanitizer output,
        omissions included, with nothing added afterwards."""

        outputs = []
        real = service_module.sanitize_untrusted_data

        def spy(value, **kwargs):
            result = real(value, **kwargs)
            outputs.append((value, result.value, kwargs))
            return result
        core = owner_core()
        core["entity/source"] = HomeAssistantTimeoutError()
        with patch.object(service_module, "sanitize_untrusted_data", spy):
            report = await self.run_report(owner_yaml(sql=[{"name": "Long", "query": "SELECT " + "x" * 5000,
                                                           "column": "x"}]), core, entity_ids=OWNER_ENTITIES,
                                             integrations=("sql",))
        envelope, value, kwargs = outputs[-1]
        self.assertIs(report, value)
        self.assertEqual(kwargs, {"known_secrets": (HA_TOKEN, ACCESS_SECRET), "max_string": c.MAX_TEXT})
        self.assertIn("source_unavailable:entity/source", envelope["omissions"])
        self.assertIn("redacted_by_policy", envelope["omissions"])
        self.assertEqual(report["omissions"], envelope["omissions"])

    def test_overflow_estimate(self):
        rows = [{"n": "x" * 10}] * 5
        self.assertEqual(service_module._overflow(rows, rows, 1), 1)
        self.assertEqual(service_module._overflow(rows, rows, 40), 3)
        self.assertEqual(service_module._overflow(rows, rows, 10_000), 5)
        self.assertEqual(service_module._overflow(rows, rows[:4], 10_000), 1)
        self.assertEqual(service_module._overflow(rows, None, 10_000), 1)

    async def test_ui_entry_limit(self):
        core = owner_core()
        core["config_entries/get"]["template"] = [
            {"entry_id": f"entry{n}", "domain": "template", "title": f"Helper {n}", "state": "loaded",
             "disabled_by": None} for n in range(60)]
        report = await self.run_report(core_values=core, integrations=("template",))
        self.assertEqual(len(report["ui_entries"]), c.MAX_UI_ENTRIES)
        self.assert_partial(report, "ui_entries_not_listed_limit")

    async def test_long_text_is_bounded(self):
        template = [{"sensor": [{"name": "Long", "state": "{{ " + "1 + " * 3000 + "1 }}"}]}]
        report = await self.run_report(owner_yaml(template=template), integrations=("template",))
        for text in walk_strings(report):
            self.assertLessEqual(len(text), c.MAX_TEXT + 64)
        self.assertFalse(report["complete"])


class NoWritePathTests(unittest.IsolatedAsyncioTestCase):
    """The read-only static check: only the fixed reads are reachable."""

    def sources(self):
        return {path.name: path.read_text() for path in sorted(PACKAGE.glob("*.py"))} | {"tool": TOOL.read_text()}

    def test_fixed_command_contract(self):
        self.assertEqual(dict(c.REGISTRY_COMMAND), {"type": "config/entity_registry/list"})
        self.assertEqual(dict(c.SOURCE_COMMAND), {"type": "entity/source"})
        self.assertEqual(dict(c.MANIFEST_COMMAND), {"type": "manifest/get", "integration": "ha_mcp_tools"})
        with self.assertRaises(TypeError):
            c.REGISTRY_COMMAND["type"] = "config/entity_registry/update"  # type: ignore[index]
        for domain in (*c.INTEGRATIONS, c.COMPONENT_DOMAIN):
            self.assertEqual(c.entries_payload(domain), {"type": "config_entries/get", "domain": domain})
        for domain in ("mqtt", "", "template/flow", None, UNKNOWN_VALUE):
            with self.assertRaises(ValueError):
                c.entries_payload(domain)
        self.assertEqual(DEFINITION_YAML_PATHS, frozenset({*DEFINITION_PATHS, "homeassistant.packages", "homeassistant"}))
        self.assertEqual(YAML_READER_PURPOSES["configuration_definitions"],
                         (DEFINITION_YAML_PATHS, "configuration_definitions_yaml_read"))
        self.assertTrue(DEFINITION_YAML_PATHS.isdisjoint({"recorder", "http", "secrets", "auth"}))

    def test_static_only_fixed_reads(self):
        """Only the fixed Core reads are named; the clients are reached only
        through ``command`` and ``request``; YAML only through the injected
        reader; no component command, flow, Supervisor or other provider."""

        literals, client_attributes, reader_calls, imports, dispatch = set(), set(), [], set(), []
        for name, text in self.sources().items():
            tree = ast.parse(text)
            skip = docstring_ids(tree)
            for node in ast.walk(tree):
                if (isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip
                        and node.value.startswith(("recorder/", "config/", "entity/", "search/", "backup/",
                                                   "hassio/", "supervisor/", "homeassistant/", "manifest/",
                                                   "config_entries/", "ha_mcp_tools/", "/"))):
                    literals.add(node.value)
                if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Attribute) \
                        and node.value.attr in ("client", "rest_client", "websocket_client", "http"):
                    client_attributes.add((node.value.attr, node.attr))
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    if node.func.attr == "read":
                        reader_calls.append((name, ast.unparse(node)))
                    if node.func.attr in ("command", "request", "command_batch", "execute", "call_tool"):
                        dispatch.append(ast.unparse(node))
                if isinstance(node, ast.ImportFrom):
                    imports.add("." * node.level + (node.module or ""))
                if isinstance(node, ast.Import):
                    imports.update(alias.name for alias in node.names)
        self.assertEqual(literals, {"/config", "/states", "config/entity_registry/list", "config_entries/get",
                                    "config_entries/get:", "entity/source", "manifest/get", "manifest/get:"})
        self.assertEqual(client_attributes, {("client", "command"), ("rest_client", "request")})
        self.assertEqual(reader_calls, [("service.py", "reader.read(path)")])
        self.assertEqual(sorted(dispatch), sorted([
            "self.rest_client.request('GET', rest_path('/states'))",
            "self.rest_client.request('GET', rest_path('/config'))",
            "self.client.command(dict(c.REGISTRY_COMMAND))", "self.client.command(dict(c.SOURCE_COMMAND))",
            "self.client.command(dict(c.MANIFEST_COMMAND))", "self.client.command(c.entries_payload(domain))"]))
        self.assertEqual(imports, {
            ".", "..clients.paths", "..configuration_definitions.contracts", "..configuration_definitions.service",
            "..entity_ids", "..entity_reconciliation.contracts", "..entity_reconciliation.service", "..errors",
            "..observability", "..providers.upstream_yaml_reader", "..recorder_settings.contracts",
            "..recorder_settings.projection", "..request_context", "..sanitization", "..tool_framework",
            ".compatibility", ".credentials", ".matching", ".naming", ".projection", ".schemas", "__future__", "asyncio",
            "datetime", "itertools", "json", "mcp.server.fastmcp.tools.base", "mcp.types", "pydantic", "re", "types",
            "typing", "unicodedata", "urllib.parse"})
        service = ast.parse(self.sources()["service.py"])
        yaml_paths = [ast.unparse(node.args[1]) for node in ast.walk(service)
                      if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                      and node.func.attr == "_yaml"]
        self.assertEqual(sorted(yaml_paths), sorted(["path", "'homeassistant.packages'", "'homeassistant'"]))
        for text in self.sources().values():
            # Docstrings document the prohibition; only code and non-docstring
            # literals are scanned (``ast.unparse`` drops comments).
            tree = ast.parse(text)
            for node in ast.walk(tree):
                if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) \
                        and node.body and id(getattr(node.body[0], "value", None)) in docstring_ids(tree):
                    node.body = node.body[1:] or [ast.Pass()]
            code = ast.unparse(tree)
            for word in ("call_service", "post(", "put(", "delete(", "options_flow", "config_entries/flow",
                         "config_entries/options", "reload", "ha_mcp_tools/", "/api/services", "supervisor",
                         "entity_registry/update", "config_entries/update", "config_entries/delete",
                         ".storage", "open("):
                self.assertNotIn(word, code)

    async def test_runtime_dispatch_is_exactly_the_fixed_reads(self):
        env = setup()
        self.addCleanup(end_request, env.token)
        await env.service.report(("sensor.ui_template", *OWNER_ENTITIES), ("sql",))
        self.assertEqual(env.calls, [
            *(f"yaml:{path}" for path in DEFINITION_PATHS), "yaml:homeassistant.packages", "yaml:homeassistant",
            "states", "config/entity_registry/list", "entity/source", "config",
            "config_entries/get:sql", "config_entries/get:template", f"config_entries/get:{c.COMPONENT_DOMAIN}",
            "manifest/get"])
        self.assertTrue(all(allowed_payload(payload) for payload in env.socket.payloads))


class RegistrationTests(unittest.IsolatedAsyncioTestCase):
    def test_core_gate_mapping(self):
        self.assertEqual(STATIC_TOOL_CORE_REQUIREMENTS["get_configuration_definitions"], c.CORE_REQUIREMENTS)
        self.assertEqual(c.CORE_REQUIREMENTS, ("core.basic_rest_read", "core.basic_websocket_read",
                                               "core.non_device_registry_read"))

    def test_capability_metadata_read_only(self):
        native = next(item for item in BETA_NATIVE_CAPABILITIES if item["tool"] == "get_configuration_definitions")
        self.assertEqual((native["risk"], native["operation_class"], native["routing"], native["fallback"],
                          native["direct_write_allowed"], native["data_providers"]),
                         ("read", "read", "engineering_native", "none", False,
                          ["direct_ha_api", "upstream_read_gateway"]))
        matrix = next(item for item in CAPABILITY_PROVIDER_MATRIX if item["tool"] == "get_configuration_definitions")
        self.assertEqual((matrix["selected_provider"], matrix["fallback_policy"], matrix["trust_mode"]),
                         ("engineering", "none", "reviewed_native_read"))

    def test_tool_annotations_and_schema(self):
        tool = registered_tools(get_registered_server())["get_configuration_definitions"]
        annotations = tool.annotations
        self.assertEqual((annotations.readOnlyHint, annotations.destructiveHint, annotations.idempotentHint,
                          annotations.openWorldHint), (True, False, True, False))
        schema = tool.parameters
        self.assertIs(schema["additionalProperties"], False)
        self.assertEqual(set(schema["properties"]), {"entity_ids", "integrations"})
        self.assertEqual(schema["properties"]["entity_ids"]["maxItems"], c.MAX_ENTITY_IDS)
        self.assertEqual(schema["properties"]["integrations"]["maxItems"], len(c.INTEGRATIONS))
        self.assertEqual(schema["properties"]["integrations"]["items"]["enum"], list(c.INTEGRATIONS))
        self.assertNotIn("required", schema)

    async def test_served_tool_rejects_raw_arguments_without_echo(self):
        tool = registered_tools(get_registered_server())["get_configuration_definitions"]
        for arguments in (None, [], {}, {"entity_ids": [], "integrations": []}, {"entity_ids": UNKNOWN_VALUE},
                          {"entity_ids": [UNKNOWN_VALUE]}, {"integrations": [UNKNOWN_VALUE]},
                          {"integrations": "sql"}, {"entity_ids": ["light.kitchen"] * 2},
                          {"integrations": ["sql", "sql"]}, {UNKNOWN_KEY: UNKNOWN_VALUE},
                          {"entity_ids": [f"sensor.s{n}" for n in range(51)]}):
            with self.subTest(arguments=arguments):
                output = await tool.run(arguments)
                result = json.loads(output)
                self.assertFalse(result["success"])
                self.assertEqual(result["error_code"], "invalid_request")
                self.assertNotIn(UNKNOWN_VALUE, output)
                self.assertNotIn(UNKNOWN_KEY, output)

    async def test_gateway_rejects_before_sdk_and_audit_excludes_invalid_material(self):
        with tempfile.TemporaryDirectory() as directory:
            audit_path = Path(directory) / "audit.jsonl"
            config = settings(audit_path=str(audit_path))
            app = Mock(side_effect=AssertionError("SDK must not be reached"))
            core = SimpleNamespace(acquire=Mock(return_value=None))
            gateway = AuthenticatedMcpGateway(app, config, AuditLogger(str(audit_path), config.access_secret),
                                              core_runtime=core)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway),
                                         base_url="http://127.0.0.1:8100") as client:
                for bad in ({"entity_ids": [UNKNOWN_VALUE]}, {UNKNOWN_KEY: UNKNOWN_VALUE},
                            {"integrations": [UNKNOWN_VALUE]},
                            {"entity_ids": ["light.kitchen"], "password": DB_PASSWORD}):
                    response = await client.post(f"/{config.access_secret}/mcp", json={
                        "jsonrpc": "2.0", "id": "synthetic-request", "method": "tools/call",
                        "params": {"name": "get_configuration_definitions", "arguments": bad}})
                    for value in (UNKNOWN_VALUE, UNKNOWN_KEY, DB_PASSWORD):
                        self.assertNotIn(value, response.text)
                    self.assertIn("invalid_request", response.text)
            text = audit_path.read_text()
            for value in (UNKNOWN_VALUE, UNKNOWN_KEY, DB_PASSWORD):
                self.assertNotIn(value, text)
            app.assert_not_called()
            core.acquire.assert_not_called()

    async def test_gateway_positive_output_and_audit(self):
        response, data, audit = await serve_through_gateway(
            {"entity_ids": list(OWNER_ENTITIES), "integrations": ["sql", "derivative"]})
        self.assertTrue(data["success"], data)
        self.assertEqual(data["data"]["schema"], c.SCHEMA)
        self.assertEqual(data["metadata"]["data_providers"], ["direct_ha_api", "upstream_read_gateway"])
        self.assertEqual(data["metadata"]["upstream_calls"], 8)
        # R2-M1: the two SQL definitions' withheld db_url values.
        self.assertEqual(data["metadata"]["completeness"], "partial")
        self.assertEqual(data["data"]["omissions"], ["withheld:definitions[0]", "withheld:definitions[1]"])
        self.assertFalse(data["metadata"]["fallback_occurred"])
        self.assertNotIn("upstream_calls", data["data"])
        for value in NEEDLES:
            self.assertNotIn(value, response + audit)
        self.assertNotIn("db_url\": \"", response)
        rows = [json.loads(row) for row in audit.splitlines()]
        record = next(r for r in rows if r.get("tool_name") == "get_configuration_definitions")
        self.assertEqual(record["resource_ids"], {})
        self.assertEqual(record["parameters"]["profile"], "configuration-definitions-read-v1")
        self.assertEqual((record["parameters"]["entity_count"], record["parameters"]["integration_count"]), (6, 2))
        self.assertNotIn("sensor.database_size", json.dumps(record))
        self.assertNotIn("derivative", json.dumps(record["parameters"]))

    async def test_gateway_sweep_with_the_access_secret(self):
        values, core, entity_ids, integrations = sweep_inputs()
        response, data, audit = await serve_through_gateway(
            {"entity_ids": list(entity_ids), "integrations": list(integrations)}, values, core,
            access_secret=ENTITY_SECRET)
        self.assertTrue(data["success"], data)
        self.assertNotIn(ENTITY_SECRET, json.dumps(data))
        self.assertNotIn(ENTITY_SECRET, audit)
        self.assertIn("credential_text_withheld", data["data"]["omissions"])
        self.assertEqual(data["metadata"]["completeness"], "partial")


def docstring_ids(tree):
    return {id(node.body[0].value) for node in ast.walk(tree)
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant)}


async def serve_through_gateway(arguments, yaml_values=None, core_values=None, *, access_secret=ACCESS_SECRET):
    env = setup(yaml_values, core_values, known_secrets=(HA_TOKEN, access_secret))
    end_request(env.token)
    core = env.core
    core.acquire = Mock(return_value=object())
    core.consume = Mock(return_value=())
    core.revalidate = Mock(return_value=True)
    core.finish = Mock(return_value=True)
    core.release = Mock(return_value=True)

    async def app(scope, receive, send):
        rpc = json.loads((await receive())["body"])
        tool = registered_tools(get_registered_server())["get_configuration_definitions"]
        rendered = await tool.run(rpc["params"]["arguments"])
        result = {"jsonrpc": "2.0", "id": rpc["id"],
                  "result": {"content": [{"type": "text", "text": rendered}], "isError": False}}
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": json.dumps(result).encode()})

    with tempfile.TemporaryDirectory() as directory:
        audit_path = Path(directory) / "audit.jsonl"
        config_ = settings(audit_path=str(audit_path), access_secret=access_secret)
        gateway = AuthenticatedMcpGateway(app, config_, AuditLogger(str(audit_path), config_.access_secret),
                                          core_runtime=core)
        with patch.object(CONFIGURATION_DEFINITIONS, "service", env.service), patch.object(public, "SETTINGS", config_):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway),
                                         base_url="http://127.0.0.1:8100") as http:
                response = await http.post(f"/{config_.access_secret}/mcp", json={
                    "jsonrpc": "2.0", "id": "synthetic-request", "method": "tools/call",
                    "params": {"name": "get_configuration_definitions", "arguments": arguments}})
        audit = audit_path.read_text()
    core.finish.assert_called_once()
    return response.text, json.loads(response.json()["result"]["content"][0]["text"]), audit


import test_hamcp162_yaml_admission as yaml_admission  # noqa: E402


class DefinitionReaderPurposeTests(yaml_admission.Harness):
    """The gateway's ``configuration_definitions`` YAML purpose: its own fixed paths only."""

    async def test_purpose_dispatches_only_its_paths(self):
        for version in yaml_admission.VERSIONS:
            with self.subTest(version=version):
                gateway, transport = await self.gateway(version)
                audit = Mock()
                reader = gateway.yaml_config_reader(audit=audit, purpose=c.READER_PURPOSE)
                self.assertIsNotNone(reader)
                for path in sorted(DEFINITION_YAML_PATHS):
                    data, failure = gateway_envelope(await reader.read(path), path)
                    self.assertIsNone(failure)
                    self.assertEqual(data["yaml_path"], path)
                for path in ("recorder", "sql.db_url", "sensor[0]", "http", "secrets", "", "../secrets", "SQL",
                             None, "template.sensor"):
                    self.assertEqual(await reader.read(path), {
                        "success": False, "details": {"failure_category": "argument_validation"}})
                self.assertEqual([call[1]["yaml_path"] for call in transport.sent], sorted(DEFINITION_YAML_PATHS))
                events = [call.args[0] for call in audit.write.call_args_list]
                self.assertEqual({e["event"] for e in events}, {"configuration_definitions_yaml_read"})
                self.assertEqual(len(events), len(DEFINITION_YAML_PATHS))
                self.assertFalse(any(e["fallback_occurred"] for e in events))

    async def test_purposes_cannot_read_each_others_paths(self):
        gateway, transport = await self.gateway()
        recorder = gateway.yaml_config_reader()
        definitions = gateway.yaml_config_reader(purpose=c.READER_PURPOSE)
        self.assertFalse((await recorder.read("sql"))["success"])
        self.assertFalse((await definitions.read("recorder"))["success"])
        self.assertEqual(transport.sent, [])
        for purpose in (UNKNOWN_VALUE, None, "", "Configuration_Definitions"):
            self.assertIsNone(gateway.yaml_config_reader(purpose=purpose))

    async def test_service_reads_real_gateway_envelopes(self):
        gateway, transport = await self.gateway()
        core = SyntheticCore()
        calls = []
        values = owner_core()
        service = ConfigurationDefinitionsService(
            SyntheticRest(values, calls), SyntheticWebSocket(values, calls), core,
            yaml_reader_factory=lambda: gateway.yaml_config_reader(purpose=c.READER_PURPOSE), clock=lambda: NOW)
        telemetry, token = begin_request()
        telemetry.caller_id = "synthetic_authenticated_caller"
        telemetry.core_dispatch_authorizer = lambda: core.available
        try:
            report = await service.report(("sensor.database_size",), ())
        finally:
            end_request(token)
        # The synthetic peer answers every path with one mapping, so each
        # definition path is a non-list or non-definition shape: reported, never guessed.
        self.assertEqual([call[1]["yaml_path"] for call in transport.sent],
                         [*DEFINITION_PATHS, "homeassistant.packages"])
        self.assertEqual(report["packages"], "present")
        self.assertIn("ha-mcp", report["coverage"]["yaml_reader"])
        self.assertEqual(report["upstream_calls"], 7)
        self.assertEqual(self.entities_of(report)["sensor.database_size"]["definition"]["reason"],
                         "no_read_definition_has_this_unique_id")

    @staticmethod
    def entities_of(report):
        return {row["entity_id"]: row for row in report["entities"]}


# Independent review of PR #294 (head dfd1f02): H1 and M1-M4. The reviewer's
# seven reproductions are kept verbatim in intent; H1 runs through the
# authenticated gateway as well as the service.
def credential_texts():
    """Credential-shaped texts, assembled at test time; each carries a needle."""

    tail = DB_HOST + ".invalid"
    return {
        "postgresql": db_url(),
        "postgresql_driver": syn.url("postgresql+psycopg2", DB_USER, DB_PASSWORD, tail + ":5432/ha"),
        "mysql": db_url("mysql"),
        "mssql": syn.url("mssql+pyodbc", DB_USER, DB_PASSWORD, tail + "/ha?driver=synthetic"),
        "sqlite_path": "sqlite" + syn.SCHEME_SEPARATOR + "//config/" + DB_PASSWORD + ".db",
        "mongodb": syn.url("mongodb", DB_USER, DB_PASSWORD, tail + ":27017/admin"),
        "http_basic_auth": syn.url("http", DB_USER, DB_PASSWORD, tail + ":8123/api"),
        "user_only_userinfo": syn.url("https", DB_PASSWORD, None, tail),
        "user_pass_host": DB_USER + ":" + DB_PASSWORD + "@" + tail,
        "password_field": "Server=" + tail + ";" + syn.pwd(DB_PASSWORD),
        "embedded_in_prose": "Copied from " + db_url() + " last week",
        "percent_encoded": syn.enc(db_url(), 1),
    }


LEAK_NEEDLES = (DB_USER, DB_PASSWORD, DB_HOST)


def plant_everywhere(text):
    """``text`` in every Core and reader field that can reach the report."""

    values = owner_yaml(kwargs={"sql": {"completeness": "partial", "limitations": [text]}})
    core = owner_core()
    core["config_entries/get"]["template"][0].update(title=text, reason=text, error_reason_translation_key=text)
    core["config_entries/get"][c.COMPONENT_DOMAIN] = [
        {"entry_id": COMPONENT_ENTRY, "domain": c.COMPONENT_DOMAIN, "title": text, "state": "loaded",
         "disabled_by": None, "reason": text, "error_reason_translation_key": text}]
    core["manifest/get"] = {"domain": c.COMPONENT_DOMAIN, "version": text, "name": text}
    core["config"]["components"] = [*COMPONENTS, text]
    core["entity/source"]["sensor.ui_template"] = {"domain": text}
    core["config/entity_registry/list"].append(registry_row("sensor.planted", text, text))
    core["states"].append({"entity_id": "sensor.planted", "state": "unavailable",
                           "attributes": {"friendly_name": text}})
    return values, core


class CredentialExitPointTests(Base):
    """H1: credential text is withheld from EVERY string of the final report."""

    def assert_no_leak(self, text):
        for needle in LEAK_NEEDLES:
            self.assertNotIn(needle, text)

    async def test_reviewer_postgresql_title_cannot_reach_the_public_gateway(self):
        core = owner_core()
        core["config_entries/get"]["template"][0]["title"] = db_url()
        response, data, audit = await serve_through_gateway({"integrations": ["template"]}, owner_yaml(), core)
        self.assertTrue(data["success"])
        self.assert_no_leak(response)
        self.assert_no_leak(audit)
        report = data["data"]
        self.assertEqual(report["ui_entries"][0]["title"], "[withheld:credential_text]")
        self.assertIn("credential_text_withheld", report["omissions"])
        self.assertEqual(report["completeness"], "partial")

    async def test_each_credential_shape_in_a_title_is_withheld(self):
        for name, text in credential_texts().items():
            with self.subTest(name):
                core = owner_core()
                core["config_entries/get"]["template"][0]["title"] = text
                report = await self.run_report(core_values=core, integrations=("template",))
                self.assert_no_leak(json.dumps(report))
                self.assertEqual(report["ui_entries"][0]["title"], "[withheld:credential_text]")
                self.assert_partial(report, "credential_text_withheld")

    async def test_each_credential_shape_in_every_metadata_field(self):
        for name, text in credential_texts().items():
            with self.subTest(name):
                values, core = plant_everywhere(text)
                report = await self.run_report(values, core, entity_ids=("sensor.ui_template", "sensor.planted"),
                                               integrations=("template", "sql"))
                self.assert_no_leak(json.dumps(report))
                self.assert_partial(report, "credential_text_withheld")
                self.assertEqual(report["capability"]["entries"][0]["title"], "[withheld:credential_text]")
                # The rest of the report is preserved.
                self.assertEqual(len(report["entities"]), 2)
                self.assertTrue(report["definitions"])

    async def test_metadata_sweep_through_the_gateway(self):
        for name, text in credential_texts().items():
            with self.subTest(name):
                values, core = plant_everywhere(text)
                response, data, audit = await serve_through_gateway(
                    {"entity_ids": ["sensor.ui_template", "sensor.planted"], "integrations": ["template", "sql"]},
                    values, core)
                self.assertTrue(data["success"])
                self.assert_no_leak(response)
                self.assert_no_leak(audit)
                self.assertIn("credential_text_withheld", data["data"]["omissions"])

    async def test_reader_provenance_is_screened(self):
        self.env = setup()
        self.addCleanup(end_request, self.env.token)
        self.env.reader.provenance = "reader at " + db_url()
        report = await self.env.service.report((), ("sql",))
        self.assert_no_leak(json.dumps(report))
        self.assertEqual(report["coverage"]["yaml_reader"], "[withheld:credential_text]")
        self.assert_partial(report, "credential_text_withheld")

    async def test_reviewer_api_error_detail_with_a_database_uri_is_never_echoed(self):
        core = owner_core()
        core["config"] = HomeAssistantApiError(details={"status": 500, "detail": db_url()})
        report = await self.run_report(core_values=core, integrations=("template",))
        self.assert_no_leak(json.dumps(report))
        self.assertFalse(report["complete"])
        self.assertEqual(source(report, "config")["reason"], "command_failed")

    async def test_ordinary_titles_are_preserved_and_complete(self):
        # Correction batch 2: a title with "@" is no longer shown (see
        # FreeTextAllowlistTests); every printable-ASCII title below still is.
        for title in ("Living room helper", "Ratio 3:2", "Port 8123 / API", "Yes", "on", "T" * c.MAX_TITLE,
                      "Kitchen (east) #2 - 50% [beta] {x} ~!$^&*_+|;'<>,.?`"):
            with self.subTest(title):
                core = owner_core()
                core["config_entries/get"]["template"][0]["title"] = title
                report = await self.run_report(core_values=core, integrations=("template",))
                self.assertEqual(report["ui_entries"][0]["title"], title)
                self.assertNotIn("credential_text_withheld", report["omissions"])
                self.assertNotIn("unverifiable_text_withheld", report["omissions"])
                self.assertTrue(report["complete"])

    def test_predicate_and_walk(self):
        from ha_mcp_engineering.configuration_definitions import credentials

        for name, text in credential_texts().items():
            with self.subTest(name):
                self.assertTrue(credentials.credential_text(text))
        self.assertTrue(credentials.credential_text("prefix " + ACCESS_SECRET, (ACCESS_SECRET,)))
        self.assertTrue(credentials.credential_text("[Withheld:Credential_Text]"))
        # Text the reader's canonicalization refuses cannot be screened.
        self.assertTrue(credentials.credential_text(syn.enc(db_url(), 5)))
        self.assertTrue(credentials.credential_text("%41 at 50% off"))
        for text in ("Living room helper", "sensor.database_size", "Alerts @ home", "12:30", "a@b", ""):
            with self.subTest(text):
                self.assertFalse(credentials.credential_text(text))
        walked, count = credentials.withhold(
            {"keep": "plain", db_url(): "key dropped", "nested": [db_url(), ("plain", db_url())], "n": 3})
        self.assertEqual(walked, {"keep": "plain", "nested": ["[withheld:credential_text]",
                                  ["plain", "[withheld:credential_text]"]], "n": 3})
        self.assertEqual(count, 3)


class ReverseMatchAmbiguityTests(Base):
    """M1: identical ambiguity rules entity -> definition and definition -> entity."""

    DUPLICATES = [{"name": "A", "unique_id": "feels_like", "state": "1"},
                  {"name": "B", "unique_id": "feels_like", "state": "2"}]

    async def test_reviewer_shared_unique_id_is_ambiguous_in_both_directions(self):
        for integrations in ((), ("template",)):
            with self.subTest(integrations=integrations):
                report = await self.run_report(owner_yaml(template=[{"sensor": self.DUPLICATES}]),
                                               entity_ids=("sensor.outdoor_feels_like",), integrations=integrations)
                entity = self.entities(report)["sensor.outdoor_feels_like"]["definition"]
                self.assertEqual((entity["method"], entity["reason"]),
                                 ("ambiguous", "several_definitions_share_this_unique_id"))
                templates = [d for d in report["definitions"] if d["integration"] == "template"]
                self.assertEqual(len(templates), 2)
                for definition in templates:
                    match = definition["expected_entities"][0]["match"]
                    self.assertEqual((match["method"], match["confidence"], match["reason"]),
                                     ("ambiguous", None, "several_definitions_share_this_unique_id"))
                    self.assertNotEqual(match["method"], "exact_unique_id")

    async def test_unique_definition_stays_exact_in_both_directions(self):
        report = await self.run_report(owner_yaml(), entity_ids=("sensor.outdoor_feels_like",),
                                       integrations=("template",))
        self.assertEqual(self.entities(report)["sensor.outdoor_feels_like"]["definition"]["method"],
                         "exact_unique_id")
        match = self.definitions(report)["template[0].sensor[0]"]["expected_entities"][0]["match"]
        self.assertEqual((match["entity_id"], match["method"], match["confidence"]),
                         ("sensor.outdoor_feels_like", "exact_unique_id", "exact"))

    async def test_matcher_counts_definitions_not_registry_hits(self):
        from ha_mcp_engineering.configuration_definitions.matching import Matcher

        definitions, _ = project(template=[{"sensor": self.DUPLICATES}, {"sensor": [
            {"name": "C", "unique_id": "other", "state": "3"}]}])
        registry = {"sensor.a": registry_row("sensor.a", "template", "feels_like"),
                    "sensor.c": registry_row("sensor.c", "template", "other")}
        matcher = Matcher(definitions, registry=registry, platforms={}, states={"sensor.a": "1", "sensor.c": "1"},
                          yaml_complete=True, ui_domains={})
        methods = {d["definition_id"]: matcher.definition(d)[0]["method"] for d in definitions}
        self.assertEqual(methods, {"template[0].sensor[0]": "ambiguous", "template[0].sensor[1]": "ambiguous",
                                   "template[1].sensor[0]": "exact_unique_id"})
        self.assertEqual(matcher.entity("sensor.a")["method"], "ambiguous")

    async def test_several_registry_rows_for_one_definition_are_ambiguous(self):
        # One definition whose unique ID two registry rows carry (here the raw
        # text and Core's YAML 1.1 reading): no row is chosen from either side.
        from ha_mcp_engineering.configuration_definitions.matching import Matcher

        definitions, _ = project(template=[{"sensor": [{"name": "A", "unique_id": "on", "state": "1"}]}])
        registry = {"sensor.a": registry_row("sensor.a", "template", "on"),
                    "sensor.b": registry_row("sensor.b", "template", "True")}
        matcher = Matcher(definitions, registry=registry, platforms={}, states={"sensor.a": "1", "sensor.b": "1"},
                          yaml_complete=True, ui_domains={})
        result = matcher.definition(definitions[0])[0]
        self.assertEqual((result["entity_id"], result["method"], result["confidence"], result["reason"]),
                         (None, "ambiguous", None, "several_registry_entries_share_this_unique_id"))
        # Control: with one of the rows the forward rule decides.
        del registry["sensor.b"]
        matcher = Matcher(definitions, registry=registry, platforms={}, states={"sensor.a": "1"},
                          yaml_complete=True, ui_domains={})
        result = matcher.definition(definitions[0])[0]
        self.assertEqual((result["entity_id"], result["method"]), ("sensor.a", "heuristic"))


class ParserDependentIdentityTests(Base):
    """M2: slice 1's parser-dependence rules apply before identity matching."""

    async def report_for(self, unique_id, registered, **definition):
        core = owner_core()
        core["config/entity_registry/list"].append(registry_row("sensor.dependent", "template", registered))
        core["states"].append(state("sensor.dependent"))
        values = owner_yaml(template=[{"sensor": [{"name": "Dependent", "unique_id": unique_id, "state": "1",
                                                   **definition}]}])
        return await self.run_report(values, core, entity_ids=("sensor.dependent",), integrations=("template",))

    async def test_reviewer_unquoted_on_is_never_an_exact_match(self):
        from ha_mcp_engineering.recorder_settings.projection import yaml11_readings

        self.assertEqual(yaml11_readings("on"), ("unquoted", [True]))
        for text, core_reading in (("on", "True"), ("off", "False"), ("yes", "True"), ("no", "False"),
                                   ("1:30", "90")):
            for registered in (text, core_reading):
                with self.subTest(text=text, registered=registered):
                    report = await self.report_for(text, registered)
                    match = self.entities(report)["sensor.dependent"]["definition"]
                    self.assertEqual((match["method"], match["confidence"], match["reason"]),
                                     ("heuristic", "medium", "unique_id_parser_dependent"))
                    expected_entity = self.definitions(report)["template[0].sensor[0]"]["expected_entities"][0]
                    self.assertTrue(expected_entity["unique_id_parser_dependent"])
                    self.assertEqual(expected_entity["unique_id_readings"], [text, core_reading])
                    self.assertEqual(expected_entity["match"]["method"], "heuristic")

    async def test_values_yaml_11_reads_the_same_stay_exact(self):
        # A reader string slice 1's resolver proves was quoted, and an integer.
        for value, registered in (("y", "y"), ("n", "n"), ("True", "True"), ("1_000", "1_000"), ("010", "010"),
                                  ("plain", "plain"), (10, "10")):
            with self.subTest(value=value):
                report = await self.report_for(value, registered)
                self.assertEqual(self.entities(report)["sensor.dependent"]["definition"]["method"],
                                 "exact_unique_id")
                expected_entity = self.definitions(report)["template[0].sensor[0]"]["expected_entities"][0]
                self.assertNotIn("unique_id_parser_dependent", expected_entity)

    async def test_two_dependent_candidates_are_ambiguous(self):
        core = owner_core()
        core["config/entity_registry/list"].append(registry_row("sensor.dependent", "template", "True"))
        core["states"].append(state("sensor.dependent"))
        values = owner_yaml(template=[{"sensor": [{"name": "A", "unique_id": "on", "state": "1"},
                                                  {"name": "B", "unique_id": "yes", "state": "1"}]}])
        report = await self.run_report(values, core, entity_ids=("sensor.dependent",), integrations=("template",))
        self.assertEqual(self.entities(report)["sensor.dependent"]["definition"]["method"], "ambiguous")

    def test_block_and_entity_unique_ids_combine(self):
        definitions, _ = project(template=[{"unique_id": "yes", "sensor": [
            {"name": "Combined", "unique_id": "a", "state": "1"}]}])
        expected_entity = definitions[0]["expected_entities"][0]
        self.assertEqual((expected_entity["unique_id"], expected_entity["unique_id_readings"]),
                         ("yes-a", ["yes-a", "True-a"]))
        self.assertTrue(expected_entity["unique_id_parser_dependent"])

    def test_dependent_names_and_keys_derive_no_entity_id(self):
        definitions, _ = project(sql=[{"name": "off", "query": "SELECT 1;", "column": "c"}],
                                 group={"on": {"entities": ["light.kitchen"]}},
                                 template=[{"sensor": [{"name": "yes", "state": "1"}]}])
        for definition in definitions:
            with self.subTest(definition["definition_id"]):
                row = definition["expected_entities"][0]
                self.assertEqual((row["derived_entity_id"], row["derivation_limit"]),
                                 (None, "name_parser_dependent"))
        control, _ = project(sql=[{"name": "y", "query": "SELECT 1;", "column": "c"}])
        self.assertEqual(control[0]["expected_entities"][0]["derived_entity_id"], "sensor.y")

    async def test_coverage_discloses_the_rule(self):
        report = await self.run_report(integrations=("template",))
        self.assertEqual(report["coverage"]["parser_dependent_identity"], c.PARSER_DEPENDENCE_DISCLOSURE)
        self.assertEqual(report["coverage"]["yaml_numeric_spelling"], c.YAML_NUMERIC_SPELLING_LIMITATION)


class DiagnosisEvidenceTests(Base):
    """M3: diagnosis claims only what its read sources support."""

    async def ghost(self, values):
        report = await self.run_report(values, entity_ids=("sensor.ghost",))
        return self.entities(report)["sensor.ghost"]["diagnosis"]

    async def test_reviewer_failed_yaml_does_not_prove_definition_absent(self):
        values = owner_yaml()
        values["template"] = RuntimeError("synthetic unavailable")
        diagnosis_ = await self.ghost(values)
        self.assertEqual(diagnosis_["classification"], "undetermined")
        self.assertIn("source_failed:configuration_yaml:template", diagnosis_["reasons"])
        self.assertNotIn("definition_absent", diagnosis_["reasons"])

    async def test_every_unread_yaml_source_blocks_absence(self):
        partial = {"completeness": "partial", "limitations": ["value_truncated:3 nodes omitted"]}
        cases = {
            "failed": (owner_yaml(sql=HomeAssistantTimeoutError()), "source_failed:configuration_yaml:sql"),
            "partial": (owner_yaml(kwargs={"sensor": partial}), "source_partial:configuration_yaml:sensor"),
            "packages": (owner_yaml(packages={"energy": "!include packages/energy.yaml"}),
                         "definitions_in_packages_not_read"),
            "packages_unknown": (owner_yaml(packages="!include_dir_named packages"), "packages_not_determined"),
            "whole_path_include": (owner_yaml(group="!include groups.yaml"), "include_not_followed:group"),
            "value_include": (owner_yaml(sql=["!include sql_one.yaml", *owner_sql()]),
                              "definition_values_include_not_followed"),
        }
        for name, (values, reason) in cases.items():
            with self.subTest(name):
                if name == "failed":
                    values["sql"] = HomeAssistantTimeoutError()
                diagnosis_ = await self.ghost(values)
                self.assertEqual(diagnosis_["classification"], "undetermined")
                self.assertIn(reason, diagnosis_["reasons"])
                self.assertNotIn("definition_absent", diagnosis_["reasons"])

    async def test_absence_with_every_source_read(self):
        diagnosis_ = await self.ghost(owner_yaml())
        self.assertEqual(diagnosis_, {"classification": "not_found",
                                      "reasons": ["no_state", "definition_absent", "labelled_keys_not_addressable"]})

    async def test_reviewer_unreadable_ui_definition_is_never_entity_level(self):
        core = owner_core()
        core["states"].append(state("sensor.ui_template", "unavailable"))
        report = await self.run_report(core_values=core, entity_ids=("sensor.ui_template",))
        self.assertEqual(self.entities(report)["sensor.ui_template"]["diagnosis"],
                         {"classification": "undetermined",
                          "reasons": ["config_entry_state:loaded", "state_unavailable",
                                      "definition_not_readable_without_flow", "dependencies_not_checked"]})

    async def test_unextracted_template_dependencies_are_never_entity_level(self):
        core = owner_core()
        core["states"] = [item for item in core["states"] if item["entity_id"] != "sensor.outdoor_feels_like"]
        core["states"].append(state("sensor.outdoor_feels_like", "unavailable"))
        report = await self.run_report(core_values=core, entity_ids=("sensor.outdoor_feels_like",))
        self.assertEqual(self.entities(report)["sensor.outdoor_feels_like"]["diagnosis"],
                         {"classification": "undetermined", "reasons": ["state_unavailable", "dependencies_not_checked"]})

    async def test_checked_dependencies_still_give_entity_level(self):
        core = owner_core()
        core["states"] = [item for item in core["states"] if item["entity_id"] != "sensor.water_rate"]
        core["states"].append(state("sensor.water_rate", "unavailable"))
        report = await self.run_report(core_values=core, entity_ids=("sensor.water_rate",))
        self.assertEqual(self.entities(report)["sensor.water_rate"]["diagnosis"],
                         {"classification": "entity_level", "reasons": ["state_unavailable"]})


class MalformedSourceValueTests(Base):
    """M4: a malformed value invalidates its item only, never the report."""

    async def test_reviewer_list_state_keeps_the_other_sources(self):
        core = owner_core()
        core["states"] = [{"entity_id": "sensor.example", "state": []}]
        report = await self.run_report(core_values=core, integrations=("template",))
        self.assertFalse(report["complete"])
        self.assertEqual(source(report, "states")["reason"], "invalid_response")
        self.assertEqual(source(report, "states")["status"], "partial")
        self.assertEqual(source(report, "config/entity_registry/list")["status"], "complete")
        self.assertTrue(report["definitions"])

    async def test_malformed_state_values(self):
        for bad in ([], {}, None, 1, True):
            with self.subTest(bad=bad):
                core = owner_core()
                core["states"].append({"entity_id": "sensor.malformed", "state": bad})
                report = await self.run_report(core_values=core,
                                               entity_ids=("sensor.malformed", "sensor.database_size"))
                rows = self.entities(report)
                self.assertEqual(rows["sensor.malformed"]["diagnosis"],
                                 {"classification": "undetermined", "reasons": ["state_invalid_response"]})
                self.assertEqual(rows["sensor.database_size"]["diagnosis"]["classification"], "available")
                self.assertEqual(source(report, "states")["reason"], "invalid_response")
                self.assertFalse(report["complete"])

    async def test_malformed_dependency_state(self):
        core = owner_core()
        core["states"] = [item for item in core["states"]
                          if item["entity_id"] not in ("sensor.water_rate", "sensor.water_total")]
        core["states"] += [state("sensor.water_rate", "unavailable"), {"entity_id": "sensor.water_total",
                                                                      "state": {"nested": 1}}]
        report = await self.run_report(core_values=core, entity_ids=("sensor.water_rate",))
        self.assertEqual(self.entities(report)["sensor.water_rate"]["diagnosis"],
                         {"classification": "undetermined",
                          "reasons": ["state_unavailable", "source_entity_invalid_response"]})

    async def test_malformed_registry_disabled_by(self):
        core = owner_core()
        core["config/entity_registry/list"][0]["disabled_by"] = ["user"]
        report = await self.run_report(core_values=core,
                                       entity_ids=("sensor.database_free_space", "sensor.database_size"))
        rows = self.entities(report)
        self.assertEqual(rows["sensor.database_free_space"]["diagnosis"],
                         {"classification": "undetermined", "reasons": ["registry_disabled_by_invalid_response"]})
        self.assertEqual(rows["sensor.database_size"]["definition"]["method"], "exact_unique_id")
        self.assertEqual(source(report, "config/entity_registry/list")["reason"], "invalid_response")
        self.assertFalse(report["complete"])

    async def test_malformed_config_entry_values(self):
        for field, bad in (("state", []), ("state", None), ("disabled_by", {"by": "user"}), ("disabled_by", 3)):
            with self.subTest(field=field, bad=bad):
                core = owner_core()
                core["config_entries/get"]["template"][0][field] = bad
                core["states"].append(state("sensor.ui_template", "unavailable"))
                report = await self.run_report(core_values=core, entity_ids=("sensor.ui_template",),
                                               integrations=("template",))
                self.assertEqual(report["ui_entries"][0][field], "invalid_response")
                self.assertEqual(self.entities(report)["sensor.ui_template"]["diagnosis"],
                                 {"classification": "undetermined", "reasons": ["config_entry_invalid_response"]})
                self.assertEqual(source(report, "config_entries/get:template")["reason"], "invalid_response")
                self.assertFalse(report["complete"])

    async def test_malformed_component_entry(self):
        core = owner_core()
        core["manifest/get"] = {"domain": c.COMPONENT_DOMAIN, "version": "1.2.0"}
        core["config_entries/get"][c.COMPONENT_DOMAIN] = [
            {"entry_id": COMPONENT_ENTRY, "domain": c.COMPONENT_DOMAIN, "title": "Tools", "state": ["loaded"],
             "disabled_by": None}]
        report = await self.run_report(core_values=core, integrations=("template",))
        self.assertEqual((report["capability"]["classification"], report["capability"]["reason"]),
                         ("undetermined", "config_entry_invalid_response"))
        self.assertFalse(report["complete"])

    async def test_a_projection_error_is_that_source_only(self):
        self.env = setup()
        self.addCleanup(end_request, self.env.token)
        service = self.env.service
        sources = []

        async def read():
            return [{"entity_id": "sensor.x", "state": "1"}]

        for error in (TypeError, ValueError, KeyError, AttributeError):
            with self.subTest(error=error.__name__):
                def project_(raw, error=error):
                    raise error("synthetic")

                value, reason = await service._projected(sources, "states", read, project_, service.authority())
                self.assertEqual((value, reason), (None, "invalid_response"))
                self.assertEqual((sources[-1]["status"], sources[-1]["reason"]), ("unavailable", "invalid_response"))

    async def test_valid_values_stay_complete(self):
        report = await self.run_report(entity_ids=OWNER_ENTITIES, integrations=("template",))
        for name in ("states", "config/entity_registry/list", "config_entries/get:template"):
            self.assertEqual(source(report, name)["status"], "complete")


def non_ascii(*code_points):
    """Text built from code points, so no non-ASCII character is in this file."""

    return "".join(chr(point) for point in code_points)


class FreeTextAllowlistTests(Base):
    """Correction batch 2 (H1, H2): Core free text is shown only through an allowlist.

    The full original title is checked before anything else; a title the
    allowlist does not clear is withheld whole and the report is partial. An
    ordinary non-ASCII title is withheld by design (disclosed).
    """

    UNVERIFIABLE = "[withheld:unverifiable_text]"

    async def title_report(self, title, *, gateway=False):
        core = owner_core()
        core["config_entries/get"]["template"][0]["title"] = title
        if not gateway:
            report = await self.run_report(core_values=core, integrations=("template",))
            return report, json.dumps(report), ""
        response, data, audit = await serve_through_gateway({"integrations": ["template"]}, owner_yaml(), core)
        self.assertTrue(data["success"])
        return data["data"], response, audit

    async def test_plain_ascii_titles_are_shown_through_the_gateway(self):
        for title in ("UI template helper", "Ratio 3:2", "Port 8123 / API", "T" * c.MAX_TITLE):
            with self.subTest(title):
                report, response, _audit = await self.title_report(title, gateway=True)
                self.assertEqual(report["ui_entries"][0]["title"], title)
                self.assertNotIn("unverifiable_text_withheld", report["omissions"])
                self.assertTrue(report["complete"])

    async def test_benign_non_ascii_titles_are_withheld_by_design(self):
        titles = {
            "latin_umlaut": "K" + non_ascii(0xFC) + "che",
            "latin_accent": "Caf" + non_ascii(0xE9) + " lights",
            "cjk": non_ascii(0x6E29, 0x5EA6),
            "em_dash": "Lights " + non_ascii(0x2014) + " hall",
            "emoji": "Lamp " + non_ascii(0x1F4A1),
            "tab": "Lamp\thall",
            "newline": "Lamp\nhall",
            "delete": "Lamp" + chr(0x7F),
        }
        for name, title in titles.items():
            with self.subTest(name):
                report, response, audit = await self.title_report(title, gateway=True)
                self.assertEqual(report["ui_entries"][0]["title"], self.UNVERIFIABLE)
                self.assertNotIn(title, response)
                self.assertNotIn(title, audit)
                self.assert_partial(report, "unverifiable_text_withheld")
                self.assertNotIn("credential_text_withheld", report["omissions"])

    async def test_allowlist_refusals_are_withheld_whole(self):
        value = syn.needle("allowlistvalue")
        titles = {
            "at_sign": "Alerts @ home",
            "address": "notify me@home",
            "scheme_separator": "see example://" + value,
            "token_equals": "token=" + value,
            "api_key_colon": "api_key: " + value,
            "pwd_colon": "pwd: " + value,
            "bearer_colon": "Bearer: " + value,
            "secret_spaced": "client secret = " + value,
            "auth_header": "Authorization: " + value,
        }
        for name, title in titles.items():
            with self.subTest(name):
                report, response, _audit = await self.title_report(title)
                self.assertEqual(report["ui_entries"][0]["title"], self.UNVERIFIABLE)
                self.assertNotIn(value, response)
                self.assert_partial(report, "unverifiable_text_withheld")

    async def test_over_length_title_is_withheld_whole_not_truncated(self):
        report, response, _audit = await self.title_report("T" * (c.MAX_TITLE + 1))
        self.assertEqual(report["ui_entries"][0]["title"], self.UNVERIFIABLE)
        self.assertNotIn("T" * 64, response)
        self.assert_partial(report, "unverifiable_text_withheld")

    async def test_credential_beyond_the_length_bound_is_found_on_the_original(self):
        # The credential tail lies wholly beyond MAX_TITLE: a check on a
        # truncated title would see only harmless padding.
        for suffix in (db_url(), DB_USER + ":" + DB_PASSWORD + "@synthetic.invalid"):
            with self.subTest(suffix=suffix[:8]):
                title = "x" * c.MAX_TITLE + " " + suffix
                report, response, _audit = await self.title_report(title)
                self.assertEqual(report["ui_entries"][0]["title"], "[withheld:credential_text]")
                self.assert_no_leak(response)
                self.assert_partial(report, "credential_text_withheld")
        # A credential that straddles the bound: its first part alone passes
        # every check, so only the original value reveals it.
        credential = DB_USER + ":" + DB_PASSWORD
        title = "x" * (c.MAX_TITLE - len(credential)) + credential + "@synthetic.invalid"
        from ha_mcp_engineering.configuration_definitions import credentials

        self.assertEqual(credentials.display_text(title[:c.MAX_TITLE], c.MAX_TITLE), title[:c.MAX_TITLE])
        self.assertEqual(credentials.display_text(title, c.MAX_TITLE), "[withheld:credential_text]")
        report, response, _audit = await self.title_report(title)
        self.assert_no_leak(response)
        self.assert_partial(report, "credential_text_withheld")

    async def test_known_secret_case_variants(self):
        from ha_mcp_engineering.configuration_definitions import credentials

        for variant in (ACCESS_SECRET.lower(), ACCESS_SECRET.upper(), ACCESS_SECRET.swapcase(),
                        "prefix " + HA_TOKEN.lower()):
            with self.subTest(variant=variant[:8]):
                self.assertEqual(credentials.display_text(variant, c.MAX_TITLE, (HA_TOKEN, ACCESS_SECRET)),
                                 "[withheld:credential_text]")
                report, response, _audit = await self.title_report(variant)
                self.assertEqual(report["ui_entries"][0]["title"], "[withheld:credential_text]")
                self.assertNotIn(variant, response)
                self.assert_partial(report, "credential_text_withheld")

    async def test_confusable_credential_title_reads_as_credential_text(self):
        confusable = db_url().replace(":", non_ascii(0x2236)).replace("/", non_ascii(0x2215))
        report, response, _audit = await self.title_report(confusable)
        self.assertEqual(report["ui_entries"][0]["title"], "[withheld:credential_text]")
        self.assert_no_leak(response)

    def assert_no_leak(self, text):
        for needle in LEAK_NEEDLES:
            self.assertNotIn(needle, text)


class ConfusableDefinitionTests(Base):
    """Correction batch 2 (H2): YAML-projected text is read through the confusable fold."""

    def forms(self):
        return {
            "ratio_division": db_url().replace(":", non_ascii(0x2236)).replace("/", non_ascii(0x2215)),
            "cyrillic_password_field": "Server=db;passw" + non_ascii(0x043E) + "rd=" + DB_PASSWORD,
            "greek_scheme": "p" + non_ascii(0x03BF) + "stgresql" + non_ascii(0xA789) + "//" + DB_USER + "@db",
            "fraction_slash_userinfo": "https:" + non_ascii(0x2044, 0x2044) + DB_USER + ":" + DB_PASSWORD
                                       + "@synthetic.invalid",
        }

    async def test_confusable_credential_in_a_definition_is_withheld(self):
        for name, text in self.forms().items():
            with self.subTest(name):
                values = owner_yaml(template=[{"sensor": [{"name": "Leak", "state": "{{ '" + text + "' }}"}]}])
                report = await self.run_report(values, integrations=("template",))
                serialized = json.dumps(report)
                self.assertNotIn(DB_PASSWORD, serialized)
                self.assertNotIn(DB_USER, serialized)
                # Withheld by the projection (field-level) or at the exit point.
                self.assertIn(report["definitions"][0]["fields"]["state"],
                              ({"withheld": "credential_uri"}, "[withheld:credential_text]"))

    def test_fold_is_bounded_and_ordinary_text_is_unchanged(self):
        from ha_mcp_engineering.configuration_definitions import credentials

        self.assertEqual(credentials.fold_confusables("living room 12:30 / api"), "living room 12:30 / api")
        self.assertEqual(credentials.fold_confusables(non_ascii(0x2236, 0x2215, 0x043E)), ":/o")
        for text in ("Living room helper", "sensor.database_size", "12:30"):
            self.assertFalse(credentials.credential_text(text))


class BlueprintGapTests(Base):
    """Correction batch 2 (M3): an unprojected blueprint block blocks absence."""

    async def test_blueprint_block_makes_absence_undetermined(self):
        values = owner_yaml(template=[{"use_blueprint": {"path": "synthetic/example.yaml", "input": {}}}])
        report = await self.run_report(values, entity_ids=("sensor.ghost",), integrations=("template",))
        self.assertEqual(report["definitions"][0]["status"], "blueprint_not_projected")
        diagnosis_ = self.entities(report)["sensor.ghost"]["diagnosis"]
        self.assertEqual(diagnosis_["classification"], "undetermined")
        self.assertIn("blueprint_definitions_not_projected", diagnosis_["reasons"])
        self.assertNotIn("definition_absent", diagnosis_["reasons"])
        self.assert_partial(report, "blueprint_definitions_not_projected")

    async def test_without_a_blueprint_absence_is_still_reported(self):
        report = await self.run_report(owner_yaml(), entity_ids=("sensor.ghost",), integrations=("template",))
        self.assertEqual(self.entities(report)["sensor.ghost"]["diagnosis"],
                         {"classification": "not_found",
                          "reasons": ["no_state", "definition_absent", "labelled_keys_not_addressable"]})
        self.assertNotIn("blueprint_definitions_not_projected", report["omissions"])


class RegistryIdentityTests(Base):
    """Correction batch 2 (M4): a malformed registry identity is not a legitimate absence."""

    MALFORMED = {
        "platform": ({"invalid": True}, ["sql"], 1, True, "Not A Domain"),
        "unique_id": ({"invalid": True}, ["sql_db_free_space"], 1, True),
        "config_entry_id": ({"invalid": True}, [UI_ENTRY], 1, True, "bad id!"),
    }

    async def test_malformed_identity_is_invalid_and_never_matched(self):
        for field, values in self.MALFORMED.items():
            for value in values:
                with self.subTest(field=field, value=repr(value)):
                    core = owner_core()
                    core["config/entity_registry/list"][0][field] = value
                    report = await self.run_report(
                        core_values=core, entity_ids=("sensor.database_free_space", "sensor.database_size"))
                    rows = self.entities(report)
                    broken = rows["sensor.database_free_space"]
                    self.assertEqual(broken["registry"]["identity"], "invalid_response")
                    self.assertEqual((broken["registry"]["platform"], broken["registry"]["config_entry_id"],
                                      broken["registry"]["has_unique_id"]), (None, None, False))
                    self.assertEqual(broken["definition"], {
                        "method": "unmatched", "confidence": None, "definition_ids": [],
                        "reason": "registry_identity_invalid_response"})
                    self.assertEqual(broken["diagnosis"], {"classification": "undetermined",
                                                           "reasons": ["registry_identity_invalid_response"]})
                    self.assertEqual(rows["sensor.database_size"]["definition"]["method"], "exact_unique_id")
                    self.assertEqual(rows["sensor.database_size"]["registry"]["identity"], "valid")
                    registry = source(report, "config/entity_registry/list")
                    self.assertEqual((registry["status"], registry["reason"]), ("partial", "invalid_response"))
                    self.assertFalse(report["complete"])
                    # The reverse match never claims the malformed row.
                    expected_ = [entity for d in report["definitions"] for entity in d["expected_entities"]
                                 if entity["match"]["entity_id"] == "sensor.database_free_space"]
                    self.assertEqual(expected_, [])

    async def test_reverse_match_names_the_malformed_identity(self):
        core = owner_core()
        core["config/entity_registry/list"][0]["unique_id"] = {"invalid": True}
        report = await self.run_report(core_values=core, integrations=("sql",))
        free_space = [entity["match"] for d in report["definitions"] for entity in d["expected_entities"]
                      if entity["unique_id"] == "sql_db_free_space"]
        self.assertEqual([(m["entity_id"], m["method"], m["reason"]) for m in free_space],
                         [(None, "unmatched", "registry_identity_invalid_response")])

    async def test_null_identity_fields_are_a_legitimate_absence(self):
        core = owner_core()
        core["config/entity_registry/list"][0].update(unique_id=None, config_entry_id=None)
        core["config/entity_registry/list"][1].update(platform=None)
        report = await self.run_report(core_values=core,
                                       entity_ids=("sensor.database_free_space", "sensor.database_size"))
        rows = self.entities(report)
        for entity_id in ("sensor.database_free_space", "sensor.database_size"):
            self.assertEqual(rows[entity_id]["registry"]["identity"], "valid")
            self.assertNotEqual(rows[entity_id]["definition"].get("reason"), "registry_identity_invalid_response")
        self.assertEqual(source(report, "config/entity_registry/list")["status"], "complete")


# Correction batch 3 (R2-H1): one representative per invisible-character
# class, named by code point so no such character is in this file.
INVISIBLE_POINTS = {
    "zero_width_space": 0x200B, "zero_width_non_joiner": 0x200C, "zero_width_joiner": 0x200D,
    "word_joiner": 0x2060, "byte_order_mark": 0xFEFF, "soft_hyphen": 0x00AD,
    "cc_bell": 0x07, "cc_escape": 0x1B, "cc_delete": 0x7F, "cc_next_line": 0x85,
    "cf_arabic_number_sign": 0x0600, "cf_interlinear_anchor": 0xFFF9, "cf_mongolian_vowel_separator": 0x180E,
    "bidi_lrm": 0x200E, "bidi_rlm": 0x200F, "bidi_alm": 0x061C, "bidi_rlo": 0x202E, "bidi_lri": 0x2066,
    "bidi_pdi": 0x2069,
    "variation_selector_1": 0xFE00, "variation_selector_16": 0xFE0F, "variation_selector_17": 0xE0100,
    "mongolian_fvs": 0x180B,
    "tag_language": 0xE0001, "tag_latin_a": 0xE0041, "tag_cancel": 0xE007F,
    "hangul_filler": 0x3164, "halfwidth_hangul_filler": 0xFFA0, "choseong_filler": 0x115F,
    "combining_grapheme_joiner": 0x034F,
    "co_private_use": 0xE000, "cn_unassigned": 0x0378, "zl_line_separator": 0x2028,
    "zp_paragraph_separator": 0x2029, "cs_surrogate": 0xD800,
}
UNVERIFIABLE = "[withheld:unverifiable_text]"
INVISIBLE_PASSWORD = syn.needle("invisiblepassword")


def percent_encoded(character):
    from urllib.parse import quote

    return quote(character, safe="")


def query_password(marker):
    """A URI whose ``password=`` field is split by ``marker``, built at runtime."""

    return "https://synthetic.invalid/?pass" + marker + "word=" + INVISIBLE_PASSWORD


class InvisibleTextTests(Base):
    """Correction batch 3 (R2-H1): invisible YAML text is withheld whole, never screened."""

    REVIEWER_POINTS = (0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF, 0xAD)

    async def state_report(self, text):
        values = owner_yaml(template=[{"sensor": [{"name": "Probe", "state": "{{ '" + text + "' }}"}]}])
        report = await self.run_report(values, integrations=("template",))
        return report, report["definitions"][0]

    async def test_reviewer_twelve_leak_cases_are_withheld_unverifiable(self):
        for point in self.REVIEWER_POINTS:
            for encoded in (False, True):
                with self.subTest(codepoint=hex(point), encoded=encoded):
                    marker = percent_encoded(chr(point)) if encoded else chr(point)
                    report, row = await self.state_report(query_password(marker))
                    self.assertNotIn(INVISIBLE_PASSWORD, json.dumps(report))
                    self.assertEqual(row["fields"]["state"], UNVERIFIABLE)
                    self.assertIn("template[0].sensor[0].state", row["withheld"])
                    self.assert_partial(report, "unverifiable_text_withheld", "withheld:definitions[0]")
                    self.assertNotIn("credential_text_withheld", report["omissions"])

    async def test_reviewer_cases_through_the_real_reader_and_gateway(self):
        from ha_mcp_engineering.providers import upstream_yaml_reader as reader

        for point in self.REVIEWER_POINTS:
            for encoded in (False, True):
                with self.subTest(codepoint=hex(point), encoded=encoded):
                    marker = percent_encoded(chr(point)) if encoded else chr(point)
                    parsed = [{"sensor": [{"name": "Probe", "state": "{{ '" + query_password(marker) + "' }}"}]}]
                    payload = {"success": True, "yaml_path": "template", "count": 1, "files_searched": 1,
                               "files_unreadable": 0, "matches": [{"file": "configuration.yaml",
                                                                   "yaml_path": "template", "parsed": parsed}]}
                    result = reader.project_success({"content": [{"type": "text", "text": json.dumps(payload)}]},
                                                    "template", known_secrets=(HA_TOKEN, ACCESS_SECRET))
                    values = owner_yaml()
                    values["template"]["data"] = result.envelope
                    response, data, audit = await serve_through_gateway({"integrations": ["template"]}, values)
                    self.assertNotIn(INVISIBLE_PASSWORD, response + audit)
                    # The reader may already redact it; either way it is withheld whole.
                    self.assertIn(data["data"]["definitions"][0]["fields"]["state"],
                                  (UNVERIFIABLE, {"withheld": "reader_redaction"}))
                    self.assertEqual(data["metadata"]["completeness"], "partial")

    def test_each_invisible_category_is_unverifiable_literal_and_encoded(self):
        from ha_mcp_engineering.configuration_definitions import credentials

        for name, point in INVISIBLE_POINTS.items():
            with self.subTest(name):
                self.assertTrue(credentials.unverifiable_text("Kitchen" + chr(point) + "lights"))
                if name != "cs_surrogate":  # a lone surrogate has no UTF-8 encoding
                    once = percent_encoded(chr(point))
                    self.assertTrue(credentials.unverifiable_text("Kitchen" + once + "lights"))
                    self.assertTrue(credentials.unverifiable_text("Kitchen" + percent_encoded(once) + "lights"))

    async def test_each_invisible_category_withholds_the_whole_definition_value(self):
        for name, point in INVISIBLE_POINTS.items():
            with self.subTest(name):
                # Not credential text at all: still withheld, never shown.
                report, row = await self.state_report("Kitchen" + chr(point) + "lights")
                self.assertEqual(row["fields"]["state"], UNVERIFIABLE)
                self.assertNotIn("Kitchen", json.dumps(report["definitions"]))
                self.assert_partial(report, "unverifiable_text_withheld", "withheld:definitions[0]")

    async def test_encoded_beyond_the_decode_bound_is_withheld(self):
        from ha_mcp_engineering.configuration_definitions import credentials

        deep = query_password(chr(0x200B))
        for _round in range(6):
            deep = percent_encoded(deep)
        self.assertTrue(credentials.credential_text(deep) or credentials.unverifiable_text(deep))
        report, row = await self.state_report(deep)
        self.assertNotIn(INVISIBLE_PASSWORD, json.dumps(report))
        self.assertIn(row["fields"]["state"], (UNVERIFIABLE, {"withheld": "credential_uri"},
                                               "[withheld:credential_text]"))
        self.assert_partial(report, "withheld:definitions[0]")

    async def test_invisible_nested_key_is_dropped(self):
        key = "pass" + chr(0x200B) + "word"
        values = owner_yaml(template=[{"sensor": [{"name": "Probe", "state": "on",
                                                   "attributes": {key: INVISIBLE_PASSWORD, "kept": "yes"}}]}])
        report = await self.run_report(values, integrations=("template",))
        row = report["definitions"][0]
        self.assertNotIn(INVISIBLE_PASSWORD, json.dumps(report))
        self.assertEqual(row["fields"]["attributes"], {"kept": "yes"})
        self.assertIn("template[0].sensor[0].attributes.<key>", row["withheld"])
        self.assert_partial(report, "withheld:definitions[0]")

    async def test_exit_point_withholds_invisible_text_outside_the_projection(self):
        values = owner_yaml(group={"lights" + chr(0x200B): {"entities": ["light.kitchen"]}})
        report = await self.run_report(values, integrations=("group",))
        serialized = json.dumps(report, ensure_ascii=False)
        self.assertNotIn(chr(0x200B), serialized)
        self.assert_partial(report, "unverifiable_text_withheld")
        self.assertTrue(any(item.startswith("withheld:definitions[") for item in report["omissions"]))

    async def test_exit_point_withholds_invisible_strings_and_keys(self):
        env = setup()
        self.addCleanup(end_request, env.token)
        base = await env.service.report((), ("template",))
        self.assertTrue(base["complete"])
        report = dict(base, probe=query_password(chr(0x2060)),
                      probe_keys={"pass" + chr(0xFEFF) + "word": INVISIBLE_PASSWORD, "kept": "yes"})
        omissions = list(report.pop("omissions"))
        result = env.service._finish(report, omissions, omissions.append)
        self.assertNotIn(INVISIBLE_PASSWORD, json.dumps(result))
        self.assertEqual(result["probe"], UNVERIFIABLE)
        self.assertEqual(result["probe_keys"], {"kept": "yes"})
        self.assertIn("unverifiable_text_withheld", result["omissions"])
        self.assertIn("withheld:probe", result["omissions"])
        self.assertIn("withheld:probe_keys", result["omissions"])
        self.assertNotIn("credential_text_withheld", result["omissions"])
        self.assertFalse(result["complete"])

    async def test_exit_point_invisible_key_alone_is_unverifiable(self):
        env = setup()
        self.addCleanup(end_request, env.token)
        base = await env.service.report((), ("template",))
        report = dict(base, probe_keys={"kept" + chr(0x2066): "x", "kept": "yes"})
        omissions = list(report.pop("omissions"))
        result = env.service._finish(report, omissions, omissions.append)
        self.assertEqual(result["probe_keys"], {"kept": "yes"})
        self.assertEqual(result["omissions"], ["unverifiable_text_withheld", "withheld:probe_keys"])
        self.assertFalse(result["complete"])

    async def test_benign_definitions_are_still_shown(self):
        texts = {
            "multi_line_template": "{% if is_state('sun.sun', 'above_horizon') %}\n\t{{ 1 }}\r\n{% endif %}",
            "degrees_celsius": non_ascii(0xB0) + "C",
            "micrograms_per_cubic_metre": non_ascii(0xB5) + "g/m" + non_ascii(0xB3),
            "umlaut_name": "K" + non_ascii(0xFC) + "che",
            "en_dash": "Hall " + non_ascii(0x2013) + " lights",
            "no_break_space": "20" + non_ascii(0xA0) + "W",
            "emoji_without_selector": "Lamp " + non_ascii(0x1F4A1),
            "percent_text": "100% {{ 50 }}%",
            "encoded_letter": "%41",
            "unit_and_lines": non_ascii(0xB0) + "C\n\t{{ 1 }}\r\n",
            "plain_template": "{{ states('sensor.outdoor_temperature') | float(0) * 1.8 + 32 }}",
        }
        for name, text in texts.items():
            with self.subTest(name):
                values = owner_yaml(template=[{"sensor": [{"name": "Shown", "state": text,
                                                           "unit_of_measurement": non_ascii(0xB0) + "C"}]}])
                report = await self.run_report(values, integrations=("template",))
                fields = report["definitions"][0]["fields"]
                self.assertEqual((fields["state"], fields["unit_of_measurement"]), (text, non_ascii(0xB0) + "C"))
                self.assertEqual(report["omissions"], [])
                self.assertTrue(report["complete"])

    def test_predicate_leaves_ordinary_text_alone(self):
        from ha_mcp_engineering.configuration_definitions import credentials

        for text in ("", "plain", "a\tb\nc\rd", non_ascii(0xB0) + "C", "100%", "%41%42", "50% off",
                     non_ascii(0x6E29, 0x5EA6), "Lamp " + non_ascii(0x1F4A1)):
            with self.subTest(text=text):
                self.assertFalse(credentials.unverifiable_text(text))
        # A decode the reader refuses is left to credential_text, which fails closed.
        self.assertFalse(credentials.unverifiable_text("%FF"))
        self.assertTrue(credentials.credential_text("%FF"))


class WithheldCompletenessTests(Base):
    """Correction batch 3 (R2-M1): anything withheld, in any form, makes the report partial."""

    async def test_reviewer_projection_confusable_cases_are_partial(self):
        for word in ("p" + non_ascii(0x03B1) + "ssword", "p" + non_ascii(0x0430) + "ssw" + non_ascii(0x03BF) + "rd"):
            with self.subTest(word=word):
                text = "https://synthetic.invalid/?" + word + "=" + INVISIBLE_PASSWORD
                values = owner_yaml(template=[{"sensor": [{"name": "Probe", "state": "{{ '" + text + "' }}"}]}])
                report = await self.run_report(values, integrations=("template",))
                self.assertNotIn(INVISIBLE_PASSWORD, json.dumps(report))
                self.assertEqual(report["definitions"][0]["fields"]["state"], {"withheld": "credential_uri"})
                self.assertEqual(report["omissions"], ["withheld:definitions[0]"])
                self.assert_partial(report)

    async def test_every_withholding_path_is_partial(self):
        template = lambda **fields: owner_yaml(template=[{"sensor": [{"name": "Probe", "state": "on", **fields}]}])
        cases = {
            "secret_ref": template(attributes={"a": {"secret_ref": SECRET_NAME}}),
            "env_var_ref": template(attributes={"a": {"env_var_ref": ENV_NAME}}),
            "reader_redaction": template(attributes={"a": "[REDACTED:token]"}),
            "reader_truncation": template(attributes={"a": "[TRUNCATED:9]"}),
            "credential_uri": template(attributes={"a": db_url()}),
            "nested_credential_key": template(attributes={"password": NESTED_PASSWORD}),
            "nested_connection_key": template(attributes={"database_connection": "x"}),
            "nested_db_url_key": template(attributes={"db_url": db_url()}),
            "non_text_key": template(attributes={7: "x"}),
            "unsupported_value": template(attributes={"a": object()}),
            "invisible_value": template(attributes={"a": "x" + chr(0x200B)}),
            "invisible_key": template(attributes={"a" + chr(0x200D): "x"}),
            "known_secret_at_exit": template(attributes={"a": "{{ '" + HA_TOKEN + "' }}"}),
            "fixed_db_url_field": owner_yaml(sql=[owner_sql()[0]]),
        }
        for name, values in cases.items():
            with self.subTest(name):
                integration = "sql" if name == "fixed_db_url_field" else "template"
                report = await self.run_report(values, integrations=(integration,))
                self.assert_partial(report, "withheld:definitions[0]")

    async def test_withheld_titles_name_their_entry(self):
        for title, summary in ((db_url(), "credential_text_withheld"),
                               ("K" + non_ascii(0xFC) + "che", "unverifiable_text_withheld")):
            with self.subTest(summary):
                core = owner_core()
                core["config_entries/get"]["template"][0]["title"] = title
                report = await self.run_report(core_values=core, integrations=("template",))
                self.assert_partial(report, summary, "withheld:ui_entries[0]")

    async def test_dropped_credential_key_names_its_item(self):
        env = setup()
        self.addCleanup(end_request, env.token)
        base = await env.service.report((), ("template",))
        report = dict(base, probe_keys={db_url(): "dropped", "kept": "yes"})
        omissions = list(report.pop("omissions"))
        result = env.service._finish(report, omissions, omissions.append)
        self.assertEqual(result["probe_keys"], {"kept": "yes"})
        self.assertEqual(result["omissions"], ["credential_text_withheld", "withheld:probe_keys"])
        self.assertFalse(result["complete"])

    async def test_omission_text_is_never_itself_an_item(self):
        env = setup()
        self.addCleanup(end_request, env.token)
        base = await env.service.report((), ("template",))
        report = dict(base)
        omissions = list(report.pop("omissions")) + ["source_note:" + db_url()]
        result = env.service._finish(report, omissions, omissions.append)
        self.assertNotIn(DB_PASSWORD, json.dumps(result))
        self.assertIn("credential_text_withheld", result["omissions"])
        self.assertFalse(any(item.startswith("withheld:omissions") for item in result["omissions"]))

    async def test_withheld_items_are_bounded(self):
        from ha_mcp_engineering.configuration_definitions import credentials

        count = credentials.MAX_WITHHELD_ITEMS + 5
        sensors = [{"name": f"Probe {index}", "state": {"secret_ref": SECRET_NAME}} for index in range(count)]
        report = await self.run_report(owner_yaml(template=[{"sensor": sensors}]), integrations=("template",))
        items = [item for item in report["omissions"] if item.startswith("withheld:")]
        self.assertEqual(items, [f"withheld:definitions[{index}]" for index in range(credentials.MAX_WITHHELD_ITEMS)])
        self.assertIn("withheld_items_not_listed:5", report["omissions"])
        self.assert_partial(report)

    async def test_nothing_withheld_stays_complete(self):
        report = await self.run_report(integrations=("template", "group", "derivative"))
        self.assertEqual(report["omissions"], [])
        self.assertTrue(report["complete"])

    def test_scan_reads_every_withheld_form(self):
        from ha_mcp_engineering.configuration_definitions import credentials

        envelope = {
            "definitions": [{"withheld": [], "fields": {"a": "kept"}},
                            {"withheld": ["x.a"], "fields": {}},
                            {"withheld": [], "fields": {"a": {"withheld": "secret_ref"}}},
                            {"withheld": [], "fields": {"a": [credentials.WITHHELD_MARKER]}}],
            "ui_entries": [{"title": credentials.UNVERIFIABLE_MARKER}],
            "capability": {"status": "kept"},
            "omissions": [credentials.WITHHELD_MARKER],
        }
        self.assertEqual(credentials.withheld_items(envelope, [(("coverage", "<key>"), credentials.WITHHELD_MARKER)]), [
            "withheld:definitions[1]", "withheld:definitions[2]", "withheld:definitions[3]",
            "withheld:ui_entries[0]", "withheld:coverage"])
