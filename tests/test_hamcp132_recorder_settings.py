"""HAMCP-132 read-only recorder settings (``get_recorder_settings``).

Fixtures model the pinned Home Assistant Core 2026.10.1 recorder surfaces
(``components/recorder/__init__.py`` ``CONFIG_SCHEMA``,
``basic_websocket_api.py`` ``recorder/info``, ``websocket_api.py``
``recorder/entity_options/get``) and the reviewed ``ha_config_get_yaml``
envelope. Everything is synthetic; no live Home Assistant. Credential-shaped
values are assembled at test time.
"""

from __future__ import annotations

import ast
import copy
from datetime import datetime, timezone
import hashlib
import importlib.util
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
import yaml as yaml11  # noqa: E402  PyYAML, the YAML 1.1 loader beneath Core's annotatedyaml

import hamcp162_synthetic as syn  # noqa: E402
from ha_mcp_engineering.audit import AuditLogger  # noqa: E402
from ha_mcp_engineering.capabilities import (  # noqa: E402
    BETA_NATIVE_CAPABILITIES,
    CAPABILITY_PROVIDER_MATRIX,
)
from ha_mcp_engineering.clients import websocket as ws  # noqa: E402
from ha_mcp_engineering.configuration import Settings  # noqa: E402
from ha_mcp_engineering.errors import (  # noqa: E402
    AuthorizationError,
    ErrorCode,
    HomeAssistantApiError,
    HomeAssistantTimeoutError,
    HomeAssistantUnavailableError,
)
from ha_mcp_engineering.ha_core_readmission.routes import STATIC_TOOL_CORE_REQUIREMENTS  # noqa: E402
from ha_mcp_engineering.providers.upstream_read_gateway import (  # noqa: E402
    YAML_CONFIG_PATHS,
    UpstreamReadGateway,
)
from ha_mcp_engineering.recorder_settings import contracts as c  # noqa: E402
from ha_mcp_engineering.recorder_settings import entity_filter as f  # noqa: E402
from ha_mcp_engineering.recorder_settings import projection  # noqa: E402
from ha_mcp_engineering.recorder_settings.projection import gateway_envelope, yaml11_readings  # noqa: E402
from ha_mcp_engineering.recorder_settings.service import (  # noqa: E402
    RECORDER_SETTINGS,
    RecorderSettingsRuntime,
    RecorderSettingsService,
)
from ha_mcp_engineering.request_context import begin_request, current_telemetry, end_request  # noqa: E402
from ha_mcp_engineering.routing import AuthenticatedMcpGateway  # noqa: E402
from ha_mcp_engineering.tools import get_registered_server, registered_tools  # noqa: E402
from ha_mcp_engineering.tools import recorder_settings as public  # noqa: E402

PACKAGE = ROOT / "hass_mcp_engineering_beta/ha_mcp_engineering/recorder_settings"
TOOL = ROOT / "hass_mcp_engineering_beta/ha_mcp_engineering/tools/recorder_settings.py"
TRUTH_TABLE = ROOT / "docs/evidence/hamcp132/filter-truth-table.json"
CORE_SOURCE = Path.home() / ".cache/hamcp209/core-2026.10.1/homeassistant/helpers/entityfilter.py"
NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
HA_TOKEN = syn.needle("hatoken")
ACCESS_SECRET = syn.needle("accesssecret")
DB_USER = syn.needle("recorderdbuser")
DB_PASSWORD = syn.needle("recorderdbpassword")
SECRET_NAME = syn.needle("recordersecretname")
ENV_NAME = syn.needle("recorderenvname")
UNKNOWN_KEY = syn.needle("unknownkey")
UNKNOWN_VALUE = syn.needle("unknownvalue")
NEEDLES = (HA_TOKEN, ACCESS_SECRET, DB_USER, DB_PASSWORD, SECRET_NAME, ENV_NAME, UNKNOWN_KEY, UNKNOWN_VALUE)
MISSING = object()
INFO = {
    "backlog": 0, "max_backlog": 65000, "migration_in_progress": False, "migration_is_live": False,
    "recording": True, "thread_running": True, "db_in_default_location": False,
}
RECORDED = (True, {"recording_disabled_by": None})
DISABLED = (True, {"recording_disabled_by": "user"})
BRANCH_CASES = {"1": f.NO_FILTER, "2": f.INCLUDE_ONLY, "3": f.EXCLUDE_ONLY,
                "4a": f.INCLUDE_DOMAIN_OR_GLOB, "4b": f.EXCLUDE_DOMAIN_OR_GLOB, "6": f.INCLUDE_ENTITIES_ONLY}


def db_url(scheme="postgresql"):
    """A non-HTTP database URL with userinfo, assembled at test time."""

    return syn.url(scheme, DB_USER, DB_PASSWORD, "db.synthetic.invalid:5432/homeassistant")


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


def yaml(recorder=MISSING, *, packages=MISSING, core=MISSING, recorder_kwargs=None, core_kwargs=None):
    return {
        "recorder": envelope("recorder", recorder, **(recorder_kwargs or {})),
        "homeassistant.packages": envelope("homeassistant.packages", packages),
        "homeassistant": envelope("homeassistant", {"name": "Synthetic home"} if core is MISSING else core,
                                  **(core_kwargs or {})),
    }


def healthy_yaml():
    return yaml({"purge_keep_days": 7, "db_url": db_url(),
                 "exclude": {"domains": ["automation"], "entity_globs": ["sensor.*_power"],
                             "entities": ["light.porch"]}})


def healthy_ws():
    return {"recorder/info": (True, dict(INFO)), c.ENTITY_OPTIONS_TYPE: {}}


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

    def __init__(self, values):
        self.values = values
        self.calls = []
        self.hook = None

    async def read(self, path):
        self.calls.append(path)
        if self.hook:
            self.hook(path)
        value = self.values[path]
        if isinstance(value, Exception):
            raise value
        return copy.deepcopy(value)


class SyntheticClient:
    """Exposes only ``command_batch``; every payload must pass the allowlist."""

    def __init__(self, values):
        self.values = values
        self.calls = []
        self.hook = None

    async def command_batch(self, payloads):
        assert all(ws.batch_command_allowed(payload) for payload in payloads), payloads
        self.calls.append(copy.deepcopy(payloads))
        if self.hook:
            self.hook(payloads)
        kind = payloads[0]["type"]
        value = self.values[kind]
        if isinstance(value, Exception):
            raise value
        if callable(value):
            return value(payloads)
        if kind == c.ENTITY_OPTIONS_TYPE:
            return [copy.deepcopy(value.get(item["entity_id"], RECORDED)) for item in payloads]
        return [copy.deepcopy(value)]


def setup(yaml_values=None, ws_values=None, *, admitted=True, known_secrets=(HA_TOKEN, ACCESS_SECRET),
          caller="synthetic_authenticated_caller"):
    reader = SyntheticReader(yaml_values if yaml_values is not None else healthy_yaml())
    client = SyntheticClient(ws_values if ws_values is not None else healthy_ws())
    core = SyntheticCore()
    service = RecorderSettingsService(
        client, core, yaml_reader_factory=(lambda: reader) if admitted else (lambda: None),
        known_secrets=known_secrets, clock=lambda: NOW)
    telemetry, token = begin_request()
    telemetry.caller_id = caller
    telemetry.core_dispatch_authorizer = lambda: core.available
    return service, reader, client, core, telemetry, token


def settings(**values):
    return Settings(**{**dict(ha_url="http://synthetic.invalid", ha_token=HA_TOKEN,
        access_secret=ACCESS_SECRET, port=8100, audit_path="unused",
        rate_limit_per_minute=1000, rate_limit_burst=1000, destructive_services=frozenset()), **values})


def source(report, name):
    return next(item for item in report["sources"] if item["source"] == name)


class Base(unittest.IsolatedAsyncioTestCase):
    async def run_report(self, yaml_values=None, ws_values=None, entity_ids=(), **kwargs):
        service, reader, client, core, telemetry, token = setup(yaml_values, ws_values, **kwargs)
        self.addCleanup(end_request, token)
        report = await service.report(entity_ids)
        self.reader, self.client, self.core, self.telemetry = reader, client, core, telemetry
        text = json.dumps(report)
        for value in NEEDLES:
            self.assertNotIn(value, text)
        return report

    def assert_default(self, entry, value):
        self.assertEqual(entry, {"value": value, "origin": "core_default", "pin": f.CORE_PIN})

    def assert_unknown(self, report, reason, keys=tuple(c.SCALAR_SETTINGS)):
        for key in keys:
            self.assertEqual(report["settings"][key], {"value": None, "origin": "unknown", "reason": reason}, key)
            self.assertIn(f"setting_unknown:{key}:{reason}", report["omissions"])
        self.assertFalse(report["complete"])
        self.assertEqual(report["completeness"], "partial")


class SettingsTests(Base):
    async def test_complete_file_reports_configured_and_core_defaults(self):
        report = await self.run_report(entity_ids=["light.kitchen"])
        self.assertTrue(report["complete"], report["omissions"])
        self.assertEqual(report["schema"], c.SCHEMA)
        self.assertEqual(report["core_pin"], "2026.10.1")
        self.assertEqual(report["packages"], "absent")
        self.assertEqual(report["settings"]["purge_keep_days"], {"value": 7, "origin": "configured"})
        for key, (_validator, default) in c.SCALAR_SETTINGS.items():
            if key != "purge_keep_days":
                self.assert_default(report["settings"][key], default)
        self.assertEqual(report["settings"]["db_url"],
                         {"value_withheld": True, "default_location": False, "origin": "configured"})
        self.assertNotIn("purge_interval", report["settings"])
        self.assertEqual([item["source"] for item in report["sources"]],
                         ["configuration_yaml:recorder", "configuration_yaml:homeassistant.packages",
                          "recorder/info", "recorder/entity_options/get"])
        self.assertTrue(all(item["status"] == "complete" for item in report["sources"]))
        self.assertEqual(self.reader.calls, ["recorder", "homeassistant.packages", "homeassistant"])
        self.assertEqual(report["coverage"]["yaml_reader"], SyntheticReader.provenance)
        self.assertFalse(report["coverage"]["includes_followed"])
        self.assertEqual(self.telemetry.result_status, "success")
        self.assertEqual(self.telemetry.audit_context["upstream_calls"], 3)
        self.assertEqual(self.telemetry.audit_context["entity_count"], 1)
        self.assertEqual(report["upstream_calls"], 3)

    async def test_no_recorder_key_and_empty_recorder_key_are_core_defaults(self):
        for recorder in (MISSING, None, {}):
            with self.subTest(recorder=recorder):
                report = await self.run_report(yaml(recorder))
                self.assertTrue(report["complete"], report["omissions"])
                for key, (_validator, default) in c.SCALAR_SETTINGS.items():
                    self.assert_default(report["settings"][key], default)
                self.assertEqual(report["settings"]["db_url"]["origin"], "core_default_location")
                self.assertEqual(report["filters"]["origin"], "core_default")
                self.assertEqual(report["filters"]["filter_case"], f.NO_FILTER)

    async def test_packages_absent_variants(self):
        variants = {
            "packages_not_found_core_not_found": yaml({}, core=MISSING) | {
                "homeassistant": envelope("homeassistant")},
            "core_null": yaml({}, core=None),
            "core_without_packages": yaml({}, core={"name": "Synthetic", "unit_system": "metric"}),
            "packages_empty_mapping": yaml({}, packages={}),
        }
        for label, values in variants.items():
            with self.subTest(label):
                report = await self.run_report(values)
                self.assertEqual(report["packages"], "absent")
                self.assertTrue(report["complete"], report["omissions"])
                if label == "packages_empty_mapping":
                    self.assertEqual(self.reader.calls, ["recorder", "homeassistant.packages"])

    async def test_packages_present_makes_unwritten_keys_unknown_not_defaults(self):
        report = await self.run_report(yaml({"purge_keep_days": 3, "include": {"domains": ["light"]}},
                                            packages={"climate": {"recorder": {"purge_keep_days": 30}}}))
        self.assertEqual(report["packages"], "present")
        self.assertEqual(report["settings"]["purge_keep_days"], {"value": 3, "origin": "configured"})
        self.assert_unknown(report, "packages_present", [k for k in c.SCALAR_SETTINGS if k != "purge_keep_days"])
        self.assertEqual(report["settings"]["db_url"]["origin"], "unknown")
        self.assertEqual(report["settings"]["db_url"]["reason"], "packages_present")
        self.assertEqual(report["filters"]["origin"], "unknown")
        self.assertTrue(report["filters"]["configuration_yaml_portion_only"])
        self.assertFalse(report["filters"]["computable"])
        self.assertIn("filter_unknown:packages_present", report["omissions"])
        self.assertNotIn("climate", json.dumps(report))
        self.assertEqual(self.reader.calls, ["recorder", "homeassistant.packages"])

    async def test_packages_unreadable_variants(self):
        variants = {
            "packages_include": yaml({}, packages="!include_dir_named packages"),
            "core_include": yaml({}, core="!include core.yaml"),
            "core_packages_key_unreadable": yaml({}, core={"packages": None}),
            "core_truncated": yaml({}, core={"name": "x"}, core_kwargs={
                "completeness": "partial", "limitations": ["value_truncated:homeassistant"]}),
            "core_read_failed": yaml({}) | {"homeassistant": RuntimeError(UNKNOWN_VALUE)},
            "packages_read_failed": yaml({}) | {"homeassistant.packages": {"success": False,
                                                                            "error_code": "yaml_parse_error"}},
        }
        for label, values in variants.items():
            with self.subTest(label):
                report = await self.run_report(values)
                self.assertEqual(report["packages"], "unreadable")
                self.assert_unknown(report, "packages_unreadable")
                self.assertEqual(source(report, "configuration_yaml:homeassistant.packages")["status"],
                                 "unavailable")
                self.assertEqual(report["filters"]["reason"], "packages_unreadable")
        report = await self.run_report(variants["packages_read_failed"])
        self.assertEqual(source(report, "configuration_yaml:homeassistant.packages")["reason"],
                         "yaml_parse_error")

    async def test_include_at_recorder_or_unnamed_key_hides_every_unwritten_key(self):
        variants = {
            "recorder_include": yaml("!include recorder.yaml"),
            "include_limitation_at_recorder": yaml({}, recorder_kwargs={
                "limitations": ["include_not_followed:recorder"]}),
            "merge_key_include": yaml({"<<": "!include recorder_common.yaml", "purge_keep_days": 4}),
            "unnamed_key_include_limitation": yaml({"extra": {"x": 1}}, recorder_kwargs={
                "limitations": ["include_not_followed:recorder.extra"]}),
        }
        for label, values in variants.items():
            with self.subTest(label):
                report = await self.run_report(values)
                keys = [k for k in c.SCALAR_SETTINGS if not (label == "merge_key_include" and k == "purge_keep_days")]
                self.assert_unknown(report, "include_not_followed", keys)
                self.assertEqual(report["filters"]["reason"], "include_not_followed")
                self.assertNotIn("recorder_common", json.dumps(report))

    async def test_include_under_a_named_key_affects_only_that_key(self):
        report = await self.run_report(yaml({"purge_keep_days": "!include keep.yaml"}))
        self.assertEqual(report["settings"]["purge_keep_days"],
                         {"value": None, "origin": "unknown", "reason": "include_not_followed"})
        self.assert_default(report["settings"]["auto_purge"], True)
        self.assertIn("setting_unknown:purge_keep_days:include_not_followed", report["omissions"])

    async def test_truncated_recorder_value_is_unknown(self):
        report = await self.run_report(yaml({"purge_keep_days": 2}, recorder_kwargs={
            "completeness": "partial", "limitations": ["value_truncated:recorder.exclude"]}))
        self.assertEqual(report["settings"]["purge_keep_days"], {"value": 2, "origin": "configured"})
        self.assert_unknown(report, "yaml_truncated", [k for k in c.SCALAR_SETTINGS if k != "purge_keep_days"])
        self.assertEqual(report["filters"]["reason"], "yaml_truncated")
        self.assertEqual(source(report, "configuration_yaml:recorder")["status"], "partial")
        for limitation in ("value_depth_limited:recorder", "strings_truncated:1", "include_not_followed_truncated"):
            report = await self.run_report(yaml({}, recorder_kwargs={"limitations": [limitation]}))
            self.assertEqual(report["settings"]["auto_purge"]["reason"], "yaml_truncated", limitation)

    async def test_yaml_unavailable_variants_name_the_exact_reason(self):
        variants = {
            "reader_raises": (RuntimeError(UNKNOWN_VALUE), "yaml_read_failed"),
            "closed_code": ({"success": False, "error_code": "yaml_file_not_found"}, "yaml_file_not_found"),
            "unknown_code": ({"success": False, "error_code": UNKNOWN_VALUE}, "yaml_read_failed"),
            "wrong_provider": (dict(envelope("recorder", {}), metadata={"provider": "direct_ha_api",
                                                                        "fallback_occurred": False}),
                               "yaml_read_response_identity_invalid"),
            "fallback": (dict(envelope("recorder", {}), metadata={"provider": "upstream_read_gateway",
                                                                  "fallback_occurred": True}),
                         "yaml_read_response_identity_invalid"),
            "other_path": (envelope("homeassistant", {}), "yaml_read_response_identity_invalid"),
            "other_file": ({**envelope("recorder", {}), "data": {**envelope("recorder", {})["data"],
                                                                  "file": "secrets.yaml"}},
                           "yaml_read_response_identity_invalid"),
            "not_a_mapping": (["recorder"], "yaml_read_failed"),
        }
        for label, (value, reason) in variants.items():
            with self.subTest(label):
                values = healthy_yaml() | {"recorder": value}
                report = await self.run_report(values)
                self.assert_unknown(report, "yaml_unavailable")
                self.assertEqual(source(report, "configuration_yaml:recorder"),
                                 {"source": "configuration_yaml:recorder", "status": "unavailable",
                                  "reason": reason, "observed_at": "2026-10-20T12:00:00Z"})
                self.assertEqual(report["settings"]["db_url"],
                                 {"value_withheld": True, "default_location": False, "origin": "unknown",
                                  "reason": "yaml_unavailable"})
                self.assertEqual(report["filters"], {"origin": "unknown", "computable": False,
                                                     "reason": "yaml_unavailable"})
                self.assertEqual(report["runtime"]["status"], "available")

    async def test_reader_not_admitted_is_partial_with_runtime_still_reported(self):
        report = await self.run_report(admitted=False, entity_ids=["light.kitchen"])
        self.assert_unknown(report, "yaml_unavailable")
        self.assertEqual(source(report, "configuration_yaml:recorder")["reason"], "yaml_reader_not_admitted")
        self.assertEqual(report["packages"], "unreadable")
        self.assertEqual(report["runtime"]["recording"], True)
        self.assertEqual(report["entities"][0]["core_recorded"], True)
        self.assertEqual(report["entities"][0]["agreement"], "unknown")
        self.assertIsNone(report["coverage"]["yaml_reader"])
        self.assertEqual(report["upstream_calls"], 0)
        self.assertEqual(self.reader.calls, [])

    async def test_recorder_value_not_a_mapping(self):
        report = await self.run_report(yaml(["purge_keep_days", 3]))
        self.assert_unknown(report, "recorder_value_not_a_mapping")
        self.assertIn("recorder_value_not_a_mapping", report["omissions"])
        self.assertEqual(report["filters"]["reason"], "recorder_value_not_a_mapping")

    async def test_values_validated_by_core_schema(self):
        report = await self.run_report(yaml({
            "purge_keep_days": 0, "commit_interval": "7", "auto_purge": "off",
            "auto_repack": "sometimes", "db_max_retries": [1], "purge_interval": 1,
            "db_integrity_check": True}))
        s = report["settings"]
        self.assertEqual(s["purge_keep_days"], {"value": None, "origin": "configured", "invalid": True,
                                                "reason": "fails_core_schema", "as_written": 0})
        self.assertEqual(s["commit_interval"], {"value": 7, "origin": "configured", "as_written": "7"})
        self.assertEqual(s["auto_purge"], {"value": False, "origin": "configured", "as_written": "off"})
        self.assertTrue(s["auto_repack"]["invalid"])
        self.assertEqual(s["db_max_retries"], {"value": None, "origin": "configured", "invalid": True,
                                               "reason": "fails_core_schema"})
        self.assertEqual(s["purge_interval"], {"value": 1, "origin": "configured", "deprecated": True})
        self.assertEqual(s["db_integrity_check"], {"value": True, "origin": "configured", "deprecated": True})
        self.assertTrue(report["complete"])

    async def test_unrecognized_keys_are_only_counted(self):
        report = await self.run_report(yaml({UNKNOWN_KEY: UNKNOWN_VALUE, "other": {"password": DB_PASSWORD}}))
        self.assertEqual(report["settings"]["unrecognized_key_count"], 2)
        self.assertIn("unrecognized_recorder_keys_not_reported", report["omissions"])
        self.assertFalse(report["complete"])


# Plain (unquoted) spellings whose YAML 1.1 and YAML 1.2 readings differ, with
# the value the YAML 1.2 reader returns, plus the spec booleans ``y``/``n``
# that both read as strings. Observed with PyYAML 6.0.2 (YAML 1.1) and
# ruamel.yaml 0.19.1 ``typ="rt"`` (YAML 1.2, the reader's loader); the YAML 1.1
# side is re-derived from PyYAML in every test, the YAML 1.2 side is recorded
# here.
YAML12_READS = {
    "off": "off", "Off": "Off", "OFF": "OFF", "on": "on", "ON": "ON", "yes": "yes", "Yes": "Yes",
    "YES": "YES", "no": "no", "NO": "NO", "1:20": "1:20", "1:20.5": "1:20.5", "190:20:30": "190:20:30",
    "y": "y", "Y": "Y", "n": "n", "N": "N",
    "1e3": 1000.0, "1.0e3": 1000.0,
}
# Plain integer spellings the reader returns as a number while YAML 1.1 reads
# them differently. The spelling is not observed, so owner decision 1
# (2026-10-20) reports the number as definite and discloses the difference in
# ``coverage.yaml_numeric_spelling``.
YAML12_INTEGER_READS = {
    "010": 10, "0010": 10, "0_10": 10, "012_3": 123, "-010": -10, "08": 8, "09": 9, "0o10": 8,
}
# Strings ruamel.yaml 0.19.1's YAML 1.2 resolver reads as another type when
# plain (recorded from ``YAML(typ="rt")``): a reader string with this text was
# quoted, and a quoted scalar is a string under YAML 1.1 as well.
YAML12_QUOTED_ONLY = (
    "010", "0010", "10", "14", "30", "365", "+10", "-010", "08", "0o10", "0x1F", "0b101", "1_000",
    "0_10", "1.5", "1e3", "1.5e3", "1E3", ".5", "1.", ".inf", "-.inf", ".nan", "true", "True",
    "FALSE", "null", "Null", "~", "",
)
# Strings the same resolver keeps strings when plain (recorded likewise).
YAML12_PLAIN_STRINGS = (
    "on", "off", "yes", "No", "y", "n", "1:20", "1:20.5", "_1", "0o", "0b2", "0xg", "e3", ".e3",
    "1e", ".", "nULL", "tRUE", "light", "sensor.temperature",
)
FIELD_VALIDATORS = {"commit_interval": "int_min_0", "purge_keep_days": "int_min_1", "auto_purge": "boolean"}


def core_outcome(field, loaded):
    """(valid, value) for what Core's YAML 1.1 loader produced, under the ported validator."""

    try:
        if FIELD_VALIDATORS[field] == "boolean":
            return True, f.boolean(loaded)
        return True, f.coerce_int(loaded, 1 if field == "purge_keep_days" else 0)
    except f.FilterValueInvalid:
        return False, None


def plausible_core_outcomes(field, spelling):
    """Core's outcome for every spelling the reader cannot tell apart from ``spelling``."""

    read = YAML12_READS[spelling]
    if type(read) is str:
        # Quoted or not, the YAML 1.2 reader returns the same string.
        others = ['"' + spelling + '"']
    else:
        # The reader's number is also what its plain decimal spelling reads as.
        others = [repr(read)]
    loaded = [yaml11.safe_load(f"k: {text}")["k"] for text in (spelling, *others)]
    return [core_outcome(field, value) for value in loaded]


class YamlSpellingTests(Base):
    """M1: a value whose YAML 1.1 reading differs is parser-dependent, never a definite claim."""

    async def test_reviewer_reproductions_are_parser_dependent(self):
        cases = {
            "commit_interval": ("off", [("quoted", False, None), ("unquoted", True, 0)]),
            "purge_keep_days": ("yes", [("quoted", False, None), ("unquoted", True, 1)]),
            "db_retry_wait": ("1:20", [("quoted", False, None), ("unquoted", True, 80)]),
        }
        report = await self.run_report(yaml({key: value for key, (value, _) in cases.items()}))
        for key, (value, readings) in cases.items():
            with self.subTest(key):
                self.assertEqual(report["settings"][key], {
                    "value": None, "origin": "configured", "validity": "parser_dependent",
                    "reason": "yaml_1_1_reading_differs", "as_read": value,
                    "readings": [{"spelling": spelling, "valid": valid, "value": coerced}
                                 for spelling, valid, coerced in readings]})
                self.assertNotIn("invalid", report["settings"][key])
                self.assertIn(f"setting_parser_dependent:{key}", report["omissions"])
        self.assertFalse(report["complete"])
        self.assertEqual(report["completeness"], "partial")

    async def test_paired_spellings_never_yield_an_unestablished_claim(self):
        seen = set()
        for spelling, read in YAML12_READS.items():
            for field in FIELD_VALIDATORS:
                with self.subTest(spelling=spelling, field=field):
                    report = await self.run_report(yaml({field: read}))
                    entry = report["settings"][field]
                    outcomes = plausible_core_outcomes(field, spelling)
                    seen.add("parser_dependent" if entry.get("validity") else
                             "invalid" if entry.get("invalid") else "definite")
                    if entry.get("validity") == "parser_dependent":
                        readings = {(item["valid"], item["value"]) for item in entry["readings"]}
                        for outcome in outcomes:
                            self.assertIn(outcome, readings)
                        self.assertGreater(len(set(outcomes)), 1)
                    elif entry.get("invalid"):
                        self.assertEqual(entry["reason"], "fails_core_schema")
                        self.assertTrue(all(not valid for valid, _ in outcomes), outcomes)
                    else:
                        self.assertEqual({(True, entry["value"])}, set(outcomes))
        self.assertEqual(seen, {"parser_dependent", "invalid", "definite"})

    async def test_common_integers_are_definite(self):
        # Owner decision 1: an integer is reported as parsed, never parser-dependent.
        for value in (10, 14, 30, 365):
            with self.subTest(value=value):
                report = await self.run_report(yaml({
                    "purge_keep_days": value, "commit_interval": value, "db_max_retries": value,
                    "db_retry_wait": value}))
                for key in ("purge_keep_days", "commit_interval", "db_max_retries", "db_retry_wait"):
                    self.assertEqual(report["settings"][key], {"value": value, "origin": "configured"})
                self.assertFalse(any(item.startswith("setting_parser_dependent:")
                                     for item in report["omissions"]))
                self.assertTrue(report["complete"])
                self.assertEqual(report["coverage"]["yaml_numeric_spelling"],
                                 c.YAML_NUMERIC_SPELLING_LIMITATION)

    async def test_integer_spellings_are_disclosed_not_detected(self):
        for spelling, read in YAML12_INTEGER_READS.items():
            with self.subTest(spelling):
                # The disclosed difference is real: YAML 1.1 reads the spelling otherwise.
                self.assertNotEqual(yaml11.safe_load(f"k: {spelling}")["k"], read)
                self.assertIsNone(yaml11_readings(read))
                report = await self.run_report(yaml({"db_max_retries": read}))
                entry = report["settings"]["db_max_retries"]
                if read < 0:
                    self.assertEqual(entry, {"value": None, "origin": "configured", "invalid": True,
                                             "reason": "fails_core_schema", "as_written": read})
                else:
                    self.assertEqual(entry, {"value": read, "origin": "configured"})
                self.assertEqual(report["coverage"]["yaml_numeric_spelling"],
                                 c.YAML_NUMERIC_SPELLING_LIMITATION)
        for text in ("leading zero", "010", "octal", "0o10"):
            self.assertIn(text, c.YAML_NUMERIC_SPELLING_LIMITATION)
        self.assertLessEqual(len(c.YAML_NUMERIC_SPELLING_LIMITATION), c.MAX_FILTER_TEXT)
        self.assertLessEqual(len(c.YAML_PARSE_DISCLOSURE), c.MAX_FILTER_TEXT)

    async def test_quoted_strings_are_definite_strings(self):
        for text in YAML12_QUOTED_ONLY:
            with self.subTest(text=text):
                # Quoted, YAML 1.1 reads the same string.
                self.assertEqual(yaml11.safe_load(f'k: "{text}"')["k"], text)
                self.assertIsNone(yaml11_readings(text))
        report = await self.run_report(yaml({
            "commit_interval": "010", "purge_keep_days": "365", "db_max_retries": "1.5",
            "auto_purge": "true", "include": {"domains": ["010", "true", "1.5", "null"]}}),
            entity_ids=["light.kitchen"])
        s = report["settings"]
        self.assertEqual(s["commit_interval"], {"value": 10, "origin": "configured", "as_written": "010"})
        self.assertEqual(s["purge_keep_days"], {"value": 365, "origin": "configured", "as_written": "365"})
        self.assertEqual(s["db_max_retries"], {"value": None, "origin": "configured", "invalid": True,
                                               "reason": "fails_core_schema", "as_written": "1.5"})
        self.assertEqual(s["auto_purge"], {"value": True, "origin": "configured", "as_written": "true"})
        self.assertEqual(report["filters"]["include"]["domains"], ["010", "true", "1.5", "null"])
        self.assertFalse(any(item.startswith(("setting_parser_dependent:", "filter_unknown:"))
                             for item in report["omissions"]))
        self.assertEqual(report["coverage"]["yaml_parse"], c.YAML_PARSE_DISCLOSURE)

    async def test_spec_booleans_y_n_are_plain_strings(self):
        # PyYAML's YAML 1.1 boolean resolver does not list y/Y/n/N.
        resolvers = [regexp for regexp_tag, regexp in yaml11.resolver.Resolver.yaml_implicit_resolvers["y"]
                     if regexp_tag == "tag:yaml.org,2002:bool"]
        self.assertEqual(len(resolvers), 1)
        for text in ("y", "Y", "n", "N"):
            with self.subTest(text=text):
                self.assertIsNone(resolvers[0].match(text))
                self.assertEqual(yaml11.safe_load(f"k: {text}")["k"], text)
                self.assertIsNone(yaml11_readings(text))
        report = await self.run_report(yaml({
            "auto_purge": "y", "auto_repack": "n", "include": {"domains": ["y", "n"]}}),
            entity_ids=["light.kitchen"])
        for key, text in (("auto_purge", "y"), ("auto_repack", "n")):
            self.assertEqual(report["settings"][key], {"value": None, "origin": "configured", "invalid": True,
                                                       "reason": "fails_core_schema", "as_written": text})
        self.assertEqual(report["filters"]["include"]["domains"], ["y", "n"])
        self.assertFalse(any(item.startswith(("setting_parser_dependent:", "filter_unknown:"))
                             for item in report["omissions"]))

    async def test_genuinely_invalid_values_still_fail_core_schema(self):
        # Every reading fails: no spelling makes these valid under Core.
        report = await self.run_report(yaml({
            "purge_keep_days": "off", "commit_interval": -10, "auto_repack": "sometimes",
            "db_max_retries": "never", "db_retry_wait": [1], "auto_purge": 1.5}))
        s = report["settings"]
        for key in ("purge_keep_days", "commit_interval", "auto_repack", "db_max_retries", "db_retry_wait"):
            with self.subTest(key):
                self.assertTrue(s[key]["invalid"])
                self.assertEqual(s[key]["reason"], "fails_core_schema")
        # 1.5 is a valid boolean; its exponent spelling would not be.
        self.assertEqual(s["auto_purge"]["validity"], "parser_dependent")

    async def test_values_every_reading_agrees_on_stay_definite(self):
        report = await self.run_report(yaml({
            "auto_purge": "off", "auto_repack": "YES", "commit_interval": 7, "purge_keep_days": "5",
            "db_max_retries": 0}))
        s = report["settings"]
        self.assertEqual(s["auto_purge"], {"value": False, "origin": "configured", "as_written": "off"})
        self.assertEqual(s["auto_repack"], {"value": True, "origin": "configured", "as_written": "YES"})
        self.assertEqual(s["commit_interval"], {"value": 7, "origin": "configured"})
        self.assertEqual(s["purge_keep_days"], {"value": 5, "origin": "configured", "as_written": "5"})
        self.assertEqual(s["db_max_retries"], {"value": 0, "origin": "configured"})
        self.assertFalse(any(item.startswith("setting_parser_dependent:") for item in report["omissions"]))
        self.assertTrue(report["complete"])

    async def test_filter_lists_with_differing_readings_are_not_computed(self):
        variants = {
            "domain_on": ({"include": {"domains": ["on"]}}, "parser_dependent"),
            "event_type_yes": ({"exclude": {"event_types": ["yes", "call_service"]}}, "parser_dependent"),
            "single_glob_off": ({"exclude": {"entity_globs": "off"}}, "parser_dependent"),
            "entity_sexagesimal": ({"exclude": {"entities": ["1:20"]}}, "fails_core_schema"),
            "entity_off": ({"exclude": {"entities": ["off"]}}, "fails_core_schema"),
        }
        for label, (recorder, reason) in variants.items():
            with self.subTest(label):
                report = await self.run_report(yaml(recorder), entity_ids=["light.kitchen"])
                self.assertEqual(report["filters"], {"origin": "unknown", "computable": False, "reason": reason})
                self.assertIn(f"filter_unknown:{reason}", report["omissions"])
                self.assertIsNone(report["entities"][0]["computed_recorded"])
                # The authoritative per-entity Core answer is retained.
                self.assertEqual(report["entities"][0]["core_recorded"], True)

    def test_yaml_1_1_port_matches_pyyaml(self):
        for spelling, read in YAML12_READS.items():
            with self.subTest(spelling):
                loaded = yaml11.safe_load(f"k: {spelling}")["k"]
                if loaded == read:
                    # Read alike under both versions (``y``/``n``).
                    self.assertIsNone(yaml11_readings(read))
                    continue
                kind, readings = yaml11_readings(read)
                self.assertEqual(kind, {str: "unquoted", float: "exponent"}[type(read)])
                if type(read) is float:
                    # The spelling is not recoverable; the reading is a
                    # representative one that YAML 1.1 also keeps a string.
                    self.assertIs(type(loaded), str)
                    self.assertEqual(yaml11.safe_load(f"k: {readings[0]}")["k"], readings[0])
                else:
                    self.assertEqual(readings, [loaded])
        # A float's exponent spelling is a YAML 1.1 string.
        for value in (1000.0, 1.5, -0.25):
            (reading,) = yaml11_readings(value)[1]
            self.assertEqual(yaml11.safe_load(f"k: {reading}")["k"], reading)
        for value in (0, 1, 7, -7, 8, 9, 10, 14, 30, 365, -10, True, False, "light", "sensor.temperature",
                      ".inf", "010", float("inf")):
            with self.subTest(value=value):
                self.assertIsNone(yaml11_readings(value))

    def test_yaml_1_2_quoting_port_matches_recorded_ruamel(self):
        for text in YAML12_QUOTED_ONLY:
            with self.subTest(quoted=text):
                self.assertTrue(projection._YAML12_NOT_STRING.match(text))
        for text in YAML12_PLAIN_STRINGS:
            with self.subTest(plain=text):
                self.assertIsNone(projection._YAML12_NOT_STRING.match(text))

    @unittest.skipUnless(importlib.util.find_spec("ruamel") and importlib.util.find_spec("ruamel.yaml"),
                         "ruamel.yaml is not installed; the recorded table is checked instead")
    def test_yaml_1_2_quoting_port_matches_installed_ruamel(self):
        import io

        from ruamel.yaml import YAML

        loader = YAML(typ="rt")
        for text in (*YAML12_QUOTED_ONLY, *YAML12_PLAIN_STRINGS, *YAML12_READS, *YAML12_INTEGER_READS):
            with self.subTest(text=text):
                loaded = loader.load(io.StringIO(f"k: {text}\n"))["k"]
                self.assertEqual(bool(projection._YAML12_NOT_STRING.match(text)), not isinstance(loaded, str))


class CredentialTests(Base):
    async def test_db_url_is_withheld_for_every_written_form(self):
        forms = {
            "postgresql_userinfo": db_url(),
            "mysql_driver_userinfo": db_url("mysql+pymysql"),
            "sqlite_with_query_secret": "sqlite" + syn.SCHEME_SEPARATOR + "/config/" + DB_PASSWORD + ".db",
            "secret_ref": {"secret_ref": SECRET_NAME},
            "env_var_ref": {"env_var_ref": ENV_NAME},
            "connection_marker": "[REDACTED:connection_string]",
            "credential_marker": "[REDACTED:credential_key]",
            "nested": {"url": db_url(), "user": DB_USER},
        }
        for label, value in forms.items():
            with self.subTest(label):
                report = await self.run_report(yaml({"db_url": value}))
                self.assertEqual(report["settings"]["db_url"],
                                 {"value_withheld": True, "default_location": False, "origin": "configured"})

    async def test_scalar_references_and_markers_are_withheld(self):
        report = await self.run_report(yaml({
            "purge_keep_days": {"secret_ref": SECRET_NAME}, "commit_interval": {"env_var_ref": ENV_NAME},
            "auto_purge": "[REDACTED:credential_key]"}))
        s = report["settings"]
        self.assertEqual(s["purge_keep_days"], {"value": None, "origin": "configured", "value_withheld": True,
                                                "reason": "secret_reference"})
        self.assertEqual(s["commit_interval"]["reason"], "secret_reference")
        self.assertEqual(s["auto_purge"], {"value": None, "origin": "configured", "value_withheld": True,
                                           "reason": "redacted"})

    async def test_filter_with_reference_or_marker_is_withheld_not_computed(self):
        for value in ({"secret_ref": SECRET_NAME}, "[REDACTED:credential_key]"):
            with self.subTest(value=value):
                report = await self.run_report(yaml({"exclude": {"entities": [value]}}),
                                               entity_ids=["light.kitchen"])
                self.assertEqual(report["filters"], {"origin": "unknown", "computable": False,
                                                     "reason": "value_withheld"})
                self.assertEqual(report["entities"][0]["computed_reason"], "filter_not_computable")

    async def test_known_secret_in_filter_text_is_redacted_and_partial(self):
        report = await self.run_report(yaml({"exclude": {"entity_globs": ["sensor." + HA_TOKEN + "*"]}}))
        self.assertIn("redacted_by_policy", report["omissions"])
        self.assertFalse(report["complete"])

    async def test_provider_exception_text_never_in_public_failure(self):
        values = {path: RuntimeError(UNKNOWN_VALUE) for path in YAML_CONFIG_PATHS}
        service, _reader, _client, _core, _telemetry, token = setup(
            values, {"recorder/info": HomeAssistantApiError(UNKNOWN_VALUE), c.ENTITY_OPTIONS_TYPE: {}})
        self.addCleanup(end_request, token)
        with patch.object(RECORDER_SETTINGS, "service", service), patch.object(public, "SETTINGS", settings()):
            rendered = await public.get_recorder_settings(entity_ids=[])
        result = json.loads(rendered)
        self.assertFalse(result["success"])
        self.assertEqual(result["error_code"], ErrorCode.ANALYSIS_UNAVAILABLE.value)
        self.assertNotIn(UNKNOWN_VALUE, rendered)


class RuntimeTests(Base):
    async def test_unknown_command_means_recorder_not_set_up_never_not_recording(self):
        report = await self.run_report(
            ws_values={"recorder/info": (False, "unknown_command"),
                       c.ENTITY_OPTIONS_TYPE: {"light.kitchen": (False, "unknown_command")}},
            entity_ids=["light.kitchen"])
        self.assertEqual(report["runtime"], {"status": "unavailable", "reason": "recorder_not_set_up"})
        self.assertEqual(source(report, "recorder/info")["status"], "unavailable")
        self.assertEqual(source(report, "recorder/info")["reason"], "recorder_not_set_up")
        self.assertIsNone(report["entities"][0]["core_recorded"])
        self.assertEqual(report["entities"][0]["core_reason"], "recorder_not_set_up")
        self.assertNotIn("recording", report["runtime"])
        self.assertFalse(report["complete"])
        self.assertEqual(self.telemetry.result_status, "partial")

    async def test_runtime_status_fields(self):
        report = await self.run_report()
        self.assertEqual(report["runtime"], {"status": "available", **INFO,
                                             "disable_service_state": "not_observable"})

    async def test_runtime_failure_shapes(self):
        cases = {
            "command_failed": ((False, "home_assistant_error"), "command_failed"),
            "not_a_mapping": ((True, ["recording"]), "invalid_response"),
        }
        for label, (outcome, reason) in cases.items():
            with self.subTest(label):
                report = await self.run_report(ws_values={"recorder/info": outcome, c.ENTITY_OPTIONS_TYPE: {}})
                self.assertEqual(report["runtime"], {"status": "unavailable", "reason": reason})
                self.assertIn("source_unavailable:recorder/info", report["omissions"])

    async def test_missing_or_mistyped_runtime_fields_are_null_and_partial(self):
        info = dict(INFO, recording="yes")
        info.pop("backlog")
        report = await self.run_report(ws_values={"recorder/info": (True, info), c.ENTITY_OPTIONS_TYPE: {}})
        self.assertIsNone(report["runtime"]["recording"])
        self.assertIsNone(report["runtime"]["backlog"])
        self.assertIn("runtime_field_unavailable:recording", report["omissions"])
        self.assertIn("runtime_field_unavailable:backlog", report["omissions"])
        info = dict(INFO, backlog=None)
        report = await self.run_report(ws_values={"recorder/info": (True, info), c.ENTITY_OPTIONS_TYPE: {}})
        self.assertIsNone(report["runtime"]["backlog"])
        self.assertTrue(report["complete"])

    async def test_batch_exceptions_map_to_closed_reasons(self):
        cases = [
            (AuthorizationError(), "permission_failure", "refused"),
            (HomeAssistantTimeoutError(), "timeout", "unavailable"),
            (HomeAssistantUnavailableError(), "home_assistant_unavailable", "unavailable"),
            (HomeAssistantApiError(), "command_failed", "unavailable"),
            (RuntimeError(UNKNOWN_VALUE), "source_unavailable", "unavailable"),
            (lambda payloads: [], "invalid_response", "unavailable"),
        ]
        for error, reason, status in cases:
            with self.subTest(reason):
                report = await self.run_report(ws_values={"recorder/info": error, c.ENTITY_OPTIONS_TYPE: {}})
                self.assertEqual(report["runtime"], {"status": "unavailable", "reason": reason})
                self.assertEqual(source(report, "recorder/info")["status"], status)
                self.assertEqual(report["settings"]["purge_keep_days"]["value"], 7)
                self.assertIsNone(report["settings"]["db_url"]["default_location"])

    async def test_entity_options_refusal_keeps_runtime_status(self):
        report = await self.run_report(
            ws_values={"recorder/info": (True, dict(INFO)), c.ENTITY_OPTIONS_TYPE: AuthorizationError()},
            entity_ids=["light.kitchen", "sensor.temperature"])
        self.assertEqual(report["runtime"]["status"], "available")
        self.assertEqual(source(report, "recorder/entity_options/get")["status"], "refused")
        self.assertEqual(source(report, "recorder/entity_options/get")["reason"], "permission_failure")
        for row in report["entities"]:
            self.assertIsNone(row["core_recorded"])
            self.assertEqual(row["core_reason"], "permission_failure")
            self.assertEqual(row["agreement"], "unknown")
        self.assertEqual(len(self.client.calls), 2)
        self.assertEqual(self.client.calls[0], [{"type": "recorder/info"}])

    async def test_every_source_unavailable_is_an_error(self):
        for ws_failure in (AuthorizationError(), HomeAssistantTimeoutError()):
            service, reader, client, core, telemetry, token = setup(
                ws_values={"recorder/info": ws_failure, c.ENTITY_OPTIONS_TYPE: ws_failure}, admitted=False)
            try:
                with self.assertRaises(c.RecorderSettingsError) as caught:
                    await service.report(["light.kitchen"])
                self.assertEqual(caught.exception.reason, "sources_unavailable")
            finally:
                end_request(token)


class EntityTests(Base):
    async def test_per_entity_answers_and_failures(self):
        options = {"light.kitchen": RECORDED, "light.porch": DISABLED,
                   "switch.heater": (True, {"recording_disabled_by": "integration"}),
                   "switch.pump": (True, {"recording_disabled_by": None, "extra": 1}),
                   "climate.hall": (False, "unknown_command"), "binary_sensor.door": (False, "not_found")}
        report = await self.run_report(yaml({}), {"recorder/info": (True, dict(INFO)),
                                                  c.ENTITY_OPTIONS_TYPE: options}, entity_ids=list(options))
        rows = {row["entity_id"]: row for row in report["entities"]}
        self.assertEqual(list(rows), list(options))
        self.assertEqual(rows["light.kitchen"], {"entity_id": "light.kitchen", "core_recorded": True,
                                                 "computed_recorded": True, "agreement": "agree"})
        self.assertEqual(rows["light.porch"]["core_recorded"], False)
        self.assertEqual(rows["light.porch"]["agreement"], "filter_drift")
        self.assertEqual(rows["switch.heater"]["core_reason"], "invalid_response")
        self.assertEqual(rows["switch.pump"]["core_reason"], "invalid_response")
        self.assertEqual(rows["climate.hall"]["core_reason"], "recorder_not_set_up")
        self.assertEqual(rows["binary_sensor.door"]["core_reason"], "command_failed")
        status = source(report, "recorder/entity_options/get")
        self.assertEqual((status["status"], status["reason"]), ("partial", "invalid_response"))
        self.assertEqual(report["coverage"]["entities_answered_by_core"], 2)
        self.assertEqual(self.client.calls[1], [{"type": c.ENTITY_OPTIONS_TYPE, "entity_id": e} for e in options])

    async def test_no_entity_ids_issues_no_entity_options_batch(self):
        report = await self.run_report()
        self.assertEqual(self.client.calls, [[{"type": "recorder/info"}]])
        self.assertEqual(report["entities"], [])
        self.assertNotIn("recorder/entity_options/get", [s["source"] for s in report["sources"]])

    async def test_fifty_entity_ids_accepted(self):
        ids = [f"sensor.synthetic_{n}" for n in range(c.MAX_ENTITY_IDS)]
        report = await self.run_report(entity_ids=ids)
        self.assertEqual(len(report["entities"]), c.MAX_ENTITY_IDS)
        self.assertEqual(len(self.client.calls[1]), c.MAX_ENTITY_IDS)

    async def test_invalid_entity_ids_refused_before_dispatch(self):
        bad = [
            [f"sensor.synthetic_{n}" for n in range(c.MAX_ENTITY_IDS + 1)],
            ["light.kitchen", "light.kitchen"], ["Light.Kitchen"], ["light"], ["light.kit__chen"],
            ["light._kitchen"], ["light.kitchen/../x"], [7], ["sensor." + "a" * 250],
            "light.kitchen", {"light.kitchen": 1}, frozenset({"light.kitchen"}), None,
        ]
        for entity_ids in bad:
            service, reader, client, core, telemetry, token = setup()
            try:
                with self.assertRaises(ValueError):
                    await service.report(entity_ids)
                self.assertEqual((reader.calls, client.calls), ([], []))
            finally:
                end_request(token)
        report = await self.run_report(entity_ids=("light.kitchen",))
        self.assertEqual(len(report["entities"]), 1)
        for arguments in (None, [], {"entity_ids": "light.kitchen"}, {"entity_ids": None},
                          {"entity_ids": [], "limit": 1}):
            with self.assertRaises(ValueError):
                c.validate_arguments(arguments)


class FilterTruthTableTests(unittest.TestCase):
    def table(self):
        return json.loads(TRUTH_TABLE.read_text())

    def test_port_matches_core_truth_table_for_every_case(self):
        table = self.table()
        self.assertEqual(table["schema"], "hamcp132-filter-truth-table-v1")
        self.assertEqual({case["core_branch"] for case in table["cases"]}, set(BRANCH_CASES))
        for case in table["cases"]:
            with self.subTest(case["id"]):
                recorder_filter = f.RecorderFilter(case["include"], case["exclude"])
                self.assertEqual(recorder_filter.case, BRANCH_CASES[case["core_branch"]])
                self.assertEqual(set(case["recorded"]), set(table["entities"]))
                for entity_id, recorded in case["recorded"].items():
                    self.assertIs(recorder_filter.recorded(entity_id), recorded, entity_id)

    def test_truth_table_provenance_binds_the_pin(self):
        provenance = self.table()["provenance"]
        self.assertEqual(provenance["core_version"], f.CORE_PIN)
        self.assertEqual(provenance["core_commit"], "abe1fdd7cd6b4de1715c87b6fd87d92699514ef9")
        self.assertEqual(provenance["source_path"], "homeassistant/helpers/entityfilter.py")
        self.assertTrue((ROOT / provenance["generator"]).is_file())

    @unittest.skipUnless(CORE_SOURCE.is_file(), "pinned Core source not cached locally")
    def test_truth_table_regenerates_from_pinned_core_source(self):
        import subprocess

        self.assertEqual(hashlib.sha256(CORE_SOURCE.read_bytes()).hexdigest(),
                         self.table()["provenance"]["source_sha256"])
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "table.json"
            subprocess.run([sys.executable, "-I", str(ROOT / self.table()["provenance"]["generator"]),
                            "--core", str(CORE_SOURCE), "--output", str(output)], check=True)
            self.assertEqual(json.loads(output.read_text()), self.table())


class FilterTests(Base):
    async def test_filter_drift_is_reported_never_resolved(self):
        report = await self.run_report(
            yaml({"exclude": {"domains": ["sensor"]}}),
            {"recorder/info": (True, dict(INFO)),
             c.ENTITY_OPTIONS_TYPE: {"sensor.temperature": RECORDED, "light.porch": DISABLED}},
            entity_ids=["sensor.temperature", "light.porch", "light.kitchen"])
        rows = {row["entity_id"]: row for row in report["entities"]}
        self.assertEqual((rows["sensor.temperature"]["core_recorded"], rows["sensor.temperature"]["computed_recorded"],
                          rows["sensor.temperature"]["agreement"]), (True, False, "filter_drift"))
        self.assertEqual((rows["light.porch"]["core_recorded"], rows["light.porch"]["agreement"]),
                         (False, "filter_drift"))
        self.assertEqual(rows["light.kitchen"]["agreement"], "agree")
        self.assertIn("filter_drift:sensor.temperature", report["omissions"])
        self.assertIn("filter_drift:light.porch", report["omissions"])
        self.assertFalse(report["complete"])
        self.assertEqual(report["filters"]["filter_case"], f.EXCLUDE_ONLY)

    async def test_case_4a_reports_ignored_exclude_domains_and_state_changed(self):
        report = await self.run_report(yaml({
            "include": {"domains": ["light"]},
            "exclude": {"domains": ["sensor"], "event_types": ["state_changed", "call_service"]}}))
        filters = report["filters"]
        self.assertEqual(filters["filter_case"], f.INCLUDE_DOMAIN_OR_GLOB)
        self.assertTrue(filters["exclude_domains_ignored"])
        self.assertTrue(filters["state_changed_event_type_ignored"])
        self.assertEqual(filters["origin"], "configured")
        self.assertEqual(filters["exclude"]["event_types"], ["state_changed", "call_service"])
        self.assertTrue(report["complete"])

    async def test_single_string_lists_coerce_like_core(self):
        report = await self.run_report(yaml({"include": {"domains": "light", "entities": "switch.heater"}}),
                                       entity_ids=["switch.heater", "sensor.temperature"])
        self.assertEqual(report["filters"]["include"]["domains"], ["light"])
        self.assertEqual(report["filters"]["include"]["entities"], ["switch.heater"])
        rows = {row["entity_id"]: row["computed_recorded"] for row in report["entities"]}
        self.assertEqual(rows, {"switch.heater": True, "sensor.temperature": False})

    async def test_filter_values_failing_core_schema_are_not_computed(self):
        variants = {
            "include_null": ({"include": None}, "fails_core_schema"),
            "include_list": ({"include": ["light"]}, "fails_core_schema"),
            "unknown_subkey": ({"include": {"domain": ["light"]}}, "fails_core_schema"),
            "include_event_types": ({"include": {"event_types": ["x"]}}, "fails_core_schema"),
            "invalid_entity": ({"exclude": {"entities": ["Not An Entity"]}}, "fails_core_schema"),
            "include_value": ({"exclude": {"entities": "!include excluded.yaml"}}, "include_not_followed"),
            "include_part": ({"exclude": "!include exclude.yaml"}, "include_not_followed"),
            "too_many": ({"exclude": {"entities": [f"sensor.s{n}" for n in range(c.MAX_FILTER_ITEMS + 1)]}},
                         "filter_too_large"),
            "too_long": ({"exclude": {"entity_globs": ["sensor." + "x" * c.MAX_FILTER_TEXT]}}, "filter_too_large"),
        }
        for label, (recorder, reason) in variants.items():
            with self.subTest(label):
                report = await self.run_report(yaml(recorder), entity_ids=["light.kitchen"])
                self.assertEqual(report["filters"], {"origin": "unknown", "computable": False, "reason": reason})
                self.assertIn(f"filter_unknown:{reason}", report["omissions"])
                self.assertIsNone(report["entities"][0]["computed_recorded"])
                self.assertEqual(report["entities"][0]["core_recorded"], True)

    async def test_large_filter_lists_become_counts_within_the_budget(self):
        lists = {key: [f"sensor.synthetic_long_entity_name_{key}_{n:04d}" for n in range(c.MAX_FILTER_ITEMS)]
                 for key in ("entities", "entity_globs")}
        report = await self.run_report(yaml({"exclude": lists}))
        self.assertEqual(report["filters"]["exclude"]["entities"], {"count": c.MAX_FILTER_ITEMS, "listed": False})
        self.assertEqual(report["filters"]["include"]["domains"], {"count": 0, "listed": False})
        self.assertIn("filter_lists_not_listed_response_bytes", report["omissions"])
        self.assertLessEqual(len(json.dumps(report, separators=(",", ":"))), c.RESPONSE_BUDGET + 200)
        self.assertFalse(report["complete"])


class AuthorityTests(Base):
    async def test_unavailable_core_route_dispatches_nothing(self):
        service, reader, client, core, telemetry, token = setup()
        self.addCleanup(end_request, token)
        core.available = False
        with self.assertRaises(c.RecorderSettingsError) as caught:
            await service.report(["light.kitchen"])
        self.assertEqual(caught.exception.reason, "authority_unavailable")
        self.assertEqual((reader.calls, client.calls), ([], []))

    async def test_anonymous_caller_refused_before_dispatch(self):
        service, reader, client, core, telemetry, token = setup(caller="anonymous")
        self.addCleanup(end_request, token)
        with self.assertRaises(c.RecorderSettingsError):
            await service.report()
        self.assertEqual((reader.calls, client.calls), ([], []))

    async def test_missing_dispatch_authorizer_or_request_refused(self):
        service, reader, client, core, telemetry, token = setup()
        telemetry.core_dispatch_authorizer = None
        with self.assertRaises(c.RecorderSettingsError):
            await service.report()
        end_request(token)
        with self.assertRaises(c.RecorderSettingsError):
            await service.report()
        self.assertEqual((reader.calls, client.calls), ([], []))

    async def test_authority_change_during_any_read_fails_closed(self):
        for stage in ("recorder", "homeassistant.packages", "homeassistant", "recorder/info", c.ENTITY_OPTIONS_TYPE):
            for change in ("generation", "version", "retired"):
                with self.subTest(stage=stage, change=change):
                    service, reader, client, core, telemetry, token = setup()

                    def bump(item, stage=stage, change=change, core=core):
                        kind = item if isinstance(item, str) else item[0]["type"]
                        if kind == stage:
                            if change == "generation":
                                core.generation += 1
                            elif change == "version":
                                core.current_observation = SimpleNamespace(version="2026.11.0")
                            else:
                                core.available = False
                    reader.hook = client.hook = bump
                    try:
                        with self.assertRaises(c.RecorderSettingsError) as caught:
                            await service.report(["light.kitchen"])
                        self.assertEqual(caught.exception.reason, "authority_unavailable")
                        dispatched = reader.calls + [batch[0]["type"] for batch in client.calls]
                        self.assertEqual(dispatched[-1], stage)
                    finally:
                        end_request(token)

    async def test_yaml_result_read_under_changed_authority_is_not_projected(self):
        # The next read's pre-dispatch check would also stop the report; this
        # pins that each YAML read itself rechecks before its result is used.
        for change in ("generation", "version", "retired"):
            with self.subTest(change=change):
                service, reader, client, core, telemetry, token = setup()
                try:
                    authority = service.authority()

                    def bump(item, change=change, core=core):
                        if change == "generation":
                            core.generation += 1
                        elif change == "version":
                            core.current_observation = SimpleNamespace(version="2026.11.0")
                        else:
                            core.available = False
                    reader.hook = bump
                    with self.assertRaises(c.RecorderSettingsError) as caught:
                        await service._yaml(reader, c.YAML_RECORDER_PATH, authority)
                    self.assertEqual(caught.exception.reason, "authority_unavailable")
                    self.assertEqual(reader.calls, [c.YAML_RECORDER_PATH])
                finally:
                    end_request(token)

    async def test_unconfigured_runtime_refused(self):
        with self.assertRaises(c.RecorderSettingsError) as caught:
            RecorderSettingsRuntime().require()
        self.assertEqual(caught.exception.reason, "authority_unavailable")

    async def test_runtime_known_secrets_and_reader_factory(self):
        runtime = RecorderSettingsRuntime()
        factory = Mock(return_value=None)
        runtime.configure(SyntheticClient(healthy_ws()), SyntheticCore(), settings(), yaml_reader_factory=factory)
        service = runtime.require()
        self.assertEqual(service.known_secrets, (ACCESS_SECRET, HA_TOKEN))
        self.assertIs(service.yaml_reader_factory, factory)
        factory.assert_not_called()

    async def test_reader_is_captured_once_per_report(self):
        service, reader, client, core, telemetry, token = setup()
        self.addCleanup(end_request, token)
        factory = Mock(return_value=reader)
        service.yaml_reader_factory = factory
        await service.report()
        factory.assert_called_once_with()

    async def test_closed_error_reasons(self):
        self.assertEqual(c.RecorderSettingsError(UNKNOWN_VALUE).reason, "sources_unavailable")
        self.assertEqual(c.RecorderSettingsError("invalid_arguments").code, ErrorCode.INVALID_REQUEST)
        self.assertEqual(c.RecorderSettingsError("authority_unavailable").code, ErrorCode.PROVIDER_UNAVAILABLE)
        self.assertEqual(c.RecorderSettingsError("sources_unavailable").code, ErrorCode.ANALYSIS_UNAVAILABLE)

    async def test_prior_telemetry_error_restored_on_partial(self):
        service, reader, client, core, telemetry, token = setup(
            ws_values={"recorder/info": HomeAssistantTimeoutError(), c.ENTITY_OPTIONS_TYPE: {}})
        self.addCleanup(end_request, token)
        telemetry.error_code = None
        report = await service.report()
        self.assertFalse(report["complete"])
        self.assertIsNone(telemetry.error_code)
        self.assertEqual(telemetry.completeness, "partial")


def docstring_ids(tree):
    return {id(node.body[0].value) for node in ast.walk(tree)
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant)}


class NoWritePathTests(unittest.IsolatedAsyncioTestCase):
    def sources(self):
        return {path.name: path.read_text() for path in sorted(PACKAGE.glob("*.py"))} | {"tool": TOOL.read_text()}

    def test_fixed_command_contract(self):
        self.assertEqual(dict(c.INFO_COMMAND), {"type": "recorder/info"})
        self.assertEqual(c.ENTITY_OPTIONS_TYPE, "recorder/entity_options/get")
        with self.assertRaises(TypeError):
            c.INFO_COMMAND["type"] = "recorder/purge"  # type: ignore[index]
        self.assertEqual((c.YAML_RECORDER_PATH, c.YAML_PACKAGES_PATH, c.YAML_CORE_PATH),
                         ("recorder", "homeassistant.packages", "homeassistant"))
        self.assertEqual(YAML_CONFIG_PATHS, frozenset({"recorder", "homeassistant.packages", "homeassistant"}))

    def test_static_only_fixed_reads(self):
        """The package names only the two recorder reads; the client is reached
        only through ``command_batch``; YAML only through the injected reader
        with contract paths; no HTTP, Supervisor, service call or provider."""

        literals, client_attributes, reader_calls = set(), set(), []
        for name, text in self.sources().items():
            tree = ast.parse(text)
            skip = docstring_ids(tree)
            for node in ast.walk(tree):
                if (isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip
                        and node.value.startswith(("recorder/", "config/", "entity/", "search/", "backup/",
                                                   "hassio/", "supervisor/", "homeassistant/"))):
                    literals.add(node.value)
                if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Attribute) \
                        and node.value.attr == "client":
                    client_attributes.add(node.attr)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "read":
                    reader_calls.append((name, ast.unparse(node)))
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    module = (node.module or "") if isinstance(node, ast.ImportFrom) else ""
                    names = [alias.name for alias in node.names]
                    for banned in ("httpx", "aiohttp", "requests", "websockets", "supervisor", "upstream",
                                   "governance", "clients", "providers", "subprocess", "socket"):
                        self.assertFalse(banned in module or any(banned in n for n in names), (name, module, names))
        self.assertEqual(literals, {"recorder/info", "recorder/entity_options/get"})
        self.assertEqual(client_attributes, {"command_batch"})
        self.assertEqual(reader_calls, [("service.py", "reader.read(path)")])
        service = ast.parse(self.sources()["service.py"])
        yaml_paths = [ast.unparse(node.args[1]) for node in ast.walk(service)
                      if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                      and node.func.attr == "_yaml"]
        self.assertEqual(sorted(yaml_paths), sorted(["c.YAML_RECORDER_PATH", "c.YAML_PACKAGES_PATH",
                                                     "c.YAML_CORE_PATH"]))
        for text in self.sources().values():
            for word in ("call_service", "post(", "put(", "delete(", "recorder/purge", "recorder/disable",
                         "recorder/enable", "entity_registry/update", "options_flow", "recorder/import",
                         "update_statistics", "clear_statistics"):
                self.assertNotIn(word, text)

    async def test_runtime_dispatch_is_exactly_the_fixed_reads(self):
        service, reader, client, _core, _telemetry, token = setup()
        self.addCleanup(end_request, token)
        await service.report(["light.kitchen"])
        self.assertEqual(reader.calls, ["recorder", "homeassistant.packages", "homeassistant"])
        self.assertEqual(client.calls, [[{"type": "recorder/info"}],
                                        [{"type": "recorder/entity_options/get", "entity_id": "light.kitchen"}]])

    def test_exact_batch_allowlist(self):
        self.assertEqual(ws._BATCH_COMMAND_KEYS, {
            "automation/config": frozenset({"type", "entity_id"}),
            "script/config": frozenset({"type", "entity_id"}),
            "search/related": frozenset({"type", "item_type", "item_id"}),
            "recorder/entity_options/get": frozenset({"type", "entity_id"}),
            "recorder/info": frozenset({"type"}),
        })
        self.assertEqual(set(ws._SEARCH_IDENTITY), {"automation", "script", "entity"})
        allowed = [{"type": "recorder/info"},
                   {"type": "recorder/entity_options/get", "entity_id": "light.kitchen"},
                   {"type": "search/related", "item_type": "entity", "item_id": "sensor.yaml_temp"}]
        refused = [{"type": "recorder/info", "entity_id": "light.kitchen"},
                   {"type": "recorder/entity_options/get"},
                   {"type": "recorder/entity_options/get", "entity_id": "Light.Kitchen"},
                   {"type": "recorder/entity_options/get", "entity_id": "light.kitchen", "options": {}},
                   {"type": "recorder/entity_options/update", "entity_id": "light.kitchen"},
                   {"type": "recorder/purge"}, {"type": "recorder/disable"}, {"type": "recorder/update_statistics_metadata"},
                   {"type": "search/related", "item_type": "entity", "item_id": "light..x"},
                   {"type": "search/related", "item_type": "config_entry", "item_id": "light.kitchen"},
                   {"type": "config/entity_registry/update", "entity_id": "light.kitchen"},
                   ["recorder/info"], None]
        for payload in allowed:
            self.assertTrue(ws.batch_command_allowed(payload), payload)
        for payload in refused:
            self.assertFalse(ws.batch_command_allowed(payload), payload)


class RegistrationTests(unittest.IsolatedAsyncioTestCase):
    def test_core_gate_mapping(self):
        self.assertEqual(STATIC_TOOL_CORE_REQUIREMENTS["get_recorder_settings"], c.CORE_REQUIREMENTS)
        self.assertEqual(c.CORE_REQUIREMENTS, ("core.basic_rest_read", "core.basic_websocket_read"))

    def test_capability_metadata_read_only(self):
        native = next(item for item in BETA_NATIVE_CAPABILITIES if item["tool"] == "get_recorder_settings")
        self.assertEqual((native["risk"], native["operation_class"], native["routing"], native["fallback"],
                          native["direct_write_allowed"]), ("read", "read", "engineering_native", "none", False))
        matrix = next(item for item in CAPABILITY_PROVIDER_MATRIX if item["tool"] == "get_recorder_settings")
        self.assertEqual((matrix["selected_provider"], matrix["fallback_policy"], matrix["trust_mode"]),
                         ("engineering", "none", "reviewed_native_read"))

    def test_tool_annotations_and_schema(self):
        tool = registered_tools(get_registered_server())["get_recorder_settings"]
        annotations = tool.annotations
        self.assertEqual((annotations.readOnlyHint, annotations.destructiveHint, annotations.idempotentHint,
                          annotations.openWorldHint), (True, False, True, False))
        schema = tool.parameters
        self.assertIs(schema["additionalProperties"], False)
        self.assertEqual(set(schema["properties"]), {"entity_ids"})
        self.assertEqual(schema["properties"]["entity_ids"]["maxItems"], c.MAX_ENTITY_IDS)
        self.assertEqual(schema["properties"]["entity_ids"]["default"], [])
        self.assertNotIn("required", schema)

    async def test_served_tool_rejects_raw_arguments_without_echo(self):
        tool = registered_tools(get_registered_server())["get_recorder_settings"]
        for arguments in (None, [], {"entity_ids": UNKNOWN_VALUE}, {"entity_ids": [UNKNOWN_VALUE]},
                          {"entity_ids": ["light.kitchen"] * 2}, {UNKNOWN_KEY: UNKNOWN_VALUE},
                          {"entity_ids": [f"sensor.s{n}" for n in range(51)]}):
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
                            {"entity_ids": ["light.kitchen"], "password": DB_PASSWORD}):
                    response = await client.post(f"/{config.access_secret}/mcp", json={
                        "jsonrpc": "2.0", "id": "synthetic-request", "method": "tools/call",
                        "params": {"name": "get_recorder_settings", "arguments": bad}})
                    for value in (UNKNOWN_VALUE, UNKNOWN_KEY, DB_PASSWORD):
                        self.assertNotIn(value, response.text)
                    self.assertIn("invalid_request", response.text)
            text = audit_path.read_text()
            for value in (UNKNOWN_VALUE, UNKNOWN_KEY, DB_PASSWORD):
                self.assertNotIn(value, text)
            app.assert_not_called()
            core.acquire.assert_not_called()

    async def test_gateway_positive_output_and_audit(self):
        response, data, audit = await serve_through_gateway({"entity_ids": ["light.kitchen", "sensor.temperature"]})
        self.assertTrue(data["success"], data)
        self.assertEqual(data["data"]["schema"], c.SCHEMA)
        self.assertEqual(data["metadata"]["data_providers"], ["direct_ha_api", "upstream_read_gateway"])
        self.assertEqual(data["metadata"]["upstream_calls"], 3)
        self.assertFalse(data["metadata"]["fallback_occurred"])
        self.assertNotIn("upstream_calls", data["data"])
        for value in NEEDLES:
            self.assertNotIn(value, response + audit)
        rows = [json.loads(row) for row in audit.splitlines()]
        record = next(r for r in rows if r.get("tool_name") == "get_recorder_settings")
        self.assertEqual(record["resource_ids"], {})
        self.assertEqual(record["parameters"]["profile"], "recorder-settings-read-v1")
        self.assertEqual(record["parameters"]["entity_count"], 2)
        self.assertNotIn("light.kitchen", json.dumps(record))
        self.assertEqual(record["analysis_summary"]["upstream_calls"], 3)
        self.assertEqual(record["analysis_summary"]["fallback"], "none")


# A lowercase synthetic access secret that is also a valid entity object id,
# so it can reach every identifier-bearing field. Assembled at test time.
ENTITY_SECRET = syn.needle("finalenvelope").lower()


class FinalEnvelopeSanitizationTests(Base):
    """H1: the strict sanitizer runs on the complete final envelope, once."""

    async def test_reviewer_reproduction_drift_omission_through_gateway(self):
        entity = "sensor." + ENTITY_SECRET
        response, data, audit = await serve_through_gateway(
            {"entity_ids": [entity]}, yaml({"exclude": {"entities": [entity]}}), access_secret=ENTITY_SECRET)
        self.assertTrue(data["success"], data)
        report = data["data"]
        self.assertEqual(report["entities"][0]["agreement"], "filter_drift")
        self.assertTrue(any(item.startswith("filter_drift:") for item in report["omissions"]))
        self.assertIn("redacted_by_policy", report["omissions"])
        self.assertFalse(report["complete"])
        self.assertEqual(report["completeness"], "partial")
        self.assertNotIn(ENTITY_SECRET, response)
        self.assertNotIn(ENTITY_SECRET, audit)

    async def test_planted_secret_never_appears_in_any_response_field(self):
        entity = "sensor." + ENTITY_SECRET
        failed = "light." + ENTITY_SECRET
        recorder = {
            "purge_keep_days": "yes", "commit_interval": "1:20",
            "include": {"domains": [ENTITY_SECRET], "entity_globs": ["sensor.*" + ENTITY_SECRET],
                        "entities": ["switch." + ENTITY_SECRET]},
            "exclude": {"entities": [entity], "entity_globs": [ENTITY_SECRET + ".*"],
                        "event_types": [ENTITY_SECRET]},
        }

        def options(payloads):
            return [(False, {"code": "unknown_error", "message": ENTITY_SECRET})
                    if item["entity_id"] == failed else RECORDED for item in payloads]

        async def run(known_secrets):
            service, *_rest, token = setup(
                yaml(recorder), {"recorder/info": (True, dict(INFO)), c.ENTITY_OPTIONS_TYPE: options},
                known_secrets=known_secrets)
            try:
                return await service.report([entity, failed, "light.kitchen"])
            finally:
                end_request(token)

        # Control: unprotected, the text reaches every identifier-bearing field.
        control = await run(())
        for field in ("filters", "entities", "omissions"):
            self.assertIn(ENTITY_SECRET, json.dumps(control[field]), field)
        report = await run((ENTITY_SECRET,))
        text = json.dumps(report)
        self.assertNotIn(ENTITY_SECRET, text)
        self.assertEqual(len(report["entities"]), 3)
        for field in ("settings", "filters", "entities", "omissions", "sources", "coverage"):
            self.assertNotIn(ENTITY_SECRET, json.dumps(report[field]), field)
        self.assertIn("redacted_by_policy", report["omissions"])
        self.assertFalse(report["complete"])
        self.assertEqual(report["completeness"], "partial")

    async def test_no_redaction_keeps_the_envelope_complete(self):
        report = await self.run_report(entity_ids=["light.kitchen"])
        self.assertNotIn("redacted_by_policy", report["omissions"])
        self.assertTrue(report["complete"])
        self.assertEqual(report["completeness"], "complete")


async def serve_through_gateway(arguments, yaml_values=None, ws_values=None, *, access_secret=ACCESS_SECRET):
    service, _reader, _client, core, _telemetry, token = setup(
        yaml_values, ws_values, known_secrets=(HA_TOKEN, access_secret))
    end_request(token)
    core.acquire = Mock(return_value=object())
    core.consume = Mock(return_value=())
    core.revalidate = Mock(return_value=True)
    core.finish = Mock(return_value=True)
    core.release = Mock(return_value=True)

    async def app(scope, receive, send):
        rpc = json.loads((await receive())["body"])
        tool = registered_tools(get_registered_server())["get_recorder_settings"]
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
        with patch.object(RECORDER_SETTINGS, "service", service), patch.object(public, "SETTINGS", config_):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway),
                                         base_url="http://127.0.0.1:8100") as http:
                response = await http.post(f"/{config_.access_secret}/mcp", json={
                    "jsonrpc": "2.0", "id": "synthetic-request", "method": "tools/call",
                    "params": {"name": "get_recorder_settings", "arguments": arguments}})
        audit = audit_path.read_text()
    core.finish.assert_called_once()
    return response.text, json.loads(response.json()["result"]["content"][0]["text"]), audit


try:
    import test_hamcp162_yaml_admission as yaml_admission  # noqa: E402
except ImportError:  # pragma: no cover - the module is in this repository
    yaml_admission = None


class YamlConfigReaderTests(yaml_admission.Harness):
    """The gateway's fixed recorder-path YAML route (``yaml_config_reader``)."""

    async def test_admitted_reader_dispatches_only_contract_paths(self):
        for version in yaml_admission.VERSIONS:
            with self.subTest(version=version):
                gateway, transport = await self.gateway(version)
                audit = Mock()
                reader = gateway.yaml_config_reader(audit=audit)
                self.assertIsNotNone(reader)
                self.assertIn(f"ha-mcp {version}", reader.provenance)
                for path in ("recorder", "homeassistant.packages", "homeassistant"):
                    result = await reader.read(path)
                    data, failure = gateway_envelope(result, path)
                    self.assertIsNone(failure, result)
                    self.assertEqual(data["yaml_path"], path)
                for path in ("http", "recorder.db_url", "homeassistant.auth_providers", "", "../secrets",
                             "Recorder", None):
                    result = await reader.read(path)
                    self.assertEqual(result, {"success": False,
                                              "details": {"failure_category": "argument_validation"}})
                self.assertEqual([call[0] for call in transport.sent], [yaml_admission.TOOL] * 3)
                self.assertEqual([call[1]["yaml_path"] for call in transport.sent],
                                 ["recorder", "homeassistant.packages", "homeassistant"])
                events = [call.args[0] for call in audit.write.call_args_list]
                self.assertEqual([e["event"] for e in events], ["recorder_settings_yaml_read"] * 3)
                self.assertEqual({e["provider"] for e in events}, {"upstream_read_gateway"})
                self.assertFalse(any(e["fallback_occurred"] for e in events))
                self.assertIsNone(current_telemetry())

    async def test_reader_propagates_caller_and_restores_parent_context(self):
        gateway, transport = await self.gateway()
        audit = Mock()
        telemetry, token = begin_request()
        telemetry.caller_id = "synthetic_authenticated_caller"
        telemetry.core_dispatch_authorizer = lambda: True
        try:
            result = await gateway.yaml_config_reader(audit=audit).read("recorder")
            self.assertTrue(result["success"])
            self.assertIs(current_telemetry(), telemetry)
        finally:
            end_request(token)
        self.assertEqual(audit.write.call_args.args[0]["parent_request_id"], telemetry.request_id)

    async def test_parent_authority_refusal_dispatches_nothing(self):
        gateway, transport = await self.gateway()
        reader = gateway.yaml_config_reader()
        telemetry, token = begin_request()
        telemetry.core_dispatch_authorizer = lambda: False
        try:
            result = await reader.read("recorder")
        finally:
            end_request(token)
        self.assertFalse(result["success"])
        self.assertEqual(transport.sent, [])

    async def test_no_reader_route_supplies_no_reader(self):
        self.assertIsNone(UpstreamReadGateway().yaml_config_reader())
        gateway, transport = await self.gateway(tools=yaml_admission.stock_catalog("8.5.0"))
        self.assertIsNone(gateway.yaml_config_reader())
        self.assertEqual(transport.sent, [])

    async def test_catalog_drift_retires_a_captured_reader(self):
        gateway, transport = await self.gateway()
        reader = gateway.yaml_config_reader()
        transport.set_tools(yaml_admission.stock_catalog("8.5.0"))
        await self.reinitialize(gateway, transport)
        self.assertFalse(reader.current())
        result = await reader.read("recorder")
        self.assertFalse(result["success"])
        self.assertEqual(transport.sent, [])

    @staticmethod
    async def reinitialize(gateway, transport):
        from mcp.server.fastmcp import FastMCP
        await gateway.initialize(FastMCP("synthetic-hamcp132-reinitialize"))

    async def test_script_dependency_reader_is_unchanged_beside_it(self):
        gateway, transport = await self.gateway()
        self.assertIsNotNone(gateway.yaml_config_reader())
        script_reader = gateway.script_dependency_reader()
        self.assertIsNotNone(script_reader)
        self.assertTrue(script_reader.current())
        self.assertFalse((await script_reader.read("../script.x"))["success"])
        self.assertEqual(transport.sent, [])

    async def test_service_reads_real_gateway_envelopes(self):
        gateway, transport = await self.gateway()
        core = SyntheticCore()
        service = RecorderSettingsService(
            SyntheticClient(healthy_ws()), core,
            yaml_reader_factory=lambda: gateway.yaml_config_reader(), clock=lambda: NOW)
        telemetry, token = begin_request()
        telemetry.caller_id = "synthetic_authenticated_caller"
        telemetry.core_dispatch_authorizer = lambda: core.available
        try:
            report = await service.report()
        finally:
            end_request(token)
        # The synthetic peer answers every path with ``{"purge_keep_days": 10}``,
        # so packages are reported present and unwritten keys unknown. The
        # parsed integer is definite (owner decision 1).
        self.assertEqual(report["settings"]["purge_keep_days"], {"value": 10, "origin": "configured"})
        self.assertEqual(report["packages"], "present")
        self.assertEqual(report["settings"]["auto_purge"]["reason"], "packages_present")
        self.assertEqual(source(report, "configuration_yaml:recorder")["status"], "complete")
        self.assertIn("ha-mcp 8.5.0", report["coverage"]["yaml_reader"])
        self.assertEqual([call[1]["yaml_path"] for call in transport.sent], ["recorder", "homeassistant.packages"])


if __name__ == "__main__":
    unittest.main()
