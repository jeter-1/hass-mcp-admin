import copy
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
BETA_DIR = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(BETA_DIR))

from ha_mcp_engineering.clients.rest import ExpectedHttpStatus  # noqa: E402
from ha_mcp_engineering.errors import HomeAssistantApiError  # noqa: E402
from ha_mcp_engineering.governance.resources import (  # noqa: E402
    ConfigurationMutationCompletedUnexpectedlyError,
    ConfigurationMutationNotDispatchedError,
    ConfigurationResourceGateway,
    RESOURCE_NORMALIZATION_VERSION,
    normalize_resource_config,
    resource_fingerprint,
    resource_identity_matches,
    structured_resource_diff,
    validate_resource_create_identity,
    validate_resource,
)


AUTOMATION = {
    "alias": "HVAC guard",
    "trigger": [{"platform": "state", "entity_id": "sensor.temperature"}],
    "action": [{"service": "climate.set_temperature"}],
    "mode": "single",
}
SCRIPT = {
    "alias": "Set HVAC comfort",
    "sequence": [
        {
            "service": "climate.set_temperature",
            "target": {"entity_id": "climate.downstairs"},
            "data": {"temperature": 22},
        }
    ],
    "mode": "single",
}
INPUT_BOOLEAN = {"name": "HVAC override", "icon": "mdi:toggle-switch"}
INPUT_NUMBER = {
    "name": "HVAC target",
    "min": 16,
    "max": 30,
    "step": 0.5,
    "mode": "slider",
    "unit_of_measurement": "°C",
}


class FakeRestClient:
    def __init__(self):
        self.calls = []
        self.responses = {}

    async def request(
        self,
        method,
        path,
        body=None,
        raw=False,
        expected_statuses=frozenset(),
    ):
        self.calls.append(
            {
                "method": method,
                "path": path,
                "body": copy.deepcopy(body),
                "raw": raw,
                "expected_statuses": expected_statuses,
            }
        )
        response = self.responses.get((method, path))
        if isinstance(response, Exception):
            raise response
        if callable(response):
            return response(body)
        if response is None and method == "POST":
            return {"result": "ok"}
        return copy.deepcopy(response)


class FakeWebSocketClient:
    def __init__(self):
        self.calls = []
        self.responses = {}

    async def command(self, payload):
        self.calls.append(copy.deepcopy(payload))
        response = self.responses.get(payload["type"])
        if isinstance(response, Exception):
            raise response
        if callable(response):
            return response(payload)
        return copy.deepcopy(response)


class ResourceValidationTests(unittest.TestCase):
    def assertValid(self, resource_type, resource_id, config):
        valid, errors, warnings = validate_resource(
            resource_type, resource_id, config
        )
        self.assertTrue(valid, errors)
        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])

    def assertInvalid(self, resource_type, resource_id, config, phrase):
        valid, errors, _ = validate_resource(
            resource_type, resource_id, config
        )
        self.assertFalse(valid)
        self.assertTrue(
            any(phrase in error for error in errors),
            f"{phrase!r} was not present in {errors!r}",
        )

    def test_supported_resource_configurations_are_valid(self):
        self.assertValid("automation", "hvac_guard", AUTOMATION)
        self.assertValid("script", "set_hvac_comfort", SCRIPT)
        self.assertValid(
            "input_boolean", "input_boolean.hvac_override", INPUT_BOOLEAN
        )
        self.assertValid(
            "input_number", "input_number.hvac_target", INPUT_NUMBER
        )

    def test_helper_create_identity_is_exact_and_conservative(self):
        self.assertEqual(
            validate_resource_create_identity(
                "input_boolean",
                "input_boolean.hvac_override",
                INPUT_BOOLEAN,
            ),
            [],
        )
        self.assertTrue(
            validate_resource_create_identity(
                "input_boolean",
                "input_boolean.different",
                INPUT_BOOLEAN,
            )
        )
        self.assertTrue(
            validate_resource_create_identity(
                "input_boolean",
                "input_boolean.hvac_override",
                {"name": "HVAC / override"},
            )
        )

    def test_unsupported_helper_type_fails_closed(self):
        self.assertInvalid(
            "input_text",
            "input_text.note",
            {"name": "Note", "min": 0, "max": 10},
            "not supported",
        )

    def test_resource_ids_are_strict_and_helper_ids_are_full(self):
        self.assertInvalid("script", "HVAC-script", SCRIPT, "lowercase")
        self.assertInvalid(
            "input_boolean", "hvac_override", INPUT_BOOLEAN, "full"
        )
        self.assertInvalid(
            "input_boolean",
            "input_number.hvac_override",
            INPUT_BOOLEAN,
            "full",
        )
        self.assertInvalid(
            "input_number",
            "input_number.hvac target",
            INPUT_NUMBER,
            "full",
        )

    def test_script_schema_rejects_missing_sequence_unknown_fields_and_bad_mode(self):
        proposed = {"alias": "Invalid", "mode": "broadcast", "action": []}
        valid, errors, _ = validate_resource(
            "script", "invalid_script", proposed
        )
        self.assertFalse(valid)
        self.assertTrue(any("sequence" in error for error in errors))
        self.assertTrue(any("unsupported fields" in error for error in errors))
        self.assertTrue(any("script mode" in error for error in errors))

    def test_script_max_is_bounded_and_mode_specific(self):
        proposed = copy.deepcopy(SCRIPT)
        proposed["max"] = 2
        self.assertInvalid(
            "script", "set_hvac_comfort", proposed, "queued or parallel"
        )
        proposed["mode"] = "queued"
        self.assertValid("script", "set_hvac_comfort", proposed)
        proposed["max"] = True
        self.assertInvalid(
            "script", "set_hvac_comfort", proposed, "integer"
        )

    def test_input_number_schema_is_strict(self):
        proposed = copy.deepcopy(INPUT_NUMBER)
        proposed["min"] = 30
        self.assertInvalid(
            "input_number",
            "input_number.hvac_target",
            proposed,
            "less than max",
        )
        proposed = copy.deepcopy(INPUT_NUMBER)
        proposed["step"] = 0
        self.assertInvalid(
            "input_number",
            "input_number.hvac_target",
            proposed,
            "greater than zero",
        )
        proposed = copy.deepcopy(INPUT_NUMBER)
        proposed["mode"] = "dial"
        self.assertInvalid(
            "input_number",
            "input_number.hvac_target",
            proposed,
            "box or slider",
        )

    def test_helper_initial_values_follow_home_assistant_schema(self):
        boolean = copy.deepcopy(INPUT_BOOLEAN)
        boolean["initial"] = True
        self.assertValid(
            "input_boolean", "input_boolean.hvac_override", boolean
        )
        boolean["initial"] = "on"
        self.assertInvalid(
            "input_boolean",
            "input_boolean.hvac_override",
            boolean,
            "must be a boolean",
        )

        number = copy.deepcopy(INPUT_NUMBER)
        number["initial"] = 22
        self.assertValid(
            "input_number", "input_number.hvac_target", number
        )
        number["initial"] = 31
        self.assertInvalid(
            "input_number",
            "input_number.hvac_target",
            number,
            "within min and max",
        )
        number["initial"] = True
        self.assertInvalid(
            "input_number",
            "input_number.hvac_target",
            number,
            "finite number",
        )

    def test_optional_icons_and_units_match_home_assistant_types(self):
        boolean = copy.deepcopy(INPUT_BOOLEAN)
        boolean["icon"] = None
        self.assertInvalid(
            "input_boolean",
            "input_boolean.hvac_override",
            boolean,
            "prefix:name",
        )
        boolean["icon"] = "toggle-switch"
        self.assertInvalid(
            "input_boolean",
            "input_boolean.hvac_override",
            boolean,
            "prefix:name",
        )
        number = copy.deepcopy(INPUT_NUMBER)
        number["unit_of_measurement"] = None
        self.assertInvalid(
            "input_number",
            "input_number.hvac_target",
            number,
            "bounded string",
        )

    def test_script_trace_is_supported_and_reserved_ids_are_rejected(self):
        proposed = copy.deepcopy(SCRIPT)
        proposed["trace"] = {"stored_traces": 5}
        self.assertValid("script", "set_hvac_comfort", proposed)
        proposed["trace"] = []
        self.assertInvalid(
            "script", "set_hvac_comfort", proposed, "trace must be an object"
        )
        for script_id in ("reload", "turn_on", "turn_off", "toggle"):
            self.assertInvalid(
                "script", script_id, SCRIPT, "reserved"
            )

    def test_secret_fields_mcp_urls_and_known_secrets_are_rejected(self):
        script = copy.deepcopy(SCRIPT)
        script["sequence"][0]["data"] = {"api_key": "do-not-store"}
        self.assertInvalid(
            "script", "set_hvac_comfort", script, "secret-bearing"
        )

        helper = {
            "name": "https://example.test/mcp/private-secret",
        }
        self.assertInvalid(
            "input_boolean",
            "input_boolean.hvac_override",
            helper,
            "MCP URLs",
        )

        valid, errors, _ = validate_resource(
            "input_boolean",
            "input_boolean.hvac_override",
            {"name": "value contains known-sensitive-value"},
            ("known-sensitive-value",),
        )
        self.assertFalse(valid)
        self.assertTrue(
            any("prohibited sensitive data" in error for error in errors)
        )

    def test_normalization_hash_diff_and_identity_are_deterministic(self):
        current = {
            "id": "set_hvac_comfort",
            "mode": "single",
            "sequence": copy.deepcopy(SCRIPT["sequence"]),
            "alias": "Set HVAC comfort",
        }
        reordered = {
            "alias": "Set HVAC comfort",
            "sequence": copy.deepcopy(SCRIPT["sequence"]),
            "mode": "single",
        }
        self.assertEqual(RESOURCE_NORMALIZATION_VERSION, 1)
        self.assertEqual(
            normalize_resource_config("script", current), reordered
        )
        self.assertEqual(
            resource_fingerprint("script", current),
            resource_fingerprint("script", reordered),
        )
        changed = copy.deepcopy(reordered)
        changed["alias"] = "New alias"
        diff = structured_resource_diff("script", current, changed)
        self.assertEqual(diff["meaningful_change_count"], 1)
        self.assertEqual(diff["changed_fields"][0]["field"], "alias")
        self.assertTrue(
            resource_identity_matches(
                "automation",
                "hvac_guard",
                {"id": "hvac_guard", **AUTOMATION},
            )
        )
        self.assertFalse(
            resource_identity_matches(
                "automation",
                "hvac_guard",
                {"id": "other", **AUTOMATION},
            )
        )
        self.assertTrue(
            resource_identity_matches(
                "input_boolean",
                "input_boolean.hvac_override",
                {"id": "hvac_override", **INPUT_BOOLEAN},
            )
        )

    def test_input_number_numeric_normalization_matches_float_readback(self):
        proposed = copy.deepcopy(INPUT_NUMBER)
        proposed["initial"] = 22
        readback = copy.deepcopy(proposed)
        for key in ("min", "max", "step", "initial"):
            readback[key] = float(readback[key])
        self.assertEqual(
            normalize_resource_config("input_number", proposed),
            normalize_resource_config("input_number", readback),
        )
        self.assertEqual(
            resource_fingerprint("input_number", proposed),
            resource_fingerprint("input_number", readback),
        )


class ConfigurationResourceGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.rest = FakeRestClient()
        self.websocket = FakeWebSocketClient()
        self.websocket.responses["input_boolean/list"] = []
        self.websocket.responses["input_number/list"] = []
        self.rest.responses[
            ("GET", "/states/input_boolean.hvac_override")
        ] = ExpectedHttpStatus(404)
        self.rest.responses[
            ("GET", "/states/input_number.hvac_target")
        ] = ExpectedHttpStatus(404)
        self.gateway = ConfigurationResourceGateway(
            self.rest, self.websocket
        )

    async def test_automation_get_and_mutations_use_only_exact_endpoint(self):
        path = "/config/automation/config/hvac_guard"
        self.rest.responses[("GET", path)] = {
            "id": "hvac_guard",
            **AUTOMATION,
        }
        value = await self.gateway.get("automation", "hvac_guard")
        await self.gateway.create(
            "automation", "hvac_guard", copy.deepcopy(AUTOMATION)
        )
        await self.gateway.update(
            "automation", "hvac_guard", copy.deepcopy(AUTOMATION)
        )
        self.assertEqual(value["id"], "hvac_guard")
        self.assertEqual(
            [(call["method"], call["path"]) for call in self.rest.calls],
            [("GET", path), ("POST", path), ("POST", path)],
        )
        self.assertEqual(
            self.rest.calls[0]["expected_statuses"], frozenset({404})
        )
        self.assertEqual(self.rest.calls[1]["body"], AUTOMATION)
        self.assertEqual(self.websocket.calls, [])

    async def test_script_get_and_mutations_use_only_exact_endpoint(self):
        path = "/config/script/config/set_hvac_comfort"
        self.rest.responses[("GET", path)] = copy.deepcopy(SCRIPT)
        value = await self.gateway.get(
            "script", "set_hvac_comfort"
        )
        await self.gateway.create(
            "script", "set_hvac_comfort", copy.deepcopy(SCRIPT)
        )
        await self.gateway.update(
            "script", "set_hvac_comfort", copy.deepcopy(SCRIPT)
        )
        self.assertEqual(value, SCRIPT)
        self.assertEqual(
            [(call["method"], call["path"]) for call in self.rest.calls],
            [("GET", path), ("POST", path), ("POST", path)],
        )
        self.assertEqual(self.rest.calls[2]["body"], SCRIPT)
        self.assertEqual(self.websocket.calls, [])

    async def test_expected_config_404_returns_absence(self):
        path = "/config/script/config/missing_script"
        self.rest.responses[("GET", path)] = ExpectedHttpStatus(404)
        self.assertIsNone(
            await self.gateway.get("script", "missing_script")
        )

    async def test_wrong_automation_identity_is_rejected(self):
        path = "/config/automation/config/hvac_guard"
        self.rest.responses[("GET", path)] = {
            "id": "different",
            **AUTOMATION,
        }
        with self.assertRaises(HomeAssistantApiError):
            await self.gateway.get("automation", "hvac_guard")

    async def test_helper_read_uses_exact_list_command(self):
        self.websocket.responses["input_boolean/list"] = [
            {"id": "other", "name": "Other"},
            {"id": "hvac_override", **INPUT_BOOLEAN},
        ]
        result = await self.gateway.get(
            "input_boolean", "input_boolean.hvac_override"
        )
        self.assertEqual(result["id"], "hvac_override")
        self.assertEqual(
            self.websocket.calls, [{"type": "input_boolean/list"}]
        )
        self.assertEqual(self.rest.calls, [])

    async def test_helper_create_verifies_generated_full_entity_id(self):
        self.websocket.responses["input_boolean/create"] = {
            "id": "hvac_override",
            **INPUT_BOOLEAN,
        }
        result = await self.gateway.create(
            "input_boolean",
            "input_boolean.hvac_override",
            copy.deepcopy(INPUT_BOOLEAN),
        )
        self.assertEqual(result["id"], "hvac_override")
        self.assertEqual(
            self.websocket.calls,
            [
                {"type": "input_boolean/list"},
                {"type": "input_boolean/create", **INPUT_BOOLEAN},
            ],
        )

    async def test_helper_generated_id_mismatch_fails_closed(self):
        self.websocket.responses["input_boolean/create"] = {
            "id": "hvac_override_2",
            **INPUT_BOOLEAN,
        }
        with self.assertRaises(
            ConfigurationMutationCompletedUnexpectedlyError
        ) as caught:
            await self.gateway.create(
                "input_boolean",
                "input_boolean.hvac_override",
                copy.deepcopy(INPUT_BOOLEAN),
            )
        self.assertEqual(
            caught.exception.details["unexpected_resource_id"],
            "input_boolean.hvac_override_2",
        )
        self.assertTrue(caught.exception.details["orphan_risk"])
        self.assertTrue(caught.exception.mutation_dispatched)
        self.assertTrue(caught.exception.mutation_completed)

    async def test_helper_name_target_mismatch_performs_no_transport_io(self):
        with self.assertRaises(ValueError):
            await self.gateway.create(
                "input_boolean",
                "input_boolean.different",
                copy.deepcopy(INPUT_BOOLEAN),
            )
        self.assertEqual(self.websocket.calls, [])
        self.assertEqual(self.rest.calls, [])

    async def test_helper_yaml_entity_collision_stops_before_create(self):
        self.rest.responses[
            ("GET", "/states/input_boolean.hvac_override")
        ] = {"entity_id": "input_boolean.hvac_override", "state": "off"}
        with self.assertRaises(
            ConfigurationMutationNotDispatchedError
        ) as caught:
            await self.gateway.create(
                "input_boolean",
                "input_boolean.hvac_override",
                copy.deepcopy(INPUT_BOOLEAN),
            )
        self.assertEqual(
            caught.exception.details["reason"],
            "target_entity_id_reserved",
        )
        self.assertFalse(caught.exception.mutation_dispatched)
        self.assertFalse(caught.exception.mutation_completed)
        self.assertEqual(
            self.websocket.calls, [{"type": "input_boolean/list"}]
        )
        self.assertEqual(
            [call["method"] for call in self.rest.calls], ["GET"]
        )

    async def test_helper_storage_collision_stops_before_create(self):
        self.websocket.responses["input_boolean/list"] = [
            {"id": "hvac_override", **INPUT_BOOLEAN}
        ]
        with self.assertRaises(
            ConfigurationMutationNotDispatchedError
        ) as caught:
            await self.gateway.create(
                "input_boolean",
                "input_boolean.hvac_override",
                copy.deepcopy(INPUT_BOOLEAN),
            )
        self.assertEqual(
            caught.exception.details["reason"], "target_already_exists"
        )
        self.assertFalse(caught.exception.mutation_dispatched)
        self.assertFalse(caught.exception.mutation_completed)
        self.assertEqual(
            self.websocket.calls, [{"type": "input_boolean/list"}]
        )
        self.assertEqual(self.rest.calls, [])

    async def test_helper_update_is_full_exact_approved_replacement(self):
        approved = {"name": "Renamed HVAC override"}
        self.websocket.responses["input_boolean/update"] = {
            "id": "hvac_override",
            **approved,
        }
        result = await self.gateway.update(
            "input_boolean",
            "input_boolean.hvac_override",
            approved,
        )
        self.assertEqual(result["name"], "Renamed HVAC override")
        self.assertEqual(
            self.websocket.calls,
            [
                {
                    "type": "input_boolean/update",
                    "input_boolean_id": "hvac_override",
                    "name": "Renamed HVAC override",
                }
            ],
        )
        self.assertNotIn("icon", self.websocket.calls[0])

    async def test_input_number_uses_fixed_commands_and_id_field(self):
        list_responses = iter(
            [
                [{"id": "hvac_target", **INPUT_NUMBER}],
                [],
            ]
        )
        self.websocket.responses["input_number/list"] = (
            lambda _payload: next(list_responses)
        )
        self.websocket.responses["input_number/create"] = {
            "id": "hvac_target",
            **INPUT_NUMBER,
        }
        self.websocket.responses["input_number/update"] = {
            "id": "hvac_target",
            **INPUT_NUMBER,
        }
        await self.gateway.get(
            "input_number", "input_number.hvac_target"
        )
        await self.gateway.create(
            "input_number",
            "input_number.hvac_target",
            copy.deepcopy(INPUT_NUMBER),
        )
        await self.gateway.update(
            "input_number",
            "input_number.hvac_target",
            copy.deepcopy(INPUT_NUMBER),
        )
        self.assertEqual(
            [call["type"] for call in self.websocket.calls],
            [
                "input_number/list",
                "input_number/list",
                "input_number/create",
                "input_number/update",
            ],
        )
        self.assertEqual(
            self.websocket.calls[3]["input_number_id"], "hvac_target"
        )

    async def test_invalid_request_causes_no_transport_activity(self):
        with self.assertRaises(ValueError):
            await self.gateway.create(
                "input_text",
                "input_text.note",
                {"name": "Note"},
            )
        with self.assertRaises(ValueError):
            await self.gateway.update(
                "script",
                "../unsafe",
                copy.deepcopy(SCRIPT),
            )
        self.assertEqual(self.rest.calls, [])
        self.assertEqual(self.websocket.calls, [])

    async def test_full_configuration_check_is_the_only_validation_endpoint(self):
        result = await self.gateway.validate_all()
        self.assertEqual(result, {"result": "ok"})
        self.assertEqual(
            self.rest.calls,
            [
                {
                    "method": "POST",
                    "path": "/config/core/check_config",
                    "body": None,
                    "raw": False,
                    "expected_statuses": frozenset(),
                }
            ],
        )

    async def test_compact_service_aliases_preserve_closed_operations(self):
        path = "/config/script/config/set_hvac_comfort"
        self.rest.responses[("GET", path)] = copy.deepcopy(SCRIPT)
        self.assertEqual(
            await self.gateway.read("script", "set_hvac_comfort"),
            SCRIPT,
        )
        await self.gateway.write(
            "update",
            "script",
            "set_hvac_comfort",
            copy.deepcopy(SCRIPT),
        )
        with self.assertRaises(ValueError):
            await self.gateway.write(
                "remove",
                "script",
                "set_hvac_comfort",
                copy.deepcopy(SCRIPT),
            )
        self.assertEqual(
            [(call["method"], call["path"]) for call in self.rest.calls],
            [("GET", path), ("POST", path)],
        )

    def test_gateway_exposes_no_unsafe_general_write_methods(self):
        for method_name in (
            "call_service",
            "delete",
            "dispatch",
            "raw_request",
            "reload",
        ):
            self.assertFalse(hasattr(self.gateway, method_name))


if __name__ == "__main__":
    unittest.main()
